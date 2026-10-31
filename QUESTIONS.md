# Questions for the owner

Each question records the default I am working with until you answer. Correct a question by
editing its **Answer** line, and I will follow it and update the code.

Format: `Qn (date) - question`, then **Default** (what I assumed) and **Answer** (yours).

---

## Q1 (2026-10-31): What is the full DN of the RCO-Admins group?

**Default:** `LDAP_ADMIN_GROUP_DN` is REQUIRED in `.env.default` and left empty. The demo uses
`CN=RCO-Admins,OU=Groups,DC=example,DC=org`.
**Answer:**

## Q2 (2026-10-31): Which AD group may sign in at all?

**Default:** anyone in `LDAP_REQUIRED_GROUP_DN` (REQUIRED). If every domain user should be
allowed in, set it to the Domain Users group DN.
**Answer:**

## Q3 (2026-10-31): Does phase 1 need a read-only catalog page?

The roadmap puts catalog pages in phase 3. But phase 1's exit ("users run them without help")
is easier to reach if users can see what exists and how to load it.
**Default:** phase 1 includes a read-only catalog (list, search and package page, with module
and srun commands), filled from a hand-maintained `catalog/` folder of YAML files. Votes,
requests and the failure DB stay in phase 3.
**Answer:**

## Q4 (2026-10-31): Which packages are the phase 1 "top 20"?

**Default:** a provisional list in `catalog/` covers both targets and all three sources:
- Spack: GROMACS, LAMMPS, OpenMPI, UCX, nccl-tests, OSU micro-benchmarks, HPL, Kokkos
- EasyBuild: AlphaFold 3, RELION, cryoDRGN, MotionCor3, Cellpose
- pixi: PyTorch, JAX, vLLM, RAPIDS (cuDF/cuML), rapids-singlecell, CuPy, JupyterLab

Each entry stays `status: planned` until it has been built.
**Answer:**

## Q5 (2026-10-31): What base OS should the per-target base images use?

The EasyBuild session (`/storage/t3/test/motuz/eb/FAILURES.md`) lost a lot of time to the host's
Ubuntu 26.04 / GCC 15 / glibc (C23 defaults, `_Generic` clashes in gnulib, gprofng).
**Default:** NGC `nvcr.io/nvidia/cuda:<ver>-devel-ubuntu24.04` for builder images and
`...-runtime-ubuntu24.04` for runtime images. Never build on the host OS. Images are multi-arch
(amd64 + arm64), with the CUDA version pinned by digest.
**Answer:**

## Q6 (2026-10-31): Which CUDA version is the platform baseline?

**Default:** CUDA 13.0 for both targets. The `generic` target keeps `sm_70`–`sm_120`, but
CUDA 13 dropped Volta (`sm_70`). So `generic` is built with CUDA 12.9 or starts at `sm_75`.
I picked **`generic` = CUDA 12.9, sm_70–sm_120**, pending confirmation.
**Answer:**

## Q7 (2026-10-31): Where do things live on shared storage?

**Default:** the paths are settings, and these are the defaults:
- `.sqsh` cache: `SQSH_DIR=/shared/containers/<target>/<name>-<version>-<target>.sqsh`
- module tree: `MODULE_DIR=/shared/modules/apparadise` (Lmod `MODULEPATH` entry)
- wrapper scripts: `WRAPPER_DIR=/shared/apps/apparadise/bin/<name>/<version>/`
**Answer:**

## Q8 (2026-10-31): Is Harbor already installed, and what is its hostname?

**Default:** not installed yet. Settings use `HARBOR_URL` (REQUIRED) and the demo uses
`registry.example`. The phase 1 runbook in `docs/operations/harbor.md` describes installing it.
**Answer:**

## Q9 (2026-10-31): Should the EasyBuild source share installs across images?

The EasyBuild session showed that two `eb` runs on one install tree corrupt each other through
locks. The architecture says EasyBuild "installs into a shared, per-target install tree that
later builds reuse".
**Default:** the coordinator holds at most **one EasyBuild job per (target, toolchain
generation) tree** at a time. The tree is read-only-mounted into other builds. Spack and pixi
builds run in parallel.
**Answer:**

## Q10 (2026-10-31): Should InfiniBand/RDMA headers go into every builder base image?

The EasyBuild session needed `--ignore-osdeps` because `libibverbs-dev` was missing, and that
silently cost OpenMPI/UCX their InfiniBand support.
**Default:** yes. Builder images always include `rdma-core`/`libibverbs-dev`, and
`--ignore-osdeps` is never used. A missing OS dependency is a `concretize` failure, not
something to skip.
**Answer:**

## Q11 (2026-10-31): Do the builder sandboxes get internet egress?

The EasyBuild session lost time to blocked `git.savannah.gnu.org` (config.guess retries).
**Default:** builders have no direct internet access. Sources come from a source mirror
(`SOURCE_MIRROR_URL`) and Harbor proxy caches. A builder egress allowlist (GitHub,
conda-forge, PyPI, the Spack mirror and the EasyBuild sources) is a setting. Builder images ship a
current `config.guess`/`config.sub`, and EasyBuild's download of them is disabled.
**Answer:**

## Q12 (2026-10-31): Which LLM should the chatbot use?

**Default:** Claude via the Anthropic API (`ANTHROPIC_API_KEY`, model `claude-opus-5-5`, with
the model as a setting). This is phase 4, so nothing is built yet.
**Answer:**

## Q13 (2026-10-31): What are Rubin's compute capability and Vera's CPU target?

NVIDIA's compute-capability appendix doesn't list Rubin yet, and archspec doesn't know Vera.
**Default:** `containers/targets.yml` leaves `vr.cuda_arch`/`ptx_arch` null, so `vr` recipes
refuse to render until someone reads the value with `nvidia-smi --query-gpu=compute_cap` on
the NVL72. The CPU tuning uses `neoverse_v2` (Grace, Armv9.0) until Spack and GCC know Vera.
Module wrappers pick `vr` by CPU architecture (aarch64) alone.
**Answer:**

## Q14 (2026-10-31): How should module wrappers start the image?

**Default:** `enroot start -- <file.sqsh> <exe> args`, run from the pre-staged read-only
`.sqsh`, keeping the working directory. Mounts and GPU/RDMA injection come from the site
Enroot config. This needs `squashfuse`/`fuse-overlayfs` on the nodes. Which host environment
variables to pass through is still open: right now only what Enroot passes by default.
**Answer:**

## Q15 (2026-10-31): Should the frontend keep Bucketeer's look with a new accent?

**Default:** the same design system (tokens, fonts, components), with the accent switched
from orange to indigo `#4f46e5` and cool neutrals, so the two apps are related but easy to
tell apart.
**Answer:**

## Q16 (2026-10-31): Are the pinned tool versions in the builder image right?

**Default:** Spack `v1.1.0`, EasyBuild `5.1.2`, pixi `0.58.0`. None of them have been checked
against upstream yet, because no container runtime is available on the development host.
Verify them at the first image build.
**Answer:**
