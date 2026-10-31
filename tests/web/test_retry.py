"""Nightly retries: recipe changed upstream, or an old failure; capped; one platform."""

from datetime import UTC, datetime, timedelta

import pytest

from apparadise import coordinator
from apparadise.config import Settings
from apparadise.db import Database
from apparadise.index import _item
from apparadise.retry import due, main, retry

from .conftest import REQUIRED

NOW = datetime(2026, 11, 20, tzinfo=UTC)


@pytest.fixture
def env(tmp_path):
    s = Settings(_env_file=None, data_dir=str(tmp_path), catalog_dir=str(tmp_path), **REQUIRED)
    db = Database(":memory:")
    db.index.replace_source("spack", [_item("spack", "gromacs", ["2026.3"], True, [], False)])
    pid = db.packages.upsert({"name": "gromacs", "title": "GROMACS", "origin": "request"})
    yield db, s, pid
    db.close()


def fail(db, pid, *, when, platform="linux/amd64", target="rtx", stage="build"):
    bid = coordinator.enqueue(db, pid, "2026.3", "spack", target, platform, "",
                              requested_by="alice", restricted=False)
    with db.tx() as c:
        c.execute("UPDATE build SET status = 'failed', stage = ?, finished_at = ? WHERE id = ?",
                  (stage, when, bid))
    return bid


def set_changed(db, when):
    with db.tx() as c:
        c.execute("UPDATE index_entry SET changed_at = ?", (when,))


def test_recent_failure_with_unchanged_recipe_waits(env):
    db, s, pid = env
    set_changed(db, "2026-11-01T00:00:00Z")
    fail(db, pid, when="2026-11-19T00:00:00Z")
    assert due(db, NOW) == []


def test_recipe_changed_upstream_triggers_retry_as_system(env):
    db, s, pid = env
    bid = fail(db, pid, when="2026-11-18T00:00:00Z")
    set_changed(db, "2026-11-19T03:15:00Z")
    out = retry(db, s, NOW)
    assert out["queued"][0]["failed"] == bid and out["queued"][0]["reason"] == \
        "recipe changed upstream"
    new = db.query_one("SELECT * FROM build WHERE id = ?", (out["queued"][0]["retry"],))
    assert new["status"] == "queued" and new["requested_by"] == "system"
    assert db.audit.query(action="build.retry")[0]["actor"] == "system"
    assert retry(db, s, NOW)["queued"] == []  # the retry is now the newest build: not due


def test_old_failure_is_retried_and_attempts_are_capped(env):
    db, s, pid = env
    set_changed(db, "2026-01-01T00:00:00Z")
    old = _iso_days_ago(20)
    fail(db, pid, when=old)
    assert [d["reason"] for d in due(db, NOW)] == ["failed over 14 days ago"]
    fail(db, pid, when=old)
    fail(db, pid, when=old)  # three failed attempts: the cap
    assert due(db, NOW) == []
    db.settings.set("retry.max_attempts", 4)
    assert len(due(db, NOW)) == 1


def test_only_the_failed_platform_of_a_generic_release_is_retried(env):
    db, s, pid = env
    ok = coordinator.enqueue(db, pid, "2026.3", "spack", "generic", "linux/amd64", "",
                             requested_by="alice", restricted=False)
    with db.tx() as c:
        c.execute("UPDATE build SET status = 'published' WHERE id = ?", (ok,))
    fail(db, pid, when=_iso_days_ago(30), target="generic", platform="linux/arm64")
    out = retry(db, s, NOW)
    new = db.query_one("SELECT platform FROM build WHERE id = ?", (out["queued"][0]["retry"],))
    assert new["platform"] == "linux/arm64"
    assert db.query_one("SELECT COUNT(*) AS n FROM build WHERE status = 'queued'")["n"] == 1


def test_no_recipe_and_superseded_failures_are_not_retried(env):
    db, s, pid = env
    fail(db, pid, when=_iso_days_ago(30), stage="no-recipe")
    assert due(db, NOW) == []
    fail(db, pid, when=_iso_days_ago(30), target="vr", platform="linux/arm64")
    later = coordinator.enqueue(db, pid, "2026.3", "spack", "vr", "linux/arm64", "",
                                requested_by="bob", restricted=False)
    assert later and due(db, NOW) == []  # a newer build of that release exists


def test_changed_at_moves_only_when_the_recipe_changes(env):
    db, s, pid = env
    before = db.index.lookup(["gromacs"])[0]["changed_at"]
    db.index.replace_source("spack", [_item("spack", "gromacs", ["2026.3"], True, [], False)])
    assert db.index.lookup(["gromacs"])[0]["changed_at"] == before
    db.index.replace_source("spack", [_item("spack", "gromacs", ["2027.0", "2026.3"], True,
                                            [], False)])
    assert db.index.lookup(["gromacs"])[0]["changed_at"] > before


def test_cli_refuses_a_missing_database(tmp_path):
    with pytest.raises(SystemExit):
        main(["--db", str(tmp_path / "nope.sqlite3")])
    assert not (tmp_path / "nope.sqlite3").exists()


def _iso_days_ago(days):
    return (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")
