"""Upstream offers (phase 4): fixes that worked here, drafted for the upstream project.

A repaired build (apparadise.repair) that was published shows the fix works. Sysadmins see
those on the admin page with the upstream repository and a drafted issue text, so the
patch can go back to Spack, EasyBuild or conda-forge. Nothing is filed automatically:
opening the issue or PR stays a human decision (and needs a human's GitHub account).
"""

from __future__ import annotations

import json
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends

from .auth import SessionUser, require_sysadmin
from .db import Database, get_db

router = APIRouter(prefix="/admin", tags=["admin"])  # mounted under /api/
REPOS = {
    "spack": "https://github.com/spack/spack-packages",
    "easybuild": "https://github.com/easybuilders/easybuild-easyconfigs",
    "pixi": "https://github.com/conda-forge",
}


def _draft(o: dict[str, Any]) -> tuple[str, str]:
    title = f"{o['package']} {o['version']}: CUDA build needs a fix ({o['rule']})"
    if o["source"] == "spack":
        change = f"Spec used: `{o['spec']}`"
    elif o["source"] == "easybuild":
        change = (f"Easyconfig `{o['easyconfig']}` with EasyBuild options "
                  f"`{json.dumps(o['ebOptions'])}`")
    else:
        change = f"Built from {o['source']} instead"
    body = (f"Building {o['package']} {o['version']} with CUDA for {o['target']} "
            f"({o['platform']}) failed with:\n\n```\n{o['error']}\n```\n\n"
            f"{o['why']}\n\nThis change made it build and pass a GPU smoke test:\n\n"
            f"{change}\n")
    return title, body


def offers(db: Database) -> list[dict[str, Any]]:
    from .repair import load

    why = {r["id"]: r.get("why", "") for r in load()}
    rows = db.query(
        "SELECT b.id, b.version, b.target, b.platform, b.source, b.spec, b.easyconfig, "
        "b.eb_options, b.repair, b.finished_at, p.name AS package, p.visibility, "
        "(SELECT f.error_line FROM build fb JOIN failure f ON f.id = fb.failure_id "
        " WHERE fb.package_id = b.package_id AND fb.version = b.version "
        " AND fb.target = b.target AND fb.platform = b.platform AND fb.id < b.id "
        " ORDER BY fb.id DESC LIMIT 1) AS error "
        "FROM build b JOIN package p ON p.id = b.package_id "
        "WHERE b.repair <> '' AND b.status = 'published' ORDER BY b.id DESC LIMIT 100")
    out = []
    for r in rows:
        o = {"build": r["id"], "package": r["package"], "version": r["version"],
             "target": r["target"], "platform": r["platform"], "source": r["source"],
             "rule": r["repair"], "why": why.get(r["repair"], ""), "spec": r["spec"],
             "easyconfig": r["easyconfig"], "ebOptions": json.loads(r["eb_options"] or "{}"),
             "error": r["error"] or "", "published": r["finished_at"],
             # licence-gated software: think before telling the world it is built here
             "restricted": r["visibility"] != "public"}
        title, body = _draft(o)
        repo = REPOS[r["source"]]
        o["repo"] = repo
        o["title"], o["body"] = title, body
        if r["source"] != "pixi":  # a feedstock per package: no single issue page
            o["issueUrl"] = f"{repo}/issues/new?title={quote(title)}&body={quote(body)}"
        out.append(o)
    return out


@router.get("/upstream")
async def list_offers(_: Annotated[SessionUser, Depends(require_sysadmin)],
                      db: Annotated[Database, Depends(get_db)]) -> dict[str, Any]:
    return {"offers": await db.run(offers, db)}
