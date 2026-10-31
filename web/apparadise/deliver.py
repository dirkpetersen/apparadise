"""Delivery: published pipeline builds become modules users can load. No human step.

Run from cron on a node of each architecture that has Enroot and the shared CephFS mounts
(the squashfs import only works for the host's own architecture):

    */10 * * * * cd /srv/apparadise/web && .venv/bin/python3 -m apparadise.deliver \\
        --db data/apparadise.sqlite3 --registry registry.example.org

Per run:
1. stage every published, undelivered pipeline build of this host's platform with
   tools/stage-sqsh (by digest), and mark it delivered;
2. join the two per-arch `generic` images into the multi-arch `<version>-generic` manifest
   once both are published;
3. regenerate Lmod modules and wrappers from the database for every delivered release
   (restricted packages only with --include-restricted, into a sysadmin-only tree).
"""

from __future__ import annotations

import argparse
import platform as _platform
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .coordinator import arch_of, image_tag
from .db import Database, now
from .modules import generate

REPO = Path(__file__).resolve().parents[2]
STAGE = REPO / "tools" / "stage-sqsh"
Run = Callable[[Sequence[str]], None]
HOST_PLATFORM = {"x86_64": "linux/amd64", "aarch64": "linux/arm64"}.get(_platform.machine(),
                                                                         "linux/amd64")


def run(argv: Sequence[str]) -> None:
    subprocess.run(list(argv), check=True)


def pending(db: Database, platform: str) -> list[dict[str, Any]]:
    return db.query(
        "SELECT b.id, p.name, b.version, b.target, b.platform, b.image_digest, b.restricted "
        "FROM build b JOIN package p ON p.id = b.package_id WHERE b.origin = 'pipeline' "
        "AND b.status = 'published' AND b.delivered_at IS NULL AND b.platform = ? "
        "ORDER BY b.id", (platform,))


def mark_delivered(db: Database, build_id: int) -> None:
    with db.tx() as c:
        c.execute("UPDATE build SET delivered_at = ? WHERE id = ?", (now(), build_id))


def generic_pairs(db: Database) -> list[dict[str, Any]]:
    """(package, version) whose generic amd64 and arm64 builds are both published."""
    return db.query(
        "SELECT p.name, b.version, MAX(b.restricted) AS restricted FROM build b "
        "JOIN package p ON p.id = b.package_id WHERE b.origin = 'pipeline' "
        "AND b.target = 'generic' AND b.status = 'published' "
        "GROUP BY p.name, b.version HAVING COUNT(DISTINCT b.platform) = 2")


def entries_from_db(db: Database) -> list[dict[str, Any]]:
    """Catalog-shaped entries for the module generator. A build counts once it is on shared
    storage: hand-built catalog builds are staged by the operator (runbook); pipeline builds
    once this module delivered them."""
    out = []
    for p in db.packages.list(include_restricted=True):
        rows = db.query(
            "SELECT version, target, platform, status FROM build WHERE package_id = ? "
            "AND status = 'published' AND (origin = 'catalog' OR delivered_at IS NOT NULL)",
            (p["id"],))
        if not rows:
            continue
        out.append({**p, "builds": rows})
    return out


def deliver(db: Database, *, registry: str, sqsh_dir: str, module_dir: str, wrapper_dir: str,
            platform: str = HOST_PLATFORM, include_restricted: bool = False,
            runner: Run = run, tool: str = "podman") -> dict[str, Any]:
    staged, manifests = [], []
    for b in pending(db, platform):
        project = "restricted" if b["restricted"] else "public"
        runner([str(STAGE), registry, project, b["name"], b["version"], b["target"],
                b["image_digest"], sqsh_dir])
        mark_delivered(db, b["id"])
        staged.append(b["id"])
    for g in generic_pairs(db):
        project = "restricted" if g["restricted"] else "public"
        repo = f"{registry}/{project}/{g['name']}"
        multi = f"{repo}:{g['version']}-generic"
        local = f"apparadise-manifest/{g['name']}:{g['version']}-generic"
        key = f"manifest:{project}/{g['name']}:{g['version']}"
        if db.settings.get(key):
            continue  # pushed on an earlier run
        runner([tool, "manifest", "create", "--amend", local])
        for plat in ("linux/amd64", "linux/arm64"):
            runner([tool, "manifest", "add", local,
                    f"docker://{repo}:{image_tag(g['version'], 'generic', plat)}"])
        runner([tool, "manifest", "push", "--all", local, f"docker://{multi}"])
        db.settings.set(key, now(), by="system")
        manifests.append(multi)
    written = generate(entries_from_db(db), module_dir=module_dir, wrapper_dir=wrapper_dir,
                       sqsh_dir=sqsh_dir, include_restricted=include_restricted)
    return {"staged": staged, "manifests": manifests, "modules": written,
            "arch": arch_of(platform)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Stage published builds and regenerate modules.")
    p.add_argument("--db", required=True)
    p.add_argument("--registry", required=True)
    p.add_argument("--sqsh-dir", default="/shared/containers")
    p.add_argument("--module-dir", default="/shared/modules/apparadise")
    p.add_argument("--wrapper-dir", default="/shared/apps/apparadise/bin")
    p.add_argument("--include-restricted", action="store_true")
    a = p.parse_args(argv)
    db = Database(a.db)
    try:
        result = deliver(db, registry=a.registry, sqsh_dir=a.sqsh_dir, module_dir=a.module_dir,
                         wrapper_dir=a.wrapper_dir, include_restricted=a.include_restricted)
    finally:
        db.close()
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
