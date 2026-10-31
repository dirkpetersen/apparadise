"""App factory. Tests build the app in-process with `create_app(settings, db=..., ...)`."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from . import auth, builder_api, catalog, coordinator, registry
from .config import Settings
from .db import DB_FILENAME, Database
from .harbor import HarborClient, HarborError, HarborUnavailable
from .ldapauth import AdAuthenticator

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# No inline script/style anywhere in the frontend, so the policy can be strict.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
ASSET_CACHE = "public, max-age=86400"  # fonts and images: stable, not fingerprinted


REAP_EVERY = 300  # seconds


async def reap_once(app: FastAPI) -> list[int]:
    """Fail builds whose builder went silent, and delete their Harbor robots."""
    settings: Settings = app.state.settings
    cutoff = (datetime.now(UTC) - timedelta(minutes=settings.builder_stale_minutes)) \
        .strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    db = app.state.db
    rows = await db.run(coordinator.reap_lost, db, cutoff)
    for r in rows:
        logging.getLogger(__name__).warning("build %s failed: builder lost", r["id"])
        await db.run(db.audit.add, "system", "build.reap", "build", str(r["id"]), {})
        if r["harbor_robot_id"] is not None and app.state.harbor is not None:
            try:
                await app.state.harbor.delete_robot(r["harbor_robot_id"])
                await db.run(coordinator.set_robot, db, r["id"], None)
            except (HarborError, HarborUnavailable):
                pass  # it expires on its own
    return [r["id"] for r in rows]


async def reaper_loop(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(REAP_EVERY)
        try:
            await reap_once(app)
        except Exception:
            logging.getLogger(__name__).exception("reaper failed")


def create_app(
    settings: Settings,
    db: Database | None = None,
    authenticator: AdAuthenticator | None = None,
    clock: Callable[[], float] = time.time,
    load_catalog: bool = True,
    harbor: HarborClient | None = None,
    background: bool = True,
) -> FastAPI:
    """`db`: an already-open database (e.g. in-memory), which the caller then closes;
    `authenticator`: a fake-LDAP one; `clock` drives session expiry and the login
    throttle; `load_catalog`: read CATALOG_DIR into the DB at startup."""
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request URL at INFO, which names internal hosts (Harbor, Slurm).
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        owned = db is None
        if owned:
            # DATA_DIR is relative to the working directory (web/).
            _app.state.db = Database(Path(settings.data_dir) / DB_FILENAME)
        if load_catalog:
            await _app.state.db.run(catalog.load_and_sync, _app.state.db, settings)
        tasks = [asyncio.create_task(reaper_loop(_app))] if background else []
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if _app.state.harbor is not None:
                await _app.state.harbor.aclose()
            if owned:
                _app.state.db.close()

    app = FastAPI(title=settings.app_name, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.clock = clock
    app.state.db = db  # else opened in the lifespan; /health never touches it
    app.state.authenticator = authenticator or AdAuthenticator(settings)
    app.state.sessions = auth.SessionManager(settings, clock)
    app.state.throttle = auth.LoginThrottle(clock)
    # One Harbor client per app; None without HARBOR_URL (no robot credentials for builders).
    app.state.harbor = harbor or (HarborClient(settings) if settings.harbor_url else None)
    # Always False here. Only mock/demo.py flips it on the finished app object;
    # no setting or environment variable can (tests/test_demo.py).
    app.state.demo = False
    app.state.registry_cache = {}  # registry.py: short-lived Harbor answers

    demo_router = APIRouter()

    @demo_router.get("/demo")
    def demo_flag() -> dict[str, bool]:
        """Public: lets the frontend show a "Demo mode" banner, also before login."""
        return {"demo": app.state.demo is True}

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable) -> Response:
        response: Response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        path = request.url.path
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        elif path.startswith(("/fonts/", "/img/")):
            response.headers.setdefault("Cache-Control", ASSET_CACHE)
        elif response.status_code in (200, 304) and path != settings.health_check_path:
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    # Cheap liveness probe: must never touch the database, LDAP, Harbor or Slurm.
    @app.get(settings.health_check_path, include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # JSON endpoints live under /api/... and are registered before the static
    # mount below, which serves the frontend at / and so must come last. Every
    # /api router is included into `api`, which enforces the CSRF header on
    # state-changing methods; never add a route to `app` under /api directly.
    api = APIRouter(prefix="/api", dependencies=[Depends(auth.csrf_protect)])
    api.include_router(auth.router)
    api.include_router(demo_router)
    api.include_router(catalog.router)
    api.include_router(registry.router)
    api.include_router(builder_api.router)
    api.include_router(builder_api.admin_router)
    app.include_router(api)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
