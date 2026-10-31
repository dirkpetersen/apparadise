"""Phase 3 community layer: the public failure database and votes, on the demo app (real
coordinator grouping, scrubbing and catalog)."""

import logging

import pytest
from fastapi.testclient import TestClient

from apparadise.catalog import merge_platforms
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
    r = c.post("/api/login", json={"login": user, "password": "demo"}, headers=H)
    assert r.status_code == 200


def by_error(rows, text):
    return next(f for f in rows if text in f["error"])


def test_failures_are_grouped_across_packages(c):
    rows = c.get("/api/failures").json()["failures"]
    nvcc = by_error(rows, "compute_103f")
    assert nvcc["packages"] == ["relion", "vllm"] and nvcc["builds"] == 2
    assert nvcc["stage"] == "build" and not nvcc["voted"]


def test_restricted_failures_hidden_from_public_and_users(c):
    for who in (None, "alice"):
        if who:
            login(c, who)
        rows = c.get("/api/failures").json()["failures"]
        assert all(not f["restricted"] for f in rows)
        assert "alphafold3" not in str(rows) and "jaxlib" not in str(rows)
        assert c.get("/api/failures?q=alphafold3").json()["failures"] == []
    fid = c.app_db.query_one("SELECT id FROM failure WHERE restricted = 1")["id"]
    assert c.get(f"/api/failures/{fid}").status_code == 404
    assert c.put(f"/api/votes/failure/{fid}", headers=H).status_code == 404


def test_sysadmin_sees_restricted_failures(c):
    login(c, "dana")
    rows = c.get("/api/failures").json()["failures"]
    assert by_error(rows, "jaxlib")["packages"] == ["alphafold3"]


def test_filters(c):
    assert {f["stage"] for f in c.get("/api/failures?stage=test").json()["failures"]} == {"test"}
    assert [f["packages"] for f in c.get("/api/failures?package=vllm").json()["failures"]] == [
        ["relion", "vllm"]]
    assert len(c.get("/api/failures?q=no%20kernel").json()["failures"]) == 1
    assert c.get("/api/failures?q=100%25").json()["failures"] == []  # LIKE wildcards escaped
    assert c.get("/api/failures?stage=nope").status_code == 422


def test_detail_is_scrubbed_and_lists_builds(c):
    rows = c.get("/api/failures").json()["failures"]
    d = c.get(f"/api/failures/{by_error(rows, 'no kernel')['id']}").json()
    assert "gpu-n12" not in str(d) and "/scratch" not in str(d) and "fredhutch" not in str(d)
    assert d["builds"][0]["package"] == "gromacs" and d["builds"][0]["target"] == "generic"
    assert "requested_by" not in str(d)
    assert c.get("/api/failures/99999").status_code == 404


def test_package_page_links_its_failure(c):
    vr = c.get("/api/catalog/relion").json()["versions"][0]["targets"]["vr"]
    assert vr["status"] == "failed" and vr["failureId"]


def test_votes_need_sign_in_csrf_and_are_idempotent(c):
    assert c.put("/api/votes/package/gromacs", headers=H).status_code == 401
    login(c, "alice")
    assert c.put("/api/votes/package/gromacs").status_code == 403  # no CSRF header
    for _ in range(2):
        assert c.put("/api/votes/package/gromacs", headers=H).json() == {"votes": 1,
                                                                          "voted": True}
    login(c, "bob")
    assert c.put("/api/votes/package/gromacs", headers=H).json()["votes"] == 2
    assert c.delete("/api/votes/package/gromacs", headers=H).json() == {"votes": 1,
                                                                         "voted": False}
    p = c.get("/api/catalog/gromacs").json()
    assert p["votes"] == 1 and not p["voted"]
    assert [x["votes"] for x in c.get("/api/catalog").json()["packages"]
            if x["name"] == "gromacs"] == [1]
    actions = [a["action"] for a in c.app_db.audit.query(actor="bob")]
    assert actions[:2] == ["vote.remove", "vote.add"]


def test_votes_on_unknown_or_restricted_subjects_are_404(c):
    login(c, "alice")
    for path in ("package/alphafold3", "package/nope", "failure/abc", "request/1", "x/1"):
        assert c.put(f"/api/votes/{path}", headers=H).status_code == 404, path


def test_failure_votes_show_in_list(c):
    login(c, "alice")
    fid = c.get("/api/failures").json()["failures"][0]["id"]
    c.put(f"/api/votes/failure/{fid}", headers=H)
    row = next(f for f in c.get("/api/failures").json()["failures"] if f["id"] == fid)
    assert row["votes"] == 1 and row["voted"]


def test_pipeline_build_supersedes_catalog_row_regardless_of_ids():
    cat = {"id": 9, "version": "1", "target": "vr", "origin": "catalog", "status": "failed",
           "platform": ""}
    pipe = {"id": 3, "version": "1", "target": "vr", "origin": "pipeline", "status": "failed",
            "platform": "linux/arm64", "failure_id": 7}
    assert merge_platforms([cat, pipe])[0]["failure_id"] == 7
    published = {**cat, "status": "published"}
    assert merge_platforms([published, pipe])[0]["origin"] == "catalog"
    assert merge_platforms([published, {**pipe, "status": "published"}])[0]["origin"] == \
        "pipeline"
