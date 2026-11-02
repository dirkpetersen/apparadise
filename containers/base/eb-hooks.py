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
