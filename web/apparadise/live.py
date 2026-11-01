"""Live build logs as server-sent events: GET /api/builds/{id}/events.

Who may watch (Q26, owner: "everyone can see it"): anyone, signed in or not, gets the log
scrubbed with failures.scrub, like the public failure database. Sysadmins get the raw log.
A restricted build (or a non-public package) is sysadmin-only; anyone else gets the same 404
as for a build that does not exist.

The stream sends only complete lines, so scrubbing never sees half a token. Each `log`
event carries one or more lines and an `id` that is the byte offset after them, so a
reconnecting EventSource resumes exactly where it stopped (Last-Event-ID). A `status`
event is sent whenever the build's status or stage changes, and `end` once the build has
finished and the log is drained. A stream lasts at most `sse_max_seconds`; the browser then
reconnects by itself.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .auth import current_user
from .coordinator import log_path
from .db import Database, get_db
from .failures import scrub

router = APIRouter(tags=["builds"])  # mounted under /api/ in main.create_app
INITIAL_TAIL = 64 * 1024     # a new viewer starts with the last 64 KiB
READ_LIMIT = 256 * 1024      # bytes read per tick
LINE_LIMIT = 16 * 1024       # a "line" longer than this (no newline at all) is cut and sent
ACTIVE = ("queued", "running")


def _event(kind: str, data: str, event_id: int | None = None) -> bytes:
    out = [f"event: {kind}"]
    if event_id is not None:
        out.append(f"id: {event_id}")
    out += [f"data: {line}" for line in data.split("\n")]
    return ("\n".join(out) + "\n\n").encode()


def _line_start(path, offset: int) -> tuple[int, bool]:
    """-> (offset, aligned). The start of the line that contains byte `offset`, if it lies
    within LINE_LIMIT bytes before it. A client-chosen offset could otherwise begin
    mid-line, so scrubbing would see half a token (`token=s3cr3t` read from after the `=`).
    When no line start is that close, `aligned` is False and the stream must drop input up
    to the next line break."""
    if offset <= 0 or not path.exists():
        return 0, True
    lo = max(0, offset - LINE_LIMIT)
    with path.open("rb") as fh:
        fh.seek(lo)
        before = fh.read(offset - lo).replace(b"\r", b"\n")
    nl = before.rfind(b"\n")
    if nl >= 0:
        return lo + nl + 1, True
    return (0, True) if lo == 0 else (offset, False)


def _start_offset(path, size: int) -> int:
    """The beginning of the first whole line in the last INITIAL_TAIL bytes."""
    if size <= INITIAL_TAIL:
        return 0
    with path.open("rb") as fh:
        fh.seek(size - INITIAL_TAIL)
        chunk = fh.read(INITIAL_TAIL)
    nl = chunk.find(b"\n")
    return size - INITIAL_TAIL + (nl + 1 if nl >= 0 else 0)


async def stream(request: Request, db: Database, build_id: int, offset: int | None,
                 clean: Callable[[str], str], aligned: bool = True) -> AsyncIterator[bytes]:
    app = request.app
    path = log_path(app.state.settings.data_dir, build_id)
    poll, limit = app.state.sse_poll_seconds, app.state.sse_max_seconds
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    if offset is None:
        offset = _start_offset(path, path.stat().st_size) if path.exists() else 0
    pending = b""
    # True while the input is mid-line (a cut line, or a resume point with no line start
    # in reach): drop bytes until the next line break, so scrubbing only sees whole lines.
    skipping = not aligned
    last: tuple[str, str | None] | None = None
    yield b"retry: 3000\n\n"
    while True:
        row = await db.run(db.query_one, "SELECT status, stage FROM build WHERE id = ?",
                           (build_id,))
        if row is None:
            return
        state = (row["status"], row["stage"])
        if state != last:
            last = state
            yield _event("status", json.dumps({"status": state[0], "stage": state[1] or ""}))
        data = b""
        if path.exists():
            size = path.stat().st_size
            if size > offset:
                with path.open("rb") as fh:
                    fh.seek(offset)
                    data = fh.read(min(size - offset, READ_LIMIT))
        if data:
            # A bare \r (progress bars) ends a line too, so they never pile up unsent.
            chunk = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            offset += len(data)
            if skipping:
                nl = chunk.find(b"\n")
                chunk = chunk[nl + 1:] if nl >= 0 else b""
                skipping = nl < 0
            pending += chunk
            whole, sep, rest = pending.rpartition(b"\n")
            if sep:
                pending = rest
            elif len(pending) > LINE_LIMIT:  # no line end in sight: send the start, skip on
                whole, pending, skipping = pending[:LINE_LIMIT] + b" [cut]", b"", True
            if whole:
                text = whole.decode(errors="replace")
                text = "\n".join(ln if len(ln) <= LINE_LIMIT else ln[:LINE_LIMIT] + " [cut]"
                                  for ln in text.split("\n"))
                # Scrubbing up to 256 KiB is CPU work: keep it off the event loop.
                yield _event("log", await asyncio.to_thread(clean, text),
                             offset - len(pending))
        finished = row["status"] not in ACTIVE
        if finished and not data:
            if pending and not skipping:  # the last line had no newline
                text = pending.decode(errors="replace")[:LINE_LIMIT]
                yield _event("log", await asyncio.to_thread(clean, text), offset)
            yield _event("end", row["status"])
            return
        if data and len(data) == READ_LIMIT:
            continue  # catching up: read on without waiting
        if loop.time() >= deadline or await request.is_disconnected():
            return
        await asyncio.sleep(poll)


@router.get("/builds/{build_id}/events")
async def build_events(
    build_id: int, request: Request, db: Annotated[Database, Depends(get_db)],
    from_: Annotated[int | None, Query(alias="from", ge=0)] = None,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    user = current_user(request)
    # Visibility is read now, from the package, not copied when the build was queued: a
    # package made restricted later hides its old builds too.
    row: dict[str, Any] | None = await db.run(
        db.query_one,
        "SELECT b.restricted, p.visibility FROM build b JOIN package p ON p.id = b.package_id "
        "WHERE b.id = ?", (build_id,))
    admin = bool(user and user.is_admin)
    hidden = bool(row and (row["restricted"] or row["visibility"] != "public"))
    if row is None or (hidden and not admin):
        raise HTTPException(404, "No such build.")
    offset = from_
    if last_event_id and last_event_id.isdigit():
        offset = int(last_event_id)
    path = log_path(request.app.state.settings.data_dir, build_id)
    aligned = True
    if offset is not None and not admin:
        offset, aligned = await asyncio.to_thread(_line_start, path, offset)
    clean = (lambda s: s) if admin else scrub
    return StreamingResponse(stream(request, db, build_id, offset, clean, aligned),
                             media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no"})
