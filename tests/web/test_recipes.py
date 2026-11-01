"""containers/targets.yml and the recipe templates."""

import tomllib

import pytest
import yaml

from apparadise.recipes import RecipeError, load_targets, render, target_vars


def test_targets_file_shape():
    t = load_targets()
    assert t.pop("easybuild_toolchains") == ["foss,2026.1", "foss,2025b"]
    assert t.pop("e4s_release")
    assert set(t) == {"vr", "rtx", "generic"}
    assert t["rtx"]["cuda_arch"] == ["120"] and t["rtx"]["ptx_arch"] == "120"
    for name, spec in t.items():
        assert spec["cuda_version"]
        if spec["cuda_arch"]:  # PTX is always the highest arch (architecture doc rule)
            assert spec["ptx_arch"] == max(spec["cuda_arch"], key=int), name


def test_easybuild_optarch_is_full_flag():
    """A bare arch name becomes -x86-64-v3 and breaks gcc (FAILURES.md #15)."""
    for name, spec in load_targets().items():
        if not isinstance(spec, dict):  # easybuild_toolchains, e4s_release
            continue
        values = spec["easybuild_optarch"]
        for v in values.values() if isinstance(values, dict) else [values]:
            assert v.startswith("GCC:-m"), (name, v)


def test_vr_refuses_until_rubin_cc_is_known():
    with pytest.raises(RecipeError, match="Q13"):
        target_vars("vr")


def test_generic_needs_matching_platform():
    v = target_vars("generic", "linux/arm64")
    assert v["SPACK_TARGET"] == "aarch64" and v["CONDA_PLATFORM"] == "linux-aarch64"
    assert v["CUDA_CC_CSV"].startswith("7.5,8.0") and v["CUDA_CC_CSV"].endswith("12.0")
    with pytest.raises(RecipeError):
        target_vars("rtx", "linux/arm64")


def test_render_spack():
    text = render("spack", "gromacs", "2026.3", "rtx", registry="reg.example",
                  spec="gromacs@2026.3+mpi")
    doc = yaml.safe_load(text)["spack"]
    assert doc["specs"] == ["gromacs@2026.3+mpi +cuda cuda_arch=120 target=x86_64_v4"]
    assert doc["container"]["images"]["final"] == "reg.example/cache/base-runtime:13.0-rtx"
    e4s = doc["mirrors"]["e4s"]  # Q35
    assert e4s["url"].startswith("https://cache.e4s.io/") and e4s["signed"] is True


def test_render_easybuild():
    text = render("easybuild", "relion", "5.0.1", "rtx", registry="r",
                  easyconfig="RELION-5.0.1-foss-2025b.eb")
    opts = yaml.safe_load(text)["easyconfigs"][0]["RELION-5.0.1-foss-2025b.eb"]["options"]
    assert opts["optarch"] == "GCC:-march=x86-64-v4"
    assert opts["cuda-compute-capabilities"] == "12.0"
    assert "ignore-osdeps" not in opts
    assert opts["try-toolchain"] == "foss,2026.1" and opts["filter-deps"] == "CUDA"
    # Q34: the bump also moves dependencies to versions the new generation has
    assert opts["try-update-deps"] is True and opts["experimental"] is True


def test_render_pixi():
    text = render("pixi", "pytorch", "2.9.1", "generic", registry="r", platform="linux/amd64")
    doc = tomllib.loads(text)
    assert doc["workspace"]["platforms"] == ["linux-64"]
    assert doc["system-requirements"]["cuda"] == "13"  # CUDA 13.0 everywhere since Q38
    assert doc["dependencies"]["pytorch"] == {"version": "==2.9.1", "build": "*cuda*"}
    assert doc["dependencies"]["cuda-version"] == "<=13.0"  # never newer than the base


@pytest.mark.parametrize("kw", [{"spec": "gromacs; rm -rf /"}, {"spec": ""},
                                {"spec": "x\nspecs: []"}])
def test_render_rejects_bad_spec(kw):
    with pytest.raises(RecipeError):
        render("spack", "gromacs", "1", "rtx", registry="r", **kw)


def test_base_refs_pin_digest_when_known(monkeypatch):
    from apparadise import coordinator

    d = "sha256:" + "1" * 64
    targets = load_targets()
    targets["rtx"]["base_digests"] = {"builder": d, "runtime": None}
    monkeypatch.setattr(coordinator, "_targets", lambda: targets)
    assert coordinator.base_ref("builder", "rtx", "13.0") == f"cache/base-builder:13.0-rtx@{d}"
    assert coordinator.base_ref("runtime", "rtx", "13.0") == "cache/base-runtime:13.0-rtx"
    targets["rtx"]["base_digests"]["runtime"] = "latest"
    with pytest.raises(ValueError):
        coordinator.base_ref("runtime", "rtx", "13.0")


def test_targets_have_digest_slots():
    for name, spec in load_targets().items():
        if not isinstance(spec, dict):  # easybuild_toolchains, e4s_release
            continue
        assert set(spec["base_digests"]) == {"builder", "runtime"}, name


def test_render_pixi_without_build_marker():
    text = render("pixi", "cupy", "14.2.0", "rtx", registry="r", cuda_build_marker=False)
    assert tomllib.loads(text)["dependencies"]["cupy"] == {"version": "==14.2.0"}


@pytest.mark.parametrize("spec,line", [
    ("gromacs@2026.3+mpi", "gromacs@2026.3+mpi +cuda cuda_arch=120 target=x86_64_v4"),
    ("gromacs@2026.3 %gcc@14 ^fftw+openmp",
     "gromacs@2026.3 +cuda cuda_arch=120 target=x86_64_v4 %gcc@14 ^fftw+openmp"),
    ("gromacs%gcc@14", "gromacs +cuda cuda_arch=120 target=x86_64_v4 %gcc@14"),
    ("relion+cuda cuda_arch=90", "relion+cuda cuda_arch=90 target=x86_64_v4"),
])
def test_spack_cuda_variants_bind_to_the_root(spec, line):
    # In Spack 1.x anything after ^x or %x applies to x: the CUDA variants must come first.
    out = render("spack", "gromacs", "2026.3", "rtx", registry="r.example.org", spec=spec)
    assert f'  - "{line}"' in out


def test_dotted_cc():
    from apparadise.recipes import dotted_cc

    assert [dotted_cc(a) for a in ("120", "89", "90a", "120f", "70")] == [
        "12.0", "8.9", "9.0a", "12.0f", "7.0"]
    with pytest.raises(RecipeError):
        dotted_cc("sm_120")


def test_render_pixi_jax_requires_a_cuda_jaxlib():
    deps = tomllib.loads(render("pixi", "jax", "0.12.0.1", "rtx", registry="r"))["dependencies"]
    assert deps["jax"] == {"version": "==0.12.0.1"}
    assert deps["jaxlib"] == {"build": "*cuda*"}


def test_render_pixi_cellpose_requires_a_cuda_pytorch():
    deps = tomllib.loads(render("pixi", "cellpose", "4.1.1", "rtx", registry="r",
                                cuda_build_marker=False))["dependencies"]
    assert deps["cellpose"] == {"version": "==4.1.1"} and deps["pytorch"] == {"build": "*cuda*"}
