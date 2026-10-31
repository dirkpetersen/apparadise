"""Three-way index scanners (on tiny fake trees) and the resolver's six rules."""

import csv
import json

import pytest

from apparadise.db import Database
from apparadise.index import normalise, scan_conda, scan_easybuild, scan_spack, write_csv
from apparadise.resolver import candidates, resolve, resolve_name


@pytest.fixture
def spack_tree(tmp_path):
    base = tmp_path / "spack" / "repos" / "spack_repo" / "builtin" / "packages"
    for name, body in {
        "gromacs": 'class Gromacs(CMakePackage, CudaPackage):\n'
                   '    version("2026.3")\n    version("2025.4")\n',
        "py_torch": 'class PyTorch(PythonPackage, CudaPackage):\n    version("2.9.1")\n',
        "zlib": 'class Zlib(Package):\n    version("1.3.1")\n',
        "lammps": 'class Lammps(CMakePackage):\n'
                  '    variant("cuda", default=False)\n    version("20250722")\n',
    }.items():
        (base / name).mkdir(parents=True)
        (base / name / "package.py").write_text(body)
    return tmp_path / "spack"


@pytest.fixture
def eb_tree(tmp_path):
    root = tmp_path / "eb" / "easybuild" / "easyconfigs"
    files = {
        "r/RELION/RELION-5.0.1-foss-2025b-CUDA-12.9.1.eb": "version = '5.0.1'\n",
        "r/RELION/RELION-4.0.1-foss-2023a.eb": "version = '4.0.1'\n",
        "c/cryoDRGN/cryoDRGN-3.4.0-foss-2025a.eb": (
            "easyblock = 'PythonBundle'\nversion = '3.4.0'\n"
            "dependencies = [('CUDA', '12.6.0', '', SYSTEM)]\n"),
        "s/SAMtools/SAMtools-1.21-GCC-14.2.0.eb": "version = '1.21'\n",
    }
    for rel, text in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return tmp_path / "eb"


@pytest.fixture
def conda_files(tmp_path):
    def rd(recs):
        return {"packages": {}, "packages.conda": {f"{i}.conda": r for i, r in enumerate(recs)}}
    x86 = [
        {"name": "pytorch", "version": "2.9.1", "build": "cuda129_py312_0",
         "depends": ["python >=3.12"]},
        {"name": "gromacs", "version": "2026.3", "build": "nompi_cuda_h1", "depends": ["libgcc"]},
        {"name": "vllm", "version": "0.11.0", "build": "cuda129_py312_0", "depends": ["python"]},
        {"name": "samtools", "version": "1.21", "build": "h0", "depends": []},
    ]
    arm = [
        {"name": "pytorch", "version": "2.9.1", "build": "cuda129_py312_0", "depends": ["python"]},
        {"name": "gromacs", "version": "2026.3", "build": "nompi_cuda_h1", "depends": []},
    ]
    a, b = tmp_path / "x86.json", tmp_path / "arm.json"
    a.write_text(json.dumps(rd(x86)))
    b.write_text(json.dumps(rd(arm)))
    return {"linux-64": a, "linux-aarch64": b}


def by_name(items):
    return {i["upstream_name"]: i for i in items}


def test_normalise():
    assert normalise("py-torch", "spack") == "pytorch"
    assert normalise("PyTorch") == "pytorch"
    assert normalise("rapids_singlecell") == "rapids-singlecell"


def test_scan_spack(spack_tree):
    it = by_name(scan_spack(spack_tree))
    assert it["gromacs"]["has_cuda"] and it["gromacs"]["versions"] == ["2026.3", "2025.4"]
    assert it["py-torch"]["norm_name"] == "pytorch" and it["py-torch"]["is_python"]
    assert it["lammps"]["has_cuda"]  # a cuda variant counts
    assert not it["zlib"]["has_cuda"]


def test_scan_easybuild(eb_tree):
    it = by_name(scan_easybuild(eb_tree))
    assert it["RELION"]["has_cuda"] and it["RELION"]["versions"] == ["5.0.1", "4.0.1"]
    assert it["cryoDRGN"]["has_cuda"] and it["cryoDRGN"]["is_python"]  # CUDA dependency
    assert not it["SAMtools"]["has_cuda"]


def test_scan_conda(conda_files):
    it = by_name(scan_conda(conda_files))
    assert it["pytorch"]["arches"] == ["linux-64", "linux-aarch64"] and it["pytorch"]["is_python"]
    assert it["vllm"]["arches"] == ["linux-64"]  # x86-only CUDA build
    assert not it["samtools"]["has_cuda"]
    assert not it["gromacs"]["is_python"]


def test_csv(tmp_path, spack_tree, eb_tree, conda_files):
    items = scan_spack(spack_tree) + scan_easybuild(eb_tree) + scan_conda(conda_files)
    out = tmp_path / "cmp.csv"
    write_csv(out, items)
    rows = {r["name"]: r for r in csv.DictReader(out.open())}
    assert rows["pytorch"] == {"name": "pytorch", "spack": "py-torch", "easybuild": "",
                               "pixi": "pytorch", "sources": "2"}
    assert "samtools" not in rows


@pytest.fixture
def db(spack_tree, eb_tree, conda_files):
    d = Database(":memory:")
    d.index.replace_source("spack", scan_spack(spack_tree))
    d.index.replace_source("easybuild", scan_easybuild(eb_tree))
    d.index.replace_source("pixi", scan_conda(conda_files))
    yield d
    d.close()


def test_replace_source_removes_missing(db):
    assert db.index.replace_source("spack", [])["removed"] == 4
    assert "spack" not in db.index.counts()


def test_rule1_requested_source(db):
    r = resolve_name(db, "gromacs", requested_source="pixi")
    assert (r.rule, r.source, r.upstream_name) == (1, "pixi", "gromacs")
    r = resolve_name(db, "gromacs", requested_source="easybuild")
    assert r.failed and r.stage == "no-recipe"


def test_rule2_pixi_needs_python_and_both_arches(db):
    r = resolve_name(db, "pytorch")
    assert (r.rule, r.source) == (2, "pixi")
    # vLLM has a conda-forge CUDA build for x86 only -> not pixi; no other source -> port
    r = resolve_name(db, "vllm")
    assert (r.rule, r.source, r.cuda_port) == (5, "pixi", True)


def test_rule3_spack_before_easybuild_and_before_compiled_conda(db):
    r = resolve_name(db, "gromacs")  # conda-forge has CUDA gromacs, but it is not Python/ML
    assert (r.rule, r.source) == (3, "spack")


def test_rule4_easybuild(db):
    r = resolve_name(db, "relion")
    assert (r.rule, r.source, r.upstream_name) == (4, "easybuild", "RELION")
    assert r.versions[0] == "5.0.1"


def test_rule5_cpu_only_port(db):
    r = resolve_name(db, "samtools")
    assert (r.rule, r.source, r.cuda_port) == (5, "easybuild", True)


def test_rule6_nothing(db):
    r = resolve_name(db, "does-not-exist")
    assert (r.rule, r.failed, r.stage) == (6, True, "no-recipe")


def test_aliases_widen_lookup(db):
    assert candidates("torch", {"spack": "py-torch"}) == ["pytorch"]
    assert resolve_name(db, "torch").source == "pixi"


def test_unknown_requested_source():
    with pytest.raises(ValueError):
        resolve([], "conan")


def test_branch_names_are_not_versions():
    from apparadise.index import releases

    assert releases(["main", "develop", "2026.3", "2.0.%s", "master", "1.2"]) == ["2026.3", "1.2"]


def test_conda_cuda_detected_from_dependencies(tmp_path):
    rd = {"packages.conda": {"a.conda": {"name": "cupy", "version": "14.0.1", "build": "py312h0",
                                         "depends": ["python", "cuda-version >=12,<13"]}}}
    f = tmp_path / "r.json"
    f.write_text(json.dumps(rd))
    assert scan_conda({"linux-64": f})[0]["has_cuda"]


def test_compiled_hpc_code_with_python_bindings_goes_to_spack():
    entries = [
        {"source": "pixi", "upstream_name": "lammps", "versions": ["1"], "has_cuda": True,
         "is_python": True, "arches": ["linux-64", "linux-aarch64"]},
        {"source": "spack", "upstream_name": "lammps", "versions": ["1"], "has_cuda": True,
         "is_python": False, "arches": []},
    ]
    assert resolve(entries).source == "spack"
    entries[1]["upstream_name"] = "py-lammps"  # a py- recipe: the Python stack wins
    assert resolve(entries).source == "pixi"
