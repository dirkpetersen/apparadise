"""A fake Harbor API for tests and the demo: robot accounts plus read-only repositories and
artifacts. Never imported by the app.

    app = create_app(); seed_from_catalog(app, "mock/catalog")
"""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request


def create_app(username: str = "admin", password: str = "harbor") -> FastAPI:
    app = FastAPI()
    app.state.robots = {}
    # project -> repository -> [artifact], artifacts in Harbor's own JSON shape
    app.state.repos = {"public": {}, "restricted": {}, "cache": {}}
    app.state.fail = None  # set to a status code to make every call fail

    def check(request: Request) -> None:
        import base64

        if app.state.fail:
            raise HTTPException(app.state.fail, "forced failure")
        auth = request.headers.get("authorization", "")
        want = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        if auth != want:
            raise HTTPException(401, "unauthorized")

    @app.post("/api/v2.0/robots", status_code=201)
    async def create(request: Request):
        check(request)
        body = await request.json()
        rid = len(app.state.robots) + 1
        robot = {"id": rid, "name": f"robot${body['name']}", "secret": secrets.token_urlsafe(16),
                 "body": body}
        app.state.robots[rid] = robot
        return {k: robot[k] for k in ("id", "name", "secret")}

    @app.delete("/api/v2.0/robots/{rid}")
    async def delete(rid: int, request: Request):
        check(request)
        if app.state.robots.pop(rid, None) is None:
            raise HTTPException(404, "not found")
        return {}

    def page(rows: list, request: Request) -> list:
        n = int(request.query_params.get("page", 1))
        size = int(request.query_params.get("page_size", 10))
        return rows[(n - 1) * size: n * size]

    @app.get("/api/v2.0/projects/{project}/repositories")
    async def repositories(project: str, request: Request):
        check(request)
        if project not in app.state.repos:
            raise HTTPException(404, "project not found")
        rows = [{"name": f"{project}/{name}", "artifact_count": len(arts),
                 "pull_count": sum(a.get("_pulls", 0) for a in arts),
                 "update_time": max((a["push_time"] for a in arts), default=None)}
                for name, arts in sorted(app.state.repos[project].items())]
        return page(rows, request)

    @app.get("/api/v2.0/projects/{project}/repositories/{repo}/artifacts")
    async def artifacts(project: str, repo: str, request: Request):
        check(request)
        name = unquote(repo)  # Harbor's double encoding: one layer is left here
        arts = app.state.repos.get(project, {}).get(name)
        if arts is None:
            raise HTTPException(404, "repository not found")
        return page([{k: v for k, v in a.items() if not k.startswith("_")} for a in arts],
                    request)

    return app


ARCH = {"vr": "arm64", "rtx": "amd64"}


def _digest(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


def artifact(digest: str, tags: list[str], *, arch: str | None = None, pushed: str,
             size: int, signed: bool = True, children: list[tuple[str, str]] = (),
             pulls: int = 0) -> dict:
    a = {"digest": digest, "size": size, "push_time": pushed, "type": "IMAGE",
         "tags": [{"name": t, "push_time": pushed} for t in tags],
         "extra_attrs": {"os": "linux", "architecture": arch} if arch else {},
         "references": [{"child_digest": d, "platform": {"os": "linux", "architecture": ar}}
                        for d, ar in children],
         "accessories": [{"type": "signature.cosign", "digest": _digest(digest + "sig")}]
         if signed else [], "_pulls": pulls}
    return a


def seed_from_catalog(app: FastAPI, catalog_dir: str | Path) -> None:
    """One artifact per published catalog build, tagged <version>-<target>, plus a few NGC
    base images in `cache`, so the demo's Registry page matches its catalog."""
    import yaml

    for f in sorted(Path(catalog_dir).glob("*.yml")):
        doc = yaml.safe_load(f.read_text())
        project = "restricted" if doc.get("visibility") == "restricted" else "public"
        arts = []
        for version, targets in (doc.get("versions") or {}).items():
            for target, b in (targets or {}).items():
                if b.get("status") != "published":
                    continue
                d = b.get("digest") or _digest(f"{doc['name']}{version}{target}")
                h = int(d[7:15], 16)
                arts.append(artifact(d, [f"{version}-{target}"], arch=ARCH.get(target),
                                     pushed=f"{b.get('built', '2026-10-01')}T12:00:00.000Z",
                                     size=2_000_000_000 + h % 6_000_000_000, pulls=h % 400))
        if arts:
            app.state.repos[project][doc["name"]] = arts
    cuda = app.state.repos["cache"].setdefault("nvidia/cuda", [])
    for tag, day in (("13.0.3-devel-ubuntu24.04", "2026-10-02"),
                     ("12.9.2-devel-ubuntu24.04", "2026-09-20")):
        kids = [(_digest(tag + ar), ar) for ar in ("amd64", "arm64")]
        cuda.append(artifact(_digest(tag), [tag], pushed=f"{day}T08:00:00.000Z",
                             size=4_100_000_000, signed=False, children=kids, pulls=37))
