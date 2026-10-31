"""Nightly retries of failed builds (phase 3). Runs after the index refresh, from cron:

    cd /srv/apparadise/web && .venv/bin/python3 -m apparadise.retry --db data/apparadise.sqlite3

A release (package, version, target, platform) is retried when its newest pipeline build
failed and either
- its recipe changed upstream since the failure (the index entry's `changed_at`: new
  versions, CUDA variant, arches or easyconfig files), or
- the failure is older than `retry.after_days` (default 14): dependencies, toolchains and
  base images move on without the package's own recipe changing.
Never more than `retry.max_attempts` (default 3) failed builds per release (auto-repair's
own rebuilds don't count: apparadise.repair has its own cap), never for
`no-recipe`, and never while a build of that release is queued or running. Retries go
through buildqueue with the same source, as user `system`, and stay linked to the original
request. Both values are runtime policy (the settings table), like the quota.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .buildqueue import QueueError, queue_builds
from .config import Settings
from .db import Database
from .resolver import candidates

log = logging.getLogger(__name__)
AFTER_DAYS_KEY, AFTER_DAYS_DEFAULT = "retry.after_days", 14
MAX_ATTEMPTS_KEY, MAX_ATTEMPTS_DEFAULT = "retry.max_attempts", 3


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def due(db: Database, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(UTC)
    after_days = int(db.settings.get(AFTER_DAYS_KEY, AFTER_DAYS_DEFAULT))
    max_attempts = int(db.settings.get(MAX_ATTEMPTS_KEY, MAX_ATTEMPTS_DEFAULT))
    stale = _iso(now - timedelta(days=after_days))
    rows = db.query(
        "SELECT b.*, p.name, p.aliases, "
        " (SELECT COUNT(*) FROM build f WHERE f.package_id = b.package_id "
        "  AND f.version = b.version AND f.target = b.target AND f.platform = b.platform "
        "  AND f.origin = 'pipeline' AND f.status = 'failed' AND f.repair = '') AS attempts "
        "FROM build b JOIN package p ON p.id = b.package_id "
        "WHERE b.origin = 'pipeline' AND b.status = 'failed' "
        "AND b.stage IS NOT 'no-recipe' AND b.id = ("
        " SELECT MAX(b2.id) FROM build b2 WHERE b2.package_id = b.package_id "
        " AND b2.version = b.version AND b2.target = b.target AND b2.platform = b.platform "
        " AND b2.origin = 'pipeline') "
        "ORDER BY b.id")
    out = []
    for r in rows:
        if r["attempts"] >= max_attempts:
            continue
        failed_at = r["finished_at"] or r["created_at"]
        names = candidates(r["name"], json.loads(r["aliases"] or "{}"))
        changed = max((e["changed_at"] for e in db.index.lookup(names)
                       if e["source"] == r["source"]), default="")
        reason = ("recipe changed upstream" if changed and changed > failed_at
                  else f"failed over {after_days} days ago" if failed_at < stale else None)
        if reason:
            out.append({"build": r, "reason": reason})
    return out


def retry(db: Database, settings: Settings, now: datetime | None = None) -> dict[str, Any]:
    queued, errors = [], []
    for item in due(db, now):
        b = item["build"]
        try:
            res = queue_builds(db, settings, package=b["name"], version=b["version"],
                               targets=[b["target"]], source=b["source"],
                               spec=b["spec"], easyconfig=b["easyconfig"],
                               eb_options=json.loads(b["eb_options"] or "{}"),
                               requested_by="system", admin=True, skip_existing=True,
                               request_id=b["request_id"], platforms=[b["platform"]])
        except QueueError as e:  # e.g. the recipe is gone from the index now
            errors.append({"build": b["id"], "error": e.message})
            continue
        new = [q["id"] for q in res["queued"]]
        if new:
            db.audit.add("system", "build.retry", "build", str(b["id"]),
                         {"retry": new, "reason": item["reason"]})
            queued.append({"failed": b["id"], "retry": new[0], "reason": item["reason"]})
    return {"queued": queued, "errors": errors}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description="Re-queue failed builds whose recipe changed "
                                            "upstream or whose failure is old.")
    p.add_argument("--db", required=True)
    a = p.parse_args(argv)
    if not Path(a.db).is_file():  # never create an empty database by accident
        p.error(f"{a.db}: no such database (run this on the apparadise server)")
    settings = Settings()  # web/.env: registry host and recipe settings
    db = Database(a.db)
    try:
        result = retry(db, settings)
    finally:
        db.close()
    print(result)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
