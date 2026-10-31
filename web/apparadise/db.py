"""SQLite storage: the platform data model, runtime settings and the audit log.

Concurrency: the app is a single process, so there is one connection
(`check_same_thread=False`) guarded by an RLock. Every statement is short.
Async handlers call `await db.run(fn, ...)`, which runs a sync repository
call in a worker thread so the event loop never waits on disk or on the lock.

Migrations: `MIGRATIONS` is an ordered list of SQL scripts; the schema version
is `PRAGMA user_version` (= number of applied scripts). Append a new script,
never edit an applied one.

PostgreSQL portability (CLAUDE.md "Database"): STRICT tables, foreign keys on,
lists stored as JSON text (no arrays/JSONB), job claims via UPDATE ... RETURNING,
no SKIP LOCKED, logs as files.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request

log = logging.getLogger(__name__)
DB_FILENAME = "apparadise.sqlite3"

TARGETS = ("vr", "rtx", "generic")
SOURCES = ("spack", "easybuild", "pixi")
STAGES = ("no-recipe", "concretize", "build", "package", "test", "publish")
BUILD_STATUSES = ("planned", "queued", "running", "published", "failed", "cancelled")
REQUEST_STATUSES = ("open", "building", "built", "failed", "declined")
VISIBILITIES = ("public", "restricted")


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


MIGRATIONS: list[str] = [
    # 1: initial schema (architecture doc "Data model" plus settings and audit_log)
    f"""
    CREATE TABLE settings (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,              -- JSON
        updated_at TEXT NOT NULL,
        updated_by TEXT
    ) STRICT;

    CREATE TABLE user (
        username      TEXT PRIMARY KEY,        -- AD sAMAccountName
        display_name  TEXT NOT NULL DEFAULT '',
        email         TEXT NOT NULL DEFAULT '',
        role          TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'sysadmin')),
        build_quota   INTEGER,                 -- NULL: the policy default applies
        builds_used   INTEGER NOT NULL DEFAULT 0,
        period_start  TEXT,
        first_seen    TEXT NOT NULL,
        last_seen     TEXT NOT NULL
    ) STRICT;

    CREATE TABLE package (
        id           INTEGER PRIMARY KEY,
        name         TEXT NOT NULL UNIQUE,     -- lowercase catalog name, e.g. gromacs
        title        TEXT NOT NULL,            -- display name, e.g. GROMACS
        description  TEXT NOT NULL DEFAULT '',
        homepage     TEXT NOT NULL DEFAULT '',
        visibility   TEXT NOT NULL DEFAULT 'public' CHECK (visibility IN {_in(VISIBILITIES)}),
        aliases      TEXT NOT NULL DEFAULT '{{}}',  -- JSON {{source: upstream name}}
        executables  TEXT NOT NULL DEFAULT '[]',    -- JSON list, exposed as module wrappers
        tags         TEXT NOT NULL DEFAULT '[]',    -- JSON list
        origin       TEXT NOT NULL DEFAULT 'catalog' CHECK (origin IN ('catalog', 'request')),
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    ) STRICT;

    CREATE TABLE index_entry (
        id            INTEGER PRIMARY KEY,
        package_id    INTEGER REFERENCES package (id) ON DELETE SET NULL,
        source        TEXT NOT NULL CHECK (source IN {_in(SOURCES)}),
        upstream_name TEXT NOT NULL,
        versions      TEXT NOT NULL DEFAULT '[]',   -- JSON list
        has_cuda      INTEGER NOT NULL DEFAULT 0,
        arches        TEXT NOT NULL DEFAULT '[]',   -- JSON list: linux-64, linux-aarch64
        refreshed_at  TEXT NOT NULL,
        UNIQUE (source, upstream_name)
    ) STRICT;

    CREATE TABLE request (
        id            INTEGER PRIMARY KEY,
        package_id    INTEGER NOT NULL REFERENCES package (id),
        version       TEXT NOT NULL DEFAULT '',
        targets       TEXT NOT NULL DEFAULT '[]',   -- JSON list of targets
        requested_by  TEXT NOT NULL REFERENCES user (username),
        restricted    INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN {_in(REQUEST_STATUSES)})
    ) STRICT;

    CREATE TABLE failure (
        id               INTEGER PRIMARY KEY,
        signature        TEXT NOT NULL UNIQUE,      -- hash of the normalised first error line
        stage            TEXT NOT NULL CHECK (stage IN {_in(STAGES)}),
        first_build_id   INTEGER,
        last_build_id    INTEGER,
        log_excerpt      TEXT NOT NULL DEFAULT '',  -- scrubbed
        repair_attempts  TEXT NOT NULL DEFAULT '[]',
        fix_url          TEXT NOT NULL DEFAULT '',
        restricted       INTEGER NOT NULL DEFAULT 0,
        created_at       TEXT NOT NULL,
        updated_at       TEXT NOT NULL
    ) STRICT;

    CREATE TABLE build (
        id            INTEGER PRIMARY KEY,
        package_id    INTEGER NOT NULL REFERENCES package (id) ON DELETE CASCADE,
        version       TEXT NOT NULL,
        source        TEXT NOT NULL CHECK (source IN {_in(SOURCES)}),
        target        TEXT NOT NULL CHECK (target IN {_in(TARGETS)}),
        origin        TEXT NOT NULL DEFAULT 'pipeline' CHECK (origin IN ('catalog', 'pipeline')),
        request_id    INTEGER REFERENCES request (id),
        requested_by  TEXT NOT NULL DEFAULT 'system',
        recipe        TEXT NOT NULL DEFAULT '',     -- the recipe text or its repo path
        stage         TEXT CHECK (stage IS NULL OR stage IN {_in(STAGES)}),
        status        TEXT NOT NULL CHECK (status IN {_in(BUILD_STATUSES)}),
        image_ref     TEXT NOT NULL DEFAULT '',     -- project/name:version-target
        image_digest  TEXT NOT NULL DEFAULT '',
        test_result   TEXT NOT NULL DEFAULT '',
        failure_id    INTEGER REFERENCES failure (id),
        log_path      TEXT NOT NULL DEFAULT '',
        created_at    TEXT NOT NULL,
        started_at    TEXT,
        finished_at   TEXT
    ) STRICT;
    CREATE INDEX build_package ON build (package_id, version, target);
    CREATE INDEX build_queue ON build (status, created_at);

    CREATE TABLE vote (
        username      TEXT NOT NULL REFERENCES user (username),
        subject_type  TEXT NOT NULL CHECK (subject_type IN ('package', 'request', 'failure')),
        subject_id    INTEGER NOT NULL,
        created_at    TEXT NOT NULL,
        PRIMARY KEY (username, subject_type, subject_id)
    ) STRICT;
    CREATE INDEX vote_subject ON vote (subject_type, subject_id);

    CREATE TABLE audit_log (
        id           INTEGER PRIMARY KEY,
        ts           TEXT NOT NULL,
        actor        TEXT NOT NULL,              -- AD username, or 'system'
        action       TEXT NOT NULL,              -- area.verb[.detail]
        target_type  TEXT NOT NULL,
        target       TEXT NOT NULL,
        details      TEXT NOT NULL DEFAULT '{{}}'  -- JSON, scrubbed
    ) STRICT;
    CREATE INDEX audit_ts ON audit_log (ts);
    """,
]

_SECRET_KEY = re.compile(
    r"secret|passw|token|credential|private_?key|authorization|api_?key|signature", re.I
)
_SECRET_VALUE = re.compile(r"^\s*Bearer\s", re.I)
REDACTED = "[redacted]"


class SchemaTooNew(RuntimeError):
    """The database was written by a newer version of apparadise."""


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def scrub(value: Any) -> Any:
    """Copy of `value` with the value of every secret-looking key redacted."""
    if isinstance(value, dict):
        return {
            str(k): REDACTED if _SECRET_KEY.search(str(k)) else scrub(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [scrub(v) for v in value]
    if isinstance(value, str) and _SECRET_VALUE.search(value):
        return REDACTED
    return value


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


class Database:
    """One SQLite connection plus a repository per table."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            p = Path(self.path)
            if not p.parent.exists():
                p.parent.mkdir(parents=True, mode=0o700)
                p.parent.chmod(0o700)  # mkdir's mode is masked by umask
            elif p.parent.stat().st_mode & 0o077:
                log.warning(
                    "%s is accessible to group/other (mode %o); the database "
                    "files themselves stay 0600",
                    p.parent, p.parent.stat().st_mode & 0o777,
                )
            os.close(os.open(p, os.O_CREAT | os.O_RDWR, 0o600))
            p.chmod(0o600)
        self._lock = threading.RLock()
        # isolation_level=None: autocommit; transactions are explicit (tx()).
        self._conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
        self.migrate()
        self.settings = SettingsRepo(self)
        self.users = Users(self)
        self.packages = Packages(self)
        self.builds = Builds(self)
        self.audit = AuditLog(self)

    # -- plumbing -----------------------------------------------------------

    @property
    def version(self) -> int:
        return self._conn.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self) -> int:
        """Apply pending migrations, each atomically. Returns the new version."""
        with self._lock:
            current = self.version
            if current > len(MIGRATIONS):
                raise SchemaTooNew(
                    f"{self.path} is schema version {current}, but this apparadise "
                    f"only knows up to {len(MIGRATIONS)}; refusing to start"
                )
            for n in range(current + 1, len(MIGRATIONS) + 1):
                script = (
                    f"BEGIN IMMEDIATE;\n{MIGRATIONS[n - 1]}\n"
                    f"PRAGMA user_version = {n};\nCOMMIT;"
                )
                try:
                    self._conn.executescript(script)
                except Exception:
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK")
                    raise
            return self.version

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Lock plus a write transaction; rolls back on error."""
        with self._lock:
            if self._conn.in_transaction:  # nested: join the outer transaction
                yield self._conn
                return
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self._lock:
            return _rows(self._conn.execute(sql, params))

    def query_one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    async def run(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        """Run a sync repository call in a worker thread: `await db.run(db.users.get, name)`."""
        return await asyncio.to_thread(fn, *args, **kwargs)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class _Repo:
    def __init__(self, db: Database):
        self.db = db


class SettingsRepo(_Repo):
    """Admin-page runtime policy as JSON values. Callers supply the defaults."""

    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        return default if row is None else json.loads(row["value"])

    def set(self, key: str, value: Any, by: str | None = None) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (key, json.dumps(value), now(), by),
            )


class Users(_Repo):
    def seen(self, username: str, display_name: str, email: str, sysadmin: bool) -> None:
        """Record a successful login. The role follows AD group membership at each login."""
        ts = now()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO user (username, display_name, email, role, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (username) DO UPDATE SET "
                "display_name = excluded.display_name, email = excluded.email, "
                "role = excluded.role, last_seen = excluded.last_seen",
                (username, display_name, email, "sysadmin" if sysadmin else "user", ts, ts),
            )

    def get(self, username: str) -> dict[str, Any] | None:
        return self.db.query_one("SELECT * FROM user WHERE username = ?", (username,))


def _package_row(r: dict[str, Any]) -> dict[str, Any]:
    for k in ("aliases", "executables", "tags"):
        r[k] = json.loads(r[k])
    return r


class Packages(_Repo):
    def upsert(self, pkg: dict[str, Any]) -> int:
        """Insert or update a package by name. Returns its id."""
        ts = now()
        with self.db.tx() as c:
            row = c.execute(
                "INSERT INTO package (name, title, description, homepage, visibility, aliases, "
                "executables, tags, origin, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (name) DO UPDATE SET "
                "title = excluded.title, description = excluded.description, "
                "homepage = excluded.homepage, visibility = excluded.visibility, "
                "aliases = excluded.aliases, executables = excluded.executables, "
                "tags = excluded.tags, updated_at = excluded.updated_at RETURNING id",
                (
                    pkg["name"], pkg["title"], pkg.get("description", ""),
                    pkg.get("homepage", ""), pkg.get("visibility", "public"),
                    json.dumps(pkg.get("aliases", {}), sort_keys=True),
                    json.dumps(pkg.get("executables", [])), json.dumps(pkg.get("tags", [])),
                    pkg.get("origin", "catalog"), ts, ts,
                ),
            ).fetchone()
            return row[0]

    def get(self, name: str, *, include_restricted: bool = False) -> dict[str, Any] | None:
        sql = "SELECT * FROM package WHERE name = ?"
        if not include_restricted:
            sql += " AND visibility = 'public'"
        row = self.db.query_one(sql, (name,))
        return _package_row(row) if row else None

    def list(self, *, include_restricted: bool = False, q: str = "") -> list[dict[str, Any]]:
        conds, params = [], []
        if not include_restricted:
            conds.append("p.visibility = 'public'")
        if q:
            esc = q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{esc}%"
            conds.append(
                "(lower(p.name) LIKE ? ESCAPE '\\' OR lower(p.title) LIKE ? ESCAPE '\\' "
                "OR lower(p.description) LIKE ? ESCAPE '\\' OR lower(p.tags) LIKE ? ESCAPE '\\' "
                "OR lower(p.aliases) LIKE ? ESCAPE '\\')"
            )
            params += [like] * 5
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = self.db.query(
            "SELECT p.*, "
            "(SELECT COUNT(*) FROM vote v WHERE v.subject_type = 'package' "
            " AND v.subject_id = p.id) AS votes "
            f"FROM package p {where} ORDER BY p.name",
            tuple(params),
        )
        return [_package_row(r) for r in rows]

    def names(self, origin: str) -> list[str]:
        return [r["name"] for r in self.db.query(
            "SELECT name FROM package WHERE origin = ? ORDER BY name", (origin,))]

    def delete(self, name: str) -> bool:
        with self.db.tx() as c:
            return c.execute("DELETE FROM package WHERE name = ?", (name,)).rowcount > 0


class Builds(_Repo):
    def replace_catalog_builds(self, package_id: int, builds: list[dict[str, Any]]) -> None:
        """Replace the hand-maintained (origin=catalog) build rows of one package."""
        ts = now()
        with self.db.tx() as c:
            c.execute(
                "DELETE FROM build WHERE package_id = ? AND origin = 'catalog'", (package_id,)
            )
            for b in builds:
                c.execute(
                    "INSERT INTO build (package_id, version, source, target, origin, recipe, "
                    "stage, status, image_ref, image_digest, test_result, created_at, finished_at) "
                    "VALUES (?, ?, ?, ?, 'catalog', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        package_id, b["version"], b["source"], b["target"], b.get("recipe", ""),
                        b.get("stage"), b["status"], b.get("image_ref", ""),
                        b.get("image_digest", ""), b.get("test_result", ""), ts,
                        b.get("finished_at"),
                    ),
                )

    def for_package(self, package_id: int) -> list[dict[str, Any]]:
        """The latest build row per (version, target)."""
        return self.db.query(
            "SELECT b.* FROM build b WHERE b.package_id = ? AND b.id = ("
            " SELECT MAX(b2.id) FROM build b2 WHERE b2.package_id = b.package_id "
            " AND b2.version = b.version AND b2.target = b.target) "
            "ORDER BY b.version DESC, b.target",
            (package_id,),
        )


class AuditLog(_Repo):
    """Append-only: only `add` and `query` exist. Never put secrets in `details`;
    keys that look secret are redacted as a safety net."""

    def add(self, actor: str, action: str, target_type: str, target: str,
            details: dict[str, Any] | None = None) -> int:
        payload = json.dumps(scrub(details or {}), default=str)
        with self.db.tx() as c:
            cur = c.execute(
                "INSERT INTO audit_log (ts, actor, action, target_type, target, details) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now(), actor, action, target_type, target, payload),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def query(self, *, actor: str | None = None, action: str | None = None,
              limit: int = 100) -> list[dict[str, Any]]:
        """Newest first."""
        conds, params = [], []
        for col, val in (("actor", actor), ("action", action)):
            if val is not None:
                conds.append(f"{col} = ?")
                params.append(val)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        rows = self.db.query(
            f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT ?", (*params, limit)
        )
        for r in rows:
            r["details"] = json.loads(r["details"])
        return rows


def get_db(request: Request) -> Database:
    """FastAPI dependency: `db: Database = Depends(get_db)`."""
    return request.app.state.db
