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

**Default (updated 2026-10-31, checked against upstream):** Spack `v1.2.2`, EasyBuild
`5.4.2`, pixi `0.83.0`, NGC CUDA `13.0.3` and `12.9.2` on Ubuntu 24.04. The image builds
themselves still haven't run, because no container runtime is available here.
**Answer:**

## Q17 (2026-10-31): May phase 2 code start before phase 1 is fully built?

CLAUDE.md says not to build phase N+1 ahead of phase N. Phase 1's remaining items need
Harbor, the hardware and a container runtime, and none of those exist on the development host.
**Default:** yes, for phase 2 code that is testable offline: the index, resolver, recipe
generation, coordinator and builder API. Nothing user-facing from phase 3 or 4 gets built.
**Answer:**

## Q18 (2026-10-31): Where does the comparison CSV `cuda_spack_easybuild_pixi.csv` live?

The architecture doc names it as the resolver's lookup table, but I couldn't find it on this
host. **Default:** `python -m apparadise.index ... --csv` regenerates it from the three
upstream sources. Its columns are name, spack, easybuild, pixi and sources, with CUDA
packages only.
**Answer:**

## Q19 (2026-10-31): How do GPU nodes pull the test tag from the `cache` project?

The test stage pushes `cache/test-<name>:<job>` with the job's robot account and then runs it
with `srun --container-image`. Pyxis/Enroot pulls it as the user who runs the test job.
**Default:** test jobs run as a service account that has site Enroot credentials
(`~/.config/enroot/.credentials`) with pull access to `cache`. A retention rule in Harbor
deletes `cache/test-*` tags after 2 days.
**Answer:**

## Q20 (2026-10-31): Is a per-project robot scope enough?

Harbor scopes robot accounts to projects, not single repositories. **Default:** each job
gets push/pull on its image's project plus `cache`, lasts 1 day at most, and is deleted
when the job ends. CLAUDE.md says "one repository"; this is the closest Harbor offers.
**Answer:**

## Q21 (2026-10-31): Some resolver results on the real index need a decision

I ran `tools/refresh-index` against the real upstreams. The index has 9,116 Spack packages
(338 CUDA), 3,267 EasyBuild (261 CUDA) and 14,231 conda-forge (421 CUDA, counted from build
strings and `cuda-version`/`cudatoolkit` deps). 910 names have a CUDA build somewhere, and
18 have one in all three. The architecture doc says roughly 510 / 250 / 180 / 26, so the
detection rules count differently than the original comparison.

Resolving the provisional top 20 gave results that follow the rules but may not be what we
want:
- **jax** goes to EasyBuild: conda-forge's CUDA build lives in `jaxlib`, not `jax`.
  **Default:** I leave it. A pixi recipe needs `jax` plus `jaxlib=*cuda*`, which the template
  can't express yet.
- **rapids, rapids-singlecell** go to EasyBuild: the conda metapackages have no CUDA marker
  of their own. **Default:** I leave them. Request `source: pixi` explicitly (rule 1) to
  build from conda-forge.
- **jupyterlab** becomes a CUDA "port" in Spack, but it needs no CUDA at all.
  **Default:** I leave the rule. Should non-GPU packages be allowed in the catalog as plain
  CPU images?
- **relion** goes to Spack (it has a cuda variant), not EasyBuild as I guessed in the
  catalog file.
**Answer:**

## Q22 (2026-10-31): How should EasyBuild recipes get bumped to the platform toolchain?

The real easyconfigs pin old toolchains and their own CUDA. RELION 5.0.0 uses
`foss-2023a-CUDA-12.1.1`, and Cellpose uses `foss-2022a-CUDA-11.7.0`. Built as-is, each one
would compile a whole old toolchain and download its own CUDA.
**Default:** the easystack options add `try-toolchain: foss,2025b` (from
`containers/targets.yml`) and `filter-deps: CUDA`, so the build uses the base image's CUDA
13.0.3. Builds where the bump doesn't work fail at `concretize`/`build` and show up as
failure records. Builds now pick the easyconfig automatically: for the requested version,
the CUDA easyconfig with the newest toolchain generation.
**Answer:**

## Q23 (2026-10-31): Who may browse the Registry page, and with which Harbor account?

The new Registry page (`#/registry`) lists what Harbor holds, with pull commands per tag.
**Default:** the `public` project is browsable without signing in, like the catalog.
`restricted` and `cache` (the NGC proxy cache plus test tags) are shown only to RCO-Admins,
and anyone else gets a 404 for them. The server reads Harbor with the same
`HARBOR_USERNAME` account that creates the per-job robots. The browser never talks to
Harbor's API, and answers are cached for 30 seconds. If you'd rather keep reads off the admin
account, a separate read-only robot (pull + list on all three projects) would be a small change.
**Answer:**
