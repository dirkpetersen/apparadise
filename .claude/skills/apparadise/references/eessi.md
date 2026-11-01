# EESSI: reference for the apparadise decision tree

EESSI (European Environment for Scientific Software Installations, "easy", <https://www.eessi.io>)
is a shared, ready-built HPC software stack. It is built with EasyBuild, exposed through Lmod, and
streamed read-only to clients over CernVM-FS.

**EESSI is not a build source in apparadise.** The resolver order (requester's choice → pixi →
Spack `+cuda` → EasyBuild CUDA easyconfig → CUDA port → `no-recipe`) does not include it. This file
explains where EESSI sits *next to* apparadise: as a non-container module stack a site could also
mount, and as a reference for EasyBuild CUDA recipes known to work. Do not propose it as a new
resolver source or image base. That would be a product decision, and it belongs in
`QUESTIONS.md`.

Researched 2026-11-01 against eessi.io/docs (pages dated 2025-12 to 2026-10) and shallow clones of
`EESSI/software-layer`, `EESSI/software-layer-scripts` and `EESSI/docs` (HEAD of 2026-11-01).

---

## 1. What it is: three layers

| Layer | What | Maintained in |
|---|---|---|
| **Filesystem** | CernVM-FS (CVMFS): a read-only, HTTP-based, FUSE-mounted network file system with a Stratum-0, public Stratum-1 mirrors and local Squid proxies. The repo is `/cvmfs/software.eessi.io`. Infrastructure status is at status.eessi.io. | `EESSI/filesystem-layer` |
| **Compatibility** | Gentoo Prefix: a minimal userland (glibc, bash, …) installed in a prefix with Portage, so the stack does not depend on the host distro. It runs on any Linux, on macOS, and on Windows via WSL. | `EESSI/compatibility-layer` |
| **Software** | EasyBuild-built installations, optimised per CPU microarchitecture (and per GPU compute capability), exposed as **Lmod** modules. archspec-based autodetection picks the subtree that fits the host. | `EESSI/software-layer` (easystacks), `EESSI/software-layer-scripts` (init, hooks, bot) |

- The stack is versioned by date. Each version has its own compat layer (a Gentoo Prefix commit)
  and its own set of EasyBuild toolchains:

  | Version | Toolchains | Notes |
  |---|---|---|
  | 2023.06 | foss/2022b, 2023a, 2023b | First release (June 2023). Still the nominal "default". |
  | 2025.06 | foss/2024a, 2025a, 2025b | Adds zen5 and the cc100 and cc120 GPU targets. |
  | 2026.06 | foss/2026.1 | "Fully available", with software being added (docs page dated 2026-10-01). The `software-layer` repo holds only ~20 easystack files for it so far, against 144 for 2025.06 and 277 for 2023.06. Adds graniterapids, graviton4 and riscv64. |

- The project aims for **yearly versions** with new toolchains. Inside a version, software is
  added continuously: the stack is rolling, not a frozen release.
- Software arrives through GitHub PRs that add easystack entries to `EESSI/software-layer`. A bot
  (`eessi-bot-software-layer`) builds each entry for every CPU (and GPU) target, then the result
  is reviewed, ingested into CVMFS and published.
- The project says it covers "over 650" open-source projects (ISC 2026 listing). It is funded
  through the MultiXscale EuroHPC CoE. Its repos are GPL-2.0.
- Other repos: `dev.eessi.io` holds pre-release builds for MultiXscale projects, and
  `riscv.eessi.io` / `dev.eessi.io-riscv` hold RISC-V builds.

## 2. Targets

### CPU (docs `software_layer/cpu_targets`, dated 2026-09-03)

| Target | 2023.06 | 2025.06 | 2026.06 |
|---|---|---|---|
| aarch64/generic, a64fx, neoverse_n1, neoverse_v1 (Graviton3) | ✓ | ✓ | ✓ |
| **aarch64/nvidia/grace** (Neoverse V2 class) | ✓ | ✓ | ✓ |
| aarch64/aws/graviton4 | | | ✓ |
| x86_64/generic, haswell, skylake_avx512, cascadelake, icelake, sapphirerapids | ✓ | ✓ | ✓ |
| x86_64/amd/zen2, zen3, zen4 | ✓ | ✓ | ✓ |
| x86_64/amd/zen5 | | ✓ | ✓ |
| x86_64/intel/graniterapids, riscv64/generic | | | ✓ |

- There is **no separate `neoverse_v2` directory**. Grace *is* the Neoverse-V2 target.
- `archdetect` (`init/eessi_archdetect.sh` plus `init/arch_specs/eessi_arch_arm.spec`) matches the
  CPU vendor or implementer and the `/proc/cpuinfo` feature flags, then falls back to the best
  matching older target, and finally to `generic`.
- The Grace spec line is `"aarch64/nvidia/grace" "0x41" "sve2 sm3 sm4 svesm4"`, which requires
  CPU implementer 0x41 (Arm Ltd).
- **Vera (Olympus cores, Armv9.2) is not listed.** It is not known whether Vera reports
  implementer 0x41 or an NVIDIA ID (unverified). If it reports an NVIDIA ID, archdetect would
  probably drop to `aarch64/generic`, not `grace` (unverified). Override with
  `EESSI_SOFTWARE_SUBDIR_OVERRIDE=aarch64/nvidia/grace` (variable present in init scripts).
  apparadise has the same gap: `targets.yml` uses `neoverse_v2` for `vr` until archspec knows
  Vera (Q13).

### GPU (docs `software_layer/gpu_targets`, dated 2026-09-03)

EESSI builds only for the **major** compute capability (CC) X.0. Forward compatibility within a
major version covers X.Y. `N` means the combination is built natively; every other combination is
cross-built on CPU-only nodes.

| CC | 2023.06 | 2025.06 | 2026.06 | Example GPUs |
|---|---|---|---|---|
| 7.0 (sm_70) | ✓ | ✓ | – | V100 |
| 7.5 | – | – | ✓ | T4 |
| 8.0 (sm_80) | ✓ | ✓ | ✓ | A100 (also serves 8.6/8.9) |
| 9.0 (sm_90) | ✓ | ✓ | ✓ | H100/GH200 (native on Grace) |
| **10.0 (sm_100)** | – | ✓ | ✓ | B200/GB200; B300 (10.3) falls back to cc100 |
| **12.0 (sm_120)** | – | ✓ | ✓ | RTX PRO 6000 Blackwell, RTX 50xx (native on zen5) |

- 2026.06 drops CC 7.0 and adds 7.5. This matches CUDA 13 dropping Volta, since 2026.06 ships
  CUDA 13.3.0.
- A64FX has no GPU builds. AMD ROCm support is a work in progress: the 2025.06 accel easystacks
  already list HIP, rocBLAS, RCCL and ROCm 6.4.1.
- **Family and arch-specific targets:** EESSI-extend maps cc90 to `9.0a`, cc100 to `10.0f` and
  cc120 to `12.0f` (`EESSI-extend-easybuild.eb`), and EESSI's EasyBuild hooks strip the suffix
  where CUDA 12.8.0 or cuDNN binaries lack it (`eb_hooks.py`). This matches apparadise's "prefer
  `f` over `a`" rule. Whether EESSI also embeds PTX for the highest arch is unverified.
- **Accelerator detection:** `nvidia-smi --query-gpu=...,compute_cap` gives `accel/nvidia/ccXY`.
  If that directory does not exist, the init falls back by replacing the last digit with 0
  (cc103 → cc100, cc121 → cc120). If that does not exist either, no GPU modules are added. The
  override is `EESSI_ACCELERATOR_TARGET_OVERRIDE=accel/nvidia/cc120`, which must match
  `^accel/(nvidia/cc[0-9]+|amd/gfx[0-9a-f]+)$`.
- **Rubin:** the CC is unpublished (apparadise Q13). If it is a new major version (11.x or 13.x,
  for example), **no EESSI version has a matching `accel` dir yet**, and Vera Rubin nodes would
  get CPU-only modules until EESSI adds that target (unverified, inferred from the fallback logic
  above).

### CUDA versions shipped (from `software-layer` easystacks)

| Stack | CUDA | cuDNN | CUDA apps (beyond UCX-CUDA, UCC-CUDA, NCCL, OSU, CUDA-Samples) |
|---|---|---|---|
| 2023.06 | 12.1.1, 12.4.0 | 8.9.2 | GROMACS 2024.4, LAMMPS 2Aug2023 (Kokkos), ESPResSo 4.2.2, LightGBM, MrBayes, beagle-lib, SPH-EXA |
| 2025.06 | 12.6.0, 12.8.0, 12.9.1 | 9.5, 9.10, 9.15 | GROMACS 2025.4 and 2026.2, ESPResSo, Siesta 5.4.2, SPH-EXA, ollama 0.24 |
| 2026.06 | 13.3.0 | 9.23 | (toolkit only so far) |

**There are no CUDA builds of PyTorch, TensorFlow or JAX.** 2023.06 has only CPU PyTorch 2.1.2
and TensorFlow 2.13. The CUDA application catalogue is small and MD/physics-heavy. Check the
current list at <https://www.eessi.io/docs/available_software/>, where `(gpu)` marks CUDA
modules.

## 3. GPU support: host_injections, drivers and CUDA licensing

EESSI cannot ship the NVIDIA driver, which is tied to the host OS and GPU. Under the CUDA and cuDNN
EULAs it may redistribute **only the runtime libraries**. Its `CUDA` and `cuDNN` modules therefore
ship runtime libraries only. Non-redistributable parts such as `nvcc` are symlinks into
`/cvmfs/software.eessi.io/host_injections`.

`host_injections` is a **CVMFS variant symlink**. Its target is set in the local client config,
and defaults to `/opt/eessi`. Per-node-type targets are possible, for example for different
driver versions on gpu1 and gpu2 nodes.

```bash
# /etc/cvmfs/domain.d/eessi.io.local  (then: sudo cvmfs_config reload software.eessi.io)
EESSI_HOST_INJECTIONS=/shared/eessi/host_injections        # optional, default /opt/eessi
EESSI_NVIDIA_OVERRIDE_DEFAULT=/opt/eessi/nvidia            # MANDATORY for 2025.06+ driver links
# per-version alternative: EESSI_202506_NVIDIA_OVERRIDE=...
```

1. **Runtime (driver) support**, needed to *run* any CUDA module: run
   `/cvmfs/software.eessi.io/versions/$EESSI_VERSION/scripts/gpu_support/nvidia/link_nvidia_host_libraries.sh`.
   It symlinks the host's `libcuda.so*`, `libnvidia-ml.so*` and related libraries into the
   compat layer's `lib/nvidia` (2025.06+), or into `host_injections/2023.06/compat/.../lib`. The
   EESSI runtime linker searches there.
   - The script is idempotent. **Re-run it after every driver update.**
   - Options: `--show-ld-preload` and `--no-download`.
   - Manual fallback: use Apptainer's `nvliblist.conf` list together with `ldconfig -p`.
2. **Compile-time SDK**, needed only to *build* CUDA code, for example with EESSI-extend: run
   `.../scripts/gpu_support/nvidia/install_cuda_and_libraries.sh --accept-cuda-eula --accept-cudnn-eula [-t TMPDIR]`.
   - It installs every CUDA and cuDNN version listed in `easystacks/eessi-*CUDA*.yml` into
     `host_injections` using EasyBuild.
   - EasyBuild refuses to run as root, so make a non-root user own the `host_injections` dir.
   - Re-run it periodically to pick up new CUDA versions.
3. **The Lmod guard:** loading a module with the `gpu` property fails unless `libcuda.so` is found
   under the compat `lib/nvidia` path, or at `/.singularity.d/libs/libcuda.so` (Apptainer `--nv`).
   - The guard also checks the driver's CUDA version against the module's requirement.
   - Bypass it with `EESSI_OVERRIDE_GPU_CHECK=1`. The application will then fail at run time if
     the driver is really missing.
   - CUDA 13.x needs an R580+ driver (unverified for 13.3).
4. **Test:** `module load CUDA-Samples && deviceQuery`. The output
   `cudaGetDeviceCount returned 35` means `libcuda.so` was not found.

## 4. Access options

| Method | Root? | Notes |
|---|---|---|
| **Native CVMFS client** (recommended) | Yes, on every node | Install the `cvmfs` and `cvmfs-config-eessi` packages (RPM or DEB) and write `/etc/cvmfs/default.local` (`CVMFS_CLIENT_PROFILE=single`, `CVMFS_QUOTA_LIMIT=10000`). Run `cvmfs_config setup`. Uses autofs and FUSE. **Clusters should run local Squid proxies**, and optionally a private Stratum-1. The default cache is `/var/lib/cvmfs`; set `CVMFS_CACHE_BASE` to move it. |
| **EESSI container** (`eessi_container.sh` from `EESSI/software-layer-scripts`) | No | Needs Apptainer ≥1.0 or Singularity 3.7 with `--fusemount`. Pulls `ghcr.io/eessi/build-node:debian12` and mounts CVMFS *inside* the container through FUSE. Flags: `--nvidia run\|install\|all`, `-i` for the host_injections dir, `-r` for the repo, `--access rw` with overlay for builds. Used by EESSI's own build bot. |
| **cvmfsexec** (`github.com/cvmfs/cvmfsexec`) | No | Run `./makedist default`, then `./cvmfsexec software.eessi.io -- cmd`. Needs unprivileged user namespaces or `fusermount`. |
| **Apptainer `--fusemount`** | No | Mount `cvmfs-config.cern.ch` too, and bind a writable `/var/run/cvmfs`. |
| **Bind the host `/cvmfs` into a container** | Host needs CVMFS | Apptainer uses `APPTAINER_BIND=/cvmfs`, Docker uses `-v /cvmfs:/cvmfs:shared`. Also bind the `host_injections` target (e.g. `/opt/eessi`) for GPU use. |
| **NFS export of CVMFS** | Server only | CVMFS NFS server mode. Kubernetes can use NFS volumes (EESSI blog, 2025-12). |
| **Kubernetes** | Varies | Options are a hostPath from CVMFS-equipped nodes, NFS, or an in-pod mount, which needs `/dev/fuse`. |
| WSL, Lima, Apple `container` | No | Laptop routes (docs `getting_access/`). |

**Enroot/Pyxis on Slurm (as on BCM):** EESSI documents none of this.

- It should work if the node has a native CVMFS client and you pass
  `--container-mounts=/cvmfs:/cvmfs,/opt/eessi:/opt/eessi`, or wherever the host_injections and
  NVIDIA override targets point (unverified).
- With GPUs, the libraries linked by `link_nvidia_host_libraries.sh` point at *host* driver paths.
  Enroot's NVIDIA hook injects the driver at the container's own library paths, so the symlink
  targets may not resolve inside the container. Expect to need `EESSI_OVERRIDE_GPU_CHECK` and
  `LD_LIBRARY_PATH` tweaks (unverified).
- FUSE-in-container (eessi_container or cvmfsexec) inside an Enroot sandbox needs `/dev/fuse` and
  user namespaces (unverified for Enroot).
- In practice EESSI is a **host-level** module stack. It is not something to put inside apparadise
  images.

## 5. Cheat sheet

```bash
ls /cvmfs/software.eessi.io                 # README.eessi host_injections init versions → accessible

# Init (bash only, irreversible in this shell)
source /cvmfs/software.eessi.io/versions/2025.06/init/bash
# or via Lmod (bash/csh/fish/ksh/zsh); configures EESSI's own Lmod and loads the EESSI module
source /cvmfs/software.eessi.io/versions/2025.06/init/lmod/bash
# if a site Lmod >= 8.6 is already present: don't mix trees
module unuse $MODULEPATH; module use /cvmfs/software.eessi.io/init/modules; module load EESSI/2025.06
module load EESSI/2026.06                   # switch version

module avail                                # (gpu) marks CUDA modules
module spider GROMACS
module load GROMACS/2025.4-foss-2025b-CUDA-12.9.1
echo $EESSI_SOFTWARE_SUBDIR $EESSI_ACCELERATOR_TARGET     # e.g. x86_64/amd/zen4 accel/nvidia/cc120
/cvmfs/software.eessi.io/versions/2025.06/init/eessi_archdetect.sh -d cpupath   # debug detection
/cvmfs/software.eessi.io/versions/2025.06/init/eessi_archdetect.sh -d accelpath

# Overrides
export EESSI_SOFTWARE_SUBDIR_OVERRIDE=aarch64/nvidia/grace
export EESSI_ACCELERATOR_TARGET_OVERRIDE=accel/nvidia/cc120   # e.g. to build GPU code on a CPU-only node
export EESSI_OVERRIDE_GPU_CHECK=1                             # skip the Lmod driver check

# GPU, once per node (admin), and again after each driver update
sudo bash -c "echo 'EESSI_NVIDIA_OVERRIDE_DEFAULT=/opt/eessi/nvidia' >> /etc/cvmfs/domain.d/eessi.io.local"
sudo cvmfs_config reload software.eessi.io
/cvmfs/software.eessi.io/versions/$EESSI_VERSION/scripts/gpu_support/nvidia/link_nvidia_host_libraries.sh
/cvmfs/software.eessi.io/versions/$EESSI_VERSION/scripts/gpu_support/nvidia/install_cuda_and_libraries.sh \
    --accept-cuda-eula --accept-cudnn-eula            # only if users compile CUDA code
module load CUDA-Samples && deviceQuery
```

## 6. Building on top: EESSI-extend

`module load EESSI-extend/<EESSI_VERSION>-easybuild`, for example `EESSI-extend/2025.06-easybuild`.
It configures EasyBuild exactly as EESSI's own builds do (same hooks, umask 022, optarch), picks an
arch-specific prefix, prepends it to `MODULEPATH`, and loads EasyBuild. Dependencies already in
EESSI are reused, not rebuilt.

| Variable (precedence top-down) | Installs to |
|---|---|
| `EESSI_CVMFS_INSTALL` | The EESSI repo itself. For EESSI CVMFS admins only. |
| `EESSI_SITE_INSTALL` | `$EESSI_SITE_SOFTWARE_PATH`, default under `host_injections/…`. Change the root with `EESSI_SITE_SOFTWARE_PREFIX` *before* loading `EESSI`. Visible to everyone. |
| `EESSI_PROJECT_INSTALL` | A shared project dir, used when `EESSI_USER_INSTALL` is unset. Run `chmod g+s` on it. |
| `EESSI_USER_INSTALL` | A personal prefix. |
| (none) | `$HOME/EESSI` |

```bash
source /cvmfs/software.eessi.io/versions/2025.06/init/bash
export EESSI_PROJECT_INSTALL=/project/x/eessi
module load EESSI-extend/2025.06-easybuild
eb --show-config
eb GROMACS-2026.2-foss-2025b-CUDA-12.9.1.eb --robot     # needs the CUDA SDK in host_injections
module help EESSI-extend/2025.06-easybuild              # all knobs
```

- Run it **on the node type you target**. The prefix is per `EESSI_SOFTWARE_SUBDIR` and
  `accel/...`, so a heterogeneous cluster needs one build per architecture.
- To cross-build GPU code on a CPU-only node, set `EESSI_ACCELERATOR_TARGET_OVERRIDE`. EESSI-extend
  then sets `EASYBUILD_CUDA_COMPUTE_CAPABILITIES` (`12.0f` for cc120). EESSI's own bot builds most
  GPU combinations this way.
- `buildenv/default-foss-<tc>` gives compiler and linker wrappers for hand builds. It is
  experimental. Use the compat layer's `ldd`, not `/usr/bin/ldd`.
- For whole site stacks, the docs (`site_build/`, 2026-06) compare two approaches:
  1. EESSI-extend on a shared FS: easy to start, but manual, run once per arch, and slow to start
     from a parallel FS.
  2. Your own CVMFS repo, fed by the EESSI bot and `software-layer-scripts`: automated and
     scalable, but it needs a Stratum-0, an object store and the bot.
- **Spack on top of EESSI** (EESSI blog, 2026-01 and 2026-02): Spack ≥1.1 can declare EESSI
  installs as concrete externals with dependencies in `packages.yaml`. A small Spack
  compiler-wrapper patch is needed for EESSI's sysroot (spack#51582). This is a proof of concept,
  not a supported product.

## 7. Limits and gotchas

- **Every compute node needs CVMFS.** Running it means root, FUSE, autofs, a local cache
  partition, Squid proxies for a cluster, and outbound HTTP to Stratum-1s or a private mirror.
  Some sites forbid FUSE or extra daemons on compute nodes. The alternative, an NFS export of
  CVMFS, adds a single point of failure.
- **Cold starts:** the first access downloads metadata and files. Python-heavy stacks with many
  small files are slow on cold caches.
- **There is no container-image delivery.** EESSI ships a file system, not OCI images. You cannot
  `docker pull` or `enroot import` a package, sign it, or pin it by digest. Reproducibility comes
  from immutable paths within a version, not from image digests. Tooling such as DUCC and
  `unpacked.cern.ch` goes the other way, publishing containers *into* CVMFS.
- **The GPU setup is per node and per driver.** `link_nvidia_host_libraries.sh` must be re-run
  after each driver change. Heterogeneous drivers need separate `EESSI_HOST_INJECTIONS` targets.
  Forgetting this gives `cudaGetDeviceCount returned 35` or a refusal from the Lmod guard.
- **Compiling CUDA needs a local SDK install**, because of the EULA: one more admin step, and it
  must be refreshed per CUDA version.
- **The CUDA catalogue is thin.** There are no GPU builds of PyTorch, TensorFlow or JAX, and only
  a handful of GPU apps per stack. Only major CCs are built: no 8.6/8.9-specific code, and no
  `10.3` (B300) or `12.1` code, only their `.0` fallbacks.
- **No GPU target exists for Rubin**, and Vera CPU detection is undocumented (see §2).
- **The CUDA version lags.** 2025.06 tops out at CUDA 12.9.1 and 2026.06 at 13.3.0, and new CUDA
  arrives only with stack updates. New compilers and toolchains arrive only with yearly versions.
- **Don't mix module trees.** EESSI recommends clearing `MODULEPATH`, because its compat-layer
  glibc differs from the host's. Mixing with BCM's or a site's modules, or with host MPI, is
  fragile. Host MPI injection is possible, but it needs extra `host_injections` configuration.
- 2023.06 is still the nominal default, while 2026.06 is young and sparse. Pin the version
  explicitly.
- Ingestion is review-gated. A new easyconfig goes through a PR, a bot build on all targets, then
  ingestion, so expect days or weeks, not minutes.

## 8. Relation to apparadise

| Aspect | EESSI | apparadise |
|---|---|---|
| Product | A CVMFS file tree plus Lmod modules | A signed OCI image per `<name>:<version>-<target>` in Harbor/Zot, *also* exposed as `module load` (Enroot wrappers over `.sqsh`) |
| Sources | EasyBuild only | Exactly one of pixi/conda-forge, Spack or EasyBuild per image |
| Targets | ~15–19 CPU targets × CC x.0 (7.0/7.5–12.0) | 3 targets: `vr` (aarch64 + Rubin), `rtx` (x86-64-v4 + sm_120), `generic` (sm_70–sm_120) |
| Runtime deps | Gentoo Prefix compat layer from CVMFS | NGC base image (`nvcr.io`) inside the image |
| Driver | Host libs symlinked through `host_injections` | Injected by Enroot/Pyxis or the NVIDIA container toolkit at run time |
| Catalogue | Curated by the community, PR-gated | User requests, upvotes and a chatbot; failures are public |
| Infra | CVMFS client on every node | Harbor/Zot plus `.sqsh` on CephFS; nodes need only Enroot/Pyxis (BCM default) |
| Containers and K8s | Indirect (bind `/cvmfs`) | Native (Pyxis, Run:ai) |

**Where it complements apparadise (no product change implied):**

- **A proven easyconfig reference.** EESSI's easystacks (`EESSI/software-layer/easystacks/
  software.eessi.io/<ver>/accel/nvidia/*.yml`) list CUDA easyconfigs that built and passed tests on
  sm_90, sm_100 and sm_120, including on Grace. Two useful references:
  - `eb_hooks.py` in `software-layer-scripts`, a catalogue of real CUDA build workarounds (for
    example, CUDA 12.8.0 lacking `10.0f`/`12.0f`, and cuDNN binaries without `9.0a` code).
    Mine it for `recipes/repairs.yml`-style fixes on the EasyBuild path.
  - The `foss/2025b` toolchain matches apparadise's `easybuild_toolchain: "foss,2025b"`, so 2025.06
    easyconfigs are the closest match.
- **A non-container module stack** for CPU-side tools, compilers and libraries on nodes where a
  site already runs CVMFS. It can sit beside apparadise's Enroot-backed modules. It is a separate
  stack that users would load separately, not an apparadise source.
- **Portable dev and CI environments** on laptops, in the cloud, or on GitHub Actions
  (`using_eessi/eessi_in_ci`), when no container runtime or registry is wanted.
- **The CPU-only build model is validated.** EESSI cross-builds most GPU combinations without a GPU
  and runs native GPU tests only on a subset. That is the same split as apparadise's "build without
  GPU, test on real nodes".

**Where it overlaps or conflicts:**

- `module load gromacs` from two stacks: name collisions and confusion over `MODULEPATH`.
- EESSI's EasyBuild CUDA builds overlap apparadise's EasyBuild branch. apparadise adds images,
  signing, Run:ai, request and vote flows, and a failure DB. EESSI adds breadth across CPU targets.

**Vera Rubin and Blackwell specifics:**

- `rtx` (RTX PRO 6000, CC 12.0, x86-64): served by EESSI 2025.06 and 2026.06 `accel/nvidia/cc120`,
  built for `12.0f`. CPU subdirs: zen4/zen5, sapphirerapids or graniterapids.
- `vr`: Grace works today (`aarch64/nvidia/grace` + cc90/cc100). Vera CPU detection and any Rubin
  `accel` dir are **missing or unverified**. Do not plan on EESSI for `vr` until the Rubin CC is
  known (Q13) and EESSI publishes a target.
- `generic`: EESSI's `x86_64/generic` and `aarch64/generic` with cc70 (2023.06/2025.06) or
  cc75–cc120 (2026.06) are roughly comparable. EESSI ships no single fat binary across sm_70–120;
  it picks per-CC directories at load time.

## 9. Decision tree hooks

**Choose EESSI (outside apparadise) when:**

- The site already runs CVMFS clients, or can, on the target nodes, and users want plain Lmod
  modules without containers.
- The software is already in EESSI for the target CPU and CC. Check `module spider` or the
  available-software page. This applies especially to CPU-side scientific apps across many CPU
  generations.
- Users need the *same* stack on laptops, cloud VMs, CI runners and several HPC sites.
- You need a known-good **EasyBuild CUDA easyconfig plus hooks** to seed or debug an apparadise
  EasyBuild recipe (use it as a reference only).
- The hardware is Grace/GH200 (cc90) or B200 (cc100) on supported CPUs, where EESSI has native or
  validated builds.

**Don't choose EESSI when:**

- The deliverable must be an **OCI image** (Pyxis/Enroot, Run:ai or Kubernetes without CVMFS,
  signing, digest pinning, Harbor/Zot RBAC for restricted software).
- You need **PyTorch, TensorFlow or JAX with CUDA**, or other ML/AI stacks. EESSI has none; use
  pixi/conda-forge or NGC.
- The target is **Vera Rubin** (no Rubin CC target, Vera detection unverified), or the software
  needs CUDA newer than the stack ships.
- Compute nodes cannot run FUSE or CVMFS, or have no outbound or proxy HTTP access.
- The package is not in EESSI and needs to exist soon. EESSI's PR, bot and ingest loop is slow,
  and EESSI-extend builds land on shared FS per arch: workable, but outside apparadise's pipeline.
- The software is restricted or proprietary. EESSI is public open source; site installs would
  live in `host_injections` or a private CVMFS repo, outside apparadise's access model.
- You would need to mix EESSI with Spack or conda in one environment. apparadise's rule is one
  source per image, and EESSI warns against mixing module trees.

## 10. Authoritative links

- Docs home: <https://www.eessi.io/docs/>; overview: <https://www.eessi.io/docs/overview/>
- Versions: <https://www.eessi.io/docs/repositories/versions/>
- CPU targets: <https://www.eessi.io/docs/software_layer/cpu_targets/>
- GPU targets: <https://www.eessi.io/docs/software_layer/gpu_targets/>
- GPU setup: <https://www.eessi.io/docs/site_specific_config/gpu/>
- host_injections: <https://www.eessi.io/docs/site_specific_config/host_injections/>
- Available software: <https://www.eessi.io/docs/available_software/>
- Native install: <https://www.eessi.io/docs/getting_access/native_installation/>
- EESSI container: <https://www.eessi.io/docs/getting_access/eessi_container/>
- Environment setup: <https://www.eessi.io/docs/using_eessi/setting_up_environment/>
- EESSI-extend: <https://www.eessi.io/docs/using_eessi/building_on_eessi/>
- Site builds: <https://www.eessi.io/docs/site_build/overview/>
- Known issues (2023.06): <https://www.eessi.io/docs/known_issues/eessi-2023.06/>
- cvmfsexec and alternatives (tutorial): <https://www.eessi.io/docs/training-events/2025/tutorial-best-practices-cvmfs-hpc/access/alternatives/>
- Containers and CVMFS (tutorial): <https://www.eessi.io/docs/training-events/2025/tutorial-best-practices-cvmfs-hpc/containers/>
- EESSI on Kubernetes (blog, 2025-12) and Spack on top of EESSI (blog, 2026-02): <https://www.eessi.io/docs/blog/>
- Repos: <https://github.com/EESSI/software-layer> (easystacks),
  <https://github.com/EESSI/software-layer-scripts> (init, `eb_hooks.py`, `eessi_container.sh`,
  `EESSI-extend-easybuild.eb`, `scripts/gpu_support/nvidia/`),
  <https://github.com/EESSI/compatibility-layer>, <https://github.com/EESSI/filesystem-layer>
- cvmfsexec: <https://github.com/cvmfs/cvmfsexec>; status: <https://status.eessi.io>
