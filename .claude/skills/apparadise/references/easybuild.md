# EasyBuild reference (for apparadise)

Sources and versions read on 2026-11-01:

- **EasyBuild 5.4.2**, released 2026-10-30 (PyPI `easybuild` and the GitHub release tags
  `easybuild-framework-v5.4.2` and `easybuild-easyconfigs-v5.4.2`). The builder image pins
  the same version (`containers/base/Containerfile.builder`, `EASYBUILD_VERSION=5.4.2`).
- Shallow clones of the `develop` branches: easybuild-framework `fcc1003` (2026-10-31, reports
  `5.4.3.dev0`), easybuild-easyblocks `0736e0d`, easybuild-easyconfigs `4925d936`,
  easybuild-docs `18c01d2` (2026-10-30). Option names and defaults below were read from
  `easybuild/tools/options.py` and `config.py`, not copied from secondary write-ups.
- Repo lessons: `docs/operations/easybuild-troubleshooting.md`,
  `docs/lessons/easybuild-host-build.md`, `recipes/_templates/easystack.yml.in`,
  `recipes/repairs.yml`, `containers/targets.yml`, `web/apparadise/{index,resolver,recipes,coordinator}.py`,
  `builder/apparadise_builder/agent.py`, and QUESTIONS.md Q9/Q10/Q11/Q22/Q30/Q31.

## 1. What it is

EasyBuild is a Python framework that builds and installs scientific software from source on
HPC systems. It produces one install prefix plus one environment module (Lmod or Tcl) per
package. Every build is described by a plain-text **easyconfig** (`.eb`, Python syntax) that
pins the exact version, toolchain and every dependency version. A Python **easyblock**
implements the build procedure. Its strength is **curated, reproducible, version-pinned
stacks**: the central easyconfigs repo is tested as a whole per toolchain generation, so a
`foss-2025b` easyconfig and all its dependencies are known to build together. Compared with
Spack it is less flexible: there is no solver, and swapping variants or dependency versions
means editing files. In return, what you build is exactly what upstream tested. It is the
standard stack tool at many European HPC centres, and EESSI uses it.

## 2. Core concepts

**Easyconfig** (`<Name>-<version>-<toolchain>-<toolchainver><versionsuffix>.eb`), for example
`GROMACS-2025.3-foss-2025b-CUDA-12.9.1.eb`. Key parameters: `name`, `version`,
`versionsuffix`, `toolchain = {'name': 'foss', 'version': '2025b'}`, `toolchainopts`,
`dependencies` / `builddependencies` (tuples `(name, version[, versionsuffix[, toolchain]])`),
`sources`, `checksums` (sha256 is the default in 5.x), `configopts`, `buildopts`,
`preconfigopts`, `postinstallcmds`, `sanity_check_paths`, `moduleclass`,
`cuda_compute_capabilities`, and `max_parallel` (the `parallel` *easyconfig parameter* is
deprecated in 5.x; the `--parallel` *option* still exists). The central repo lives at
`easybuild/easyconfigs/<first letter>/<Name>/*.eb`.

**Easyblock**: a Python class that runs the steps fetch → ready → extract → patch → prepare →
configure → build → test → install → extensions → postiter → postproc → sanitycheck →
cleanup → module → permissions → package → testcases. Generic easyblocks (`ConfigureMake`,
`CMakeMake`, `PythonPackage`, `PythonBundle`, `Bundle`, `Tarball`, `Binary`) cover most
software, and software-specific ones (`gromacs.py`, `pytorch.py`) cover the hard cases.
`eb --list-easyblocks` lists them. `--include-easyblocks` adds site ones.

**Toolchains**: a compiler, optionally with MPI and math libraries, built as modules.
- `GCCcore` → `GCC` → `gompi` (GCC + OpenMPI) → `foss` (+ FlexiBLAS/OpenBLAS, FFTW,
  ScaLAPACK). `gfbf` is GCC + FlexiBLAS + FFTW without MPI. The Intel equivalents are
  `intel` and `iimpi`. There are also LLVM-based and MPICH-based variants (`lfoss`,
  `gmpich`, `lmpich`).
- **Generations.** Until 2025 they were named `<year>a` (around January) and `<year>b`
  (around July). **The newest generation is called `2026.1`, not `2026a`** (foss-2026.1: GCC
  15.2.0, OpenMPI 5.0.10, FlexiBLAS 3.5.0, binutils 2.45, released March 2026). The docs'
  policy page says "As of April 2026 (EasyBuild 5.3.0) the latest (common) toolchain
  generation is `2026.1`". `2024b` was **skipped**. Supported generations: 2026.1 (GCC
  15.2.0), 2025b (14.3.0), 2025a (14.2.0), 2024a (13.3.0), 2023b (13.2.0) and 2023a (12.3.0).
  2022a and 2022b are deprecated, and 2021b and older are archived (moved to
  `easybuild-easyconfigs-archive`). Policy: the central repo accepts PRs only for the 6
  newest generations.
- `foss-2025b` = GCC 14.3.0, OpenMPI 5.0.8, FlexiBLAS 3.4.5, FFTW 3.3.10, ScaLAPACK 2.2.2,
  binutils 2.44.
- **There is no CUDA toolchain any more.** The old `fosscuda` and `gcccuda` toolchains are
  gone (archived). CUDA is an ordinary dependency, `('CUDA', '12.9.1', '', SYSTEM)`, and the
  easyconfig gets the **versionsuffix `-CUDA-<x.y.z>`** (for example
  `PyTorch-2.9.1-foss-2025b-CUDA-12.9.1.eb`). Each generation pairs with one CUDA version in
  the central repo: 2023a→12.1.1, 2023b→12.4.0, 2024a→12.6.0, 2025a→12.8.0, 2025b→12.9.1,
  and 2026.1→13.3.0 for most packages. These pairings are counted from file names, and a
  few exceptions exist, such as `OpenMM-8.5.2-foss-2026.1-CUDA-12.9.1`. CUDA itself is
  installed by the binary `CUDA` easyblock (`CUDA-13.0.0.eb` … `CUDA-13.4.1.eb` exist). cuDNN,
  NCCL, cuTENSOR and the like carry `-CUDA-x.y.z` suffixes too.

**Robot** (`--robot` / `-r`): resolves and builds missing dependencies by searching the robot
path (`--robot-paths`; the bundled easyconfigs are the default, and `--robot=<dir>` is
searched first). It does not solve anything. It looks up exact file names, so every
dependency must exist at exactly the pinned version and toolchain. Since 5.0, `--dry-run`
(`-D`) **no longer implies `--robot`**.

**Hooks** (`--hooks=<file.py>`): a Python module that defines functions such as
`start_hook`, `parse_hook(ec)`, `pre_<step>_hook(self)` / `post_<step>_hook(self)` for every
step, `pre_single_extension_hook`, `module_write_hook(self, filepath, module_txt)`,
`pre_run_shell_cmd_hook`, `fail_hook`, `crash_hook` and `end_hook`. `eb --avail-hooks` lists
them. A `parse_hook` can rewrite *any* easyconfig, dependencies included, which makes it the
right place for site-wide fixes such as `--disable-gprofng` on every binutils (`--try-amend`
cannot do this; see §6).

**`--try-*`** ("USE WITH CARE" per `eb --help`): `--try-toolchain=<name>,<ver>`,
`--try-toolchain-name`, `--try-toolchain-version`, `--try-software-version`,
`--try-amend=<param>=<value>` (strings or lists of strings only, can be repeated), and
`--try-update-deps` (with `--try-ignore-versionsuffixes`). With `--robot`, a toolchain bump
**trickles down to the toolchain level only**. Dependencies keep their versions and are
re-generated as "tweaked" easyconfigs for the new toolchain, written under
`/tmp/eb-*/tweaked_dep_easyconfigs/`. A software-version bump never trickles down.
`--map-toolchains` (on by default) maps subtoolchains, for example GCCcore-12.3.0 →
GCCcore-14.3.0. `--try-amend` and `--amend` apply to the top-level easyconfig.

**Easystack** (`eb --easystack stack.yml`, stable since 5.0): YAML with a top-level
`easyconfigs:` list. Each entry may carry `options:` (long option names without `--`).
Options given on the command line apply to every entry. Repeatable options (`amend`,
`try-amend`) take YAML lists. apparadise renders one easystack per build (§7).

**Module naming schemes** (`--module-naming-scheme`): `EasyBuildMNS` (the default; flat,
`GROMACS/2025.3-foss-2025b-CUDA-12.9.1`), `HierarchicalMNS` (Core/Compiler/MPI layers),
`CategorizedMNS`, `CategorizedHMNS` and `MigrateFromEBToHMNS`. apparadise does not use
EasyBuild's modules for users. Its own generator writes one Lmod file per *image*, so
inside an image the flat default is fine.

**Other 5.x defaults worth knowing**: RPATH linking is on (`--rpath`, Linux), `--trace` is
on, generated modules use `depends_on`, the default job backend is Slurm, **`max-parallel`
defaults to 16**, sanity-check commands run from an empty temp directory, unresolved
templates are an error, and EasyBuild has **granular exit codes** (`EasyBuildExit`: 4 missing
easyconfig, 15 configure step, **16 build step**, 22 sanity-check step, 31 missing
dependency, 32 missing OS dependency, 41 download, 42 checksum, …). The repo's
troubleshooting page reads `exit=16` as "some dependency failed". Precisely, 16 is
`FAIL_BUILD_STEP`: the build step of whichever easyconfig in the tree failed first.
**EasyBuild refuses to run as root** unless you pass
`--allow-use-as-root-and-accept-consequences`.

## 3. CUDA specifics

**`cuda_compute_capabilities`**: dotted values (`7.0,8.0,9.0,12.0`). Set it with:
- the CLI or config: `--cuda-compute-capabilities=8.0,9.0,12.0`, env
  `EASYBUILD_CUDA_COMPUTE_CAPABILITIES`, or `cuda-compute-capabilities` in a `.cfg` or
  easystack;
- or in the easyconfig: `cuda_compute_capabilities = ['8.0', '9.0']`. **The build option
  wins over the easyconfig value**: `build_option(...) or config.get(...)` in
  `templates.py`, and the same pattern in `CMakeMake`.
- Generic `CMakeMake` turns it into `CMAKE_CUDA_ARCHITECTURES` and **errors out** if a CUDA
  build has none set.
- Templates for easyconfigs: `%(cuda_compute_capabilities)s`, `%(cuda_cc_space_sep)s`,
  `%(cuda_cc_semicolon_sep)s`, `%(cuda_cc_cmake)s` (`80;90`), `%(cuda_int_comma_sep)s`,
  `%(cuda_sm_comma_sep)s` (`sm_80,…`), `%(cuda_cc_nvhpc)s` (`cc80,…`), and new in 5.4.2,
  `%(cuda_cc_gencode)s`. The last one emits only
  `-gencode arch=compute_XX,code=sm_XX` (SASS, **no PTX**), so easyconfigs that rely on it
  do not embed PTX by themselves.

**The CUDA sanity check** (framework, since 5.1.0) runs `cuobjdump` on every binary and
library. Its policy: device code for *every* requested CC, plus **PTX for the highest
requested CC** (forward compatibility). That is the same rule as apparadise's "always embed
PTX for the highest arch". Related options: `--cuda-sanity-check-accept-missing-ptx`,
`--cuda-sanity-check-accept-ptx-as-devcode`, `--cuda-sanity-check-strict` (also fail on
*extra* CCs) and `--cuda-sanity-check-error-on-failed-checks`. **The last one is off by
default, so failures are only warnings.** The easyconfig parameter
`cuda_sanity_ignore_files` exempts files. The `cuobjdump` parser recognises `a`/`f` suffixes
(`sm_90a`, `sm_120f` → `9.0a`, `12.0f`). Whether `--cuda-compute-capabilities=12.0f` is
passed through correctly by every easyblock is **(unverified)**.

**Caveat for apparadise, from reading the source:** the sanity check runs only
`if 'CUDA' in [dep['name'] for dep in self.cfg.dependencies()]`. `dependencies()` applies
`--filter-deps`, so **with `filter-deps: CUDA` the CUDA sanity check is skipped**. Worse,
easyblocks detect CUDA through `get_software_root('CUDA')`, which simply reads
`$EBROOTCUDA` (`tools/modules.py`); 28 easyblocks do this, including GROMACS, PyTorch and
generic `CMakeMake`. With CUDA filtered and no `EBROOTCUDA` / `EBVERSIONCUDA` in the build
environment, those easyblocks take their **CPU path**, so the build can succeed without
CUDA. The builder's EasyBuild template now sets `EBROOTCUDA`, `EBVERSIONCUDA` and also
`CUDA_HOME`/`CUDA_ROOT`/`CUDA_PATH` (what the CUDA module sets; MotionCor3's makefile reads
`$CUDA_HOME` and misses `cufft.h` without it). Another option is CUDA as an external module
(`EXTERNAL_MODULE` plus metadata, see "Using external modules" in the docs). Either way,
apparadise's own test stage should verify that the binary contains GPU code. Whether a
`ENV EBROOTCUDA` survives EasyBuild's environment handling is **(unverified)**. Dependencies
such as cuDNN or NCCL with a `-CUDA-12.9.1` suffix are *not* filtered, and still pull
binaries built for the easyconfig's CUDA, not the base image's 13.0.3 **(unverified impact)**.

**How apparadise maps targets** (`web/apparadise/recipes.py`, `containers/targets.yml`):
`cuda_arch` → `CUDA_CC_CSV` via `f"{a[:-1]}.{a[-1]}"`:

| target | platform | CUDA in base | `cuda-compute-capabilities` | `optarch` |
|---|---|---|---|---|
| `rtx` | linux/amd64 | 13.0.3 | `12.0` (sm_120; PTX for 12.0 via the sanity-check rule) | `GCC:-march=x86-64-v4` |
| `generic` | amd64 + arm64 | 12.9.2 (CUDA 13 dropped sm_70) | `7.0,7.5,8.0,8.6,8.9,9.0,10.0,12.0` | `GCC:-march=x86-64-v3` / `GCC:-march=armv8.2-a` |
| `vr` | linux/arm64 | 13.0.3 | none yet: `cuda_arch: null` (Q13), so vr builds refuse to start | `GCC:-mcpu=neoverse-v2` |

The conversion assumes plain numbers. A family or arch-specific value (`120f`, `90a`) would
become `120.f` / `90.a`, so it must change before `f` targets go into `targets.yml`.
Whether an easyblock actually embeds PTX depends on the easyblock (the `--help` text says
"where possible"). Check with `cuobjdump --list-ptx` in the test stage.

**Finding CUDA easyconfigs.** In a checkout: `ls easybuild/easyconfigs/*/*/*-CUDA-*.eb`. On
`develop` (2026-10-31) that gives 626 files for 259 software names, out of about 3320 names.
With `eb`: `eb --search -- '-CUDA-12\.9\.1'` or `eb -S 'foss-2025b-CUDA'` (regex on paths).
Browse online under docs.easybuild.io → "Supported software" (version-specific pages). The
apparadise index (`index.py: scan_easybuild`) counts a package as CUDA when the **file name
contains `-CUDA-` or the text has a `('CUDA', …)` dependency**. For each version,
`pick_easyconfig` keeps the file with the newest generation. **Its regex `-(20\d\d[ab])\b`
does not match the new `2026.1` names**, so a 2026.1 file ranks below any 20xxa/b file.
2025b CUDA easyconfigs (27, on CUDA 12.9.1) include PyTorch-2.9.1, GROMACS-2025.3/2025.4/2026.2,
CuPy-13.6.0, magma-2.9.0, GPAW-25.7.0, Triton-3.5.0, NVSHMEM-3.3.20, cuFFTMp, cuBLASMp,
OpenCV-4.12.0 (contrib), numba-cuda, OSU-Micro-Benchmarks, Score-P, HPCToolkit, Umpire and
CellBender. 2026.1 CUDA easyconfigs (CUDA 13.3.0) so far include CuPy-14.2.0, magma-2.10.0,
Kokkos/Kokkos-Kernels-5.1.1, CUDA-Python-13.3.1, Umpire, Score-P and OSU-Micro-Benchmarks.

## 4. Containers

- **`eb --containerize` / `-C`** writes a container *recipe* that runs EasyBuild inside the
  image. `--container-type` is `singularity` (the default), `apptainer` or `docker`.
  `--container-config` sets the bootstrap and base OS, `--container-template-recipe`
  supplies a custom template, `--container-build-image` builds the image too (**requires
  sudo**), and `--container-image-format` is `sif`, `squashfs` or `sandbox`. **apparadise
  does not use it.** The builder writes its own two-stage Containerfile, and the CLAUDE.md
  rules forbid Apptainer on the platform anyway.
- **Building inside a container** is the recommended way out of host-toolchain trouble (§6).
  Requirements: a non-root user or `--allow-use-as-root-and-accept-consequences`, a working
  modules tool (`lmod` on `PATH` or `$LMOD_CMD` set; EasyBuild checks the tool and its
  version), Python ≥ 3.6, a C/C++ compiler, and the OS dependencies (`osdependencies`, such
  as `libibverbs-dev`).
- **How apparadise's builder runs eb** (`builder/apparadise_builder/agent.py`):

  ```Dockerfile
  FROM {registry}/{base_builder} AS build        # NGC cuda:<full>-devel-ubuntu24.04 + EB 5.4.2 + Lmod
  COPY easystack.yml /work/easystack.yml
  RUN eb --easystack /work/easystack.yml --installpath=/opt/apps --robot
  FROM {registry}/{base_runtime}
  COPY --from=build /opt/apps /opt/apps
  ENV MODULEPATH=/opt/apps/modules/all
  ```

  The `build` stage runs `podman build --target build`, and `package` builds the final image.
  The base image installs EasyBuild non-editable in `/opt/easybuild` (FAILURES #3), gets Lmod
  from apt, ships `rdma-core`/`libibverbs-dev` (Q10) and `autotools-dev` (config.guess, Q11),
  and sets `EASYBUILD_BUILDPATH=/scratch/eb-build`, `EASYBUILD_SOURCEPATH=/scratch/sources`,
  `EASYBUILD_MODULES_TOOL=Lmod`, `EASYBUILD_DOWNLOAD_TIMEOUT=30` and `TMPDIR=/scratch/tmp`.
  **Two things to check before the first real EasyBuild job:**
  1. The Containerfile has no `USER`, so `RUN eb` executes as uid 0, and EasyBuild aborts
     with "You seem to be running EasyBuild with root privileges…". Add a build user or set
     `EASYBUILD_ALLOW_USE_AS_ROOT_AND_ACCEPT_CONSEQUENCES=1`.
  2. Ubuntu's `lmod` package puts the `lmod` binary under `/usr/share/lmod/lmod/libexec/`
     and sets `LMOD_CMD` only from `/etc/profile.d` in login shells. A plain `RUN` does not
     read that, so set `ENV LMOD_CMD=…` (or source the init) **(path unverified for 24.04)**.

## 5. Cheat sheet

```bash
# install (the recommended way; the meta-package pulls framework + easyblocks + easyconfigs)
python3 -m venv /opt/easybuild && /opt/easybuild/bin/pip install "easybuild==5.4.2"
eb --version ; eb --show-config ; eb --show-full-config
# NOT pip install -e: an editable install breaks the easybuild namespace package (FAILURES #3)

eb --search GROMACS                 # -S for short paths; the argument is a regex on file paths
eb -S '^PyTorch-2\.9.*CUDA'         # anchored regex
eb --list-toolchains ; eb --list-easyblocks ; eb --avail-easyconfig-params ; eb --avail-hooks

eb X.eb --dry-run --robot           # -Dr: full plan, [x] installed / [ ] to build (5.x: -D no longer implies -r)
eb X.eb --dry-run-short --robot     # same, shorter paths
eb X.eb --extended-dry-run          # -x: build env + commands, without building
eb X.eb --missing-modules           # -M: only what is missing
eb X.eb --robot                     # build it and its missing deps
eb X.eb --rebuild                   # rebuild even if the module exists (keeps the OS-deps check)
eb X.eb --force                     # rebuild and skip some checks (avoid in pipelines)
eb X.eb --fetch                     # download sources only (implies --ignore-osdeps; fine for prefetch only)
eb X.eb --module-only               # regenerate the module file
eb X.eb --sanity-check-only         # re-run the sanity check on an existing install
eb --from-pr 12345 --robot          # easyconfigs from a GitHub PR (also --from-commit <sha>)
eb --review-pr 12345 ; eb --check-github ; eb --new-pr ; eb --upload-test-report
eb X.eb --try-toolchain=foss,2025b --robot -D      # preview a toolchain bump
eb X.eb --try-amend=configopts='--disable-gprofng'
eb --easystack stack.yml --robot
eb --copy-ec X.eb ./local/          # copy for local editing; local dirs go first in --robot=<dir>
eb --inject-checksums X.eb          # add sha256 for new sources/patches
eb X.eb --dep-graph=deps.dot
```

**Config precedence** (lowest → highest): built-in defaults → config files → `EASYBUILD_*`
env vars → command line. Config files: `$XDG_CONFIG_DIRS/easybuild.d/*.cfg` (5.x default
`/etc/xdg/easybuild.d/`; `/etc/easybuild.d` is deprecated), then
`$XDG_CONFIG_HOME/easybuild/config.cfg` (`~/.config/easybuild/config.cfg`), then
`--configfiles`. Any long option `--foo-bar` becomes `EASYBUILD_FOO_BAR`; `.cfg` files use
`[config]`/`[override]` sections with `foo-bar = …`. The default prefix is
`~/.local/easybuild`.

The env vars that matter:

| Variable | Purpose |
|---|---|
| `EASYBUILD_PREFIX` | sets build, install, source and repository paths at once |
| `EASYBUILD_INSTALLPATH` (`_MODULES`, `_SOFTWARE`) | where software and modules land (apparadise: `--installpath=/opt/apps`) |
| `EASYBUILD_BUILDPATH`, `EASYBUILD_SOURCEPATH`, `TMPDIR` | put these on big scratch; PyTorch needs tens of GB |
| `EASYBUILD_ROBOT_PATHS` | extra easyconfig dirs (a site override dir goes here) |
| `EASYBUILD_MODULES_TOOL` (`Lmod`), `EASYBUILD_MODULE_SYNTAX` (`Lua`) | modules tool |
| `EASYBUILD_MODULE_NAMING_SCHEME` | MNS (§2) |
| `EASYBUILD_OPTARCH` | full compiler flags, e.g. `GCC:-march=x86-64-v4` (never a bare arch name) |
| `EASYBUILD_CUDA_COMPUTE_CAPABILITIES` | e.g. `12.0` |
| `EASYBUILD_CUDA_SANITY_CHECK_ERROR_ON_FAILED_CHECKS` | make CUDA sanity failures fatal |
| `EASYBUILD_PARALLEL` / `EASYBUILD_MAX_PARALLEL` (default 16) | build parallelism |
| `EASYBUILD_FILTER_DEPS`, `EASYBUILD_HIDE_DEPS` | treat deps as provided by the OS / hide modules |
| `EASYBUILD_HOOKS`, `EASYBUILD_INCLUDE_EASYBLOCKS` | site hooks / easyblocks |
| `EASYBUILD_DOWNLOAD_TIMEOUT` | per-download timeout (apparadise: 30 s) |
| `EASYBUILD_ACCEPT_EULA_FOR` | e.g. `CUDA,cuDNN` when EasyBuild installs CUDA itself |
| `EASYBUILD_FAILED_INSTALL_BUILD_DIRS_PATH`, `EASYBUILD_FAILED_INSTALL_LOGS_PATH` | keep the build dir and logs of failed installs |
| `EASYBUILD_ALLOW_USE_AS_ROOT_AND_ACCEPT_CONSEQUENCES` | needed when `eb` runs as uid 0 in a container |
| `EASYBUILD_GITHUB_USER` | for `--from-pr` / `--new-pr` (`--from-pr` works anonymously within rate limits) |

## 6. Common failures and fixes

Most of these come from the October 2026 bare-host session (see
`docs/operations/easybuild-troubleshooting.md`, which has the full symptom table and
credits that session). The `#n` numbers refer to its `FAILURES.md`.

**Host compiler: Ubuntu 26.04, GCC 15.2, glibc 2.43.** The root cause of most hard
failures was a host newer than the recipes were tested on. apparadise builds in
**Ubuntu 24.04 NGC images (GCC 13, glibc 2.39)**, which avoids all of them. Note that
`foss-2026.1` itself uses GCC 15.2, which also defaults to C23, so old code built *with*
that toolchain can hit the C23 issues even inside the container **(inference, not tested)**.

| Symptom | Cause | Fix |
|---|---|---|
| `Cannot find a type to use in place of socklen_t` (M4); `bool`/`true` typedef errors | GCC 15 defaults to `-std=gnu23`, and old autoconf probes and code break | `-std=gnu17`: a `gcc`/`cc` wrapper on `PATH` (#10). Spack: `cflags==-std=gnu17` (repair rule `c23-old-code`) |
| `Failed to determine the GCC version from: gcc-15 (...)` | EasyBuild parses the first line of `gcc --version` | the wrapper rewrites only that line (#11) |
| `cannot execute 'cc1': posix_spawnp` | `exec -a gcc` renamed argv[0] | don't rename it; exec the real `gcc-15` (#12) |
| binutils: `iolib.c: expected identifier before '_Generic'`, `all-gprofng Error 2` | glibc ≥ 2.42 turns `memchr`/`strchr`/`bsearch`… into `_Generic` macros, and gprofng clashes with them | `configopts = '--disable-gprofng'` in a local copy of **every** binutils in the plan (2.44 SYSTEM, 2.44-GCCcore-14.3.0, 2.45) (#13/17/19) |
| gettext/gnulib: `./string.h: expected identifier or '(' before '_Generic'` | bundled gnulib redeclares the names. `_GNU_SOURCE` enables the macros in **every** `-std` mode, so `-std=gnu17` does not help | forced include `CPPFLAGS='-include glibc-generic-off.h'` (it undefines `__glibc_const_generic`) (#20/21) |
| util-linux 2.41: `macro "bsearch" passed 6 arguments` | the same macros, plus a compound literal containing a comma | the same forced include (**not yet tried**, #23) |

**Bootstrap, options and environment:**

| Symptom | Fix |
|---|---|
| `Lmod modules tool can not be used, 'lmod' command is not available` | install Lmod first (there is no Lmod easyconfig), or put it on `PATH` / set `LMOD_CMD` (#2) |
| `FileRepository missing from list of repositories` | install EasyBuild non-editable (#3) |
| `One or more OS dependencies were not found: libibverbs-dev` | install `libibverbs-dev`/`rdma-core`. **Never `--ignore-osdeps`**: it silently built OpenMPI/UCX without InfiniBand (#9, Q10) |
| `gcc: error: language 86-64-v3 not recognized` | `--optarch=x86-64-v3` is a bare name and became `-x86-64-v3`. Write the full flag, `GCC:-march=x86-64-v3` (#15) |
| AOCL-BLAS/BLIS: `register 'k2' cannot be clobbered`, `target specific option mismatch` | BLIS auto-detected AVX-512 on the host while optarch said x86-64-v3. Keep auto-detecting libraries (BLIS, OpenBLAS `DYNAMIC_ARCH`, FFTW) at the same level as `-march` (#22) |
| `Lock .../.locks/<module>.lock already exists, aborting!` | two `eb` runs on one install tree. Run one at a time per tree, and remove the lock only when no `eb` is running (#16/18) |
| repeated `Failed to download recent config.guess` | no egress to savannah. Ship `config.guess` in the image (#14) |
| background build dies silently | process tied to the shell. Run as a service or batch job (#8) |
| `You seem to be running EasyBuild with root privileges` | non-root user, or `--allow-use-as-root-and-accept-consequences` |
| `Failed to execute CUDA sanity check: cuobjdump not found` | CUDA is a dependency but `cuobjdump` is not on `PATH` |
| `--try-toolchain` plan shows dozens of `tweaked_dep_easyconfigs` that then fail | the bumped toolchain has no matching deps. Add `--try-update-deps`, or build with the easyconfig's own toolchain (repair rule `easybuild-toolchain-bump`, Q22) |
| `checksum verification failed` (exit 42) | upstream re-tagged the source. Never skip it; `--inject-checksums` only after checking |

Debugging aids: the `== FAILED:` line names the failing module. In 5.x, the
`run-shell-cmd-output/<step>-*/out.txt` directory holds the full output, and each failing
command leaves `env.sh` and `cmd.sh` so you can re-run it interactively. Use
`--failed-install-logs-path` to keep logs. Use `--dry-run` first and scan the plan for every
binutils, gettext and util-linux version that needs an override.

**`--try-amend` limits (Q30):** it reaches only the top-level easyconfig, not a
robot-resolved dependency. It **replaces** a parameter instead of appending to it. Values are
split on commas (the renderer rejects commas). Dependency-level fixes need a `parse_hook` or
patched easyconfigs in a site robot path. The owner answered Q30 "yes try fixing easyconfigs
also". The planned mechanism (`recipes/easybuild-overrides/` in the builder image) is not
built yet.

**Rootless podman builds on CephFS (2026-11-02, alphafold3, relion, motioncor3).** Each of
these cost one 4 to 8 hour GCC build to find:

| Symptom | Cause | Fix in the repo |
|---|---|---|
| GCCcore `make` fails at the end: `ptxas fatal: Value 'sm_50' is not defined` (in `build-nvptx-gcc/.../libgcc/config.log`) | GCCcore's `withnvptx` offload compiler runs the image's CUDA 13 `ptxas`, which dropped `sm_50` | `parse_hook` in `containers/base/eb-hooks.py` sets `withnvptx = False` |
| Perl test step fails: Net-Ping `*_icmp*.t`, `perl5db.t` | tests see UID 0 and open raw ICMP sockets; a rootless container has no `CAP_NET_RAW` | same hook: `runtest = False` for Perl |
| LLVM test step: `Too many failed tests: 4` (llvm-ar/llvm-ranlib `error-opening-permission`, llvm-dwarfdump `output.s`, llvm-ifs `fail-file-write`) | the tests expect "Permission denied" on a read-only file; root can write it | hook adds them to `test_suite_ignore_patterns` (keeps the other 45,000 tests) |
| MotionCor3 `nvToolsExt.h: No such file or directory` | CUDA 13 ships only header-only NVTX3 under `include/nvtx3/` | `base-builder` adds forwarding headers and a linker-script `libnvToolsExt.so` (`INPUT(AS_NEEDED(libnvtx3interop.so.1))`) |
| Sanity check: `Library visibility=hidden not found` | NVIDIA's `libnvtx3interop.so.1` has a bogus `DT_AUXILIARY`; `ldd` reports it missing | never link it unless a symbol is used (the `AS_NEEDED` script above) |
| UCX-CUDA 1.16 `unknown type name 'PFN_cuMemGetHandleForAddressRange'` | CUDA 13 drops the unversioned `PFN_cu*` typedefs | hook adds `-DPFN_cuMemGetHandleForAddressRange=PFN_cuMemGetHandleForAddressRange_v11070` to CPPFLAGS |
| UCX-CUDA sanity check: `Incorrect format for sanity_check_paths` | the easyblock picks plugins from dependency names; `filter-deps: CUDA` empties the list (module would also miss the CUDA transports) | `pre_configure_hook` sets `self._plugins` to the cuda (+gdrcopy) plugins |
| `Couldn't find file v0.2.0.tar.gz anywhere` (only sources.easybuild.io tried) | a Bundle with URLs only in `default_component_specs` (SentencePiece 0.2.0): EasyBuild 5.4 never passes them to the download | hook copies them to the top-level `source_urls` |
| NCCL 2.26 `fatal error: cuda/atomic: No such file or directory` | CUDA 13 moved CCCL (`cuda/`, `thrust/`, `cub/`) to `include/cccl/`; g++ with only `-I$CUDA/include` misses it | base-builder links each `include/cccl/*` back into `include/` |
| package stage: `unreadable as a non-root user: .../OpenSSL/3/include/openssl` | EasyBuild's OpenSSL wrapper links to the builder's `/usr/include/openssl`, absent at runtime | the check skips symlinks (`! -type l`) |
| OpenMPI sanity check: `mpirun has detected an attempt to run as root` | the build runs as (namespaced) root | base-builder sets `OMPI_ALLOW_RUN_AS_ROOT=1` and `OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1` |
| NCCL 2.26 `libcu++ requires at least C++ 17` | NCCL < 2.27 builds with `-std=c++11` | hook prepends `CXXSTD=-std=c++17` to `buildopts` |
| `cuDNN ... EULA is currently not accepted` | EasyBuild needs explicit acceptance | `EASYBUILD_ACCEPT_EULA_FOR=cuDNN` (Q48) |
| `podman commit` fails on `lstat ... confdir3/confdir3/...`, or `rm -rf` says "Directory not empty" | gnulib's deliberately deep test tree (M4, Bison, tar) on overlayfs over CephFS | build trees on the job's `/scratch` bind mount, never in a layer |
| EasyBuild crashes with `RecursionError` in `make_builddir` → `shutil.rmtree` | an old build dir with that deep tree; Python 3.12.3's rmtree recurses per level | a fresh `/scratch` per job (the agent deletes the job dir) |
| deleting a job dir: `Permission denied` | sources unpacked as container root keep tarball owners, which are sub-UIDs on the host | `rm -rf`, then `podman unshare rm -rf` |
| LAMMPS/GROMACS final link: `undefined reference to cuModuleLoad` | no driver on the builder, so no `libcuda.so.1` | `libcuda.so.1` stub symlink plus `ld.so.conf` entry in `base-builder` only |
| MotionCor3: `cufft.h: No such file or directory` | makefile passes `CUDAHOME=$CUDA_HOME`; with CUDA filtered nothing sets it | `CUDA_HOME`/`CUDA_ROOT`/`CUDA_PATH` in the EasyBuild template |
| FlexiBLAS: checksum verification failed; the "tarball" is 7 KB of HTML ("Making sure you're not a bot") | `gitlab.mpi-magdeburg.mpg.de` serves an anti-bot page with HTTP 200, so EasyBuild never tries another URL | hook puts `https://csc.mpi-magdeburg.mpg.de/mpcsc/software/flexiblas/` first (same files, 3.2.0 to 3.5.0) |

Read the real error in the job's `scratch/tmp/eb-*/.../run-shell-cmd-output/*/out.txt`, not
in the podman log, which shows only the last lines of the failing command.

**Shared install tree.** With `CACHE_DIR`, sysadmin-started builds install into
`/opt/ap/eb/<target>-<arch>` (one EasyBuild job per tree), and the image copies only the
modules `eb --dry-run` lists for its easystack (`software/<mod>` and
`modules/all/<mod>.lua`). A GCCcore once built for a target is reused by every later build.

## 7. How apparadise uses EasyBuild

**Place in the resolver (`resolver.py`), rule 4:** requested source → pixi (Python/ML with
conda-forge CUDA builds on **both** linux-64 and linux-aarch64, and Spack has no non-`py-`
CUDA recipe) → Spack with a `cuda` variant → **EasyBuild CUDA easyconfig** → CUDA port
attempt (`PORT_ORDER = spack, easybuild, pixi`) → `no-recipe`. So EasyBuild is picked
automatically only when Spack has no CUDA recipe for the package, or when the requester asks
for `easybuild`. One image, one source: EasyBuild images contain only EasyBuild-built software
on top of the NGC runtime base.

**Index (`index.py`):** a nightly scan of an easybuild-easyconfigs checkout. Versions come
from `version = '…'` lines (up to 12, newest first). `details.easyconfigs` maps
version → the chosen CUDA `.eb` file, so a build needs no human to choose one. Python
detection looks for `PythonPackage` / `PythonBundle`.

**Rendered recipe** (`python -m apparadise.recipes easybuild relion 5.0.1 rtx --easyconfig
RELION-5.0.1-foss-2025b-CUDA-12.9.1.eb --stdout`, real output):

```yaml
# EasyBuild easystack for relion 5.0.1 on target rtx (linux/amd64).
# Run inside the builder image: eb --easystack easystack.yml --robot
# One EasyBuild run per (target, toolchain generation) install tree at a time (Q9).
easyconfigs:
  - RELION-5.0.1-foss-2025b-CUDA-12.9.1.eb:
      options:
        optarch: "GCC:-march=x86-64-v4"
        cuda-compute-capabilities: "12.0"
        try-toolchain: "foss,2025b"     # from targets.yml easybuild_toolchain (Q22)
        filter-deps: CUDA               # use the base image's /usr/local/cuda (see §3 caveat)
        robot: true
        parallel: 16
```

The renderer validates the easyconfig name (`^[A-Za-z0-9][A-Za-z0-9_.+-]{0,200}\.eb$`).
Repairs may set only `try-amend` and `try-toolchain` (`EB_OPTIONS`), with single-line,
comma-free values. `try-toolchain: null` deletes the bump line. The platform toolchain is
`foss,2025b` today. Moving to `2026.1` would bring GCC 15.2 and CUDA 13.3 easyconfigs, and
that is an owner decision. Q22's answer: "always try the latest toolchain first and then fall
back to the documented one".

**Concurrency (Q9):** the default is one EasyBuild job per (target, toolchain generation)
install tree. The owner asked for "a middle ground": maximum cores, no corruption. Inside a
disposable `podman build`, every job has its own `/opt/apps`, so locks cannot collide across
jobs. Sharing a pre-built tree between jobs is where the lock rule matters.

**Failure stages** (fixed: `no-recipe`, `concretize`, `build`, `package`, `test`, `publish`):

| Stage | EasyBuild cause |
|---|---|
| `no-recipe` | no easyconfig at all (and no other source) |
| `concretize` | intended for "the plan cannot be formed": missing easyconfig or dependency, failed toolchain bump, missing OS dependency (Q10) |
| `build` | anything inside `eb` in the Containerfile's `build` stage: fetch, checksum, configure, compile, sanity check |
| `package` | final-image assembly (copying `/opt/apps` into the runtime base) |
| `test` | GPU smoke test via `srun --container-image` on a real node |
| `publish` | push and sign |

**Mismatch to know about:** builders report only `build/package/test/publish`
(`coordinator.BUILDER_STAGES`), and `eb` does its robot resolution *inside* the build stage.
So a failed toolchain bump currently lands at **`build`**. The `easybuild-toolchain-bump`
repair rule matches only `stages: [concretize]`, and as wired today it would not fire for
EasyBuild jobs. A cheap fix would be an `eb --easystack … --dry-run --robot` pre-step whose
failure is reported as `concretize`, but that is a product decision (not made).

**Repairs (`recipes/repairs.yml`):** the only EasyBuild action is
`easybuild-toolchain-bump` (drop `try-toolchain`). `binutils-gprofng` and `c23-old-code` are
Spack-only (Q30). The glibc forced include has no rule, because the 24.04 images don't need
it (Q31). Auto-repair is capped by `repair.max_attempts` (default 2), and never applies the
same rule twice.

## 8. Decision tree

**Choose EasyBuild when:**
- The package has a `-CUDA-` easyconfig and Spack has no CUDA recipe (resolver rule 4), or
  the requester asks for it.
- The central repo has a **tested, version-pinned** stack for exactly that version, and you
  want upstream's tested combination rather than a solver's choice. GROMACS, RELION,
  PyTorch, CuPy, magma, NVSHMEM, cuFFTMp and Score-P all exist for 2025b with CUDA.
- The software has a custom easyblock that encodes hard build knowledge, such as PyTorch,
  GROMACS, OpenFOAM, or Bundles with many Python or R extensions.
- You need module-hierarchy-style provenance (exact toolchain plus dependency versions in
  the name) or an EESSI-compatible recipe.
- A Spack build failed at `concretize` and EasyBuild has a recipe (`switch_source`).

**Don't choose EasyBuild when:**
- Spack has a `cuda` variant for it (resolver rule 3 wins). Spack also gives finer variant
  control and `^dep` fixes that auto-repair can target.
- It is a Python/ML package that conda-forge ships with CUDA for both arches (pixi, rule 2).
- Only an old-generation easyconfig exists (≤ 2023a, CUDA 12.1). The toolchain bump will
  likely cascade into dozens of tweaked dependencies. Expect failures, or a slow build of a
  whole old toolchain if the bump is dropped.
- You need a dependency-level fix that auto-repair can apply today. EasyBuild can't do this
  without site overrides or hooks (Q30).
- The target is `vr` before Q13 is answered (no CC yet).
- The easyconfig uses a binary CUDA-version-locked dependency (cuDNN, NCCL, TensorRT) whose
  `-CUDA-12.x` suffix conflicts with the image's CUDA 13 **(unverified impact)**.

## 9. Authoritative links

- Docs home: https://docs.easybuild.io/
- Release notes (5.4.2, 2026-10-30): https://docs.easybuild.io/release-notes/
- EasyBuild 5 changes: https://docs.easybuild.io/easybuild-v5/changes/ and https://docs.easybuild.io/easybuild-v5/enhancements/
- Installation: https://docs.easybuild.io/installation/
- Configuration: https://docs.easybuild.io/configuration/
- Command line, robot and `--try-*`: https://docs.easybuild.io/using-easybuild/
- Common toolchains and generations: https://docs.easybuild.io/common-toolchains/ and the policy at https://docs.easybuild.io/policies/toolchains/
- Easystack files: https://docs.easybuild.io/easystack-files/
- Hooks: https://docs.easybuild.io/hooks/
- Manipulating dependencies (`--filter-deps`, `--hide-deps`, minimal toolchains): https://docs.easybuild.io/manipulating-dependencies/
- External modules (`EXTERNAL_MODULE`, `$EBROOT*`): https://docs.easybuild.io/using-external-modules/
- Optarch: https://docs.easybuild.io/controlling-compiler-optimization-flags/
- Containers (`--containerize`): https://docs.easybuild.io/containers/
- Locks: https://docs.easybuild.io/locks/ ; interactive debugging: https://docs.easybuild.io/interactive-debugging-failing-shell-commands/
- Easyconfig parameters and templates (incl. CUDA): https://docs.easybuild.io/version-specific/easyconfig-parameters/ , https://docs.easybuild.io/version-specific/easyconfig-templates/
- Supported software list: https://docs.easybuild.io/version-specific/supported-software/
- Repos: https://github.com/easybuilders/easybuild-framework (options: `easybuild/tools/options.py`; exit codes: `easybuild/tools/build_log.py`), https://github.com/easybuilders/easybuild-easyblocks , https://github.com/easybuilders/easybuild-easyconfigs , https://github.com/easybuilders/easybuild-easyconfigs-archive
- PyPI: https://pypi.org/project/easybuild/
