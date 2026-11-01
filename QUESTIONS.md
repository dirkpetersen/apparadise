# Questions for the owner

Each question records the default I am working with until you answer. Correct a question by
editing its **Answer** line, and I will follow it and update the code.

Newest question first: new questions go at the top, right below this line.

Format: `Qn (date) - question`, then **Default** (what I assumed) and **Answer** (yours).

---

## Q31 (2026-11-01): Ship the glibc forced include in the builder image?

The `gnulib-glibc-generic` repair rule appended `cflags==-std=gnu17`, which cannot work: old
gnulib (and util-linux) define `_GNU_SOURCE`, and that switches on glibc 2.42+'s `_Generic`
macros (`memchr`, `bsearch`, ...) in any language mode. Checked on glibc 2.43. The only fix
that worked in the EasyBuild session is a forced include, `-include glibc-generic-off.h`, which
undefines `__glibc_const_generic`. A Spack spec cannot name a file path (`SPEC_RE` forbids `/`).
**Default:** the rule is removed so it no longer wastes a repair attempt. The NGC/Ubuntu 24.04
builder images ship glibc 2.39, which does not have these macros, so builds there should not
hit it. If newer bases are used, ship the header at a fixed path in `base-builder` and add a
repair action that adds exactly that `-include`.
**Answer:** don't are your recommendation: but note: I want to continue building and never stop 

## Q30 (2026-10-31): How should auto-repair fix a failing EasyBuild dependency?

The compiler-level repairs (gprofng in binutils, old C under C23, gnulib against new
glibc) almost always hit a *dependency* that EasyBuild resolves through `--robot`. EasyBuild's
`--try-amend` changes only the top-level easyconfig. It also replaces a parameter instead of
adding to it, and splits values on commas. Spack can target the dependency (`^binutils~gprofng`)
or propagate a flag (`cflags==-std=gnu17`).
**Default:** those rules are Spack-only. For EasyBuild, the only automatic repair is
dropping the toolchain bump. An EasyBuild-side fix would need patched easyconfigs in a
site robot path (`recipes/easybuild-overrides/`), shipped in the builder image.
**Answer:** yes try fixing easyconfigs also, try try try 

## Q29 (2026-10-31): Auto-repair scope

**Default:**
- When a build fails and its scrubbed error line matches a rule in `recipes/repairs.yml`,
  one repaired build is queued at once, as `system`, for the same release and platform.
- At most 2 repaired builds per release (`repair.max_attempts`), and never the same rule
  twice.
- Repairs can only append to the Spack spec, set allowlisted EasyBuild options
  (`try-amend`, or drop `try-toolchain`), or switch to the next source that has a CUDA
  recipe.
- No LLM is involved and nothing is patched by hand. A rule only reaches the library
  through a reviewed commit.
- A repaired image that passes its GPU test is published under the normal tag. Its build
  record says which rule produced it.
**Open:** should a repaired build that passes be published automatically, or wait for a
sysadmin to approve it?
**Answer:** automatic 

## Q28 (2026-10-31): Is it acceptable that a restricted name can be inferred?

A non-sysadmin who asks for a restricted package gets exactly the answer an unknown name
gets: "no recipe", with or without a source, and under any alias. But Spack, EasyBuild and
conda-forge listings are public. Anyone who knows that `AlphaFold3` has a CUDA easyconfig
upstream, and still sees "no recipe" here, can infer that it is restricted on this site.
**Default:** accepted. Hiding it would mean building it, or pretending to, for people who
mustn't have it.
**Answer:** yes, no problem 

## Q27 (2026-10-31): Chatbot defaults

**Default:**
- Model `claude-opus-5-5` (`CHAT_MODEL`), effort `medium`, with the server-side refusal
  fallback (`fallbacks: "default"`). With no `ANTHROPIC_API_KEY` the page says the assistant
  is switched off.
- `create_request` and `start_build` only *propose*. The user confirms with a button, which
  calls the normal request or queue API (quota, CSRF, audit). Text that comes from recipes,
  indexes or build logs can therefore never spend anyone's quota through the model (prompt
  injection).
- Conversations are kept in the server's memory for 2 hours: 30 messages per conversation,
  40 messages per user per hour. They are lost on restart. The audit log records that a
  message was sent and which tools ran, never the text.
- The demo uses a keyword-rule stand-in (`mock/claude_mock.py`), never the real API.
**Answer:** ok fine 

## Q26 (2026-10-31): Who can watch a live build log?

**Default:**
- Sysadmins see the raw log of any build. The user who requested a build sees its log
  scrubbed (the same scrubbing as the failure database). Restricted builds are visible to
  sysadmins only.
- Everyone else gets a 404, so build logs aren't public while a build runs. The public
  record is the scrubbed failure excerpt.
- A stream lasts at most 5 minutes, then the browser reconnects by itself and resumes at
  the byte offset where it stopped.

Making scrubbed live logs public for all public builds would be a one-line change.
**Answer:** everyone can see it

## Q25 (2026-10-31): Requests: default targets, weekly quota, and unknown names

**Default:**
- A request with no targets builds `vr` and `rtx`. `generic` is opt-in, because it costs two
  build jobs (amd64 and arm64).
- With no version given, the newest release the chosen source lists is built. For EasyBuild,
  that is the newest version that has a CUDA easyconfig.
- Quota: 6 build jobs per user in any 7 days, counted per platform job. Sysadmins have no
  quota. The value is runtime policy, changed on the admin page.
- A release that is already available or already queued is skipped and costs nothing.
- A name that no source knows gets a 422 and nothing is stored: no package row, no request
  and no `no-recipe` failure record. Typos shouldn't fill the catalog or the failure
  database. Requests for restricted packages from non-sysadmins get the same answer as
  unknown names.
- Who made a request is visible only to themselves and to sysadmins.
**Answer:** this is not important 

## Q24 (2026-10-31): Phase 3 started early; failure database and vote defaults

Phase 1 is blocked on hardware (Harbor install, Rubin compute capability), so I kept going
with phase 3 code, as Q17 did for phase 2.
**Default:**
- The failure database (`#/failures`) is public without signing in, like the catalog.
  Restricted failure groups are visible to RCO-Admins only.
- The excerpt and the error line are scrubbed when recorded. Paths, internal host names
  (node part included), IP addresses and tokens are replaced.
- Who requested a failed build is never shown publicly.
- Voting needs a signed-in user. Each user gets one vote per package or failure group, and
  can take it back.
- Votes are counted, but nothing is ranked or scheduled by them yet.
**Answer:**  switch to zot 

## Q23 (2026-10-31): Who may browse the Registry page, and with which Harbor account?

The new Registry page (`#/registry`) lists what Harbor holds, with pull commands per tag.
**Default:** the `public` project is browsable without signing in, like the catalog.
`restricted` and `cache` (the NGC proxy cache plus test tags) are shown only to RCO-Admins,
and anyone else gets a 404 for them. The server reads Harbor with the same
`HARBOR_USERNAME` account that creates the per-job robots. The browser never talks to
Harbor's API, and answers are cached for 30 seconds. If you'd rather keep reads off the admin
account, a separate read-only robot (pull + list on all three projects) would be a small change.
**Answer:** everyone 

## Q22 (2026-10-31): How should EasyBuild recipes get bumped to the platform toolchain?

The real easyconfigs pin old toolchains and their own CUDA. RELION 5.0.0 uses
`foss-2023a-CUDA-12.1.1`, and Cellpose uses `foss-2022a-CUDA-11.7.0`. Built as-is, each one
would compile a whole old toolchain and download its own CUDA.
**Default:** the easystack options add `try-toolchain: foss,2025b` (from
`containers/targets.yml`) and `filter-deps: CUDA`, so the build uses the base image's CUDA
13.0.3. Builds where the bump doesn't work fail at `concretize`/`build` and show up as
failure records. Builds now pick the easyconfig automatically: for the requested version,
the CUDA easyconfig with the newest toolchain generation.
**Answer:** yes you should always try the latest toolchain first and then fall back to the documented one if that does not work 

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
**Answer:** build all cuda packages on all 3 platforms, and if there is apackage with the samename and  version on multiple platforms (eb, pixi) then point this out in the module file meta data 

## Q20 (2026-10-31): Is a per-project robot scope enough?

Harbor scopes robot accounts to projects, not single repositories. **Default:** each job
gets push/pull on its image's project plus `cache`, lasts 1 day at most, and is deleted
when the job ends. CLAUDE.md says "one repository"; this is the closest Harbor offers.
**Answer:** ok 

## Q19 (2026-10-31): How do GPU nodes pull the test tag from the `cache` project?

The test stage pushes `cache/test-<name>:<job>` with the job's robot account and then runs it
with `srun --container-image`. Pyxis/Enroot pulls it as the user who runs the test job.
**Default:** test jobs run as a service account that has site Enroot credentials
(`~/.config/enroot/.credentials`) with pull access to `cache`. A retention rule in Harbor
deletes `cache/test-*` tags after 2 days.
**Answer:** ok 

## Q18 (2026-10-31): Where does the comparison CSV `cuda_spack_easybuild_pixi.csv` live?

The architecture doc names it as the resolver's lookup table, but I couldn't find it on this
host. **Default:** `python -m apparadise.index ... --csv` regenerates it from the three
upstream sources. Its columns are name, spack, easybuild, pixi and sources, with CUDA
packages only.
**Answer:** I don't care, where it makes sense it will run under a username so XDG paths are relevant  look at appmotel as example 

## Q17 (2026-10-31): May phase 2 code start before phase 1 is fully built?

CLAUDE.md says not to build phase N+1 ahead of phase N. Phase 1's remaining items need
Harbor, the hardware and a container runtime, and none of those exist on the development host.
**Default:** yes, for phase 2 code that is testable offline: the index, resolver, recipe
generation, coordinator and builder API. Nothing user-facing from phase 3 or 4 gets built.
**Answer:** ok sounds good

## Q16 (2026-10-31): Are the pinned tool versions in the builder image right?

**Default (updated 2026-10-31, checked against upstream):** Spack `v1.2.2`, EasyBuild
`5.4.2`, pixi `0.83.0`, NGC CUDA `13.0.3` and `12.9.2` on Ubuntu 24.04. The image builds
themselves still haven't run, because no container runtime is available here.
**Answer:** We should use DGX OS 8.1 based on Ubuntu 26.04

## Q15 (2026-10-31): Should the frontend keep Bucketeer's look with a new accent?

**Default:** the same design system (tokens, fonts, components), with the accent switched
from orange to indigo `#4f46e5` and cool neutrals, so the two apps are related but easy to
tell apart.
**Answer:** no use same colors 

## Q14 (2026-10-31): How should module wrappers start the image?

**Default:** `enroot start -- <file.sqsh> <exe> args`, run from the pre-staged read-only
`.sqsh`, keeping the working directory. Mounts and GPU/RDMA injection come from the site
Enroot config. This needs `squashfuse`/`fuse-overlayfs` on the nodes. Which host environment
variables to pass through is still open: right now only what Enroot passes by default.
**Answer:** ok

## Q13 (2026-10-31): What are Rubin's compute capability and Vera's CPU target?

NVIDIA's compute-capability appendix doesn't list Rubin yet, and archspec doesn't know Vera.
**Default:** `containers/targets.yml` leaves `vr.cuda_arch`/`ptx_arch` null, so `vr` recipes
refuse to render until someone reads the value with `nvidia-smi --query-gpu=compute_cap` on
the NVL72. The CPU tuning uses `neoverse_v2` (Grace, Armv9.0) until Spack and GCC know Vera.
Module wrappers pick `vr` by CPU architecture (aarch64) alone.
**Answer:** ok

## Q12 (2026-10-31): Which LLM should the chatbot use?

**Default:** Claude via the Anthropic API (`ANTHROPIC_API_KEY`, model `claude-opus-5-5`, with
the model as a setting). This is phase 4, so nothing is built yet.
**Answer:** We are using Sonnet 5.5 as a default and if If there's any issues we're switching to Opus 5.5 immediately with high level thinking in that case 

## Q11 (2026-10-31): Do the builder sandboxes get internet egress?

The EasyBuild session lost time to blocked `git.savannah.gnu.org` (config.guess retries).
**Default:** builders have no direct internet access. Sources come from a source mirror
(`SOURCE_MIRROR_URL`) and Harbor proxy caches. A builder egress allowlist (GitHub,
conda-forge, PyPI, the Spack mirror and the EasyBuild sources) is a setting. Builder images ship a
current `config.guess`/`config.sub`, and EasyBuild's download of them is disabled.
**Answer:** apparadise shoul dhave access to the entire internet, If other tools are not configured as such they can use up Paradise as a proxy 

## Q10 (2026-10-31): Should InfiniBand/RDMA headers go into every builder base image?

The EasyBuild session needed `--ignore-osdeps` because `libibverbs-dev` was missing, and that
silently cost OpenMPI/UCX their InfiniBand support.
**Default:** yes. Builder images always include `rdma-core`/`libibverbs-dev`, and
`--ignore-osdeps` is never used. A missing OS dependency is a `concretize` failure, not
something to skip.
**Answer:** Yes we need Infiniband support for everything but we need the Infiniband support specifically for ARM based packages in Vera Rubin umm the other packages for the RTX do not require Infiniband 

## Q9 (2026-10-31): Should the EasyBuild source share installs across images?

The EasyBuild session showed that two `eb` runs on one install tree corrupt each other through
locks. The architecture says EasyBuild "installs into a shared, per-target install tree that
later builds reuse".
**Default:** the coordinator holds at most **one EasyBuild job per (target, toolchain
generation) tree** at a time. The tree is read-only-mounted into other builds. Spack and pixi
builds run in parallel.
**Answer:** You need to figure this out we want to maximize performance we want to use as many cores as possible but we also um want to have no data corruption so you need to find a middle ground how this all works 

## Q8 (2026-10-31): Is Harbor already installed, and what is its hostname?

**Default:** not installed yet. Settings use `HARBOR_URL` (REQUIRED) and the demo uses
`registry.example`. The phase 1 runbook in `docs/operations/harbor.md` describes installing it.
**Answer:** you You're installing it right now I installed Docker and Docker Compose 

## Q7 (2026-10-31): Where do things live on shared storage?

**Default:** the paths are settings, and these are the defaults:
- `.sqsh` cache: `SQSH_DIR=/opt/ap/containers/<target>/<name>-<version>-<target>.sqsh`
- module tree: `MODULE_DIR=/opt/ap/modules` (Lmod `MODULEPATH` entry)
- wrapper scripts: `WRAPPER_DIR=/opt/ap/bin/<name>/<version>/`
**Answer:** use the above

## Q6 (2026-10-31): Which CUDA version is the platform baseline?

**Default:** CUDA 13.0 for both targets. The `generic` target keeps `sm_70`–`sm_120`, but
CUDA 13 dropped Volta (`sm_70`). So `generic` is built with CUDA 12.9 or starts at `sm_75`.
I picked **`generic` = CUDA 12.9, sm_70–sm_120**, pending confirmation.
**Answer:** 13.0 

## Q5 (2026-10-31): What base OS should the per-target base images use?

The EasyBuild session (`/storage/t3/test/motuz/eb/FAILURES.md`) lost a lot of time to the host's
Ubuntu 26.04 / GCC 15 / glibc (C23 defaults, `_Generic` clashes in gnulib, gprofng).
**Default:** NGC `nvcr.io/nvidia/cuda:<ver>-devel-ubuntu24.04` for builder images and
`...-runtime-ubuntu24.04` for runtime images. Never build on the host OS. Images are multi-arch
(amd64 + arm64), with the CUDA version pinned by digest.
**Answer:** build everything with ubuntu 24.06 (DGX OS 8.1.0) and cuda13 

## Q4 (2026-10-31): Which packages are the phase 1 "top 20"?

**Default:** a provisional list in `catalog/` covers both targets and all three sources:
- Spack: GROMACS, LAMMPS, OpenMPI, UCX, nccl-tests, OSU micro-benchmarks, HPL, Kokkos
- EasyBuild: Pytorch, aplphsfold 3, RELION, cryoDRGN, MotionCor3, Cellpose
- pixi:  JAX, vLLM, RAPIDS (cuDF/cuML), rapids-singlecell, CuPy, JupyterLab

Each entry stays `status: planned` until it has been built.
**Answer:** ok

## Q3 (2026-10-31): Does phase 1 need a read-only catalog page?

The roadmap puts catalog pages in phase 3. But phase 1's exit ("users run them without help")
is easier to reach if users can see what exists and how to load it.
**Default:** phase 1 includes a read-only catalog (list, search and package page, with module
and srun commands), filled from a hand-maintained `catalog/` folder of YAML files. Votes,
requests and the failure DB stay in phase 3.
**Answer:**  Yes agreed 

## Q2 (2026-10-31): Which AD group may sign in at all?

**Default:** anyone in `LDAP_REQUIRED_GROUP_DN` (REQUIRED). If every domain user should be
allowed in, set it to the Domain Users group DN.
**Answer:** If this is empty everyone can log in 

## Q1 (2026-10-31): What is the full DN of the RCO-Admins group?

**Default:** `LDAP_ADMIN_GROUP_DN` is REQUIRED in `.env.default` and left empty. The demo uses
`CN=RCO-Admins,OU=Groups,DC=example,DC=org`.
**Answer:** will find it 
