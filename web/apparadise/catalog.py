"""Phase 1 catalog: hand-maintained `catalog/<name>.yml` files, loaded into the DB,
served read-only under /api/catalog.

The catalog is public: anyone may read it without signing in. Restricted packages
are only listed for sysadmins (RCO-Admins), and a non-sysadmin asking for one by name
gets the same 404 as for a package that does not exist.

File format (see catalog/README.md):

    name: gromacs                 # lowercase; also the module name
    title: GROMACS
    description: Molecular dynamics ...
    homepage: https://www.gromacs.org
    source: spack                 # spack | easybuild | pixi (one source per image)
    visibility: public            # public | restricted
    tags: [molecular-dynamics, mpi]
    aliases: {spack: gromacs, easybuild: GROMACS}
    executables: [gmx_mpi]
    versions:
      "2026.3":
        vr:  {status: planned}
        rtx: {status: published, digest: "sha256:...", test: "smoke: gmx --version ok"}
"""

import logging
import re
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .auth import current_user
from .config import Settings
from .db import SOURCES, TARGETS, VISIBILITIES, Database, get_db

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,63}\Z")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z")
EXE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}\Z")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}\Z")
CATALOG_STATUSES = ("planned", "queued", "running", "published", "failed")
FIELDS = {"name", "title", "description", "homepage", "source", "visibility", "tags",
          "aliases", "executables", "versions"}
TARGET_FIELDS = {"status", "digest", "test", "built", "stage"}


class CatalogError(ValueError):
    """A catalog file is invalid. The message names the file and the field."""


def parse_entry(data: Any, origin: str = "<memory>") -> dict[str, Any]:
    """Validate one catalog document and return it normalised. Raises CatalogError."""

    def bad(msg: str) -> CatalogError:
        return CatalogError(f"{origin}: {msg}")

    if not isinstance(data, dict):
        raise bad("must be a mapping")
    unknown = set(data) - FIELDS
    if unknown:
        raise bad(f"unknown field(s): {', '.join(sorted(map(str, unknown)))}")
    name = data.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise bad("name: lowercase letters, digits, '.', '_', '+' or '-', at most 64")
    source = data.get("source")
    if source not in SOURCES:
        raise bad(f"source: must be one of {', '.join(SOURCES)}")
    visibility = data.get("visibility", "public")
    if visibility not in VISIBILITIES:
        raise bad(f"visibility: must be one of {', '.join(VISIBILITIES)}")
    for key in ("title", "description", "homepage"):
        if key in data and not isinstance(data[key], str):
            raise bad(f"{key}: must be text")
    homepage = data.get("homepage", "")
    if homepage and not re.match(r"^https://[^\s\"'<>]+\Z", homepage):
        raise bad("homepage: must be an https:// URL")
    tags = data.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) and TAG_RE.match(t) for t in tags):
        raise bad("tags: a list of lowercase words with '-'")
    exes = data.get("executables", [])
    if not isinstance(exes, list) or not all(isinstance(e, str) and EXE_RE.match(e) for e in exes):
        raise bad("executables: a list of command names (letters, digits, '.', '_', '+', '-')")
    aliases = data.get("aliases", {})
    if not isinstance(aliases, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in aliases.items()
    ):
        raise bad("aliases: a mapping of source to upstream name")
    versions = data.get("versions", {})
    if not isinstance(versions, dict):  # empty: planned, version chosen at build time
        raise bad("versions: a mapping of version to targets")
    builds = []
    for version, targets in versions.items():
        version = str(version)
        if not VERSION_RE.match(version):
            raise bad(f"versions: {version!r} is not a valid version")
        if not isinstance(targets, dict) or not targets:
            raise bad(f"versions.{version}: a mapping of target to status")
        for target, info in targets.items():
            if target not in TARGETS:
                raise bad(f"versions.{version}: unknown target {target!r} "
                          f"(one of {', '.join(TARGETS)})")
            info = info or {}
            if not isinstance(info, dict) or set(info) - TARGET_FIELDS:
                raise bad(f"versions.{version}.{target}: fields are "
                          f"{', '.join(sorted(TARGET_FIELDS))}")
            status = info.get("status", "planned")
            if status not in CATALOG_STATUSES:
                raise bad(f"versions.{version}.{target}.status: one of "
                          f"{', '.join(CATALOG_STATUSES)}")
            digest = str(info.get("digest", ""))
            if status == "published" and not DIGEST_RE.match(digest):
                raise bad(f"versions.{version}.{target}: a published build needs "
                          "digest: sha256:<64 hex>")
            if digest and not DIGEST_RE.match(digest):
                raise bad(f"versions.{version}.{target}.digest: must be sha256:<64 hex>")
            stage = info.get("stage")
            if status == "failed" and stage is None:
                raise bad(f"versions.{version}.{target}: a failed build needs stage:")
            builds.append({
                "version": version,
                "target": target,
                "source": source,
                "status": status,
                "stage": stage,
                "image_digest": digest,
                "test_result": str(info.get("test", "")),
                "finished_at": str(info["built"]) if "built" in info else None,
                "recipe": f"recipes/{name}/{version}/",
            })
    return {
        "name": name,
        "title": data.get("title") or name,
        "description": (data.get("description") or "").strip(),
        "homepage": homepage,
        "source": source,
        "visibility": visibility,
        "tags": tags,
        "aliases": aliases,
        "executables": exes,
        "builds": builds,
    }


def load_dir(path: str | Path) -> list[dict[str, Any]]:
    """Parse every `*.yml` file in `path` (README and dotfiles excluded)."""
    entries, seen = [], set()
    for f in sorted(Path(path).glob("*.yml")):
        with f.open() as fh:
            entry = parse_entry(yaml.safe_load(fh), f.name)
        if f.stem != entry["name"]:
            raise CatalogError(f"{f.name}: file name must be {entry['name']}.yml")
        if entry["name"] in seen:
            raise CatalogError(f"{f.name}: duplicate package {entry['name']}")
        seen.add(entry["name"])
        entries.append(entry)
    return entries


def sync(db: Database, entries: list[dict[str, Any]]) -> dict[str, int]:
    """Make the DB's catalog-origin packages match `entries`."""
    wanted = {e["name"] for e in entries}
    removed = 0
    for name in db.packages.names("catalog"):
        if name not in wanted:
            db.packages.retire_from_catalog(name)  # keeps pipeline build history
            removed += 1
    for e in entries:
        pid = db.packages.upsert({**e, "origin": "catalog"})
        db.builds.replace_catalog_builds(pid, e["builds"])
    return {"packages": len(entries), "removed": removed}


def load_and_sync(db: Database, settings: Settings) -> dict[str, int]:
    path = Path(settings.catalog_dir)
    if not path.is_dir():
        log.warning("CATALOG_DIR %s does not exist; the catalog is empty", path)
        return {"packages": 0, "removed": 0}
    result = sync(db, load_dir(path))
    log.info("catalog loaded: %(packages)d packages, %(removed)d removed", result)
    return result


# -- delivery commands ---------------------------------------------------------------


def version_key(v: str) -> tuple:
    """Natural sort key: 2026.10 sorts after 2026.3."""
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"[._+-]", v))


def image_tag(name: str, version: str, target: str) -> str:
    return f"{name}:{version}-{target}"


def project_for(visibility: str) -> str:
    return "restricted" if visibility == "restricted" else "public"


def commands(settings: Settings, pkg: dict[str, Any], version: str, target: str) -> dict[str, str]:
    """The copy-paste lines a user needs for one published build."""
    tag = image_tag(pkg["name"], version, target)
    project = project_for(pkg["visibility"])
    exe = pkg["executables"][0] if pkg["executables"] else "bash"
    sqsh = f"{settings.sqsh_dir}/{target}/{pkg['name']}-{version}-{target}.sqsh"
    return {
        "module": f"module load {pkg['name']}/{version}",
        "srunRegistry": f"srun --container-image={settings.registry_host}#{project}/{tag} {exe}",
        "srunSqsh": f"srun --container-image={sqsh} {exe}",
        "pull": f"{settings.registry_host}/{project}/{tag}",
        "sqsh": sqsh,
    }


# -- API -------------------------------------------------------------------------------

router = APIRouter(tags=["catalog"])  # mounted under /api/ in main.create_app


def _sysadmin(request: Request) -> bool:
    user = current_user(request)
    return bool(user and user.is_admin)


def _summary(pkg: dict[str, Any], builds: list[dict[str, Any]]) -> dict[str, Any]:
    targets: dict[str, str] = {}
    for b in sorted(builds, key=lambda b: version_key(b["version"]), reverse=True):
        targets.setdefault(b["target"], b["status"])  # newest version wins per target
    versions = sorted({b["version"] for b in builds}, key=version_key, reverse=True)
    return {
        "name": pkg["name"],
        "title": pkg["title"],
        "description": pkg["description"],
        "source": builds[0]["source"] if builds else None,
        "visibility": pkg["visibility"],
        "tags": pkg["tags"],
        "votes": pkg.get("votes", 0),
        "latest": versions[0] if versions else None,
        "targets": targets,
        "published": any(b["status"] == "published" for b in builds),
        "status": _overall(builds),
    }


def _overall(builds: list[dict[str, Any]]) -> str:
    """One word for the list view: published > running > queued > failed > planned."""
    statuses = {b["status"] for b in builds}
    for s in ("published", "running", "queued", "failed"):
        if s in statuses:
            return s
    return "planned"


@router.get("/catalog")
async def list_catalog(
    request: Request,
    db: Annotated[Database, Depends(get_db)],
    q: Annotated[str, Query(max_length=100)] = "",
) -> dict[str, Any]:
    admin = _sysadmin(request)
    pkgs = await db.run(db.packages.list, include_restricted=admin, q=q.strip())
    out = []
    for p in pkgs:
        builds = await db.run(db.builds.for_package, p["id"])
        out.append(_summary(p, builds))
    return {"packages": out, "targets": list(TARGETS)}


@router.get("/catalog/{name}")
async def get_package(
    name: str, request: Request, db: Annotated[Database, Depends(get_db)]
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    pkg = await db.run(db.packages.get, name, include_restricted=_sysadmin(request))
    if pkg is None:
        raise HTTPException(404, "No such package.")
    builds = await db.run(db.builds.for_package, pkg["id"])
    versions: dict[str, dict[str, Any]] = {}
    for b in builds:
        v = versions.setdefault(b["version"], {"version": b["version"], "targets": {}})
        entry: dict[str, Any] = {
            "status": b["status"],
            "stage": b["stage"],
            "digest": b["image_digest"],
            "test": b["test_result"],
            "built": b["finished_at"],
        }
        if b["status"] == "published":
            entry["commands"] = commands(settings, pkg, b["version"], b["target"])
        v["targets"][b["target"]] = entry
    return {
        **_summary(pkg, builds),
        "homepage": pkg["homepage"],
        "aliases": pkg["aliases"],
        "executables": pkg["executables"],
        "versions": sorted(
            versions.values(), key=lambda v: version_key(v["version"]), reverse=True
        ),
        "module": {
            "load": f"module load {pkg['name']}" if any(
                b["status"] == "published" for b in builds) else None,
        },
    }
