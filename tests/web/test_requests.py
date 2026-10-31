"""Phase 3 requests: any signed-in user queues builds within a weekly quota, through the
same resolve/render/queue path as the sysadmin console."""

import logging

import pytest
from fastapi.testclient import TestClient

from mock.demo import build_demo_app, demo_settings

H = {"X-Requested-With": "apparadise"}


@pytest.fixture
def c(tmp_path):
    app = build_demo_app(demo_settings(port=8000, data_dir=str(tmp_path)))
    logging.disable(logging.INFO)
    with TestClient(app) as client:
        client.app_db = app.state.db
        yield client
    logging.disable(logging.NOTSET)
    app.state.db.close()


def login(c, user):
    assert c.post("/api/login", json={"login": user, "password": "demo"},
                  headers=H).status_code == 200


def ask(c, **body):
    return c.post("/api/requests", json=body, headers=H)


def test_requests_need_sign_in(c):
    assert ask(c, name="lammps").status_code == 401
    assert c.get("/api/quota").status_code == 401
    assert c.get("/api/requests?mine=true").status_code == 401


def test_request_resolves_picks_newest_version_and_queues(c):
    login(c, "alice")
    r = ask(c, name="lammps", targets=["rtx"]).json()
    assert r["request"] and r["source"] == "spack" and r["rule"] == 3
    assert r["version"] == "20250722" and [q["target"] for q in r["queued"]] == ["rtx"]
    b = c.app_db.query_one("SELECT * FROM build WHERE id = ?", (r["queued"][0]["id"],))
    assert b["requested_by"] == "alice" and b["request_id"] == r["request"]
    assert b["status"] == "queued" and "lammps@20250722" in b["recipe"]
    assert c.app_db.packages.get("lammps")["origin"] == "request"
    assert c.app_db.audit.query(action="request.create")[0]["actor"] == "alice"


def test_duplicates_cost_nothing(c):
    login(c, "alice")
    ask(c, name="lammps", targets=["rtx"])
    login(c, "bob")
    r = ask(c, name="lammps", targets=["rtx"]).json()
    assert r["request"] is None and r["queued"] == []
    assert r["skipped"] == [{"target": "rtx", "platform": "linux/amd64",
                             "reason": "already queued"}]
    assert c.get("/api/quota").json()["used"] == 0


def test_published_release_is_skipped(c):
    login(c, "alice")
    r = ask(c, name="gromacs", version="2026.3", targets=["rtx"]).json()
    assert r["queued"] == [] and r["skipped"][0]["reason"] == "already available"


def test_unknown_and_restricted_names_look_the_same(c):
    login(c, "alice")
    a, b = ask(c, name="nosuchthing"), ask(c, name="alphafold3")
    assert a.status_code == b.status_code == 422 and a.json() == b.json()
    pkg = c.app_db.packages.get("alphafold3", include_restricted=True)
    assert pkg["visibility"] == "restricted"  # never overwritten by a request


def test_quota_counts_platform_jobs_and_spares_sysadmins(c):
    login(c, "dana")
    assert c.put("/api/admin/policy", json={"quotaBuildsPerWeek": 2}, headers=H).json() == {
        "quotaBuildsPerWeek": 2}
    assert c.get("/api/quota").json()["unlimited"]
    login(c, "alice")
    assert c.get("/api/quota").json() == {"unlimited": False, "limit": 2, "used": 0, "left": 2}
    assert ask(c, name="pytorch", targets=["generic"]).status_code == 200  # 2 jobs
    r = ask(c, name="lammps", targets=["rtx"])
    assert r.status_code == 429 and "0 left" in r.json()["detail"]
    assert c.app_db.query_one("SELECT COUNT(*) AS n FROM build WHERE package_id = ("
                              "SELECT id FROM package WHERE name = 'lammps')")["n"] == 0
    login(c, "dana")
    assert ask(c, name="lammps", targets=["rtx"]).status_code == 200


def test_policy_is_sysadmin_only_and_validated(c):
    login(c, "alice")
    assert c.get("/api/admin/policy").status_code == 403
    assert c.put("/api/admin/policy", json={"quotaBuildsPerWeek": 99},
                 headers=H).status_code == 403
    login(c, "dana")
    assert c.put("/api/admin/policy", json={"quotaBuildsPerWeek": -1},
                 headers=H).status_code == 422
    assert c.app_db.audit.query(action="policy.update") == []


def test_list_hides_requesters_from_others(c):
    login(c, "alice")
    rid = ask(c, name="lammps", targets=["rtx"]).json()["request"]
    mine = c.get("/api/requests").json()["requests"][0]
    assert mine["id"] == rid and mine["mine"] and mine["requestedBy"] == "alice"
    assert mine["status"] == "building" and mine["builds"]["active"] == 1
    login(c, "bob")
    other = c.get("/api/requests").json()["requests"][0]
    assert other["requestedBy"] is None and not other["mine"]
    assert c.get("/api/requests?mine=true").json()["requests"] == []
    c.cookies.clear()
    assert c.get("/api/requests").json()["requests"][0]["requestedBy"] is None
    login(c, "dana")
    assert c.get("/api/requests").json()["requests"][0]["requestedBy"] == "alice"


def test_status_follows_builds_and_requests_take_votes(c):
    login(c, "alice")
    r = ask(c, name="lammps", targets=["rtx"]).json()
    with c.app_db.tx() as x:
        x.execute("UPDATE build SET status = 'failed' WHERE id = ?", (r["queued"][0]["id"],))
    assert c.get("/api/requests").json()["requests"][0]["status"] == "failed"
    login(c, "bob")
    assert c.put(f"/api/votes/request/{r['request']}", headers=H).json() == {"votes": 1,
                                                                              "voted": True}
    assert c.put("/api/votes/request/999", headers=H).status_code == 404


def test_index_search_resolves_and_hides_restricted(c):
    res = c.get("/api/index?q=rel").json()["results"]
    relion = next(x for x in res if x["name"] == "relion")
    assert set(relion["sources"]) == {"spack", "easybuild"}
    assert relion["resolution"]["source"] == "spack" and relion["resolution"]["rule"] == 3
    assert c.get("/api/index?q=alphafold").json()["results"] == []
    login(c, "dana")
    assert c.get("/api/index?q=alphafold").json()["results"][0]["resolution"]["source"] == \
        "easybuild"
    assert c.get("/api/index?q=x").status_code == 422  # at least two characters
