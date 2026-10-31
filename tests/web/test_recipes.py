"""containers/targets.yml and the recipe templates."""

import tomllib

import pytest
import yaml

from apparadise.recipes import RecipeError, load_targets, render, target_vars


def test_targets_file_shape():
    t = load_targets()
    assert set(t) == {"vr", "rtx", "generic"}
    assert t["rtx"]["cuda_arch"] == ["120"] and t["rtx"]["ptx_arch"] == "120"
    for name, spec in t.items():
        assert spec["cuda_version"]
        if spec["cuda_arch"]:  # PTX is always the highest arch (architecture doc rule)
            assert spec["ptx_arch"] == max(spec["cuda_arch"], key=int), name


def test_easybuild_optarch_is_full_flag():
    """A bare arch name becomes -x86-64-v3 and breaks gcc (FAILURES.md #15)."""
    for name, spec in load_targets().items():
        values = spec["easybuild_optarch"]
        for v in values.values() if isinstance(values, dict) else [values]:
            assert v.startswith("GCC:-m"), (name, v)


def test_vr_refuses_until_rubin_cc_is_known():
    with pytest.raises(RecipeError, match="Q13"):
        target_vars("vr")


def test_generic_needs_matching_platform():
    v = target_vars("generic", "linux/arm64")
    assert v["SPACK_TARGET"] == "aarch64" and v["CONDA_PLATFORM"] == "linux-aarch64"
    assert v["CUDA_CC_CSV"].startswith("7.0,7.5") and v["CUDA_CC_CSV"].endswith("12.0")
    with pytest.raises(RecipeError):
        target_vars("rtx", "linux/arm64")


def test_render_spack():
    text = render("spack", "gromacs", "2026.3", "rtx", registry="reg.example",
                  spec="gromacs@2026.3+mpi")
    doc = yaml.safe_load(text)["spack"]
    assert doc["specs"] == ["gromacs@2026.3+mpi +cuda cuda_arch=120 target=x86_64_v4"]
    assert doc["container"]["images"]["final"] == "reg.example/cache/base-runtime:13.0-rtx"


def test_render_easybuild():
    text = render("easybuild", "relion", "5.0.1", "rtx", registry="r",
                  easyconfig="RELION-5.0.1-foss-2025b.eb")
    opts = yaml.safe_load(text)["easyconfigs"][0]["RELION-5.0.1-foss-2025b.eb"]["options"]
    assert opts["optarch"] == "GCC:-march=x86-64-v4"
    assert opts["cuda-compute-capabilities"] == "12.0"
    assert "ignore-osdeps" not in opts


def test_render_pixi():
    text = render("pixi", "pytorch", "2.9.1", "generic", registry="r", platform="linux/amd64")
    doc = tomllib.loads(text)
    assert doc["workspace"]["platforms"] == ["linux-64"]
    assert doc["system-requirements"]["cuda"] == "12"
    assert doc["dependencies"]["pytorch"] == {"version": "==2.9.1", "build": "*cuda*"}


@pytest.mark.parametrize("kw", [{"spec": "gromacs; rm -rf /"}, {"spec": ""},
                                {"spec": "x\nspecs: []"}])
def test_render_rejects_bad_spec(kw):
    with pytest.raises(RecipeError):
        render("spack", "gromacs", "1", "rtx", registry="r", **kw)
