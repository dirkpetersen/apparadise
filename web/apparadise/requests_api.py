"""Software requests (phase 3): any signed-in user can ask for a package to be built.

POST /api/requests resolves the source with the fixed rule order, renders the recipes and
queues the builds through buildqueue (the same path as the sysadmin console), within the
user's weekly quota. Releases already available or already queued are skipped, so a second
request for the same thing costs nothing; upvoting the first one is the better signal.
A name no source knows is answered with 422 and nothing is stored (Q25).

GET /api/index?q= searches the three-way index for the request form, with the source the
resolver would pick. Restricted packages never appear for non-sysadmins.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .auth import SessionUser, current_user, require_sysadmin, require_user
from .buildqueue import QUOTA_KEY, QueueError, queue_builds, quota, restricted_names, used_this_week
from .catalog import NAME_RE, VERSION_RE
from .db import Database, get_db
from .resolver import resolve

router = APIRouter(tags=["requests"])  # mounted under /api/ in main.create_app
admin_router = APIRouter(prefix="/admin", tags=["admin"])
DEFAULT_TARGETS = ["vr", "rtx"]


class RequestBody(BaseModel):
    name: str = Field(pattern=NAME_RE.pattern.replace("\\Z", "$"))
    version: str = Field("", pattern=r"^$|" + VERSION_RE.pattern.replace("\\Z", "$"))
    targets: list[Literal["vr", "rtx", "generic"]] = Field(
        default_factory=lambda: list(DEFAULT_TARGETS), min_length=1, max_length=3)
    source: Literal["spack", "easybuild", "pixi"] | None = None


def index_search(db: Database, q: str, admin: bool) -> list[dict[str, Any]]:
    """Index matches grouped by normalised name, with the resolver's pick. Shared with the
    chatbot's search_index tool. Restricted names are hidden unless `admin`."""
    rows = db.index.search(q.strip())
    hidden = set() if admin else restricted_names(db)
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["norm_name"] not in hidden:
            groups.setdefault(r["norm_name"], []).append(r)
    out = []
    for name, entries in groups.items():
        res = resolve(entries)
        out.append({
            "name": name,
            "sources": {e["source"]: {"upstream": e["upstream_name"], "cuda": e["has_cuda"],
                                      "versions": e["versions"][:6], "arches": e["arches"]}
                        for e in entries},
            "resolution": {"source": res.source, "rule": res.rule, "reason": res.reason,
                           "cudaPort": res.cuda_port, "versions": list(res.versions[:6])},
        })
    return out


@router.get("/index")
async def search_index(request: Request, db: Annotated[Database, Depends(get_db)],
                       q: Annotated[str, Query(min_length=2, max_length=64)]) -> dict[str, Any]:
    user = current_user(request)
    return {"results": await db.run(index_search, db, q, bool(user and user.is_admin))}


@router.get("/quota")
async def my_quota(user: Annotated[SessionUser, Depends(require_user)],
                   db: Annotated[Database, Depends(get_db)]) -> dict[str, Any]:
    if user.is_admin:
        return {"unlimited": True, "limit": None, "used": None, "left": None}
    limit = await db.run(quota, db)
    used = await db.run(used_this_week, db, user.username)
    return {"unlimited": False, "limit": limit, "used": used, "left": max(limit - used, 0)}


async def submit_request(db: Database, settings: Any, user: SessionUser, *, name: str,
                         version: str = "", targets: list[str] | None = None,
                         source: str | None = None, via: str = "web") -> dict[str, Any]:
    """Queue a user's request within their quota. Raises QueueError. Shared with the
    chatbot's create_request tool (`via="chatbot"`)."""
    targets = list(targets or DEFAULT_TARGETS)
    res = await db.run(queue_builds, db, settings, package=name, version=version,
                       targets=targets, source=source, requested_by=user.username,
                       admin=user.is_admin, skip_existing=True, enforce_quota=True)
    rid = None
    if res["queued"]:
        queued_targets = list(dict.fromkeys(q["target"] for q in res["queued"]))
        rid = await db.run(db.requests.create, res["packageId"], res["version"],
                           queued_targets, user.username, res["restricted"],
                           [q["id"] for q in res["queued"]])
    await db.run(db.audit.add, user.username, "request.create", "package", name,
                 {"request": rid, "version": res["version"], "source": res["source"],
                  "queued": [q["id"] for q in res["queued"]], "via": via})
    return {"request": rid, **{k: res[k] for k in ("package", "version", "source", "rule",
                                                    "reason", "cudaPort", "queued",
                                                    "skipped")}}


@router.post("/requests")
async def create_request(body: RequestBody, request: Request,
                         user: Annotated[SessionUser, Depends(require_user)],
                         db: Annotated[Database, Depends(get_db)]) -> dict[str, Any]:
    try:
        return await submit_request(db, request.app.state.settings, user, name=body.name,
                                    version=body.version, targets=body.targets,
                                    source=body.source)
    except QueueError as e:
        raise HTTPException(e.status, e.message) from None


@router.get("/requests")
async def list_requests(request: Request, db: Annotated[Database, Depends(get_db)],
                        mine: bool = False) -> dict[str, Any]:
    user = current_user(request)
    if mine and user is None:
        raise HTTPException(401, "Sign in to see your requests.")
    admin = bool(user and user.is_admin)
    rows = await db.run(db.requests.list, include_restricted=admin,
                        username=user.username if user else None, mine=mine)
    voted = await db.run(db.votes.mine, user.username, "request") if user else set()
    out = []
    for r in rows:
        own = bool(user and r["requested_by"] == user.username)
        out.append({
            "id": r["id"], "package": r["package"], "version": r["version"],
            "targets": r["targets"], "status": r["status"], "votes": r["votes"],
            "voted": r["id"] in voted, "mine": own, "created": r["created_at"],
            "builds": {"active": r["active"], "published": r["published"],
                       "failed": r["failed"]},
            "failureId": r["failure_id"], "restricted": bool(r["restricted"]),
            # who asked is shown to themselves and to sysadmins only, and so are the build
            # jobs (their live logs are theirs to watch: live.py)
            "requestedBy": r["requested_by"] if own or admin else None,
            "jobs": (await db.run(db.query, "SELECT id, target, platform, status FROM build "
                                  "WHERE request_id = ? ORDER BY id", (r["id"],)))
            if own or admin else [],
        })
    return {"requests": out}


class PolicyBody(BaseModel):
    quotaBuildsPerWeek: int = Field(ge=0, le=1000)


@admin_router.get("/policy")
async def get_policy(_: Annotated[SessionUser, Depends(require_sysadmin)],
                     db: Annotated[Database, Depends(get_db)]) -> dict[str, Any]:
    return {"quotaBuildsPerWeek": await db.run(quota, db)}


@admin_router.put("/policy")
async def set_policy(body: PolicyBody, user: Annotated[SessionUser, Depends(require_sysadmin)],
                     db: Annotated[Database, Depends(get_db)]) -> dict[str, Any]:
    await db.run(db.settings.set, QUOTA_KEY, body.quotaBuildsPerWeek, user.username)
    await db.run(db.audit.add, user.username, "policy.update", "policy", QUOTA_KEY,
                 {"value": body.quotaBuildsPerWeek})
    return {"quotaBuildsPerWeek": body.quotaBuildsPerWeek}
