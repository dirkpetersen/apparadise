"""EasyBuild hooks for the apparadise builder image (EASYBUILD_HOOKS)."""


def parse_hook(ec, *args, **kwargs):
    # GCCcore's nvptx offload compiler runs the image's CUDA 13 ptxas, which no longer knows
    # GCC's default nvptx target (sm_50): every GCCcore build failed at the end of `make`.
    # CUDA code is compiled with nvcc; nothing here uses GCC's own GPU offloading.
    if ec.name == "GCCcore" and ec.get("withnvptx"):
        ec["withnvptx"] = False
    # Perl's own test suite fails in a rootless build container: Net-Ping's ICMP tests see
    # UID 0 and open raw sockets, which the container cannot (alphafold3, 5.38.2).
    if ec.name == "Perl":
        ec["runtest"] = False
    # LLVM's lit suite has tests that expect "Permission denied" on a read-only file; as
    # root in the build container the write works, so 4 tests fail (relion, LLVM 16.0.6).
    if ec.name == "LLVM":
        ec["test_suite_ignore_patterns"] = [*(ec.get("test_suite_ignore_patterns") or []),
                                            *LLVM_ROOT_TESTS]
    # CUDA 13 drops the unversioned PFN_cu* driver typedefs; UCX 1.16 uses one of them
    # (alphafold3). The _v11070 name exists in CUDA 11.7 to 13, so the define is safe for all.
    if ec.name == "UCX-CUDA":
        ec["preconfigopts"] = (
            'export CPPFLAGS="$CPPFLAGS -DPFN_cuMemGetHandleForAddressRange='
            'PFN_cuMemGetHandleForAddressRange_v11070" && ' + (ec.get("preconfigopts") or ""))
    # The MPI GitLab answers downloads with an HTML anti-bot page (HTTP 200), so EasyBuild
    # never tries a second URL and the checksum fails: every foss toolchain needs FlexiBLAS.
    # The group's own web server has the same files (checksums match for 3.2.0 to 3.5.0).
    # NCCL before 2.27 hard-codes -std=c++11 (makefiles/common.mk, for g++ and nvcc);
    # CUDA 13's libcu++ refuses anything below C++17.
    if ec.name == "NCCL" and tuple(int(x) for x in ec["version"].split(".")[:2]) < (2, 27):
        ec["prebuildopts"] = ("sed -i 's/-std=c++11/-std=c++17/' makefiles/common.mk && "
                              + (ec.get("prebuildopts") or ""))
    # LLVM before 20 knows no Blackwell GPU (sm_100, sm_120), so its OpenMP offload runtime
    # stops configure ("LIBOMPTARGET: Unknown GPU architecture 'sm_100'", Clang 18.1.8 for
    # alphafold3). Our packages use Clang as a compiler, not for OpenMP GPU offload.
    if ec.name in ("Clang", "LLVM") and int(ec["version"].split(".")[0]) < 20:
        ec["build_openmp_offload"] = False
    # CUDA 13 dropped the unversioned PFN_cu* typedefs and changed driver signatures
    # (cuCtxCreate) that NCCL looks up at run time: only NCCL 2.27.7 and later support it.
    # containers/base/easyconfigs/ has 2.27.7 for toolchains EasyBuild ships none for.
    for key in ("dependencies", "builddependencies"):
        deps = ec.get(key) or []
        new = [(d[0], NCCL_CUDA13, *d[2:]) if _old_nccl(d) else d for d in deps]
        if new != deps:
            ec[key] = new
    # CUDA 13 removed cudaDeviceProp.clockRate; magma only prints it (interface.cpp; the
    # second use is in the HIP branch).
    if ec.name == "magma":
        ec["preconfigopts"] = (
            "sed -i '0,/prop\\.clockRate/s//[\\&]{ int r = 0; cudaDeviceGetAttribute("
            "\\&r, cudaDevAttrClockRate, dev); return r; }()/' "
            "%(start_dir)s/interface_cuda/interface.cpp && " + (ec.get("preconfigopts") or ""))
    # A Bundle that sets its URLs only in default_component_specs (SentencePiece 0.2.0) never
    # passes them to EasyBuild 5.4's download step, which then tries only sources.easybuild.io.
    specs = ec.get("default_component_specs") or {}
    if ec.get("easyblock") == "Bundle" and not ec.get("source_urls") and specs.get("source_urls"):
        ec["source_urls"] = list(specs["source_urls"])
    _mirror_first(ec, "gitlab.mpi-magdeburg.mpg.de/", FLEXIBLAS_MIRROR)


LLVM_ROOT_TESTS = [
    "error-opening-permission.test",  # llvm-ar, llvm-ranlib
    "llvm-dwarfdump/X86/output.s",
    "llvm-ifs/fail-file-write.test",
]
NCCL_CUDA13 = "2.27.7"


def _old_nccl(dep):
    return (isinstance(dep, tuple) and dep[0] == "NCCL"
            and tuple(int(x) for x in dep[1].split(".")) < (2, 27, 7))


FLEXIBLAS_MIRROR = "https://csc.mpi-magdeburg.mpg.de/mpcsc/software/flexiblas/"


def _mirror_first(ec, host, mirror):
    def fix(urls):
        return [mirror, *urls] if any(host in u for u in urls) else urls

    urls = list(ec.get("source_urls") or [])
    if fix(urls) != urls:
        ec["source_urls"] = fix(urls)
    comps = ec.get("components") or []
    if any(len(c) > 2 for c in comps):
        ec["components"] = [
            (*c[:2], {**c[2], "source_urls": fix(list(c[2].get("source_urls") or []))}, *c[3:])
            if len(c) > 2 else c for c in comps]


def pre_configure_hook(self, *args, **kwargs):
    # UCX-CUDA's easyblock picks its plugins from the easyconfig's dependency names, but our
    # builds filter out CUDA (the image has it), so the list is empty: the sanity check then
    # fails and the module would not enable the CUDA transports. Configure still finds CUDA
    # through EBROOTCUDA, so name the plugins it builds.
    if self.name == "UCX-CUDA" and not getattr(self, "plugins", None):
        uct_cuda = ["gdrcopy"] if "GDRCopy" in self.cfg.dependency_names() else []
        self._plugins = {"ucm": ["cuda"], "uct": ["cuda"], "ucx_perftest": ["cuda"],
                         **({"uct_cuda": uct_cuda} if uct_cuda else {})}
