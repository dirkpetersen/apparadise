"""Server-side delivery: multi-arch manifests and module files. No human step.

Staging the squashfs files happens on cluster nodes, one per architecture, through the
builder API (`python -m apparadise_builder.stage`, which uses the node's builder token and
never touches this database). This module runs on the apparadise server, from cron:

    */10 * * * * cd /srv/apparadise/web && .venv/bin/python3 -m apparadise.deliver \\
        --db data/apparadise.sqlite3 --registry registry.example.org

1. When the newest generic build of a (package, version) is published for both amd64 and
   arm64, push the multi-arch `<version>-generic` manifest, built from the two images'
   recorded digests (never their mutable tags). It is pushed again when either side is
   rebuilt.
2. Regenerate Lmod modules and wrappers for every release whose files are in place. A
   pipeline build counts once it has been delivered, and a generic release only once both
   architectures have been delivered.

Every item is handled on its own: one failure is logged and recorded, and the rest go on.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .catalog import release_tag
from .db import Database, now
from .modules import generate

log = logging.getLogger(__name__)
Run = Callable[[Sequence[str]], None]


def run(argv: Sequence[str]) -> None:
    subprocess.run(list(argv), check=True, capture_output=True)


def _latest_rows(db: Database) -> list[dict[str, Any]]:
    """The current release per (package, version, target, platform): its newest *published*
    build. A failed rebuild never takes a working release away. Same rule as
    coordinator.pending_deliveries."""
    return db.query(
        "SELECT b.*, p.name, p.title, p.description, p.homepage, p.executables, p.visibility "
        "FROM build b JOIN package p ON p.id = b.package_id WHERE b.id = ("
        " SELECT MAX(b2.id) FROM build b2 WHERE b2.package_id = b.package_id"
        " AND b2.version = b.version AND b2.target = b.target AND b2.platform = b.platform"
        " AND b2.status = 'published')")


def manifests_due(db: Database) -> list[dict[str, Any]]:
    """Generic (package, version) pairs whose newest amd64 and arm64 builds are both
    published, and at least one of the two is not in the current manifest yet."""
    pairs: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for r in _latest_rows(db):
        if r["target"] == "generic" and r["origin"] == "pipeline":
            pairs.setdefault((r["name"], r["version"]), {})[r["platform"]] = r
    due = []
    for (name, version), rows in sorted(pairs.items()):
        if set(rows) != {"linux/amd64", "linux/arm64"}:
            continue
        if any(r["status"] != "published" for r in rows.values()):
            continue
        if all(r["manifested_at"] for r in rows.values()):
            continue
        due.append({"name": name, "version": version, "rows": rows,
                    "restricted": any(r["restricted"] for r in rows.values())})
    return due


def entries_from_db(db: Database) -> list[dict[str, Any]]:
    """Catalog-shaped entries for the module generator, with only releases whose squashfs
    files exist: hand-built catalog builds (staged by the operator, see the runbook) and
    delivered pipeline builds; generic only when every platform it has is delivered."""
    by_pkg: dict[str, dict[str, Any]] = {}
    rows = _latest_rows(db)
    # A pipeline generic release is ready only when both platforms are published + delivered.
    staged: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        if r["target"] == "generic" and r["origin"] == "pipeline" \
                and r["status"] == "published" and r["delivered_at"] is not None:
            staged.setdefault((r["name"], r["version"]), set()).add(r["platform"])
    generic_ok = {k: v == {"linux/amd64", "linux/arm64"} for k, v in staged.items()}
    for r in rows:
        if r["status"] != "published":
            continue
        ready = r["origin"] == "catalog" or r["delivered_at"] is not None
        if r["target"] == "generic" and r["origin"] == "pipeline":
            ready = True  # decided per release below
        if not ready:
            continue
        e = by_pkg.setdefault(r["name"], {
            "name": r["name"], "title": r["title"], "description": r["description"],
            "homepage": r["homepage"], "executables": json.loads(r["executables"]),
            "visibility": r["visibility"], "builds": []})
        e["builds"].append({"version": r["version"], "target": r["target"],
                            "status": "published", "origin": r["origin"]})
    for e in by_pkg.values():
        e["builds"] = [b for b in e["builds"]
                       if not (b["target"] == "generic" and b["origin"] == "pipeline"
                               and not generic_ok.get((e["name"], b["version"]), False))]
    return [e for e in by_pkg.values() if e["builds"]]


def deliver(db: Database, *, registry: str, sqsh_dir: str, module_dir: str, wrapper_dir: str,
            include_restricted: bool = False, runner: Run = run,
            tool: str = "podman") -> dict[str, Any]:
    manifests, errors = [], []
    for item in manifests_due(db):
        project = "restricted" if item["restricted"] else "public"
        repo = f"{registry}/{project}/{item['name']}"
        target = f"{repo}:{release_tag(item['version'], 'generic')}"
        local = f"apparadise-manifest/{item['name']}:{item['version']}-generic"
        try:
            try:
                runner([tool, "manifest", "rm", local])  # start from a clean local list
            except subprocess.CalledProcessError:
                pass  # there was none
            runner([tool, "manifest", "create", local])
            for plat in ("linux/amd64", "linux/arm64"):
                runner([tool, "manifest", "add", local,
                        f"docker://{repo}@{item['rows'][plat]['image_digest']}"])
            runner([tool, "manifest", "push", "--all", local, f"docker://{target}"])
        except (subprocess.CalledProcessError, OSError) as e:
            log.error("generic manifest for %s %s failed: %s", item["name"], item["version"],
                      type(e).__name__)
            errors.append({"package": item["name"], "version": item["version"],
                           "error": type(e).__name__})
            continue
        with db.tx() as c:
            for r in item["rows"].values():
                c.execute("UPDATE build SET manifested_at = ? WHERE id = ?", (now(), r["id"]))
        manifests.append(target)
    written = generate(entries_from_db(db), module_dir=module_dir, wrapper_dir=wrapper_dir,
                       sqsh_dir=sqsh_dir, include_restricted=include_restricted)
    return {"manifests": manifests, "modules": written, "errors": errors}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description="Push generic manifests and regenerate modules.")
    p.add_argument("--db", required=True)
    p.add_argument("--registry", required=True)
    p.add_argument("--sqsh-dir", default="/shared/containers")
    p.add_argument("--module-dir", default="/shared/modules/apparadise")
    p.add_argument("--wrapper-dir", default="/shared/apps/apparadise/bin")
    p.add_argument("--include-restricted", action="store_true")
    a = p.parse_args(argv)
    if not Path(a.db).is_file():  # never create an empty database by accident
        p.error(f"{a.db}: no such database (run this on the apparadise server)")
    db = Database(a.db)
    try:
        result = deliver(db, registry=a.registry, sqsh_dir=a.sqsh_dir, module_dir=a.module_dir,
                         wrapper_dir=a.wrapper_dir, include_restricted=a.include_restricted)
    finally:
        db.close()
    print(result)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
