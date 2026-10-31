"""Resolve a source, render the recipe per target and platform, and queue the builds.

One code path for the sysadmin console (POST /api/admin/builds), user requests
(POST /api/requests) and, later, the chatbot's `start_build` tool. Synchronous: call it as
`await db.run(queue_builds, db, settings, ...)`.

Quota (users only; sysadmins have none): at most `quota.builds_per_week` build jobs per user
in any 7 days, counted per platform job (a generic build is two jobs). The value lives in
the DB (runtime policy, edited on the admin page), not in .env.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from . import coordinator
from .config import Settings
from .db import Database
from .index import normalise
from .recipes import RecipeError, render
from .resolver import resolve_name

QUOTA_KEY = "quota.builds_per_week"
QUOTA_DEFAULT = 6
ACTIVE = ("queued", "running")


class QueueError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def quota(db: Database) -> int:
    return int(db.settings.get(QUOTA_KEY, QUOTA_DEFAULT))


def used_this_week(db: Database, username: str) -> int:
    since = (datetime.now(UTC) - timedelta(days=7)).isoformat().replace("+00:00", "Z")
    return db.query_one(
        "SELECT COUNT(*) AS n FROM build WHERE origin = 'pipeline' AND requested_by = ? "
        "AND created_at >= ?", (username, since))["n"]


def _existing(db: Database, package_id: int, version: str, target: str,
              platform: str) -> str | None:
    """The status of the current build of this release, if it is active or published."""
    row = db.query_one(
        "SELECT status FROM build WHERE package_id = ? AND version = ? AND target = ? "
        "AND (platform = ? OR origin = 'catalog') AND status IN ('queued', 'running', "
        "'published') ORDER BY CASE status WHEN 'published' THEN 0 ELSE 1 END, id DESC "
        "LIMIT 1", (package_id, version, target, platform))
    return row["status"] if row else None


def queue_builds(db: Database, settings: Settings, *, package: str, version: str,
                 targets: list[str], requested_by: str, admin: bool,
                 source: str | None = None, easyconfig: str = "", spec: str = "",
                 request_id: int | None = None, skip_existing: bool = False,
                 enforce_quota: bool = False,
                 platforms: list[str] | None = None) -> dict[str, Any]:
    """-> {package, version, source, rule, reason, cudaPort, queued, skipped}.
    Raises QueueError(422) for no recipe, (429) when over quota."""
    pkg = db.packages.get(package, include_restricted=True)
    if pkg and pkg["visibility"] == "restricted" and not admin:
        # Same answer as for a name nobody knows: a restricted package does not exist here.
        raise QueueError(422, "No recipe: no recipe in Spack, EasyBuild or conda-forge.")
    aliases = pkg["aliases"] if pkg else {}
    res = resolve_name(db, package, aliases, source)
    if res.failed:
        raise QueueError(422, f"No recipe: {res.reason}.")
    known_ecs: dict[str, str] = {}
    if res.source == "easybuild":
        rows = db.index.lookup([normalise(res.upstream_name, "easybuild")])
        known_ecs = next((r["details"].get("easyconfigs", {}) for r in rows
                          if r["source"] == "easybuild"
                          and r["upstream_name"] == res.upstream_name), {})
    if not version:  # the newest release; for EasyBuild, the newest with a CUDA easyconfig
        candidates = [v for v in res.versions if res.source != "easybuild" or v in known_ecs]
        if not candidates:
            raise QueueError(422, f"{res.source} lists no release of {res.upstream_name} "
                                  "to build; name a version.")
        version = candidates[0]
    if res.source == "easybuild" and not easyconfig:
        easyconfig = known_ecs.get(version, "")
        if not easyconfig:
            raise QueueError(422, f"No CUDA easyconfig for {res.upstream_name} {version}"
                                  + ("; name one in 'easyconfig'." if admin else "."))
    marker = True
    if res.source == "pixi":
        rows = db.index.lookup([normalise(res.upstream_name, "pixi")])
        marker = next((r["details"].get("cuda_build_marker", True) for r in rows
                       if r["source"] == "pixi" and r["upstream_name"] == res.upstream_name),
                      True)
    if pkg is None:
        pid = db.packages.upsert({"name": package, "title": package, "origin": "request"})
        restricted = False
    else:
        pid, restricted = pkg["id"], pkg["visibility"] == "restricted"

    plan, skipped = [], []
    for target in dict.fromkeys(targets):
        for platform in coordinator.TARGET_PLATFORMS[target]:
            if platforms is not None and platform not in platforms:
                continue  # e.g. a retry of only the arm64 half of a generic release
            if skip_existing and (st := _existing(db, pid, version, target, platform)):
                skipped.append({"target": target, "platform": platform,
                                "reason": "already available" if st == "published"
                                else "already queued"})
                continue
            try:
                recipe = render(res.source, package, version, target,
                                registry=settings.registry_host, platform=platform,
                                spec=spec or f"{res.upstream_name}@{version}",
                                easyconfig=easyconfig, conda_name=res.upstream_name,
                                cuda_build_marker=marker)
            except RecipeError as e:
                skipped.append({"target": target, "platform": platform, "reason": str(e)})
                continue
            plan.append((target, platform, recipe))
    if enforce_quota and not admin and plan:
        left = quota(db) - used_this_week(db, requested_by)
        if len(plan) > left:
            raise QueueError(429, f"This needs {len(plan)} build job"
                                  f"{'s' if len(plan) != 1 else ''}, and your weekly quota "
                                  f"has {max(left, 0)} left. Upvote an existing request, or "
                                  "ask the research computing team.")
    queued = []
    for target, platform, recipe in plan:
        bid = coordinator.enqueue(db, pid, version, res.source, target, platform, recipe,
                                  requested_by=requested_by, restricted=restricted,
                                  request_id=request_id)
        queued.append({"id": bid, "target": target, "platform": platform})
    return {"package": package, "version": version, "source": res.source, "rule": res.rule,
            "reason": res.reason, "cudaPort": res.cuda_port, "queued": queued,
            "skipped": skipped, "packageId": pid, "restricted": restricted}
