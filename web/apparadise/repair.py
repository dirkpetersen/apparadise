"""Bounded auto-repair (phase 4): when a pipeline build fails, try one known fix.

The known-patch library is `recipes/repairs.yml` (see its header). After a failure is
recorded, `attempt()` looks for the first rule that matches the failure's scrubbed error
line at the failed stage, for the build's source, and has not been tried on this release.
It queues one repaired build of the same release and platform, as user `system`, linked to
the same request, and records the attempt on the failure group and in the audit log.

Bounds: at most `repair.max_attempts` (runtime policy, default 2) repaired builds per
release (package, version, target, platform), never the same rule twice, never for
`no-recipe`, never while another build of the release is queued or running, and never on
top of a build that was itself already the last allowed attempt. A repair can only change
what the recipe generator already validates: a Spack spec suffix, EasyBuild options from an
allowlist (recipes.EB_OPTIONS), or the source.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .buildqueue import QueueError, queue_builds
from .config import Settings
from .db import Database, now
from .recipes import load_targets
from .resolver import PORT_ORDER, candidates

log = logging.getLogger(__name__)
LIBRARY = Path(__file__).resolve().parents[2] / "recipes" / "repairs.yml"
MAX_KEY, MAX_DEFAULT = "repair.max_attempts", 2
ACTIONS = {"spec_append", "eb_options", "switch_source", "next_toolchain"}


class LibraryError(ValueError):
    pass


@lru_cache(maxsize=4)
def load(path: str = str(LIBRARY)) -> tuple[dict[str, Any], ...]:
    """The validated rules, in file order. Raises LibraryError naming the bad rule."""
    rules = yaml.safe_load(Path(path).read_text()) or []
    out, seen = [], set()
    for i, r in enumerate(rules):
        where = f"{path} rule {i + 1}"
        if not isinstance(r, dict) or not re.fullmatch(r"[a-z0-9-]{3,40}", str(r.get("id"))):
            raise LibraryError(f"{where}: needs an id of lowercase letters, digits and -")
        if r["id"] in seen:
            raise LibraryError(f"{where}: duplicate id {r['id']}")
        seen.add(r["id"])
        try:
            rx = re.compile(r["match"], re.I)
        except (KeyError, re.error, TypeError) as e:
            raise LibraryError(f"{where}: bad match: {e}") from None
        stages = r.get("stages") or []
        if not stages or not set(stages) <= {"concretize", "build", "package", "test"}:
            raise LibraryError(f"{where}: stages must be concretize/build/package/test")
        actions = r.get("actions") or {}
        for source, action in actions.items():
            if source not in ("spack", "easybuild", "pixi") or not isinstance(action, dict) \
                    or not action or not set(action) <= ACTIONS:
                raise LibraryError(f"{where}: bad action for {source}")
        out.append({**r, "rx": rx, "stages": tuple(stages), "actions": actions})
    return tuple(out)


def next_toolchain(eb_options: dict[str, Any]) -> str | None | bool:
    """The try-toolchain after the one these options used: the next generation in
    targets.yml, then None (the easyconfig's own toolchain), then False (nothing left)."""
    chain = list(load_targets()["easybuild_toolchains"]) + [None]
    current = eb_options.get("try-toolchain", chain[0])
    if current is None:
        return False
    i = chain.index(current) if current in chain else len(chain) - 2
    return chain[i + 1]


def _release_builds(db: Database, b: dict[str, Any]) -> list[dict[str, Any]]:
    return db.query(
        "SELECT id, status, repair FROM build WHERE package_id = ? AND version = ? "
        "AND target = ? AND platform = ? AND origin = 'pipeline' ORDER BY id",
        (b["package_id"], b["version"], b["target"], b["platform"]))


def _next_source(db: Database, b: dict[str, Any], tried: set[str]) -> str | None:
    """The next source, in the resolver's port order, that has a CUDA recipe for this
    very version (a repair never changes what is being built)."""
    pkg = db.query_one("SELECT name, aliases FROM package WHERE id = ?", (b["package_id"],))
    rows = db.index.lookup(candidates(pkg["name"], json.loads(pkg["aliases"] or "{}")))
    cuda = {r["source"] for r in rows if r["has_cuda"] and b["version"] in r["versions"]}
    return next((s for s in PORT_ORDER if s in cuda and s not in tried), None)


def plan(db: Database, build_id: int, rules=None) -> dict[str, Any] | None:
    """-> {build, rule, action, failure} for the repair to queue, or None."""
    b = db.query_one(
        "SELECT b.*, f.error_line, f.id AS fid FROM build b JOIN failure f "
        "ON f.id = b.failure_id WHERE b.id = ? AND b.status = 'failed' "
        "AND b.origin = 'pipeline'", (build_id,))
    if b is None or b["stage"] in (None, "no-recipe", "publish"):
        return None
    history = _release_builds(db, b)
    if history[-1]["id"] != b["id"]:
        return None  # a newer build of this release exists (queued, running, or done)
    used = {h["repair"] for h in history if h["repair"]}
    if len(used) >= int(db.settings.get(MAX_KEY, MAX_DEFAULT)):
        return None
    prior = json.loads(b["eb_options"] or "{}")
    for rule in rules if rules is not None else load():
        action = rule["actions"].get(b["source"])
        if action is None or b["stage"] not in rule["stages"]:
            continue
        if not rule["rx"].search(b["error_line"] or ""):
            continue
        if action.get("next_toolchain"):
            # Walks the whole chain (newest generation first, then the easyconfig's own), so
            # it may repeat; it counts as one repair rule against the cap.
            nxt = next_toolchain(prior)
            if nxt is False:
                continue
            return {"build": b, "rule": rule, "failure": b["fid"],
                    "action": {"eb_options": {"try-toolchain": nxt}}}
        if rule["id"] in used:
            continue
        if action.get("spec_append") and action["spec_append"] in (b["spec"] or "").split():
            continue  # this fix is already in the spec (another rule added it)
        if set(action.get("eb_options") or {}) & set(prior):
            continue  # never overwrite an earlier fix's option
        return {"build": b, "rule": rule, "action": action, "failure": b["fid"]}
    return None


def attempt(db: Database, settings: Settings, build_id: int, rules=None) -> dict[str, Any] | None:
    """Queue at most one repaired build for a failed build. -> {rule, retry} or None."""
    p = plan(db, build_id, rules)
    if p is None:
        return None
    b, rule, action = p["build"], p["rule"], p["action"]
    kwargs: dict[str, Any] = {"source": b["source"], "spec": b["spec"],
                              "easyconfig": b["easyconfig"],
                              "eb_options": json.loads(b["eb_options"] or "{}")}
    if action.get("spec_append"):
        kwargs["spec_append"] = action["spec_append"]
    if action.get("eb_options"):
        kwargs["eb_options"] = {**kwargs["eb_options"], **action["eb_options"]}
    if action.get("switch_source"):
        tried = {b["source"]} | {r["source"] for r in db.query(
            "SELECT DISTINCT source FROM build WHERE package_id = ? AND version = ?",
            (b["package_id"], b["version"]))}
        source = _next_source(db, b, tried)
        if source is None:
            return None
        kwargs = {"source": source}  # a new source starts from its own defaults
    name = db.query_one("SELECT name FROM package WHERE id = ?", (b["package_id"],))["name"]
    try:
        res = queue_builds(db, settings, package=name, version=b["version"],
                           targets=[b["target"]], platforms=[b["platform"]],
                           requested_by="system", admin=True, skip_existing=True,
                           request_id=b["request_id"], repair=rule["id"], **kwargs)
    except QueueError as e:
        log.info("repair %s of build %s not queued: %s", rule["id"], build_id, e.message)
        return None
    if not res["queued"]:
        return None
    retry = res["queued"][0]["id"]
    entry = {"build": build_id, "rule": rule["id"], "why": rule.get("why", ""),
             "retry": retry, "at": now()}
    with db.tx() as c:
        row = c.execute("SELECT repair_attempts FROM failure WHERE id = ?",
                        (p["failure"],)).fetchone()
        attempts = json.loads(row[0] or "[]") + [entry]
        c.execute("UPDATE failure SET repair_attempts = ? WHERE id = ?",
                  (json.dumps(attempts), p["failure"]))
    db.audit.add("system", "build.repair", "build", str(build_id),
                 {"rule": rule["id"], "retry": retry})
    return {"rule": rule["id"], "retry": retry}

