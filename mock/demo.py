#!/usr/bin/env python3
"""Try apparadise locally with no AD, Harbor or cluster: one command.

    cd web && .venv/bin/python3 ../mock/demo.py [--port 8000] [--password demo] [--keep-data]

Runs the real app (real login flow, throttle, sessions, CSRF) on 127.0.0.1 with:
- `DemoDirectory` (mock/directory.py) in place of AD, injected via create_app(authenticator=...);
  users alice and bob are normal users, dana is a sysadmin (RCO-Admins);
- the sample catalog in mock/catalog/ (published, building, failed and restricted entries);
- a fake Harbor (mock/harbor_mock.py, in-process) seeded from that catalog, for the Registry
  page and the per-job robot accounts;
- a temporary DATA_DIR, removed on exit unless --keep-data.

Dev tooling only: nothing in web/apparadise imports this, and no setting or environment
variable can switch the demo on. The developer's real .env and environment are ignored.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "web", ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from apparadise.config import Settings  # noqa: E402
from apparadise.main import create_app  # noqa: E402
from mock.directory import DEMO_PASSWORD, DEMO_USERS, DemoDirectory  # noqa: E402

DEMO_CATALOG = ROOT / "mock" / "catalog"


def scrub_environment() -> None:
    """Everything the real environment could set: the demo must not see any of it."""
    for name in Settings.model_fields:
        os.environ.pop(name.upper(), None)


def demo_settings(*, port: int, data_dir: str, catalog_dir: str = str(DEMO_CATALOG)) -> Settings:
    return Settings(
        _env_file=None,
        port=port,
        data_dir=data_dir,
        session_secret=os.urandom(32).hex(),
        session_cookie_secure=False,  # plain http on 127.0.0.1
        ldap_url=["ldaps://directory.demo.invalid:636"],  # never contacted
        ldap_base_dn="DC=demo,DC=example",
        ldap_bind_dn="CN=svc,DC=demo,DC=example",
        ldap_bind_password="unused",
        ldap_admin_group_dn="CN=RCO-Admins,OU=Groups,DC=demo,DC=example",
        catalog_dir=catalog_dir,
        registry_host="registry.example.org",
        # mock/harbor_mock.py, reached in-process; nothing listens on this name
        harbor_url="https://harbor.demo.invalid",
        harbor_username="admin",
        harbor_password="harbor",
    )


# A small three-way index so "Queue a build" on the admin page resolves real-looking sources.
DEMO_INDEX = {
    "spack": [("gromacs", ["2026.3", "2025.4"], True, False), ("lammps", ["20250722"], True, False),
              ("relion", ["5.0.1"], True, False), ("samtools", ["1.21"], False, False)],
    "easybuild": [("RELION", ["5.0.1"], True, False), ("AlphaFold3", ["3.0.1"], True, True)],
    "pixi": [("pytorch", ["2.9.1"], True, True), ("vllm", ["0.11.0"], True, True)],
}


def seed_index(db) -> None:
    from apparadise.index import _item

    for source, rows in DEMO_INDEX.items():
        arches = ["linux-64", "linux-aarch64"]
        db.index.replace_source(source, [
            _item(source, n, v, cuda, arches if n != "vllm" else ["linux-64"], py)
            for n, v, cuda, py in rows])


def build_demo_app(settings: Settings, db=None, directory: DemoDirectory | None = None):
    from apparadise.db import DB_FILENAME, Database

    if db is None:
        db = Database(Path(settings.data_dir) / DB_FILENAME)
    seed_index(db)
    import httpx

    from apparadise.harbor import HarborClient
    from mock.harbor_mock import create_app as create_harbor
    from mock.harbor_mock import seed_from_catalog

    fake_harbor = create_harbor()
    seed_from_catalog(fake_harbor, settings.catalog_dir)
    harbor = HarborClient(settings, transport=httpx.ASGITransport(app=fake_harbor))
    app = create_app(settings, db=db, authenticator=directory or DemoDirectory(), harbor=harbor)
    app.state.fake_harbor = fake_harbor
    app.state.demo = True
    return app


def port_free(port: int) -> bool:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Run apparadise with a fake directory and sample catalog.")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--password", default=DEMO_PASSWORD)
    p.add_argument("--keep-data", action="store_true", help="keep the temporary DATA_DIR on exit")
    args = p.parse_args(argv)
    if not port_free(args.port):
        sys.exit(f"error: port {args.port} is already in use; pick another with --port")
    scrub_environment()
    import uvicorn

    data_dir = tempfile.mkdtemp(prefix="apparadise-demo-")
    try:
        settings = demo_settings(port=args.port, data_dir=data_dir)
        app = build_demo_app(settings, directory=DemoDirectory(args.password))
        users = ", ".join(
            f"{u}{' (sysadmin)' if admin else ''}" for u, (_, admin) in DEMO_USERS.items())
        print(f"apparadise demo on http://127.0.0.1:{args.port}  users: {users}  "
              f"password: {args.password}  data: {data_dir}", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        if args.keep_data:
            print(f"kept {data_dir}")
        else:
            shutil.rmtree(data_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
