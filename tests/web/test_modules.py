"""Lmod files and Enroot wrappers from the catalog (no Enroot or Lmod needed)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from apparadise.catalog import load_dir, parse_entry
from apparadise.modules import Release, _lua_str, generate, lua_module, main, releases

ROOT = Path(__file__).resolve().parents[2]
D = "sha256:" + "b" * 64


def entry(**kw):
    e = {"name": "gromacs", "title": "GROMACS", "source": "spack", "description": "MD",
         "homepage": "https://www.gromacs.org", "executables": ["gmx_mpi", "gmx"],
         "versions": {"2026.3": {"rtx": {"status": "published", "digest": D},
                                 "vr": {"status": "running"}},
                      "2025.4": {"rtx": {"status": "published", "digest": D},
                                 "vr": {"status": "published", "digest": D}}}}
    e.update(kw)
    return parse_entry(e)


def test_releases_only_published_targets():
    rs = releases([entry()])
    assert [(r.version, r.targets) for r in rs] == [("2025.4", ("rtx", "vr")), ("2026.3", ("rtx",))]


def test_release_revalidates_names():
    with pytest.raises(ValueError):
        Release("x;rm", "X", "1", "", "", (), ("rtx",), False)
    with pytest.raises(ValueError):
        Release("x", "X", "1", "", "", ("a b",), ("rtx",), False)


def test_lua_string_cannot_be_closed():
    assert _lua_str("a]]b") == "[=[a]]b]=]"
    assert _lua_str("]=]]]") == "[==[]=]]]]==]"


def test_lua_module_content():
    r = releases([entry(description="Uses ]] and \" quotes")])[0]
    text = lua_module(r, "/shared/bin")
    assert 'prepend_path("PATH", [[/shared/bin/gromacs/2025.4]])' in text
    assert "conflict([[gromacs]])" in text
    assert "[=[Description: Uses ]] and \" quotes]=]" in text


@pytest.mark.skipif(not shutil.which("luac"), reason="luac not installed")
def test_lua_module_parses(tmp_path):
    r = releases([entry(description="weird ]] ]=] text")])[0]
    f = tmp_path / "m.lua"
    f.write_text(lua_module(r, "/shared/bin"))
    subprocess.run(["luac", "-p", str(f)], check=True)


def test_restricted_skipped_by_default(tmp_path):
    e = entry(visibility="restricted")
    assert generate([e], module_dir=str(tmp_path / "m"), wrapper_dir=str(tmp_path / "w"),
                    sqsh_dir="/x") == []
    assert generate([e], module_dir=str(tmp_path / "m"), wrapper_dir=str(tmp_path / "w"),
                    sqsh_dir="/x", include_restricted=True)


@pytest.fixture
def tree(tmp_path):
    sqsh = tmp_path / "sqsh"
    for t in ("rtx", "vr"):
        (sqsh / t).mkdir(parents=True)
        (sqsh / t / f"gromacs-2025.4-{t}.sqsh").write_bytes(b"")
    (sqsh / "rtx" / "gromacs-2026.3-rtx.sqsh").write_bytes(b"")
    written = generate([entry()], module_dir=str(tmp_path / "modules"),
                       wrapper_dir=str(tmp_path / "bin"), sqsh_dir=str(sqsh))
    # A fake enroot that prints its arguments, one per line.
    fake = tmp_path / "fake-enroot"
    fake.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
    fake.chmod(0o755)
    return tmp_path, written, fake


def run(wrapper, fake, target=None, args=(), cwd=None):
    env = {**os.environ, "APPARADISE_ENROOT": str(fake)}
    env.pop("APPARADISE_TARGET", None)
    if target:
        env["APPARADISE_TARGET"] = target
    return subprocess.run([str(wrapper), *args], env=env, capture_output=True, text=True, cwd=cwd)


def test_files_written_with_modes(tree):
    tmp, written, _ = tree
    assert len(written) == 2 * 3  # two versions x (one .lua + two wrappers)
    w = tmp / "bin" / "gromacs" / "2025.4" / "gmx_mpi"
    assert w.stat().st_mode & 0o777 == 0o755
    assert (tmp / "modules" / "gromacs" / "2025.4.lua").stat().st_mode & 0o777 == 0o644
    subprocess.run(["bash", "-n", str(w)], check=True)
    # idempotent: nothing changes on a second run
    assert generate([entry()], module_dir=str(tmp / "modules"), wrapper_dir=str(tmp / "bin"),
                    sqsh_dir=str(tmp / "sqsh")) == []


def test_wrapper_runs_image_for_forced_target(tree):
    tmp, _, fake = tree
    w = tmp / "bin" / "gromacs" / "2025.4" / "gmx_mpi"
    r = run(w, fake, "vr", ["mdrun", "-s", "a b.tpr"], cwd=tmp)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert lines[:3] == ["start", "--", str(tmp / "sqsh" / "vr" / "gromacs-2025.4-vr.sqsh")]
    assert lines[-4:] == ["gmx_mpi", "mdrun", "-s", "a b.tpr"]  # arguments survive intact
    assert str(tmp) in lines  # working directory passed through


def test_wrapper_refuses_missing_target(tree):
    tmp, _, fake = tree
    w = tmp / "bin" / "gromacs" / "2026.3" / "gmx"  # only rtx published
    r = run(w, fake, "vr")
    assert r.returncode == 1 and "no image for this node" in r.stderr


def test_wrapper_reports_unstaged_image(tree):
    tmp, _, fake = tree
    (tmp / "sqsh" / "rtx" / "gromacs-2026.3-rtx.sqsh").unlink()
    r = run(tmp / "bin" / "gromacs" / "2026.3" / "gmx", fake, "rtx")
    assert r.returncode == 1 and "not staged" in r.stderr


def test_wrapper_auto_pick_on_this_host(tree):
    """Without APPARADISE_TARGET: aarch64 -> vr; x86 with an sm_120+ GPU -> rtx; else generic.
    This release has rtx and vr only, so a node that wants generic gets a clear error."""
    import platform

    tmp, _, fake = tree
    r = run(tmp / "bin" / "gromacs" / "2025.4" / "gmx", fake)
    if platform.machine() == "aarch64":
        assert r.returncode == 0 and "gromacs-2025.4-vr.sqsh" in r.stdout
    elif r.returncode == 0:
        assert "gromacs-2025.4-rtx.sqsh" in r.stdout  # this host has a Blackwell GPU
    else:
        assert "wanted 'generic'" in r.stderr


def test_cli_dry_run(tmp_path, capsys):
    assert main(["--catalog", str(ROOT / "mock" / "catalog"), "--module-dir", str(tmp_path / "m"),
                 "--wrapper-dir", str(tmp_path / "w"), "--sqsh-dir", "/s", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "gromacs/2026.3.lua" in out and "alphafold3" not in out
    assert not (tmp_path / "m").exists()


def test_repo_catalog_generates():
    assert isinstance(generate(load_dir(ROOT / "catalog"), module_dir="/m", wrapper_dir="/w",
                               sqsh_dir="/s", dry_run=True), list)


def test_restricted_wrapper_points_at_restricted_tree(tmp_path):
    r = releases([entry(visibility="restricted")])[0]
    from apparadise.modules import wrapper_script

    text = wrapper_script(r, "gmx", "/shared/containers")
    assert "/shared/containers/restricted/rtx/gromacs-" in text
