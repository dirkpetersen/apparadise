"""Failure signatures, log scrubbing, and the public failure database API.

A signature groups builds that failed the same way. It hashes the first error line after
normalising versions, paths, hex ids and numbers, so the gprofng failure that hit three
binutils easyconfigs in the EasyBuild session (FAILURES.md #13, #17, #19) is one group.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .auth import current_user
from .db import STAGES, Database, get_db

# The first line that looks like an error, in priority order.
ERROR_LINE_RES = [
    # Spack's own exception names the cause; its "==> Error:" line after it is a generic summary
    re.compile(r"^.*\bspack\.error\.\w+: .*$", re.M),
    re.compile(r"^.*\berror\b[: ].*$", re.I | re.M),
    re.compile(r"^.*\b(fatal|failed|cannot|undefined reference)\b.*$", re.I | re.M),
]
_NORMALISE = [
    (re.compile(r"(/[\w.+-]+)+/?"), "<path>"),
    (re.compile(r"\b[0-9a-f]{7,64}\b", re.I), "<hex>"),
    (re.compile(r"\b\d+(\.\d+)+([-_.]?[a-z][\w.]*)?\b", re.I), "<ver>"),
    (re.compile(r"\b\d+\b"), "<n>"),
    (re.compile(r"\s+"), " "),
]
_SCRUB = [
    # tokens and credentials first, before paths eat parts of them
    (re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"), r"\1[redacted]"),
    (re.compile(r"(?i)\b(token|password|passwd|secret|api[_-]?key)=\S+"), r"\1=[redacted]"),
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1[redacted]@"),
    (re.compile(r"/(home|users|scratch|storage|gpfs|fh|fast|tmp)/[^\s:'\"]*"), "/<path>"),
    # internal host names are handled by _hosts() below (linear time, unlike a regex with
    # nested repetition, which a long dotted token in a log made quadratic)
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
]
EXCERPT_LINES = 30


def first_error_line(log: str) -> str:
    for rx in ERROR_LINE_RES:
        if m := rx.search(log):
            return m.group(0).strip()
    lines = [ln for ln in log.strip().splitlines() if ln.strip()]
    return lines[-1].strip() if lines else ""


def normalise_error(line: str) -> str:
    out = line.lower()
    for rx, repl in _NORMALISE:
        out = rx.sub(repl, out)
    return out.strip()


def signature(stage: str, error_line: str, restricted: bool = False) -> str:
    key = f"{stage}\n{normalise_error(error_line)}\n{'restricted' if restricted else 'public'}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


# A dotted token, tried only at a token start and matched possessively (never re-tried):
# linear in the input.
_DOTTED = re.compile(r"(?<![\w.-])[\w-]++(?:\.[\w-]++)+")
_INTERNAL = {"fhcrc", "fredhutch", "osu", "internal", "local", "lan"}


def _hosts(m: re.Match) -> str:
    """The whole host name, node part included: gpu-n12.cluster.fredhutch.org -> <host>."""
    labels = m.group(0).lower().split(".")
    return "<host>" if _INTERNAL & set(labels[1:]) else m.group(0)


def scrub(text: str) -> str:
    for rx, repl in _SCRUB:
        text = rx.sub(repl, text)
    return _DOTTED.sub(_hosts, text)


def excerpt(log: str, around: str | None = None, lines: int = EXCERPT_LINES) -> str:
    """Up to `lines` lines around the error line (or the tail), scrubbed."""
    all_lines = log.splitlines()
    idx = len(all_lines)
    if around:
        for i, ln in enumerate(all_lines):
            if around in ln:
                idx = i + 1
                break
    start = max(0, idx - lines + 5)
    return scrub("\n".join(all_lines[start:start + lines]))


# --- the public failure database: /api/failures --------------------------------------------
# Every failed build joins a group by signature. Groups are public, so anyone can see what
# breaks and why, except restricted ones: only sysadmins see those, and anyone else gets the
# same 404 as for a group that does not exist. Excerpts and error lines were scrubbed when
# they were recorded (coordinator.report); requesters' usernames are never shown here.

router = APIRouter(tags=["failures"])  # mounted under /api/ in main.create_app


def _viewer(request: Request) -> tuple[str | None, bool]:
    user = current_user(request)
    return (user.username if user else None), bool(user and user.is_admin)


def _summary(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": r["id"], "stage": r["stage"], "error": r["error_line"],
            "builds": r["builds"], "votes": r["votes"], "packages": r.get("packages", []),
            "fixUrl": r["fix_url"] or None, "restricted": bool(r["restricted"]),
            "firstSeen": r["created_at"], "lastSeen": r["updated_at"]}


@router.get("/failures")
async def list_failures(
    request: Request, db: Annotated[Database, Depends(get_db)],
    q: Annotated[str, Query(max_length=100)] = "",
    stage: Annotated[str, Query(max_length=20)] = "",
    package: Annotated[str, Query(max_length=64)] = "",
) -> dict[str, Any]:
    if stage and stage not in STAGES:
        raise HTTPException(422, "Unknown stage.")
    username, admin = _viewer(request)
    rows = await db.run(db.failures.list, include_restricted=admin, q=q.strip(), stage=stage,
                        package=package)
    voted = await db.run(db.votes.mine, username, "failure") if username else set()
    return {"failures": [{**_summary(r), "voted": r["id"] in voted} for r in rows],
            "stages": list(STAGES)}


@router.get("/failures/{failure_id}")
async def get_failure(failure_id: int, request: Request,
                      db: Annotated[Database, Depends(get_db)]) -> dict[str, Any]:
    username, admin = _viewer(request)
    row = await db.run(db.failures.get, failure_id, include_restricted=admin)
    if row is None:
        raise HTTPException(404, "No such failure.")
    builds = await db.run(db.failures.builds, failure_id)
    voted = await db.run(db.votes.mine, username, "failure") if username else set()
    attempts = json.loads(row["repair_attempts"] or "[]")
    return {**_summary({**row, "packages": sorted({b["package"] for b in builds})}),
            "voted": failure_id in voted, "excerpt": row["log_excerpt"],
            "repairAttempts": attempts,
            "builds": [{"id": b["id"], "package": b["package"], "version": b["version"],
                        "source": b["source"], "target": b["target"], "platform": b["platform"],
                        "stage": b["stage"], "status": b["status"],
                        "finished": b["finished_at"] or b["created_at"]} for b in builds]}
