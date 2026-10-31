import sqlite3

import pytest

from apparadise.db import MIGRATIONS, Database, SchemaTooNew, scrub


def test_migrations_apply_and_tables_are_strict():
    db = Database(":memory:")
    assert db.version == len(MIGRATIONS)
    tables = {r["name"]: r["sql"] for r in db.query(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table'")}
    for t in ("user", "package", "index_entry", "request", "build", "failure", "vote",
              "audit_log", "settings"):
        assert t in tables
        assert tables[t].rstrip().endswith("STRICT"), t
    assert db.query_one("PRAGMA foreign_keys")["foreign_keys"] == 1


def test_refuses_newer_schema(tmp_path):
    path = tmp_path / "d" / "x.sqlite3"
    Database(path).close()
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
    conn.close()
    with pytest.raises(SchemaTooNew):
        Database(path)


def test_files_are_private(tmp_path):
    path = tmp_path / "data" / "x.sqlite3"
    Database(path).close()
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_check_constraints_reject_unknown_values():
    db = Database(":memory:")
    pid = db.packages.upsert({"name": "x", "title": "X"})
    with pytest.raises(sqlite3.IntegrityError):
        db.builds.replace_catalog_builds(pid, [
            {"version": "1", "source": "spack", "target": "mars", "status": "published"}])
    with pytest.raises(sqlite3.IntegrityError):
        db.builds.replace_catalog_builds(pid, [
            {"version": "1", "source": "conan", "target": "rtx", "status": "published"}])
    with pytest.raises(sqlite3.IntegrityError):
        db.builds.replace_catalog_builds(pid, [
            {"version": "1", "source": "spack", "target": "rtx", "status": "failed",
             "stage": "compile"}])


def test_audit_scrubs_secrets():
    db = Database(":memory:")
    db.audit.add("alice", "login.success", "user", "alice",
                 {"password": "x", "nested": {"api_key": "y"}, "auth": "Bearer abc", "ok": 1})
    row = db.audit.query()[0]
    assert row["details"] == {"password": "[redacted]", "nested": {"api_key": "[redacted]"},
                              "auth": "[redacted]", "ok": 1}
    assert scrub(["a", {"token": 1}]) == ["a", {"token": "[redacted]"}]


def test_user_seen_follows_ad_role():
    db = Database(":memory:")
    db.users.seen("dana", "Dana", "d@x", True)
    assert db.users.get("dana")["role"] == "sysadmin"
    db.users.seen("dana", "Dana", "d@x", False)
    assert db.users.get("dana")["role"] == "user"
