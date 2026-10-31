"""Regression tests for the phase 3 code review (2026-10-31): one test per finding."""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from apparadise import coordinator
from apparadise.buildqueue import QueueError, queue_builds
from apparadise.catalog import merge_platforms
from apparadise.coordinator import log_path
from apparadise.failures import scrub
from apparadise.index import _item
from apparadise.retry import retry
from mock.demo import build_demo_app, demo_settings

H = {"X-Requested-With": "apparadise"}


@pytest.fixture
def env(tmp_path):
    settings = demo_settings(port=8000, data_dir=str(tmp_path))
    app = build_demo_app(settings)
    app.state.sse_poll_seconds = 0.01
    app.state.sse_max_seconds = 0.2
    logging.disable(logging.INFO)
    with TestClient(app) as c:
        c.app_db, c.settings = app.state.db, settings
        yield c
    logging.disable(logging.NOTSET)
    app.state.db.close()


def login(c, user):
    assert c.post("/api/login", json={"login": user, "password": "demo"},
                  headers=H).status_code == 200


def ask(c, **body):
    return c.post("/api/requests", json=body, headers=H)


def n_builds(db):
    return db.query_one("SELECT COUNT(*) AS n FROM build")["n"]


# 1: a restricted package requested under an alias
def test_restricted_alias_is_refused_like_an_unknown_name(env):
    c, db = env, env.app_db
    with db.tx() as x:
        x.execute("UPDATE package SET aliases = ? WHERE name = 'alphafold3'",
                  (json.dumps({"easybuild": "AlphaFold3", "spack": "py-alphafold"}),))
    db.index.replace_source("spack", [_item("spack", "py-alphafold", ["3.0.1"], True, [], True),
                                      _item("spack", "lammps", ["20250722"], True, [], False)])
    login(c, "alice")
    before = n_builds(db)
    a, b = ask(c, name="alphafold"), ask(c, name="nosuchthing")
    assert a.status_code == b.status_code == 422 and a.json() == b.json()
    assert n_builds(db) == before and db.packages.get("alphafold") is None


def test_alias_of_a_public_package_reuses_it(env):
    c, db = env, env.app_db
    login(c, "alice")
    r = ask(c, name="torch", targets=["rtx"]).json()
    assert r["package"] == "pytorch" and db.packages.get("torch") is None


# 2: client-chosen SSE offsets
def test_mid_line_offsets_are_snapped_for_requesters(env):
    c, db = env, env.app_db
    login(c, "alice")
    bid = ask(c, name="lammps", targets=["rtx"]).json()["queued"][0]["id"]
    path = log_path(c.settings.data_dir, bid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("start\ncd /home/alice/stage && token=s3cr3t\nend\n")
    with db.tx() as x:
        x.execute("UPDATE build SET status = 'failed' WHERE id = ?", (bid,))
    for off in (8, 10, 40):
        with c.stream("GET", f"/api/builds/{bid}/events?from={off}") as r:
            body = r.read().decode()
        assert "s3cr3t" not in body and "alice" not in body, off


# 3 + 4: quota races, leftovers, versions
def test_concurrent_requests_cannot_exceed_the_quota(env):
    c, db = env, env.app_db
    db.settings.set("quota.builds_per_week", 1)

    def one(_):
        try:
            return queue_builds(db, c.settings, package="lammps", version="", targets=["rtx"],
                                requested_by="alice", admin=False, skip_existing=True,
                                enforce_quota=True)["queued"]
        except QueueError as e:
            return e.status
    results = list(ThreadPoolExecutor(4).map(one, range(4)))
    assert sum(1 for r in results if isinstance(r, list) and r) == 1
    assert db.query_one("SELECT COUNT(*) AS n FROM build WHERE requested_by = 'alice'")["n"] == 1


def test_a_refused_request_leaves_no_package(env):
    c, db = env, env.app_db
    db.settings.set("quota.builds_per_week", 0)
    login(c, "alice")
    assert ask(c, name="lammps").status_code == 429
    assert db.packages.get("lammps") is None


def test_users_may_only_request_listed_versions(env):
    c = env
    login(c, "alice")
    r = ask(c, name="gromacs", version="9999", targets=["rtx"])
    assert r.status_code == 422 and "Known releases" in r.json()["detail"]
    login(c, "dana")
    assert ask(c, name="gromacs", version="9999", targets=["rtx"]).status_code == 200


# 5: no oracle through `source`
def test_restricted_and_unknown_match_with_a_source(env):
    c = env
    login(c, "alice")
    a = ask(c, name="alphafold3", source="easybuild")
    b = ask(c, name="nosuchthing", source="easybuild")
    assert a.status_code == b.status_code == 422 and a.json() == b.json()


# 6: scrubbing is linear
def test_scrub_is_fast_on_hostile_input():
    for t in ("a." * 128_000, "a" * 256_000, "a-" * 128_000):
        start = time.perf_counter()
        scrub(t)
        assert time.perf_counter() - start < 1.0


# 7: retries keep their inputs
def test_retry_keeps_spec_and_easyconfig(env):
    c, db = env, env.app_db
    res = queue_builds(db, c.settings, package="lammps", version="20250722", targets=["rtx"],
                       requested_by="dana", admin=True, spec="lammps@20250722+kokkos~mpi")
    bid = res["queued"][0]["id"]
    with db.tx() as x:
        x.execute("UPDATE build SET status = 'failed', stage = 'build', "
                  "finished_at = '2026-01-01T00:00:00Z' WHERE id = ?", (bid,))
    new = retry(db, c.settings)["queued"]
    row = db.query_one("SELECT spec, recipe FROM build WHERE id = ?",
                       (next(q["retry"] for q in new if q["failed"] == bid),))
    assert row["spec"] == "lammps@20250722+kokkos~mpi" and "+kokkos~mpi" in row["recipe"]


# 8: visibility is read from the package now
def test_package_made_restricted_hides_its_history(env):
    c, db = env, env.app_db
    login(c, "alice")
    rid = ask(c, name="lammps", targets=["rtx"]).json()["request"]
    with db.tx() as x:
        x.execute("UPDATE package SET visibility = 'restricted' WHERE name IN ('relion', "
                  "'lammps')")
    login(c, "bob")
    fails = c.get("/api/failures").json()["failures"]
    assert all("relion" not in f["packages"] for f in fails)
    assert c.get("/api/requests").json()["requests"] == []
    assert c.put(f"/api/votes/request/{rid}", headers=H).status_code == 404


# 10: \r progress output and retries' logs
def test_carriage_returns_end_lines_and_retries_belong_to_the_requester(env):
    c, db = env, env.app_db
    login(c, "alice")
    r = ask(c, name="lammps", targets=["rtx"]).json()
    bid = coordinator.enqueue(db, db.packages.get("lammps")["id"], r["version"], "spack", "rtx",
                              "linux/amd64", "", requested_by="system", restricted=False,
                              request_id=r["request"])
    path = log_path(c.settings.data_dir, bid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"10%\r50%\r100%\rdone\n")
    with c.stream("GET", f"/api/builds/{bid}/events") as resp:
        assert resp.status_code == 200
        body = resp.read().decode()
    assert "data: 50%" in body and "data: done" in body


# 11: a half-published generic rebuild never hides a published catalog release
def test_half_published_generic_keeps_the_catalog_release():
    cat = {"id": 1, "version": "1", "target": "generic", "origin": "catalog",
           "status": "published", "platform": ""}
    amd = {"id": 5, "version": "1", "target": "generic", "origin": "pipeline",
           "status": "published", "platform": "linux/amd64"}
    arm = {**amd, "id": 6, "status": "failed", "platform": "linux/arm64"}
    assert merge_platforms([cat, amd, arm])[0]["origin"] == "catalog"
    assert merge_platforms([cat, amd, {**arm, "status": "published"}])[0]["origin"] == \
        "pipeline"
