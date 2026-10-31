"""tools/: shell syntax and stage-sqsh argument validation (with a fake enroot)."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "tools" / "stage-sqsh"
DIGEST = "sha256:" + "c" * 64


def test_tools_are_executable_and_parse():
    for f in (ROOT / "tools").iterdir():
        if f.is_file():
            assert os.access(f, os.X_OK), f
            if f.read_text().startswith("#!/bin/bash"):
                subprocess.run(["bash", "-n", str(f)], check=True)


@pytest.fixture
def fake_enroot(tmp_path):
    f = tmp_path / "enroot"
    # `enroot import --output FILE URI`: record the URI, create the file.
    f.write_text('#!/bin/bash\necho "$4" > "$3"\n')
    f.chmod(0o755)
    return f


def stage(fake, *args, env=None):
    return subprocess.run([str(STAGE), *args], capture_output=True, text=True,
                          env={**os.environ, "ENROOT": str(fake), **(env or {})})


def test_stage_writes_readonly_file_by_digest(tmp_path, fake_enroot):
    r = stage(fake_enroot, "registry.example.org", "public", "gromacs", "2026.3", "rtx",
              DIGEST, str(tmp_path / "c"))
    assert r.returncode == 0, r.stderr
    dest = tmp_path / "c" / "rtx" / "gromacs-2026.3-rtx.sqsh"
    assert dest.read_text().strip() == f"docker://registry.example.org#public/gromacs@{DIGEST}"
    assert dest.stat().st_mode & 0o777 == 0o444
    assert [p.name for p in (tmp_path / "c" / "rtx").iterdir()] == [dest.name]  # no temp left
    again = stage(fake_enroot, "registry.example.org", "public", "gromacs", "2026.3", "rtx",
                  DIGEST, str(tmp_path / "c"))
    assert again.returncode == 0 and "exists" in again.stdout


@pytest.mark.parametrize("args", [
    ("reg", "secret", "g", "1", "rtx", DIGEST),
    ("reg", "public", "g;x", "1", "rtx", DIGEST),
    ("reg", "public", "g", "1", "mars", DIGEST),
    ("reg", "public", "g", "1", "rtx", "latest"),
    ("https://reg", "public", "g", "1", "rtx", DIGEST),
])
def test_stage_rejects_bad_args(tmp_path, fake_enroot, args):
    r = stage(fake_enroot, *args, str(tmp_path))
    assert r.returncode == 2 and "invalid" in r.stderr


def test_restricted_images_staged_apart_and_private(tmp_path, fake_enroot):
    r = stage(fake_enroot, "reg", "restricted", "af3", "3.0.1", "rtx", DIGEST, str(tmp_path / "c"))
    assert r.returncode == 0, r.stderr
    dest = tmp_path / "c" / "restricted" / "rtx" / "af3-3.0.1-rtx.sqsh"
    assert dest.stat().st_mode & 0o777 == 0o440
    assert (tmp_path / "c" / "restricted").stat().st_mode & 0o777 == 0o750
    assert not (tmp_path / "c" / "rtx").exists()  # nothing in the public tree
