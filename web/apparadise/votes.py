"""Upvotes: one per signed-in user per package or failure group.

Votes tell the build team what people want (packages) and what hurts most (failures). A
vote on something the voter cannot see is the same 404 as one on something that does not
exist. Subjects: packages (key = name), failure groups and requests (key = id).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from .auth import SessionUser, require_user
from .db import Database, get_db

SUBJECTS = ("package", "failure", "request")
router = APIRouter(tags=["votes"])  # mounted under /api/ in main.create_app


def _visible(db: Database, subject: str, key: str, admin: bool) -> int | None:
    """-> the subject's id, or None when it does not exist for this user."""
    if subject == "package":
        p = db.packages.get(key, include_restricted=admin)
        return p["id"] if p else None
    if not key.isdigit():
        return None
    if subject == "request":
        r = db.requests.get(int(key))
        return r["id"] if r and (admin or not r["restricted"]) else None
    f = db.failures.get(int(key), include_restricted=admin)
    return f["id"] if f else None


async def _vote(db: Database, user: SessionUser, subject: str, key: str, on: bool):
    if subject not in SUBJECTS:
        raise HTTPException(404, "Nothing to vote on.")
    sid = await db.run(_visible, db, subject, key, user.is_admin)
    if sid is None:
        raise HTTPException(404, "Nothing to vote on.")
    changed = await db.run(db.votes.set, user.username, subject, sid, on)
    if changed:
        await db.run(db.audit.add, user.username, "vote.add" if on else "vote.remove",
                     subject, key, {})
    return {"votes": await db.run(db.votes.count, subject, sid), "voted": on}


@router.put("/votes/{subject}/{key}")
async def vote(subject: str, key: str, user: Annotated[SessionUser, Depends(require_user)],
               db: Annotated[Database, Depends(get_db)]) -> dict[str, Any]:
    """Idempotent: voting twice is one vote. `key` is a package name or a failure id."""
    return await _vote(db, user, subject, key, True)


@router.delete("/votes/{subject}/{key}")
async def unvote(subject: str, key: str, user: Annotated[SessionUser, Depends(require_user)],
                 db: Annotated[Database, Depends(get_db)]) -> dict[str, Any]:
    return await _vote(db, user, subject, key, False)
