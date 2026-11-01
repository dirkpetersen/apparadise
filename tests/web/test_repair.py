"""Bounded auto-repair from the known-patch library (recipes/repairs.yml)."""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from apparadise import failures, repair
from apparadise.buildqueue import queue_builds
from apparadise.index import _item
from mock.demo import build_demo_app, demo_settings

H = {"X-Requested-With": "apparadise"}
GPROFNG = "make[3]: *** [Makefile:1028: all-gprofng] Error 2"


@pytest.fixture
def env(tmp_path):
    settings = demo_settings(port=8000, data_dir=str(tmp_path))
    app = build_demo_app(settings)
    logging.disable(logging.INFO)
    with TestClient(app) as c:
        c.app_db, c.settings = app.state.db, settings
        yield c
    logging.disable(logging.NOTSET)
    app.state.db.close()


def queue(c, **kw):
    args = {"package": "lammps", "version": "20250722", "targets": ["rtx"],
            "requested_by": "alice", "admin": True} | kw
    return queue_builds(c.app_db, c.settings, **args)["queued"][0]["id"]


EC = "LAMMPS-22Jul2025-foss-2025b-kokkos-CUDA-12.9.1.eb"


def eb_build(c):
    c.app_db.index.replace_source("easybuild", [_item("easybuild", "LAMMPS", ["20250722"],
                                                      True, [], False)])
    return queue(c, source="easybuild", easyconfig=EC)


def fail(db, bid, stage, line):
    with db.tx() as x:
        x.execute("UPDATE build SET status = 'failed', stage = ? WHERE id = ?", (stage, bid))
    db.failures.record(bid, failures.signature(stage, line), stage, line, False,
                       error_line=failures.scrub(line))


def build(db, bid):
    return db.query_one("SELECT * FROM build WHERE id = ?", (bid,))


def test_the_library_loads_and_validates(tmp_path):
    rules = repair.load()
    assert {"binutils-gprofng", "c23-old-code", "spack-unsatisfiable"} <= {r["id"] for r in rules}
    bad = tmp_path / "bad.yml"
    for text in ("- {id: x}", "- {id: ok-id, match: '(', stages: [build], actions: {}}",
                 "- {id: ok-id, match: a, stages: [publish], actions: {}}",
                 "- {id: ok-id, match: a, stages: [build], actions: {spack: {rm: 1}}}"):
        bad.write_text(text)
        repair.load.cache_clear()
        with pytest.raises(repair.LibraryError):
            repair.load(str(bad))
    repair.load.cache_clear()


def test_spack_failure_gets_one_repaired_build(env):
    c, db = env, env.app_db
    bid = queue(c)
    fail(db, bid, "build", GPROFNG)
    out = repair.attempt(db, c.settings, bid)
    assert out["rule"] == "binutils-gprofng"
    new = build(db, out["retry"])
    # the fix goes to the dependency that failed, not to the package itself
    assert new["repair"] == "binutils-gprofng"
    assert new["spec"] == "lammps@20250722 ^binutils~gprofng"
    assert ("lammps@20250722 +cuda cuda_arch=120 target=x86_64_v4 ^binutils~gprofng"
            in new["recipe"])  # root variants before the ^dep (Spack 1.x)
    assert new["requested_by"] == "system"
    f = db.query_one("SELECT repair_attempts FROM failure WHERE id = ?",
                     (build(db, bid)["failure_id"],))
    assert json.loads(f["repair_attempts"])[0]["retry"] == out["retry"]
    assert db.audit.query(action="build.repair")[0]["actor"] == "system"
    # the same rule is never tried twice on a release
    fail(db, out["retry"], "build", GPROFNG)
    assert repair.attempt(db, c.settings, out["retry"]) is None


def test_attempts_are_capped_per_release(env):
    c, db = env, env.app_db
    db.settings.set("repair.max_attempts", 1)
    bid = queue(c)
    fail(db, bid, "build", GPROFNG)
    retry = repair.attempt(db, c.settings, bid)["retry"]
    fail(db, retry, "build", "error: 'bool' cannot be defined via typedef")
    assert repair.attempt(db, c.settings, retry) is None  # c23 would match, but the cap


def test_easybuild_toolchain_fallback_walks_the_chain(env):
    # Q22/Q34: newest generation first, then the next, then the easyconfig's own toolchain
    c, db = env, env.app_db
    bid = eb_build(c)
    assert 'try-toolchain: "foss,2026.1"' in build(db, bid)["recipe"]
    err = "ERROR: No easyconfig found for dependency libtiff 4.6.0"
    seen = []
    for _ in range(3):
        fail(db, bid, "concretize", err)
        res = repair.attempt(db, c.settings, bid)
        if res is None:
            break
        bid = res["retry"]
        new = build(db, bid)
        seen.append(json.loads(new["eb_options"]).get("try-toolchain", "?"))
        assert new["repair"] == "easybuild-toolchain-fallback" and "filter-deps" in new["recipe"]
    assert seen == ["foss,2025b", None]
    assert "try-toolchain" not in new["recipe"]  # the easyconfig's own toolchain


def test_next_toolchain():
    assert repair.next_toolchain({}) == "foss,2025b"
    assert repair.next_toolchain({"try-toolchain": "foss,2025b"}) is None
    assert repair.next_toolchain({"try-toolchain": None}) is False


def test_easybuild_options_are_quoted_and_vetted():
    import yaml

    from apparadise.recipes import RecipeError, render
    value = 'preconfigopts=export CFLAGS="$CFLAGS -std=gnu17" && '
    text = render("easybuild", "lammps", "20250722", "rtx", registry="r.example.org",
                  easyconfig=EC, eb_options={"try-amend": value})
    assert yaml.safe_load(text)["easyconfigs"][0][EC]["options"]["try-amend"] == value
    for bad in ({"try-amend": "a=1,2"}, {"try-amend": "x\ny"}, {"robot-paths": "/x"}):
        with pytest.raises(RecipeError):
            render("easybuild", "lammps", "20250722", "rtx", registry="r.example.org",
                   easyconfig=EC, eb_options=bad)


def test_a_fix_already_in_the_spec_is_not_tried_again(env):
    c, db = env, env.app_db
    bid = queue(c)
    fail(db, bid, "build", "error: 'bool' cannot be defined via typedef")
    retry = repair.attempt(db, c.settings, bid)["retry"]
    assert build(db, retry)["spec"].endswith("cflags==-std=gnu17")
    # gnulib's rule has the same fix: it is skipped, not appended twice
    fail(db, retry, "build", "error: _Generic selection in memchr")
    assert repair.attempt(db, c.settings, retry) is None


def test_switch_source_when_spack_cannot_concretize(env):
    c, db = env, env.app_db
    eb = _item("easybuild", "LAMMPS", ["20250722"], True, [], False)
    eb["details"] = {"easyconfigs": {"20250722": EC}}
    db.index.replace_source("easybuild", [eb])
    bid = queue(c)
    fail(db, bid, "concretize", "==> Error: concretization failed for lammps")
    new = build(db, repair.attempt(db, c.settings, bid)["retry"])
    assert new["source"] == "easybuild" and EC in new["recipe"]
    assert new["repair"] == "spack-unsatisfiable"


def test_switch_source_needs_the_same_version(env):
    c, db = env, env.app_db
    db.index.replace_source("pixi", [_item("pixi", "lammps", ["2024.08.29"], True,
                                           ["linux-64", "linux-aarch64"], False)])
    bid = queue(c)
    fail(db, bid, "concretize", "==> Error: concretization failed for lammps")
    assert repair.attempt(db, c.settings, bid) is None


def test_no_repair_without_a_match_or_when_superseded(env):
    c, db = env, env.app_db
    bid = queue(c)
    fail(db, bid, "build", "nvcc fatal : Unsupported gpu architecture 'compute_103f'")
    assert repair.attempt(db, c.settings, bid) is None
    bid2 = queue(c, version="20250722", targets=["rtx"])
    fail(db, bid2, "build", GPROFNG)
    queue(c)  # a newer build of the same release
    assert repair.attempt(db, c.settings, bid2) is None


def test_a_failed_report_triggers_a_repair(env):
    c, db = env, env.app_db
    c.post("/api/login", json={"login": "dana", "password": "demo"}, headers=H)
    tok = c.post("/api/admin/builder-tokens", headers=H).json()["token"]
    bh = {**H, "Authorization": "Bearer " + c.post(
        "/api/builder/register", headers=H,
        json={"token": tok, "name": "b1", "platform": "linux/amd64"}).json()["token"]}
    c.post("/api/admin/builds", headers=H,
           json={"package": "lammps", "version": "20250722", "targets": ["rtx"]})
    job = c.post("/api/builder/claim", headers=bh).json()
    c.post(f"/api/builder/jobs/{job['id']}/log", headers={**bh, "Content-Type":
                                                         "application/octet-stream"},
           content=f"checking...\n{GPROFNG}\n".encode())
    r = c.post(f"/api/builder/jobs/{job['id']}/report", headers=bh,
               json={"stage": "build", "ok": False})
    assert r.status_code == 200 and r.json()["repair"]["rule"] == "binutils-gprofng"
    assert build(db, r.json()["repair"]["retry"])["status"] == "queued"


def test_published_repairs_become_upstream_offers_for_sysadmins(env):
    c, db = env, env.app_db
    bid = queue(c)
    fail(db, bid, "build", GPROFNG)
    retry = repair.attempt(db, c.settings, bid)["retry"]
    c.post("/api/login", json={"login": "alice", "password": "demo"}, headers=H)
    assert c.get("/api/admin/upstream").status_code == 403
    c.post("/api/login", json={"login": "dana", "password": "demo"}, headers=H)
    assert c.get("/api/admin/upstream").json()["offers"] == []  # not published yet
    with db.tx() as x:
        x.execute("UPDATE build SET status = 'published' WHERE id = ?", (retry,))
    o = c.get("/api/admin/upstream").json()["offers"][0]
    assert o["rule"] == "binutils-gprofng" and o["error"] == GPROFNG and not o["restricted"]
    assert "lammps@20250722 ^binutils~gprofng" in o["body"]
    assert o["issueUrl"].startswith("https://github.com/spack/spack-packages/issues/new?")
