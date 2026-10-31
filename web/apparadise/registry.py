"""The Registry page: a read-only view of what Harbor actually holds.

The registry is the source of truth (the catalog says what *should* exist, Harbor what
does). Everyone sees the `public` project; only sysadmins (RCO-Admins) see `restricted`
and `cache` (the NGC proxy cache plus test tags). A non-sysadmin asking for a hidden
project gets the same 404 as for one that does not exist.

Harbor is read with the app's admin account, server-side; the browser never talks to
Harbor's API. Answers are cached for CACHE_SECONDS so the page cannot hammer Harbor.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from .auth import current_user
from .catalog import NAME_RE
from .config import Settings
from .db import Database, get_db
from .harbor import REPO_RE, HarborClient, HarborError, HarborUnavailable

CACHE_SECONDS = 30
PUBLIC = ("public",)
ADMIN = ("public", "restricted", "cache")

router = APIRouter(tags=["registry"])  # mounted under /api/ in main.create_app


def _projects(request: Request) -> tuple[str, ...]:
    user = current_user(request)
    return ADMIN if user and user.is_admin else PUBLIC


def _harbor(request: Request) -> HarborClient:
    h = request.app.state.harbor
    if h is None:
        raise HTTPException(503, "The registry is not configured on this server.")
    return h


async def _cached(request: Request, key: tuple, fetch) -> Any:
    cache: dict = request.app.state.registry_cache
    now = request.app.state.clock()
    hit = cache.get(key)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        value = await fetch()
    except HarborUnavailable:
        raise HTTPException(503, "The registry is not reachable right now.") from None
    except HarborError as e:
        if e.status == 404:
            raise HTTPException(404, "No such repository.") from None
        raise HTTPException(502, "The registry returned an error.") from None
    cache[key] = (now, value)
    return value


def pull_commands(host: str, project: str, repo: str, ref: str) -> dict[str, str]:
    """`ref` is ':tag' or '@sha256:...'."""
    return {
        "podman": f"podman pull {host}/{project}/{repo}{ref}",
        "srun": f"srun --container-image={host}#{project}/{repo}{ref} --pty bash",
        "enroot": f"enroot import docker://{host}#{project}/{repo}{ref}",
    }


async def _packages(db: Database, admin: bool) -> set[str]:
    return {p["name"] for p in await db.run(db.packages.list, include_restricted=admin, q="")}


@router.get("/registry")
async def overview(request: Request, db: Annotated[Database, Depends(get_db)]) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    harbor = request.app.state.harbor
    projects = _projects(request)
    if harbor is None:
        return {"configured": False, "host": settings.registry_host, "projects": []}
    known = await _packages(db, len(projects) > 1)
    out = []
    for project in projects:
        repos = await _cached(request, ("repos", project),
                              lambda p=project: harbor.list_repositories(p))
        out.append({"name": project, "repositories": [
            {**r, "package": r["name"] if r["name"] in known and project != "cache" else None}
            for r in sorted(repos, key=lambda r: r["name"])]})
    return {"configured": True, "host": settings.registry_host, "projects": out}


@router.get("/registry/{project}/{repo:path}")
async def repository(project: str, repo: str, request: Request,
                     db: Annotated[Database, Depends(get_db)]) -> dict[str, Any]:
    if project not in _projects(request) or not REPO_RE.match(repo) or ".." in repo:
        raise HTTPException(404, "No such repository.")
    harbor = _harbor(request)
    host = request.app.state.settings.registry_host
    artifacts = await _cached(request, ("artifacts", project, repo),
                              lambda: harbor.list_artifacts(project, repo))
    out = []
    for a in artifacts:
        ref = f":{a['tags'][0]}" if a["tags"] else f"@{a['digest']}"
        out.append({**a, "commands": pull_commands(host, project, repo, ref)})
    known = await _packages(db, project != "public") if NAME_RE.match(repo) else set()
    return {"project": project, "name": repo, "host": host,
            "package": repo if repo in known and project != "cache" else None,
            "artifacts": out}
