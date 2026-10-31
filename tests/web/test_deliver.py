"""Delivery: stage published pipeline builds, join generic manifests, regenerate modules."""

import os
import subprocess
from pathlib import Path

import pytest

from apparadise import coordinator
from apparadise.db import Database
from apparadise.deliver import deliver, entries_from_db
from apparadise.modules import releases

D = "sha256:" + "a" * 64


@pytest.fixture
def db():
    d = Database(":memory:")
    pid = d.packages.upsert({"name": "gromacs", "title": "GROMACS", "executables": ["gmx"],
                             "origin": "request"})
    builder = {"id": None}
    with d.tx() as c:
        builder["id"] = c.execute(
            "INSERT INTO builder (name, platform, token_hash, registered_at) "
            "VALUES ('b', 'linux/amd64', 'h', 'now') RETURNING id").fetchone()[0]
    ids = {}
    for target, plat in (("rtx", "linux/amd64"), ("generic", "linux/amd64"),
                         ("generic", "linux/arm64"), ("vr", "linux/arm64")):
        bid = coordinator.enqueue(d, pid, "2026.3", "spack", target, plat, "r",
                                  requested_by="dana", restricted=False)
        ids[(target, plat)] = bid
    # mark rtx + both generic published, vr still queued
    with d.tx() as c:
        for key in (("rtx", "linux/amd64"), ("generic", "linux/amd64"),
                    ("generic", "linux/arm64")):
            c.execute("UPDATE build SET status = 'published', image_digest = ? WHERE id = ?",
                      (D, ids[key]))
    yield d, ids
    d.close()


def test_generic_tags_are_per_arch():
    assert coordinator.image_tag("1", "generic", "linux/arm64") == "1-generic-arm64"
    assert coordinator.image_tag("1", "rtx", "linux/amd64") == "1-rtx"


def test_deliver_stages_host_platform_and_builds_manifest(db, tmp_path):
    d, ids = db
    calls = []
    out = deliver(d, registry="reg.example", sqsh_dir="/s", module_dir=str(tmp_path / "m"),
                  wrapper_dir=str(tmp_path / "w"), platform="linux/amd64", runner=calls.append)
    assert out["staged"] == [ids[("rtx", "linux/amd64")], ids[("generic", "linux/amd64")]]
    stage = [c for c in calls if c[0].endswith("stage-sqsh")]
    assert stage[0][1:] == ["reg.example", "public", "gromacs", "2026.3", "rtx", D, "/s"]
    assert out["manifests"] == ["reg.example/public/gromacs:2026.3-generic"]
    adds = [c[-1] for c in calls if c[:3] == ["podman", "manifest", "add"]]
    assert adds == ["docker://reg.example/public/gromacs:2026.3-generic-amd64",
                    "docker://reg.example/public/gromacs:2026.3-generic-arm64"]
    # modules: only delivered builds count -> rtx + generic (amd64 delivered so far)
    rel = releases(entries_from_db(d))
    assert [(r.version, r.targets) for r in rel] == [("2026.3", ("generic", "rtx"))]
    assert (tmp_path / "m" / "gromacs" / "2026.3.lua").exists()
    # second run: nothing new, manifest not pushed again
    calls.clear()
    out = deliver(d, registry="reg.example", sqsh_dir="/s", module_dir=str(tmp_path / "m"),
                  wrapper_dir=str(tmp_path / "w"), platform="linux/amd64", runner=calls.append)
    assert out["staged"] == [] and out["manifests"] == [] and calls == []


def test_generic_wrapper_picks_arch_specific_sqsh(db, tmp_path):
    d, _ = db
    deliver(d, registry="r", sqsh_dir=str(tmp_path / "s"), module_dir=str(tmp_path / "m"),
            wrapper_dir=str(tmp_path / "w"), platform="linux/amd64", runner=lambda a: None)
    (tmp_path / "s" / "generic").mkdir(parents=True)
    import platform as plat

    arch = "arm64" if plat.machine() == "aarch64" else "amd64"
    (tmp_path / "s" / "generic" / f"gromacs-2026.3-generic-{arch}.sqsh").write_bytes(b"")
    fake = tmp_path / "enroot"
    fake.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
    fake.chmod(0o755)
    w = tmp_path / "w" / "gromacs" / "2026.3" / "gmx"
    r = subprocess.run([str(w)], capture_output=True, text=True,
                       env={**os.environ, "APPARADISE_ENROOT": str(fake),
                            "APPARADISE_TARGET": "generic"})
    assert r.returncode == 0, r.stderr
    assert f"gromacs-2026.3-generic-{arch}.sqsh" in r.stdout


def test_stage_sqsh_generic_suffix(tmp_path):
    fake = tmp_path / "enroot"
    fake.write_text('#!/bin/bash\necho "$4" > "$3"\n')
    fake.chmod(0o755)
    stage = Path(__file__).resolve().parents[2] / "tools" / "stage-sqsh"
    r = subprocess.run([str(stage), "reg", "public", "g", "1", "generic", D, str(tmp_path / "c")],
                       capture_output=True, text=True,
                       env={**os.environ, "ENROOT": str(fake), "ARCH": "aarch64"})
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "c" / "generic" / "g-1-generic-arm64.sqsh").exists()
