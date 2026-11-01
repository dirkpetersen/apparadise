"""Build coordinator: the job queue that pull-based builders claim from.

- enqueue: one `build` row per (target, platform); `generic` builds both platforms.
- claim: a single `UPDATE ... RETURNING` (no SKIP LOCKED, PostgreSQL-portable). A builder
  only gets jobs for its own platform, restricted jobs only go to on-prem builders, and at
  most one EasyBuild job runs per target install tree at a time (QUESTIONS.md Q9).
- report: a builder reports each stage it passes; a failure records the stage and joins a
  failure group by signature.

All functions are synchronous and run through `db.run(...)` from the API.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shlex
from functools import cache
from pathlib import Path
from typing import Any

from . import failures
from .catalog import release_tag
from .db import STAGES, Database, now

TARGET_PLATFORMS = {
    "vr": ("linux/arm64",),
    "rtx": ("linux/amd64",),
    "generic": ("linux/amd64", "linux/arm64"),
}
BUILDER_STAGES = ("build", "package", "test", "publish")  # resolve/generate run server-side
LOG_LIMIT = 64 * 1024 * 1024
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


@cache
def _targets() -> dict[str, Any]:
    from .recipes import load_targets

    return load_targets()


def registry_secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def job_repositories(job: dict[str, Any], package: str) -> tuple[set[str], set[str]]:
    """-> (push_and_pull, pull_only) repository names for a build job's registry token:
    its own image, its test tag and its build cache; the base images it builds FROM."""
    project = "restricted" if job["restricted"] else "public"
    own = {f"{project}/{package}", f"cache/test-{package}",
           f"cache/{job['source']}-{job['target']}"}
    return own, {"cache/base-builder", "cache/base-runtime"}


def base_ref(role: str, target: str, cuda: str) -> str:
    """cache/base-<role>:<cuda>-<target>, pinned @sha256 once targets.yml has the digest."""
    ref = f"cache/base-{role}:{cuda}-{target}"
    digest = ((_targets()[target].get("base_digests") or {}).get(role)) or ""
    if digest:
        if not DIGEST_RE.fullmatch(digest):
            raise ValueError(f"targets.yml {target}.base_digests.{role}: not sha256:<64 hex>")
        ref += f"@{digest}"
    return ref


class JobError(Exception):
    """A builder asked for something it may not do (wrong job, finished job, bad stage)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def enqueue(db: Database, package_id: int, version: str, source: str, target: str,
            platform: str, recipe: str, *, requested_by: str, restricted: bool,
            request_id: int | None = None, spec: str = "", easyconfig: str = "",
            eb_options: dict[str, Any] | None = None, repair: str = "") -> int:
    """Queue one build of one (target, platform); `generic` is queued once per platform."""
    if platform not in TARGET_PLATFORMS[target]:
        raise ValueError(f"{target} does not build for {platform}")
    ts = now()
    with db.tx() as c:
        return c.execute(
                "INSERT INTO build (package_id, version, source, target, platform, origin, "
                "request_id, requested_by, recipe, stage, status, restricted, created_at, "
                "spec, easyconfig, eb_options, repair) "
                "VALUES (?, ?, ?, ?, ?, 'pipeline', ?, ?, ?, 'concretize', 'queued', ?, ?, ?, ?, "
                "?, ?) RETURNING id",
                (package_id, version, source, target, platform, request_id, requested_by,
                 recipe, int(restricted), ts, spec, easyconfig,
                 json.dumps(eb_options or {}, sort_keys=True), repair),
        ).fetchone()[0]


def claim(db: Database, builder: dict[str, Any]) -> dict[str, Any] | None:
    """Atomically hand the oldest eligible queued build to `builder`, or None."""
    ts = now()
    with db.tx() as c:
        row = c.execute(
            "UPDATE build SET status = 'running', builder_id = :bid, started_at = :ts "
            "WHERE status = 'queued' AND id = ("
            "  SELECT b.id FROM build b WHERE b.status = 'queued' AND b.platform = :platform"
            "  AND (b.restricted = 0 OR :onprem = 1)"
            "  AND NOT (b.source = 'easybuild' AND EXISTS ("
            "    SELECT 1 FROM build r WHERE r.status = 'running' AND r.source = 'easybuild'"
            "    AND r.target = b.target AND r.platform = b.platform))"
            "  ORDER BY b.created_at, b.id LIMIT 1) "
            "RETURNING *",
            {"bid": builder["id"], "ts": ts, "platform": builder["platform"],
             "onprem": builder["onprem"]},
        ).fetchone()
        if row is None:
            return None
        job = dict(row)
        pkg = c.execute("SELECT name, visibility, executables FROM package WHERE id = ?",
                        (job["package_id"],)).fetchone()
        # The job's registry login (registry_auth.py): job-<id> plus a one-time secret. Only
        # its SHA-256 is stored, and it stops working when the job leaves 'running'.
        secret = secrets.token_urlsafe(32)
        c.execute("UPDATE build SET registry_secret = ? WHERE id = ?",
                  (registry_secret_hash(secret), job["id"]))
    project = "restricted" if job["restricted"] else "public"
    cuda = str(_targets()[job["target"]]["cuda_version"])
    exes = json.loads(pkg["executables"] or "[]")
    # Smoke test: the GPU is visible inside the container, and the software starts.
    smoke = "nvidia-smi -L" + (f" && {shlex.quote(exes[0])} --version" if exes else "")
    return {
        "id": job["id"],
        "package": pkg["name"],
        "version": job["version"],
        "source": job["source"],
        "target": job["target"],
        "platform": job["platform"],
        "recipe": job["recipe"],
        "image": f"{project}/{pkg['name']}:"
                 f"{release_tag(job['version'], job['target'], job['platform'])}",
        "cache": f"cache/{job['source']}-{job['target']}",
        "stages": list(BUILDER_STAGES),
        "baseBuilder": base_ref("builder", job["target"], cuda),
        "baseRuntime": base_ref("runtime", job["target"], cuda),
        "executables": exes,
        "smoke": smoke,
        "registryToken": {"username": f"job-{job['id']}", "password": secret},
    }


LOST_ERROR = "builder lost: no contact from the builder while the job was running"


def reap_lost(db: Database, cutoff: str) -> list[dict[str, Any]]:
    """Fail running builds whose builder has not been seen since `cutoff` (ISO time), and
    void their registry credentials. Returns the reaped rows."""
    rows = db.query(
        "SELECT b.id, b.stage, b.restricted FROM build b "
        "JOIN builder w ON w.id = b.builder_id WHERE b.status = 'running' "
        "AND COALESCE(w.last_seen, b.started_at) < ?", (cutoff,))
    ts = now()
    for r in rows:
        stage = r["stage"] if r["stage"] in BUILDER_STAGES else "build"
        with db.tx() as c:
            if c.execute("UPDATE build SET status = 'failed', stage = ?, finished_at = ?, "
                         "registry_secret = NULL WHERE id = ? AND status = 'running'",
                         (stage, ts, r["id"])).rowcount:
                db.failures.record(r["id"], failures.signature(stage, LOST_ERROR,
                                                               bool(r["restricted"])),
                                   stage, LOST_ERROR, bool(r["restricted"]),
                                   error_line=LOST_ERROR)
    return rows


def _own_running(db: Database, builder: dict[str, Any], build_id: int) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM build WHERE id = ?", (build_id,))
    if row is None or row["builder_id"] != builder["id"]:
        raise JobError(404, "No such job for this builder.")
    if row["status"] != "running":
        raise JobError(409, "The job is not running.")
    return row


def log_path(data_dir: str | Path, build_id: int) -> Path:
    return Path(data_dir) / "logs" / f"{build_id}.log"


def append_log(db: Database, data_dir: str | Path, builder: dict[str, Any], build_id: int,
               chunk: bytes) -> int:
    _own_running(db, builder, build_id)
    path = log_path(data_dir, build_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    size = path.stat().st_size if path.exists() else 0
    if size + len(chunk) > LOG_LIMIT:
        raise JobError(413, "The build log is over its size limit.")
    with path.open("ab") as fh:
        fh.write(chunk)
    if size == 0:
        with db.tx() as c:
            c.execute("UPDATE build SET log_path = ? WHERE id = ?", (str(path), build_id))
    return size + len(chunk)


def report(db: Database, data_dir: str | Path, builder: dict[str, Any], build_id: int, *,
           stage: str, ok: bool, digest: str = "", test: str = "",
           error: str = "") -> dict[str, Any]:
    if stage not in BUILDER_STAGES:
        raise JobError(422, f"stage must be one of {', '.join(BUILDER_STAGES)}")
    row = _own_running(db, builder, build_id)
    current = row["stage"] if row["stage"] in STAGES else "concretize"
    # Each stage is reported in order: the same stage again, or the next one. Nobody gets
    # to publish without having passed test.
    if not 0 <= STAGES.index(stage) - STAGES.index(current) <= 1:
        raise JobError(409, f"stage {stage} does not follow the reported stage {current}")
    ts = now()
    if ok and stage == "publish":
        if not DIGEST_RE.fullmatch(digest or ""):
            raise JobError(422, "publish needs the image digest (sha256:<64 hex>)")
        with db.tx() as c:
            c.execute("UPDATE build SET stage = 'publish', status = 'published', image_digest = ?, "
                      "test_result = CASE WHEN ? = '' THEN test_result ELSE ? END, "
                      "finished_at = ? WHERE id = ?", (digest, test, test, ts, build_id))
    elif ok:
        with db.tx() as c:
            c.execute("UPDATE build SET stage = ?, test_result = CASE WHEN ? = '' THEN "
                      "test_result ELSE ? END WHERE id = ?", (stage, test, test, build_id))
    else:
        log = ""
        path = log_path(data_dir, build_id)
        if path.exists():
            with path.open("rb") as fh:  # the tail is where the error is
                fh.seek(max(0, path.stat().st_size - 256 * 1024))
                log = fh.read().decode(errors="replace")
        line = error or failures.first_error_line(log)
        restricted = bool(row["restricted"])
        # Scrubbing reads up to 256 KiB of untrusted text: do it before taking the lock.
        excerpt = failures.excerpt(log or line, around=line[:80] or None)
        if error and error not in excerpt:  # the builder's own diagnosis, e.g. no digest
            excerpt = f"{excerpt}\n{failures.scrub(error)}".strip()
        error_line = failures.scrub(line[:2000])[:500]
        with db.tx() as c:
            c.execute("UPDATE build SET stage = ?, status = 'failed', finished_at = ? "
                      "WHERE id = ?", (stage, ts, build_id))
            db.failures.record(build_id, failures.signature(stage, line, restricted), stage,
                               excerpt, restricted, error_line=error_line)
    with db.tx() as c:  # the job is over: its registry login stops working
        c.execute("UPDATE build SET registry_secret = NULL WHERE id = ? "
                  "AND status NOT IN ('queued', 'running')", (build_id,))
    return db.query_one("SELECT id, stage, status, image_digest, failure_id "
                        "FROM build WHERE id = ?", (build_id,))



def pending_deliveries(db: Database, builder: dict[str, Any]) -> list[dict[str, Any]]:
    """Newest published pipeline builds of this builder's platform not yet staged as
    squashfs. Restricted ones only for on-prem builders. Superseded builds are skipped."""
    rows = db.query(
        "SELECT b.id, p.name AS package, b.version, b.target, b.platform, b.image_digest, "
        "b.restricted, b.delivery_error FROM build b JOIN package p ON p.id = b.package_id "
        "WHERE b.origin = 'pipeline' AND b.status = 'published' AND b.delivered_at IS NULL "
        "AND b.platform = ? AND (b.restricted = 0 OR ? = 1) AND b.id = ("
        " SELECT MAX(b2.id) FROM build b2 WHERE b2.package_id = b.package_id"
        " AND b2.version = b.version AND b2.target = b.target AND b2.platform = b.platform"
        " AND b2.status = 'published') ORDER BY b.id",
        (builder["platform"], builder["onprem"]))
    for r in rows:
        r["project"] = "restricted" if r.pop("restricted") else "public"
    return rows


def record_delivery(db: Database, builder: dict[str, Any], build_id: int, ok: bool,
                    error: str = "") -> None:
    if not any(r["id"] == build_id for r in pending_deliveries(db, builder)):
        raise JobError(404, "No such pending delivery for this builder.")
    with db.tx() as c:
        if ok:
            c.execute("UPDATE build SET delivered_at = ?, delivery_error = '' WHERE id = ?",
                      (now(), build_id))
        else:  # stays pending: the next run retries it
            c.execute("UPDATE build SET delivery_error = ? WHERE id = ?",
                      (error[:500], build_id))
