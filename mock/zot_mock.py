"""A fake Zot search API (GraphQL at /v2/_zot/ext/search) for tests and the demo: only the
two queries apparadise.zot sends. Never imported by the app.

    app = create_app(); seed_from_catalog(app, "mock/catalog")
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request


def create_app(username: str = "apparadise-web", password: str = "zot") -> FastAPI:
    app = FastAPI()
    # "project/repo" -> [ImageSummary-shaped dict], one per tag
    app.state.repos = {}
    app.state.fail = None  # set to a status code to make every call fail
    app.state.queries = []

    def check(request: Request) -> None:
        if app.state.fail:
            raise HTTPException(app.state.fail, "forced failure")
        want = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        if request.headers.get("authorization", "") != want:
            raise HTTPException(401, "unauthorized")

    @app.get("/v2/_zot/ext/search")
    async def search(query: str, request: Request):
        check(request)
        app.state.queries.append(query)
        if query.startswith("{RepoListWithNewestImage"):
            m = re.search(r"limit:(\d+) offset:(\d+)", query)
            limit, offset = int(m.group(1)), int(m.group(2))
            names = sorted(app.state.repos)
            rows = []
            for name in names[offset:offset + limit]:
                imgs = app.state.repos[name]
                newest = max(imgs, key=lambda i: i["PushTimestamp"])
                rows.append({"Name": name, "LastUpdated": newest["PushTimestamp"],
                             "DownloadCount": sum(i["DownloadCount"] for i in imgs),
                             "NewestImage": {"Tag": newest["Tag"]}})
            return {"data": {"RepoListWithNewestImage": {
                "Page": {"TotalCount": len(names)}, "Results": rows}}}
        m = re.match(r'\{ImageList\(repo:("(?:[^"\\]|\\.)*")', query)
        if m:
            name = json.loads(m.group(1))
            if name not in app.state.repos:
                return {"errors": [{"message": "repository: repo not found"}],
                        "data": {"ImageList": None}}
            return {"data": {"ImageList": {"Results": app.state.repos[name]}}}
        return {"errors": [{"message": "unsupported query"}]}

    return app


ARCH = {"vr": "arm64", "rtx": "amd64"}
INDEX = "application/vnd.oci.image.index.v1+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"


def _digest(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


def image(digest: str, tag: str, *, arches: list[str], pushed: str, size: int,
          signed: bool = True, pulls: int = 0) -> dict:
    return {"Tag": tag, "Digest": digest, "MediaType": INDEX if len(arches) > 1 else MANIFEST,
            "Size": str(size), "PushTimestamp": pushed, "LastUpdated": pushed,
            "IsSigned": signed, "DownloadCount": pulls,
            "Manifests": [{"Platform": {"Os": "linux", "Arch": a}} for a in arches]}


def seed_from_catalog(app: FastAPI, catalog_dir: str | Path) -> None:
    """One image per published catalog build, tagged <version>-<target>, plus NGC base
    images in the `nvcr` mirror, so the demo's Registry page matches its catalog."""
    import yaml

    for f in sorted(Path(catalog_dir).glob("*.yml")):
        doc = yaml.safe_load(f.read_text())
        project = "restricted" if doc.get("visibility") == "restricted" else "public"
        imgs = []
        for version, targets in (doc.get("versions") or {}).items():
            for target, b in (targets or {}).items():
                if b.get("status") != "published":
                    continue
                d = b.get("digest") or _digest(f"{doc['name']}{version}{target}")
                h = int(d[7:15], 16)
                arches = [ARCH[target]] if target in ARCH else ["amd64", "arm64"]
                imgs.append(image(d, f"{version}-{target}", arches=arches,
                                  pushed=f"{b.get('built', '2026-10-01')}T12:00:00Z",
                                  size=2_000_000_000 + h % 6_000_000_000, pulls=h % 400))
        if imgs:
            app.state.repos[f"{project}/{doc['name']}"] = imgs
    app.state.repos["nvcr/nvidia/cuda"] = [
        image(_digest(tag), tag, arches=["amd64", "arm64"], pushed=f"{day}T08:00:00Z",
              size=4_100_000_000, signed=False, pulls=37)
        for tag, day in (("13.0.3-devel-ubuntu24.04", "2026-10-02"),
                         ("12.9.2-devel-ubuntu24.04", "2026-09-20"))]
