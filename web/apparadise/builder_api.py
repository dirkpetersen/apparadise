"""Builder API (/api/builder/...) and the sysadmin routes that feed it (/api/admin/...).

Builders authenticate with a per-builder bearer token, never a session cookie. They get one
by trading a one-time registration token that a sysadmin creates. All state-changing calls
still carry the CSRF header like every /api call (`X-Requested-With: apparadise`).
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import coordinator
from .auth import SessionUser, require_sysadmin
from .catalog import NAME_RE, VERSION_RE
from .db import Database, get_db
from .recipes import RecipeError, render
from .resolver import resolve_name

log = logging.getLogger(__name__)
REGISTRATION_TTL = timedelta(hours=24)
LOG_CHUNK_LIMIT = 1024 * 1024

router = APIRouter(prefix="/builder", tags=["builder"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


async def _audit(db: Database, actor: str, action: str, target_type: str, target: str,
                 details: dict[str, Any] | None = None) -> None:
    try:
        await db.run(db.audit.add, actor, action, target_type, target, details or {})
    except Exception:
        log.exception("audit write failed for %s", action)


async def require_builder(request: Request, db: Annotated[Database, Depends(get_db)]):
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Builder token required.", headers={"WWW-Authenticate": "Bearer"})
    builder = await db.run(db.builders.by_token, token.strip())
    if builder is None:
        raise HTTPException(401, "Unknown or disabled builder.",
                            headers={"WWW-Authenticate": "Bearer"})
    return builder


Builder = Annotated[dict, Depends(require_builder)]


def _job_error(e: coordinator.JobError) -> HTTPException:
    return HTTPException(e.status, str(e))


# -- builder side -----------------------------------------------------------------------


class RegisterBody(BaseModel):
    token: str = Field(min_length=16, max_length=200)
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,62}$")
    platform: Literal["linux/amd64", "linux/arm64"]
    onprem: bool = True


@router.post("/register")
async def register(body: RegisterBody, db: Annotated[Database, Depends(get_db)]):
    builder_token = secrets.token_urlsafe(32)
    bid = await db.run(db.builders.register, body.token, body.name, body.platform, body.onprem,
                       builder_token)
    if bid is None:
        await _audit(db, f"builder:{body.name}", "builder.register.refused", "builder", body.name)
        raise HTTPException(401, "Invalid, used or expired registration token.")
    await _audit(db, f"builder:{body.name}", "builder.register", "builder", body.name,
                 {"platform": body.platform, "onprem": body.onprem})
    return {"id": bid, "token": builder_token}


@router.post("/claim")
async def claim(builder: Builder, response: Response, db: Annotated[Database, Depends(get_db)]):
    job = await db.run(coordinator.claim, db, builder)
    if job is None:
        response.status_code = 204
        return None
    await _audit(db, f"builder:{builder['name']}", "build.claim", "build", str(job["id"]),
                 {"package": job["package"], "target": job["target"]})
    return job


@router.post("/jobs/{build_id}/log")
async def append_log(build_id: int, request: Request, builder: Builder,
                     db: Annotated[Database, Depends(get_db)]):
    chunk = await request.body()
    if len(chunk) > LOG_CHUNK_LIMIT:
        raise HTTPException(413, "Send the log in chunks of at most 1 MiB.")
    try:
        size = await db.run(coordinator.append_log, db, request.app.state.settings.data_dir,
                            builder, build_id, chunk)
    except coordinator.JobError as e:
        raise _job_error(e) from None
    return {"size": size}


class ReportBody(BaseModel):
    stage: Literal["build", "package", "test", "publish"]
    ok: bool
    digest: str = Field("", max_length=80)
    test: str = Field("", max_length=500)
    error: str = Field("", max_length=2000)


@router.post("/jobs/{build_id}/report")
async def report(build_id: int, body: ReportBody, request: Request, builder: Builder,
                 db: Annotated[Database, Depends(get_db)]):
    try:
        result = await db.run(coordinator.report, db, request.app.state.settings.data_dir,
                              builder, build_id, stage=body.stage, ok=body.ok,
                              digest=body.digest, test=body.test, error=body.error)
    except coordinator.JobError as e:
        raise _job_error(e) from None
    await _audit(db, f"builder:{builder['name']}", "build.report", "build", str(build_id),
                 {"stage": body.stage, "ok": body.ok})
    return result


# -- sysadmin side ------------------------------------------------------------------------

Sysadmin = Annotated[SessionUser, Depends(require_sysadmin)]


@admin_router.post("/builder-tokens")
async def create_registration_token(user: Sysadmin, db: Annotated[Database, Depends(get_db)]):
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(UTC) + REGISTRATION_TTL).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    await db.run(db.builders.add_registration_token, token, user.username, expires)
    await _audit(db, user.username, "builder.token.create", "builder", "-", {"expires": expires})
    return {"token": token, "expiresAt": expires}


@admin_router.get("/builders")
async def list_builders(_: Sysadmin, db: Annotated[Database, Depends(get_db)]):
    return {"builders": await db.run(db.builders.list)}


@admin_router.post("/builders/{builder_id}/disable")
async def disable_builder(builder_id: int, user: Sysadmin,
                          db: Annotated[Database, Depends(get_db)]):
    if not await db.run(db.builders.disable, builder_id):
        raise HTTPException(404, "No such builder.")
    await _audit(db, user.username, "builder.disable", "builder", str(builder_id))
    return {"ok": True}


class BuildBody(BaseModel):
    package: str = Field(pattern=NAME_RE.pattern.replace("\\Z", "$"))
    version: str = Field(pattern=VERSION_RE.pattern.replace("\\Z", "$"))
    targets: list[Literal["vr", "rtx", "generic"]] = Field(min_length=1, max_length=3)
    source: Literal["spack", "easybuild", "pixi"] | None = None
    easyconfig: str = Field("", max_length=200)
    spec: str = Field("", max_length=200)


@admin_router.post("/builds")
async def start_build(body: BuildBody, request: Request, user: Sysadmin,
                      db: Annotated[Database, Depends(get_db)]):
    """Resolve the source, generate the recipe per target and queue the builds.
    Sysadmin-only until requests and quotas arrive (phase 3)."""
    settings = request.app.state.settings
    pkg = await db.run(db.packages.get, body.package, include_restricted=True)
    aliases = pkg["aliases"] if pkg else {}
    res = await db.run(resolve_name, db, body.package, aliases, body.source)
    if res.failed:
        raise HTTPException(422, f"No recipe: {res.reason}.")
    if pkg is None:
        pid = await db.run(db.packages.upsert, {"name": body.package, "title": body.package,
                                                "origin": "request"})
        restricted = False
    else:
        pid, restricted = pkg["id"], pkg["visibility"] == "restricted"
    queued, skipped = [], []
    for target in dict.fromkeys(body.targets):
        for platform in coordinator.TARGET_PLATFORMS[target]:
            try:
                recipe = render(res.source, body.package, body.version, target,
                                registry=settings.registry_host, platform=platform,
                                spec=body.spec or f"{res.upstream_name}@{body.version}",
                                easyconfig=body.easyconfig, conda_name=res.upstream_name)
            except RecipeError as e:
                skipped.append({"target": target, "platform": platform, "reason": str(e)})
                continue
            bid = await db.run(coordinator.enqueue, db, pid, body.version, res.source, target,
                               platform, recipe, requested_by=user.username,
                               restricted=restricted)
            queued.append({"id": bid, "target": target, "platform": platform})
    await _audit(db, user.username, "build.queue", "package", body.package,
                 {"version": body.version, "source": res.source, "rule": res.rule,
                  "queued": [q["id"] for q in queued]})
    return {"source": res.source, "rule": res.rule, "reason": res.reason,
            "cudaPort": res.cuda_port, "queued": queued, "skipped": skipped}
