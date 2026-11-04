# Builder base: compilers, CUDA toolkit, RDMA headers and the three build tools, preinstalled
# and tested once, so no build ever bootstraps its own toolchain on a host OS.
# Lessons: docs/lessons/easybuild-host-build.md.
#
#   podman build --platform linux/amd64 --build-arg CUDA_FULL=13.0.3 --build-arg TARGET=rtx \
#     -f Containerfile.builder -t $REGISTRY/cache/base-builder:13.0-rtx .
ARG CUDA_FULL=13.0.3
ARG BASE=nvcr.io/nvidia/cuda:${CUDA_FULL}-devel-ubuntu24.04
FROM ${BASE}

ARG TARGET=rtx
# Pinned tool versions: bump deliberately, together with docs/stack.md.
ARG SPACK_REF=v1.2.2
ARG EASYBUILD_VERSION=5.4.2
ARG PIXI_VERSION=0.83.0

LABEL io.apparadise.target="${TARGET}" io.apparadise.role="builder"

# Ubuntu 24.04's GCC 13 and glibc 2.39 avoid the C23 / _Generic clashes seen with the
# Ubuntu 26.04 host (FAILURES.md #10-#21). OS deps such as libibverbs-dev are installed
# here, so --ignore-osdeps is never needed (Q10). autotools-dev ships a current
# config.guess/config.sub, so nothing downloads them at build time (Q11).
# APT::Sandbox::User=root: apt's download helper otherwise switches to the _apt user, which
# a rootless builder with a single UID mapping (no /etc/subuid range) cannot do.
RUN printf 'APT::Sandbox::User "root";\n' > /etc/apt/apt.conf.d/00apparadise-sandbox \
 && apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      build-essential gfortran git curl openssh-client ca-certificates file patch unzip bzip2 xz-utils zstd \
      python3 python3-venv python3-pip pkg-config autoconf automake libtool autotools-dev \
      cmake ninja-build rdma-core libibverbs-dev librdmacm-dev libnuma-dev \
      lmod tcl squashfs-tools zlib1g-dev libssl-dev libbz2-dev liblzma-dev \
      libncurses-dev libreadline-dev gettext texinfo help2man locales \
 && rm -rf /var/lib/apt/lists/* \
 && locale-gen en_US.UTF-8

# EasyBuild: a regular (non-editable) install, so the easybuild namespace package resolves
# (FAILURES.md #3). Lmod comes from the distro package above (FAILURES.md #2, #4-#6).
RUN python3 -m venv /opt/easybuild \
 && /opt/easybuild/bin/pip install --no-cache-dir "easybuild==${EASYBUILD_VERSION}" PyYAML archspec \
 && ln -s /opt/easybuild/bin/eb /usr/local/bin/eb

# Spack at a pinned release; its build cache lives in the registry's `cache/` (Zot).
# Its package recipes (spack-packages, branch releases/v2026.06 for Spack 1.2.2) are
# fetched here, not by every build: one base image digest = one recipe snapshot. The index
# must scan the same branch (docs/operations/go-live.md, index refresh), or it offers versions builders lack.
# `spack bootstrap now`: the solver (clingo) is installed here once; otherwise every job's
# concretize and build stage compiled it from source again.
RUN git clone --depth 1 --branch "${SPACK_REF}" https://github.com/spack/spack.git /opt/spack \
 && ln -s /opt/spack/bin/spack /usr/local/bin/spack \
 && { spack repo update || spack list zlib; } >/dev/null 2>&1 \
 && spack repo list && spack versions --safe zlib | head -1 \
 && spack bootstrap now

# pixi: one static binary.
RUN arch="$(uname -m)" \
 && curl -fsSL "https://github.com/prefix-dev/pixi/releases/download/v${PIXI_VERSION}/pixi-${arch}-unknown-linux-musl.tar.gz" \
    | tar -xz --no-same-owner -C /usr/local/bin pixi

# Build trees on the builder's scratch volume, never on a small root filesystem.
ENV APPARADISE_TARGET=${TARGET} \
    LANG=en_US.UTF-8 \
    TMPDIR=/scratch/tmp \
    EASYBUILD_BUILDPATH=/scratch/eb-build \
    EASYBUILD_SOURCEPATH=/scratch/sources \
    EASYBUILD_MODULES_TOOL=Lmod \
    LMOD_CMD=/usr/share/lmod/lmod/libexec/lmod \
    EASYBUILD_DOWNLOAD_TIMEOUT=30 \
    EB_PYTHON=/opt/easybuild/bin/python \
    MODULEPATH=/opt/apps/modules/all \
    OMPI_ALLOW_RUN_AS_ROOT=1 \
    OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1

# OMPI_ALLOW_RUN_AS_ROOT*: EasyBuild's OpenMPI sanity check runs mpirun, which refuses uid 0
# (and needs an ssh binary even on one node: openssh-client above);
# here that is the rootless builder's mapped user, not host root. Builder only.
# The eb wrapper looks for `python` on PATH, where only the system python3 is: EB_PYTHON
# names the venv's. Ubuntu's lmod package sets LMOD_CMD only in login shells (/etc/profile.d); a plain
# RUN does not read that, so it is set above. Fail here, not in the first build, if moved.
# TMPDIR and the EasyBuild paths must exist even without a mounted scratch volume
# (the concretize stage runs without one).
RUN test -x "$LMOD_CMD" && eb --version && /opt/easybuild/bin/python -c "import yaml, archspec" \
 && mkdir -p /scratch/tmp /scratch/eb-build /scratch/sources && chmod 1777 /scratch/tmp

# No GPU driver here, only CUDA's stub libcuda.so. Packages that call the driver API (LAMMPS
# GPU, GROMACS) link a shared library that needs libcuda.so.1, and the final link fails
# with "undefined reference to cuModuleLoad". Spack clears LIBRARY_PATH, so the stub dir goes
# to ld.so.conf, which GNU ld also searches. Builder only: base-runtime never sees the stub.
RUN ln -sf libcuda.so /usr/local/cuda/lib64/stubs/libcuda.so.1 \
 && echo /usr/local/cuda/lib64/stubs > /etc/ld.so.conf.d/zz-cuda-stubs.conf && ldconfig

# CUDA 13 has only the header-only NVTX3 (include/nvtx3/). Older code (MotionCor3) still has
# `#include <nvToolsExt.h>` and `-lnvToolsExt`: forward each header. The library name is a
# linker script that links libnvtx3interop only if a symbol is used (header-only NVTX3 uses
# none): that library has a bogus DT_AUXILIARY "visibility=hidden", so ldd prints "not found"
# and EasyBuild's sanity check fails every binary linked to it (MotionCor3).
RUN cd /usr/local/cuda/include && for f in nvtx3/nvToolsExt*.h; do \
      [ -e "${f#nvtx3/}" ] || echo "#include <$f>" > "${f#nvtx3/}"; done \
 && echo 'INPUT(AS_NEEDED(libnvtx3interop.so.1))' > /usr/local/cuda/lib64/libnvToolsExt.so

# CUDA 13 moved the CCCL headers (cuda/, thrust/, cub/) to include/cccl/. nvcc finds them,
# but C++ files compiled by g++ with only -I$CUDA/include do not (NCCL 2.26: cuda/atomic).
RUN cd /usr/local/cuda/include && for d in cccl/*; do \
      [ -e "${d#cccl/}" ] || ln -s "$d" "${d#cccl/}"; done && test -e cuda/atomic

COPY eb-hooks.py /opt/easybuild/hooks.py
ENV EASYBUILD_HOOKS=/opt/easybuild/hooks.py

WORKDIR /work
