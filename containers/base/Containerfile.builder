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
      build-essential gfortran git curl ca-certificates file patch unzip bzip2 xz-utils zstd \
      python3 python3-venv python3-pip pkg-config autoconf automake libtool autotools-dev \
      cmake ninja-build rdma-core libibverbs-dev librdmacm-dev libnuma-dev \
      lmod tcl squashfs-tools zlib1g-dev libssl-dev libbz2-dev liblzma-dev \
      libncurses-dev libreadline-dev gettext texinfo help2man locales \
 && rm -rf /var/lib/apt/lists/* \
 && locale-gen en_US.UTF-8

# EasyBuild: a regular (non-editable) install, so the easybuild namespace package resolves
# (FAILURES.md #3). Lmod comes from the distro package above (FAILURES.md #2, #4-#6).
RUN python3 -m venv /opt/easybuild \
 && /opt/easybuild/bin/pip install --no-cache-dir "easybuild==${EASYBUILD_VERSION}" \
 && ln -s /opt/easybuild/bin/eb /usr/local/bin/eb

# Spack at a pinned release; its build cache lives in the registry's `cache/` (Zot).
RUN git clone --depth 1 --branch "${SPACK_REF}" https://github.com/spack/spack.git /opt/spack \
 && ln -s /opt/spack/bin/spack /usr/local/bin/spack

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
    MODULEPATH=/opt/apps/modules/all

# Ubuntu's lmod package sets LMOD_CMD only in login shells (/etc/profile.d); a plain
# RUN does not read that, so it is set above. Fail here, not in the first build, if moved.
RUN test -x "$LMOD_CMD"

WORKDIR /work
