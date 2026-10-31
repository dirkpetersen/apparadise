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
from .resolver import candidates, resolve, resolve_name

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


def restricted_names(db: Database) -> set[str]:
    """Normalised names and aliases of every restricted package."""
    out = set()
    for p in db.packages.list(include_restricted=True):
        if p["visibility"] == "restricted":
            out.add(normalise(p["name"]))
            out.update(normalise(v, s) for s, v in p["aliases"].items())
    return out


def find_package(db: Database, name: str) -> dict[str, Any] | None:
    """The package called `name`, or the one that has `name` as an alias, so `torch` finds
    `pytorch` and a request never creates a second package for the same software."""
    pkg = db.packages.get(name, include_restricted=True)
    if pkg:
        return pkg
    wanted = set(candidates(name))
    for p in db.packages.list(include_restricted=True):
        if normalise(p["name"]) in wanted or wanted & {normalise(v, s)
                                                       for s, v in p["aliases"].items()}:
            return p
    return None


def _no_recipe(source: str | None) -> QueueError:
    # Exactly what an unknown name gets, so a refusal never tells a restricted name apart.
    return QueueError(422, f"No recipe: {resolve([], source).reason}.")


def queue_builds(db: Database, settings: Settings, *, package: str, version: str,
                 targets: list[str], requested_by: str, admin: bool,
                 source: str | None = None, easyconfig: str = "", spec: str = "",
                 request_id: int | None = None, skip_existing: bool = False,
                 enforce_quota: bool = False,
                 platforms: list[str] | None = None, spec_append: str = "",
                 eb_options: dict[str, Any] | None = None,
                 repair: str = "") -> dict[str, Any]:
    """-> {package, version, source, rule, reason, cudaPort, queued, skipped, packageId,
    restricted}. Raises QueueError(422) for no recipe or an unknown version, (429) over quota.

    Rendering happens first, outside the lock. The duplicate check, the quota count, creating
    the package and queueing then happen in one transaction, so concurrent requests can
    neither exceed the quota nor queue the same release twice, and a refused request leaves
    nothing behind."""
    pkg = find_package(db, package)
    if not admin and (
            (pkg and pkg["visibility"] == "restricted")
            or set(candidates(package)) & restricted_names(db)):
        raise _no_recipe(source)  # a restricted package does not exist for non-sysadmins
    if pkg:
        package = pkg["name"]
    res = resolve_name(db, package, pkg["aliases"] if pkg else {}, source)
    if res.failed:
        raise _no_recipe(source)
    known_ecs: dict[str, str] = {}
    if res.source == "easybuild":
        rows = db.index.lookup([normalise(res.upstream_name, "easybuild")])
        known_ecs = next((r["details"].get("easyconfigs", {}) for r in rows
                          if r["source"] == "easybuild"
                          and r["upstream_name"] == res.upstream_name), {})
    if not version:  # the newest release; for EasyBuild, the newest with a CUDA easyconfig
        versions = [v for v in res.versions if res.source != "easybuild" or v in known_ecs]
        if not versions:
            raise QueueError(422, f"{res.source} lists no release of {res.upstream_name} "
                                  "to build; name a version.")
        version = versions[0]
    elif not admin and version not in res.versions:
        # Users build releases the index lists; sysadmins may name anything (a branch, a
        # patched version) and own the result.
        shown = ", ".join(res.versions[:6]) or "none"
        raise QueueError(422, f"{res.source} has no release {version} of "
                              f"{res.upstream_name}. Known releases: {shown}.")
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
    restricted = bool(pkg and pkg["visibility"] == "restricted")
    if spec_append:  # a repair (or a retry of one) adds to the spec; store the result
        spec = f"{spec or f'{res.upstream_name}@{version}'} {spec_append}"

    plan, skipped = [], []
    for target in dict.fromkeys(targets):
        for platform in coordinator.TARGET_PLATFORMS[target]:
            if platforms is not None and platform not in platforms:
                continue  # e.g. a retry of only the arm64 half of a generic release
            try:
                recipe = render(res.source, package, version, target,
                                registry=settings.registry_host, platform=platform,
                                spec=spec or f"{res.upstream_name}@{version}",
                                easyconfig=easyconfig, conda_name=res.upstream_name,
                                cuda_build_marker=marker, eb_options=eb_options)
            except RecipeError as e:
                skipped.append({"target": target, "platform": platform, "reason": str(e)})
                continue
            plan.append((target, platform, recipe))

    queued = []
    with db.tx():  # check and act under one lock (see the docstring)
        # Look the package up again: a concurrent request may have created it since.
        current = find_package(db, package)
        pid = current["id"] if current else None
        todo = []
        for target, platform, recipe in plan:
            if skip_existing and pid is not None \
                    and (st := _existing(db, pid, version, target, platform)):
                skipped.append({"target": target, "platform": platform,
                                "reason": "already available" if st == "published"
                                else "already queued"})
            else:
                todo.append((target, platform, recipe))
        if enforce_quota and not admin and todo:
            left = quota(db) - used_this_week(db, requested_by)
            if len(todo) > left:
                raise QueueError(429, f"This needs {len(todo)} build job"
                                      f"{'s' if len(todo) != 1 else ''}, and your weekly "
                                      f"quota has {max(left, 0)} left. Upvote an existing "
                                      "request, or ask the research computing team.")
        if todo and pid is None:
            pid = db.packages.upsert({"name": package, "title": package, "origin": "request"})
        for target, platform, recipe in todo:
            bid = coordinator.enqueue(db, pid, version, res.source, target, platform, recipe,
                                      requested_by=requested_by, restricted=restricted,
                                      request_id=request_id, spec=spec,
                                      easyconfig=easyconfig, eb_options=eb_options,
                                      repair=repair)
            queued.append({"id": bid, "target": target, "platform": platform})
    return {"package": package, "version": version, "source": res.source, "rule": res.rule,
            "reason": res.reason, "cudaPort": res.cuda_port, "queued": queued,
            "skipped": skipped, "packageId": pid, "restricted": restricted}
