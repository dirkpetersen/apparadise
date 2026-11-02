"""EasyBuild hooks for the apparadise builder image (EASYBUILD_HOOKS)."""


def parse_hook(ec, *args, **kwargs):
    # GCCcore's nvptx offload compiler runs the image's CUDA 13 ptxas, which no longer knows
    # GCC's default nvptx target (sm_50): every GCCcore build failed at the end of `make`.
    # CUDA code is compiled with nvcc; nothing here uses GCC's own GPU offloading.
    if ec.name == "GCCcore" and ec.get("withnvptx"):
        ec["withnvptx"] = False
