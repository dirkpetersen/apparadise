"""Server-side delivery: digest-pinned generic manifests and modules from the database."""

import os
import subprocess
from pathlib import Path

import pytest

from apparadise import coordinator
from apparadise.catalog import release_tag
from apparadise.db import Database
from apparadise.deliver import deliver, entries_from_db, main
from apparadise.modules import releases

DA, DB_ = "sha256:" + "a" * 64, "sha256:" + "b" * 64


def publish(d, ids, *keys, digest=DA, delivered=True):
    with d.tx() as c:
        for k in keys:
            c.execute("UPDATE build SET status = 'published', image_digest = ?, "
                      "delivered_at = CASE WHEN ? THEN 'now' ELSE NULL END WHERE id = ?",
                      (digest, int(delivered), ids[k]))


@pytest.fixture
def db():
    d = Database(":memory:")
    pid = d.packages.upsert({"name": "gromacs", "title": "GROMACS", "executables": ["gmx"],
                             "origin": "request"})

    def enq(target, plat):
        return coordinator.enqueue(d, pid, "2026.3", "spack", target, plat, "r",
                                   requested_by="dana", restricted=False)
    ids = {k: enq(*k) for k in (("rtx", "linux/amd64"), ("generic", "linux/amd64"),
                                ("generic", "linux/arm64"), ("vr", "linux/arm64"))}
    yield d, ids, enq
    d.close()


def run_deliver(d, tmp_path, calls):
    return deliver(d, registry="reg.example", sqsh_dir="/s", module_dir=str(tmp_path / "m"),
                   wrapper_dir=str(tmp_path / "w"), runner=calls.append)


def test_release_tag_is_the_one_naming_rule():
    assert release_tag("1", "generic", "linux/arm64") == "1-generic-arm64"
    assert release_tag("1", "generic") == "1-generic"
    assert release_tag("1", "rtx", "linux/amd64") == "1-rtx"


def test_generic_waits_for_both_arches(db, tmp_path):
    d, ids, _ = db
    publish(d, ids, ("rtx", "linux/amd64"), ("generic", "linux/amd64"))
    calls = []
    out = run_deliver(d, tmp_path, calls)
    assert out["manifests"] == [] and calls == []
    # rtx is in the module; generic is not (arm64 not published/delivered yet)
    assert [(r.version, r.targets) for r in releases(entries_from_db(d))] == [("2026.3", ("rtx",))]


def test_manifest_by_digest_and_refreshed_on_rebuild(db, tmp_path):
    d, ids, enq = db
    publish(d, ids, ("generic", "linux/amd64"), ("generic", "linux/arm64"))
    calls = []
    out = run_deliver(d, tmp_path, calls)
    assert out["manifests"] == ["reg.example/public/gromacs:2026.3-generic"]
    adds = [c[-1] for c in calls if c[:3] == ["podman", "manifest", "add"]]
    assert adds == [f"docker://reg.example/public/gromacs@{DA}"] * 2
    assert [(r.version, r.targets) for r in releases(entries_from_db(d))] == [
        ("2026.3", ("generic",))]
    calls.clear()
    assert run_deliver(d, tmp_path, calls)["manifests"] == []  # nothing changed
    # a rebuild of the amd64 side gets a new digest -> manifest pushed again
    new = enq("generic", "linux/amd64")
    publish(d, {"n": new}, "n", digest=DB_)
    calls.clear()
    assert run_deliver(d, tmp_path, calls)["manifests"]
    adds = [c[-1] for c in calls if c[:3] == ["podman", "manifest", "add"]]
    assert f"docker://reg.example/public/gromacs@{DB_}" in adds


def test_failure_is_isolated(db, tmp_path):
    d, ids, _ = db
    publish(d, ids, ("rtx", "linux/amd64"), ("generic", "linux/amd64"), ("generic", "linux/arm64"))

    def boom(argv):
        if argv[:3] == ["podman", "manifest", "push"]:
            raise subprocess.CalledProcessError(1, argv)
    out = deliver(d, registry="r", sqsh_dir="/s", module_dir=str(tmp_path / "m"),
                  wrapper_dir=str(tmp_path / "w"), runner=boom)
    assert out["errors"] and out["manifests"] == []
    assert (tmp_path / "m" / "gromacs" / "2026.3.lua").exists()  # modules still written


def test_refuses_missing_database(tmp_path):
    with pytest.raises(SystemExit):
        main(["--db", str(tmp_path / "nope.sqlite3"), "--registry", "r"])
    assert not (tmp_path / "nope.sqlite3").exists()


def test_generic_wrapper_picks_arch_specific_sqsh(db, tmp_path):
    d, ids, _ = db
    publish(d, ids, ("generic", "linux/amd64"), ("generic", "linux/arm64"))
    deliver(d, registry="r", sqsh_dir=str(tmp_path / "s"), module_dir=str(tmp_path / "m"),
            wrapper_dir=str(tmp_path / "w"), runner=lambda a: None)
    import platform as plat

    arch = "arm64" if plat.machine() == "aarch64" else "amd64"
    (tmp_path / "s" / "generic").mkdir(parents=True)
    (tmp_path / "s" / "generic" / f"gromacs-2026.3-generic-{arch}.sqsh").write_bytes(b"")
    fake = tmp_path / "enroot"
    fake.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
    fake.chmod(0o755)
    r = subprocess.run([str(tmp_path / "w" / "gromacs" / "2026.3" / "gmx")],
                       capture_output=True, text=True,
                       env={**os.environ, "APPARADISE_ENROOT": str(fake),
                            "APPARADISE_TARGET": "generic"})
    assert r.returncode == 0, r.stderr
    assert f"gromacs-2026.3-generic-{arch}.sqsh" in r.stdout


def test_wrapper_survives_arch_in_names(tmp_path):
    from apparadise.catalog import parse_entry
    from apparadise.modules import generate

    e = parse_entry({"name": "x", "source": "spack", "executables": ["x"], "versions": {
        "1.0-ARCH2": {"generic": {"status": "published", "digest": DA}}}})
    assert generate([e], module_dir=str(tmp_path / "m"), wrapper_dir=str(tmp_path / "w"),
                    sqsh_dir="/shared/ARCHIVE")


def test_stage_sqsh_generic_suffix(tmp_path):
    fake = tmp_path / "enroot"
    fake.write_text('#!/bin/bash\necho "$4" > "$3"\n')
    fake.chmod(0o755)
    stage = Path(__file__).resolve().parents[2] / "tools" / "stage-sqsh"
    r = subprocess.run([str(stage), "reg", "public", "g", "1", "generic", DA, str(tmp_path / "c")],
                       capture_output=True, text=True,
                       env={**os.environ, "ENROOT": str(fake), "ARCH": "aarch64"})
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "c" / "generic" / "g-1-generic-arm64.sqsh").exists()
