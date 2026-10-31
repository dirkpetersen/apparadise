"""End to end: sysadmin queues a build, a builder registers, claims, logs and reports."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apparadise.config import Settings
from apparadise.db import Database
from apparadise.index import _item
from apparadise.main import create_app
from mock.directory import DemoDirectory

from .conftest import REQUIRED

H = {"X-Requested-With": "apparadise"}
D = "sha256:" + "d" * 64


@pytest.fixture
def env(tmp_path):
    s = Settings(_env_file=None, data_dir=str(tmp_path / "data"),
                 catalog_dir=str(Path(__file__).resolve().parents[2] / "mock" / "catalog"),
                 session_cookie_secure=False, **REQUIRED)
    db = Database(":memory:")
    db.index.replace_source("spack", [
        _item("spack", "gromacs", ["2026.3"], True, [], False),
        _item("spack", "relion", ["5.0.1"], True, [], False)])
    af = _item("easybuild", "AlphaFold3", ["3.0.1"], True, [], True)
    af["details"] = {"easyconfigs": {"3.0.1": "AlphaFold3-3.0.1-foss-2025b-CUDA-12.9.1.eb"}}
    db.index.replace_source("easybuild", [af])
    db.index.replace_source("pixi", [
        _item("pixi", "pytorch", ["2.9.1"], True, ["linux-64", "linux-aarch64"], True)])
    app = create_app(s, db=db, authenticator=DemoDirectory())
    with TestClient(app) as c:
        yield c, db, tmp_path
    db.close()


def login(c, user):
    r = c.post("/api/login", json={"login": user, "password": "demo"}, headers=H)
    assert r.status_code == 200


def new_builder(c, name="b1", platform="linux/amd64", onprem=True):
    login(c, "dana")
    tok = c.post("/api/admin/builder-tokens", headers=H).json()["token"]
    c.post("/api/logout", headers=H)
    r = c.post("/api/builder/register", headers=H,
               json={"token": tok, "name": name, "platform": platform, "onprem": onprem})
    assert r.status_code == 200, r.text
    return {**H, "Authorization": f"Bearer {r.json()['token']}"}, tok


def queue(c, **body):
    login(c, "dana")
    r = c.post("/api/admin/builds", headers=H, json=body)
    c.post("/api/logout", headers=H)
    return r


def test_admin_routes_need_sysadmin(env):
    c, _, _ = env
    assert c.post("/api/admin/builder-tokens", headers=H).status_code == 401
    login(c, "alice")
    assert c.post("/api/admin/builder-tokens", headers=H).status_code == 403
    assert c.post("/api/admin/builds", headers=H, json={}).status_code in (403, 422)


def test_registration_token_is_one_time(env):
    c, _, _ = env
    _, tok = new_builder(c)
    r = c.post("/api/builder/register", headers=H,
               json={"token": tok, "name": "b2", "platform": "linux/amd64"})
    assert r.status_code == 401


def test_builder_routes_need_token(env):
    c, _, _ = env
    assert c.post("/api/builder/claim", headers=H).status_code == 401
    bad = {**H, "Authorization": "Bearer nope"}
    assert c.post("/api/builder/claim", headers=bad).status_code == 401
    bh, _ = new_builder(c)
    assert c.post("/api/builder/claim", headers={k: v for k, v in bh.items()
                                                 if k != "X-Requested-With"}).status_code == 403


def test_full_happy_path(env):
    c, db, tmp = env
    r = queue(c, package="gromacs", version="2026.3", targets=["rtx", "vr"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "spack" and body["rule"] == 3
    assert [q["target"] for q in body["queued"]] == ["rtx"]
    assert body["skipped"][0]["target"] == "vr" and "Q13" in body["skipped"][0]["reason"]

    arm, _ = new_builder(c, "arm1", "linux/arm64")
    assert c.post("/api/builder/claim", headers=arm).status_code == 204  # nothing for arm64

    bh, _ = new_builder(c, "x86-1")
    job = c.post("/api/builder/claim", headers=bh).json()
    assert job["image"] == "public/gromacs:2026.3-rtx" and job["platform"] == "linux/amd64"
    assert "cuda_arch=120" in job["recipe"]
    assert c.post("/api/builder/claim", headers=bh).status_code == 204  # queue now empty

    jid = job["id"]
    r = c.post(f"/api/builder/jobs/{jid}/log", headers=bh, content=b"compiling\n")
    assert r.json() == {"size": 10}
    for stage in ("build", "package", "test"):
        assert c.post(f"/api/builder/jobs/{jid}/report", headers=bh,
                      json={"stage": stage, "ok": True}).status_code == 200
    r = c.post(f"/api/builder/jobs/{jid}/report", headers=bh,
               json={"stage": "publish", "ok": True, "digest": D, "test": "gmx --version"})
    assert r.json()["status"] == "published"
    # the published build now shows in the catalog with commands
    pkg = c.get("/api/catalog/gromacs").json()
    rtx = next(v for v in pkg["versions"] if v["version"] == "2026.3")["targets"]["rtx"]
    assert rtx["status"] == "published" and rtx["digest"] == D
    assert (tmp / "data" / "logs" / f"{jid}.log").read_text() == "compiling\n"


def test_failure_groups_and_scrubs(env):
    c, db, _ = env
    bh, _ = new_builder(c)
    for _ in range(2):
        queue(c, package="relion", version="5.0.1", targets=["rtx"], source="spack")
    fids = []
    for _ in range(2):
        job = c.post("/api/builder/claim", headers=bh).json()
        c.post(f"/api/builder/jobs/{job['id']}/log", headers=bh,
               content=b"cd /home/alice/x\nmake: *** [Makefile:12: all-gprofng] Error 2\n")
        r = c.post(f"/api/builder/jobs/{job['id']}/report", headers=bh,
                   json={"stage": "build", "ok": False}).json()
        assert r["status"] == "failed" and r["stage"] == "build"
        fids.append(r["failure_id"])
    assert fids[0] == fids[1]
    f = db.failures.get(fids[0])
    assert f["builds"] == 2
    assert "alice" not in f["log_excerpt"] and "all-gprofng" in f["log_excerpt"]


def test_reports_are_checked(env):
    c, _, _ = env
    bh, _ = new_builder(c)
    other, _ = new_builder(c, "x86-2")
    queue(c, package="gromacs", version="2026.3", targets=["rtx"])
    jid = c.post("/api/builder/claim", headers=bh).json()["id"]
    url = f"/api/builder/jobs/{jid}/report"
    assert c.post(url, headers=other, json={"stage": "build", "ok": True}).status_code == 404
    assert c.post(url, headers=bh, json={"stage": "publish", "ok": True}).status_code == 422
    assert c.post(url, headers=bh, json={"stage": "test", "ok": True}).status_code == 200
    assert c.post(url, headers=bh, json={"stage": "build", "ok": True}).status_code == 409
    assert c.post(url, headers=bh, json={"stage": "resolve", "ok": True}).status_code == 422


def test_restricted_only_onprem(env):
    c, _, _ = env
    cloud, _ = new_builder(c, "aws-1", onprem=False)
    r = queue(c, package="alphafold3", version="3.0.1", targets=["rtx"],
              easyconfig="AlphaFold3-3.0.1-foss-2025b-CUDA-12.9.1.eb")
    assert r.status_code == 200 and r.json()["source"] == "easybuild"
    assert c.post("/api/builder/claim", headers=cloud).status_code == 204
    onprem, _ = new_builder(c, "onprem-1")
    job = c.post("/api/builder/claim", headers=onprem).json()
    assert job["image"].startswith("restricted/alphafold3:")


def test_one_easybuild_job_per_tree(env):
    c, _, _ = env
    for v in ("5.0.1", "5.0.2"):
        queue(c, package="relion", version=v, targets=["rtx"], source="spack")
    b1, _ = new_builder(c, "x1")
    b2, _ = new_builder(c, "x2")
    # spack jobs run in parallel
    assert c.post("/api/builder/claim", headers=b1).status_code == 200
    assert c.post("/api/builder/claim", headers=b2).status_code == 200
    for v in ("3.0.1", "3.0.2"):
        queue(c, package="alphafold3", version=v, targets=["rtx"],
              easyconfig=f"AlphaFold3-{v}.eb")
    first = c.post("/api/builder/claim", headers=b1).json()
    assert first["source"] == "easybuild"
    assert c.post("/api/builder/claim", headers=b2).status_code == 204  # tree is busy
    c.post(f"/api/builder/jobs/{first['id']}/report", headers=b1,
           json={"stage": "build", "ok": False, "error": "error: x"})
    assert c.post("/api/builder/claim", headers=b2).json()["source"] == "easybuild"


def test_generic_queues_both_platforms_with_own_recipes(env):
    c, _, _ = env
    r = queue(c, package="pytorch", version="2.9.1", targets=["generic"]).json()
    assert r["source"] == "pixi" and r["rule"] == 2
    assert sorted(q["platform"] for q in r["queued"]) == ["linux/amd64", "linux/arm64"]
    arm, _ = new_builder(c, "arm1", "linux/arm64")
    job = c.post("/api/builder/claim", headers=arm).json()
    assert 'platforms = ["linux-aarch64"]' in job["recipe"]


def test_unresolvable_package(env):
    c, _, _ = env
    r = queue(c, package="nothing-here", version="1.0", targets=["rtx"])
    assert r.status_code == 422 and "No recipe" in r.json()["detail"]


def test_admin_build_list_and_log(env):
    c, _, _ = env
    bh, _ = new_builder(c)
    queue(c, package="gromacs", version="2026.3", targets=["rtx"])
    jid = c.post("/api/builder/claim", headers=bh).json()["id"]
    c.post(f"/api/builder/jobs/{jid}/log", headers=bh, content=b"hello log\n")
    assert c.get("/api/admin/builds").status_code == 401
    login(c, "dana")
    rows = c.get("/api/admin/builds").json()["builds"]
    assert rows[0]["id"] == jid and rows[0]["builder"] == "b1" and rows[0]["hasLog"]
    assert "log_path" not in rows[0]
    assert c.get("/api/admin/builds", params={"status": "queued"}).json()["builds"] == []
    assert c.get(f"/api/admin/builds/{jid}/log").json() == {"log": "hello log\n", "size": 10}
    assert c.get("/api/admin/builds/9999/log").status_code == 404


def test_reaper_fails_jobs_of_silent_builders(env):
    import asyncio

    from apparadise.main import reap_once

    c, db, _ = env
    bh, _ = new_builder(c)
    queue(c, package="gromacs", version="2026.3", targets=["rtx"])
    jid = c.post("/api/builder/claim", headers=bh).json()["id"]
    assert asyncio.run(reap_once(c.app)) == []  # just seen: not stale
    with db.tx() as t:
        t.execute("UPDATE builder SET last_seen = '2000-01-01T00:00:00.000000Z'")
    assert asyncio.run(reap_once(c.app)) == [jid]
    row = db.query_one("SELECT status, stage, failure_id FROM build WHERE id = ?", (jid,))
    assert row["status"] == "failed" and row["failure_id"]
    assert "builder lost" in db.failures.get(row["failure_id"])["log_excerpt"]
    # the builder can no longer report on it
    r = c.post(f"/api/builder/jobs/{jid}/report", headers=bh, json={"stage": "build", "ok": True})
    assert r.status_code == 409


def test_queue_counts_for_builders(env):
    c, _, _ = env
    bh, _ = new_builder(c)
    assert c.get("/api/builder/queue").status_code == 401
    queue(c, package="gromacs", version="2026.3", targets=["rtx"])
    assert c.get("/api/builder/queue", headers=bh).json() == {
        "queued": {"linux/amd64": 1}, "running": {}}


def test_easyconfig_picked_from_index(env):
    c, _, _ = env
    r = queue(c, package="alphafold3", version="3.0.1", targets=["rtx"])
    assert r.status_code == 200, r.text
    onprem, _ = new_builder(c)
    job = c.post("/api/builder/claim", headers=onprem).json()
    assert "AlphaFold3-3.0.1-foss-2025b-CUDA-12.9.1.eb" in job["recipe"]
    r = queue(c, package="alphafold3", version="9.9", targets=["rtx"])
    assert r.status_code == 422 and "No CUDA easyconfig" in r.json()["detail"]
