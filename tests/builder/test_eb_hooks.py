import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "eb_hooks", Path(__file__).parents[2] / "containers" / "base" / "eb-hooks.py")
hooks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hooks)


class EC(dict):
    def __init__(self, name, **kw):
        super().__init__(kw)
        self.name = name


def test_flexiblas_component_tries_the_mirror_first():
    gitlab = "https://gitlab.mpi-magdeburg.mpg.de/api/v4/projects/386/packages/generic/flexiblas-source/v3.3.1/"
    ec = EC("FlexiBLAS", components=[
        ("FlexiBLAS", "3.3.1", {"source_urls": [gitlab], "sources": ["x.tar.gz"]}),
        ("LAPACK", "3.11.0", {"source_urls": ["https://github.com/Reference-LAPACK/lapack/archive/"]}),
    ])
    hooks.parse_hook(ec)
    flexi, lapack = ec["components"]
    assert flexi[2]["source_urls"] == [hooks.FLEXIBLAS_MIRROR, gitlab]
    assert flexi[2]["sources"] == ["x.tar.gz"]
    assert lapack[2]["source_urls"] == ["https://github.com/Reference-LAPACK/lapack/archive/"]
    assert "source_urls" not in ec


def test_other_easyconfigs_keep_their_urls():
    ec = EC("zlib", source_urls=["https://zlib.net/"])
    hooks.parse_hook(ec)
    assert ec["source_urls"] == ["https://zlib.net/"]
    assert "components" not in ec


def test_llvm_ignores_the_tests_that_fail_as_root():
    ec = EC(name="LLVM", test_suite_ignore_patterns=["keep"])
    hooks.parse_hook(ec)
    assert ec["test_suite_ignore_patterns"][0] == "keep"
    assert "llvm-dwarfdump/X86/output.s" in ec["test_suite_ignore_patterns"]


def test_ucx_cuda_maps_the_dropped_cuda_typedef():
    ec = EC("UCX-CUDA", preconfigopts="x && ")
    hooks.parse_hook(ec)
    assert "PFN_cuMemGetHandleForAddressRange_v11070" in ec["preconfigopts"]
    assert ec["preconfigopts"].endswith("&& x && ")


class Block:
    def __init__(self, name, deps, plugins=None):
        self.name, self._plugins = name, plugins
        self.cfg = type("Cfg", (), {"dependency_names": lambda _: deps})()

    @property
    def plugins(self):
        return self._plugins


def test_ucx_cuda_names_its_plugins_when_cuda_is_filtered():
    block = Block("UCX-CUDA", ["UCX", "GDRCopy"], plugins={})
    hooks.pre_configure_hook(block)
    assert block.plugins["uct"] == ["cuda"]
    assert block.plugins["uct_cuda"] == ["gdrcopy"]
    other = Block("UCX-CUDA", ["UCX", "CUDA"], plugins={"uct": ["cuda"]})
    hooks.pre_configure_hook(other)
    assert other.plugins == {"uct": ["cuda"]}
