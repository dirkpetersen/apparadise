"""mock/real_catalog.py: this host's images and build logs become catalog build states."""

from apparadise.catalog import parse_entry
from mock.real_catalog import build_state, split

ID = "a" * 64


def test_split_keeps_dashes_in_the_package_name():
    assert split("nccl-tests-2.17.6-rtx", "nccl-tests") == ("2.17.6", "rtx")
    assert split("nccl-tests-2.17.6-rtx", "nccl") is None  # nccl's version must start with a digit
    assert split("gromacs-2026.1-rtx.3", "gromacs") is None


def test_images_logs_and_running_builds(tmp_path):
    (tmp_path / "pkg-relion-5.0.0-rtx.log").write_text("...\nEXIT=16 STAGE=build\n")
    (tmp_path / "pkg-relion-5.0.0-generic.log").write_text("still going\n")
    (tmp_path / "pkg-gromacs-2025.4-rtx.log").write_text("EXIT=1 STAGE=concretize\n")
    images = {"gromacs-2026.1-rtx": {"id": ID, "created": "2026-11-02T14:05:37Z"}}
    assert build_state("gromacs", images, tmp_path, "") == {"2026.1": {"rtx": {
        "status": "published", "digest": f"sha256:{ID}",
        "test": "built on an x86 host without a GPU; GPU test pending", "built": "2026-11-02"}}}
    running = "123 python localbuild.py easybuild relion 5.0.0 generic --platform linux/amd64"
    v = build_state("relion", {}, tmp_path, running)
    assert v == {"5.0.0": {"rtx": {"status": "failed", "stage": "build"},
                           "generic": {"status": "running"}}}
    entry = parse_entry({"name": "relion", "source": "easybuild", "versions": v}, "relion.yml")
    assert {b["status"] for b in entry["builds"]} == {"failed", "running"}
