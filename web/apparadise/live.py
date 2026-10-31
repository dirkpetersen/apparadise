"""Live build logs as server-sent events: GET /api/builds/{id}/events.

Who may watch: sysadmins (raw log) and the user who requested the build (scrubbed with
failures.scrub, like the public failure database). Anyone else, and anyone but sysadmins for
a restricted build, gets the same 404 as for a build that does not exist (Q26).

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
ACTIVE = ("queued", "running")


def _event(kind: str, data: str, event_id: int | None = None) -> bytes:
    out = [f"event: {kind}"]
    if event_id is not None:
        out.append(f"id: {event_id}")
    out += [f"data: {line}" for line in data.split("\n")]
    return ("\n".join(out) + "\n\n").encode()


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
                 clean: Callable[[str], str]) -> AsyncIterator[bytes]:
    app = request.app
    path = log_path(app.state.settings.data_dir, build_id)
    poll, limit = app.state.sse_poll_seconds, app.state.sse_max_seconds
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    if offset is None:
        offset = _start_offset(path, path.stat().st_size) if path.exists() else 0
    pending = b""
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
            pending += data
            offset += len(data)
            whole, sep, rest = pending.rpartition(b"\n")
            if sep:
                pending = rest
                text = whole.decode(errors="replace").replace("\r", "")
                yield _event("log", clean(text), offset - len(pending))
        finished = row["status"] not in ACTIVE
        if finished and not data:
            if pending:  # the last line had no newline
                text = pending.decode(errors="replace").replace("\r", "")
                yield _event("log", clean(text), offset)
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
    row: dict[str, Any] | None = await db.run(
        db.query_one, "SELECT requested_by, restricted FROM build WHERE id = ?", (build_id,))
    admin = bool(user and user.is_admin)
    own = bool(user and row and row["requested_by"] == user.username)
    if row is None or not (admin or (own and not row["restricted"])):
        raise HTTPException(404, "No such build.")
    offset = from_
    if last_event_id and last_event_id.isdigit():
        offset = int(last_event_id)
    clean = (lambda s: s) if admin else scrub
    return StreamingResponse(stream(request, db, build_id, offset, clean),
                             media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no"})
