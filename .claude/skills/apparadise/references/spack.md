# Spack reference (for apparadise)

Sources read on 2026-11-01: Spack GitHub releases v1.0.0, v1.1.0, v1.2.0 and v1.2.2; the
`spack/spack` v1.2.2 tree (`etc/spack/defaults`, `lib/spack/spack/cmd/*.py`, the vendored
archspec); `spack/spack-packages` `develop`, `releases/v2026.06` and `releases/v2026.10`
(`build_systems/cuda.py`, `build_systems/cmake.py`, `packages/cuda`, `packages/gromacs`);
`archspec/archspec-json` (v0.2.7, 2026-10-28); spack.readthedocs.io `latest` (containers and
binary-cache pages, which already describe the development line toward v1.4). Commands
below were checked against the v1.2.2 argument parsers, not run: Spack was not executed
while writing this file.

## 1. What Spack is

- A from-source package manager for HPC. One package recipe (`package.py`) describes all
  versions, variants and dependencies. A SAT/ASP solver (clingo) "concretizes" an abstract
  request into one fully pinned dependency graph, then builds it for a chosen
  microarchitecture.
- Best at: compiled HPC codes with many build options (MPI, CUDA, FFT, BLAS), per-target
  CPU tuning, mixing system externals with built packages, and reproducible environments
  (`spack.yaml` + `spack.lock`). Weak at: Python/ML stacks (wheels and conda are faster and
  better tested), and anything where upstream only supports one blessed build.
- **Current release: v1.2.2 (2026-07-20).** apparadise pins `SPACK_REF=v1.2.2`
  (`containers/base/Containerfile.builder`, `docs/stack.md`, Q16). History: v1.0.0 (2025-07),
  v1.1.0 (2025-11-14), v1.2.0 (2026-06-21), v1.2.1, v1.2.2. The `latest` docs already
  mention v1.4 features: check which version a docs page describes.
- **The 1.0 split:** the `builtin` package repository no longer lives in `spack/spack`. It
  is `spack/spack-packages`, with its own releases (v2025.07 ... v2026.06.0, v2026.10.0,
  2026-10-16). Spack v1.2.2 clones it on first use. Its default (`etc/spack/defaults/base/
  repos.yaml`) is `branch: releases/v2026.06`. Layout: `repos/spack_repo/builtin/packages/
  <name>/package.py` (underscores in directory names) and `.../build_systems/*.py`. Build
  system classes (`CudaPackage`, `CMakePackage`, ...) are imported from
  `spack_repo.builtin.build_systems.<x>`, no longer from `spack.package`.
  - Pin it: `spack repo update --commit <sha> builtin`, or in config
    `repos: {builtin: {git: https://github.com/spack/spack-packages.git, commit: <sha>}}`.
  - Move the clone: `spack repo set --destination /path builtin`.
  - The first run needs network access to clone it. A network-restricted builder must have
    the repo pre-cloned into the image or pointed at a local path.
- **v1.1:** `concretizer:compiler_mixing: false` (one compiler per language across the
  root unification set), the `%%` sigil (direct dependency plus a preference for the
  transitive link/run deps), externals that can have dependencies, git `include:`, and
  configuration scopes defined in `include.yaml` (see section 5).
- **v1.2:** a new parallel installer (DAG-level parallelism with a shared jobserver, TUI),
  concretization groups (`specs: - group: ... needs: [...] override: ...`), concretization
  caching on by default, SPDX 2.3 SBOMs at `$prefix/.spack/sbom`, `spack isolate`
  (`--self`, `--path`, `--undo`), and experimental Landlock sandboxing. Breaking:
  `spack gpg` trust now prompts unless `--yes-to-all`, and `include_concrete:` is
  deprecated in favour of `include: [spack.lock]`.

## 2. Core concepts

### Spec syntax

| Sigil | Meaning | Example |
|---|---|---|
| `@` | version or range (`@1.2:`, `@:1.4`, `@1.2:1.4`, `@=1.2` exact) | `gromacs@2026.3` |
| `+v` / `~v` (`-v`) | boolean variant on / off | `+mpi ~double` |
| `v=x,y` | multi-valued variant | `cuda_arch=90,120` |
| `v==x`, `++v` | **propagate** to all dependencies that have it | `cflags==-std=gnu17` |
| `%` | in 1.x: a **direct** dependency (usually the compiler) | `%gcc@14` |
| `%c,cxx=gcc %fortran=gfortran` | virtual assignment: which provider serves which language | |
| `%%` (1.1+) | direct dependency plus a preference for transitive deps | `%%gcc@14` |
| `^` | a dependency anywhere in the DAG (must unify) | `^openmpi@5 +cuda` |
| `target=` / `os=` / `platform=` | architecture | `target=x86_64_v4` |
| `cflags= cxxflags= ldflags=` | compiler flags | `cflags="-O3"` |

**Order matters (1.x).** Everything after `%dep` or `^dep` applies to that dependency, until
the next sigil. `pkg %gcc +foo` sets `+foo` on **gcc**. Put root variants and `target=`
right after `name@version`, then the `%` and `^` clauses.

> **apparadise gotcha (current template).** `recipes/_templates/spack.yaml.in` writes
> `"$SPEC +cuda cuda_arch=$CUDA_ARCH_CSV target=$SPACK_TARGET"`, and a repair appends to
> `$SPEC` (`buildqueue.py`: `f"{spec} {spec_append}"`). After the `binutils-gprofng` repair
> the root becomes `gromacs@2026.3+mpi ^binutils~gprofng +cuda cuda_arch=120 target=...`,
> so `+cuda` and `cuda_arch` attach to **binutils**, which has no such variant. The same
> happens for any requested spec containing `%gcc@..` or `^dep`. Safe forms: put the
> root attributes before the `%`/`^` part, or move them into
> `packages: <root>: require: [...]`. This is a finding, not yet a fix.

### Concretizer

- `spack spec -lI <spec>` shows the solved DAG (`-l` hashes, `-I` install status, `-t`
  dependency types). In an env: `spack -e DIR concretize -f`.
- Preference vs requirement (`packages.yaml`): `prefer:` and `variants:` are soft; `require:`
  is hard (unsatisfiable means failure, not fallback); `conflict:` forbids. `buildable: false`
  forces an external or an already-installed spec.
- `concretizer: unify: true` (one version of each package per environment, which is what one
  image wants), `false`, or `when_possible`. `reuse:` (default true) prefers installed and
  build-cache specs, so the binary cache shapes the result.
- v1.2 concretization groups let one environment solve sub-sets with different
  preferences. apparadise does not use them (one root per image).

### Environments (`spack.yaml`)

```bash
spack env create -d .        # or: spack env create myenv spack.yaml
spack -e . add 'gromacs@2026.3 +mpi +cuda cuda_arch=120'
spack -e . concretize -f     # writes spack.lock (fully pinned, commit it for reproducibility)
spack -e . install -j 32 --fail-fast
spack env activate -p .      # or: spack -e . env activate --sh  > activate.sh
```

Top-level keys inside `spack:`: `specs`, `packages`, `concretizer`, `view`, `config`,
`mirrors`, `repos`, `container`, `include`, `env_vars`, `modules`. Every config section can
appear here (the environment is the highest-precedence scope apart from `-C`).

### Externals

```yaml
packages:
  cuda:
    buildable: false
    externals:
    - spec: cuda@13.0.3
      prefix: /usr/local/cuda
```

- `spack external find [--not-buildable] [--path P] [--scope S] cuda cmake openssl`
  detects them. `spack external find --all` searches for all known detectable packages.
- Since 1.1 an external is a concrete spec and may declare dependencies.
- The version of an external may be newer than any `version()` in `package.py`. In
  `releases/v2026.06` the cuda package lists up to 13.0.2 and 12.9.1, while apparadise
  declares externals `13.0.3` and `12.9.2`. Externals with undeclared versions are accepted
  as far as I know (unverified for v1.2.2: test with `spack spec` once).

### Compilers are dependencies (1.0+)

- Compilers are ordinary packages (`gcc`, `llvm`, `nvhpc`, `intel-oneapi-compilers`).
  Packages declare `depends_on("c"|"cxx"|"fortran", type="build")`; `%gcc` satisfies those
  virtuals.
- `compilers.yaml` is deprecated. Compilers are externals in `packages.yaml`:

```yaml
packages:
  gcc:
    externals:
    - spec: gcc@13.3.0 languages:='c,c++,fortran'
      prefix: /usr
      extra_attributes:
        compilers: {c: /usr/bin/gcc, cxx: /usr/bin/g++, fortran: /usr/bin/gfortran}
```

  (`languages:=` is how `spack compiler find` records it, unverified in v1.2.2; let
  `spack compiler find` write it rather than typing it.)
- `toolchains.yaml` defines aliases such as `%clang_gfortran`, which expand to several `%`
  constraints and flags.
- `spack compiler find` / `spack compiler list` still exist and write `packages.yaml`.

### Mirrors and binary caches

- A mirror holds sources and/or binaries. `spack mirror add NAME URL`, `spack mirror list`,
  `spack mirror set --signed|--unsigned NAME`. `mirrors.yaml` keys used here: `url`,
  `signed`, `access_pair`, `autopush`; v1.2 adds `include_binary` / `exclude_binary`.
- **OCI build caches**: any OCI registry works as a binary cache.

```bash
spack mirror add --oci-username-variable REG_USER --oci-password-variable REG_TOKEN \
    apparadise-cache oci://registry.example/cache/spack-rtx
spack buildcache push --unsigned --update-index apparadise-cache gromacs
spack install --use-buildcache only gromacs      # binaries only; package:dependencies forms exist
```

  - HTTPS only. A plain-HTTP registry needs `oci+http://`.
  - Each spec is a blob plus a manifest tagged `<name>-<version>-<hash>.spack`.
  - `--base-image IMG` makes each pushed tag a runnable image: IMG plus one layer per
    runtime dependency. Spack itself is not included. The base's glibc must be compatible.
    Without a base image the tags are distroless (not runnable). Docker's overlay driver
    caps images at 128 layers.
  - Build-cache index views (v1.2) are not supported on OCI caches.
  - Signing: pushes are GPG-signed by default. `--unsigned` on push,
    `--no-check-signature` on install, or `signed: false` per mirror (the apparadise
    template uses this, with cosign signing the final image instead).
  - Registry auth against Zot through apparadise's token service (`/registry/token`,
    Q8, 2026-11-01) is not tested with Spack's OCI client (unverified).

### Views

`view: /opt/view` (or a mapping with `root`, `select`, `exclude`, `projections`, `link:
run|all|roots`) symlinks the environment into one prefix (`bin/`, `lib/` ...). apparadise
exposes `/opt/view` from the image, and the module wrappers run executables from it.

## 3. CUDA in Spack

- **`CudaPackage`** (`spack_repo.builtin.build_systems.cuda`) is the mixin. It adds
  `variant("cuda", default=False)`, a sticky multi-valued `cuda_arch` (only when `+cuda`),
  `depends_on("cuda", when="+cuda")`, a CUDA-version-per-arch table, and the
  host-compiler conflict matrix. Packages may instead declare their own `cuda` variant
  (apparadise's index regex accepts either: `CudaPackage` or `variant("cuda"`).
- **`cuda_arch` values** (spack-packages `develop`, 2026-11): `70 72 75 80 86 87 89 90 90a
  100 100a 100f 101* 103 103a 103f 107 107a 107f 110 110a 110f 120 120a 120f 121 121a 121f`
  (plus older ones). Minimum CUDA per value: `120` needs 12.8+, `120f`/`121*`/`100f`/`103*`
  need 12.9+, `110*` needs 13.0+, `101*` was renamed `110` in CUDA 13 (`101` only 12.8 to
  12.9), and `70` (Volta) is limited to `cuda@9.0:12.9`, so `generic` stays on CUDA 12.9.
  - **Rubin:** `develop` and `releases/v2026.10` list `107/107a/107f` "Rubin support"
    requiring `cuda@13.4:`. `releases/v2026.06` (Spack 1.2.2's default) has **no 107**.
    This is Spack's claim, not checked against NVIDIA or the hardware (unverified). Q13
    stays open: read `nvidia-smi --query-gpu=compute_cap` on the NVL72. It implies `vr`
    may need CUDA 13.4+ instead of the 13.0 in `targets.yml` (unverified).
- **What the arch list compiles to:**
  - Packages that use `CudaPackage.cuda_flags(...)` pass, per arch,
    `--generate-code arch=compute_X,code=sm_X --generate-code arch=compute_X,code=compute_X`:
    SASS **and PTX** for every listed arch.
  - `CMakePackage.define_cuda_architectures` passes `-DCMAKE_CUDA_ARCHITECTURES=<list>`
    (CMake 3.18+). A bare `120` in CMake means real + virtual (SASS + PTX), and `120-real`
    means SASS only (CMake documents this; per-package overrides vary).
  - Many packages map `cuda_arch` to their own option (GROMACS: `-DGMX_CUDA_TARGET_SM`).
    PTX embedding then follows that package's logic. **Verify PTX in the test stage**:
    `cuobjdump --list-ptx <lib or exe>` should show `sm_<ptx_arch>`.
  - Family values (`120f`) exist in `CudaPackage` and follow the CLAUDE.md "prefer `f`"
    rule. `containers/targets.yml` currently uses plain `120`. Whether every CMake/package
    path accepts `f` suffixes is unverified.
- **List the packages with CUDA:**
  - `spack pkg grep -l CudaPackage` (args pass straight to grep) or
    `spack pkg grep -lE 'variant\("cuda"'`.
  - `spack info --all <pkg>` shows the `cuda` and `cuda_arch` variants and their conditions.
  - Offline (as apparadise's index does): grep a `spack-packages` checkout,
    `grep -lE 'CudaPackage|variant\(\s*"cuda"' repos/spack_repo/builtin/packages/*/package.py`.
- **CUDA as an external**: always `buildable: false` with the toolkit from the NGC base
  image (`/usr/local/cuda`). Building `cuda` in Spack just downloads the runfile, which
  duplicates gigabytes in the image.
- **`%cuda` in conflicts.** The host-compiler conflicts are written against `%cuda` (CUDA
  as a direct dependency) inside `with when("+cuda %cuda~allow-unsupported-compilers")`.
  `cuda+allow-unsupported-compilers` disables them (it passes `-allow-unsupported-compiler`
  to nvcc). Use it only as a last resort, because nvcc's host check exists for real
  header incompatibilities.

### Archspec targets

| apparadise target | `spack_target` | Notes |
|---|---|---|
| `rtx` | `x86_64_v4` | AVX-512 level. GCC 11.1+ flag `-march=x86-64-v4 -mtune=generic`. `zen4`/`zen5`/`sapphirerapids` descend from it. |
| `generic` amd64 | `x86_64_v3` | AVX2/FMA. |
| `generic` arm64 | `aarch64` | Plain Armv8. |
| `vr` | `neoverse_v2` | Grace (Armv9.0, cpupart 0xd4f). GCC 12.3+ / Clang 16+ use `-mcpu=neoverse-v2`. |

- **Vera (Olympus cores):** archspec-json added `olympus` (vendor Nvidia, parent
  `armv9.2a`, cpupart 0x010; GCC 15.1+ `-mcpu=olympus`, Clang 21.1+, nvhpc 26.9+) on
  2026-10-28 (archspec-json v0.2.7). Spack **develop** vendors it. **Spack v1.2.2 does
  not**: it knows `neoverse_v2` but not `olympus` or `neoverse_v3ae`. Note that
  `olympus` does not descend from `neoverse_v2` in archspec, so `neoverse_v2` binaries
  running on Vera is the repo's assumption (Q13), unverified on hardware. Switch only after
  a Spack release ships `olympus` and `spack arch` on a Vera node reports it.
- `spack arch` / `spack arch --known-targets` show the host and known targets.
  `target=x86_64_v4:` (a trailing colon) means "this or newer".

## 4. Containers

`spack containerize` (environment only) turns `spack.yaml` with a `container:` section into
a two-stage Dockerfile (`format: docker`) or Singularity definition.

- **Build stage** (`images: build`, or `spack/<os>` with `images: {os:, spack:}`): writes
  the manifest to `/opt/spack-environment/spack.yaml` (install tree `/opt/software`, view
  `/opt/views/view`), runs `spack install --fail-fast`, `spack gc -y`, strips binaries
  (`strip: true`, the default), and writes `activate.sh`.
- **Final stage** (`images: final`): copies **only** `/opt/spack-environment`,
  `/opt/software` and `/opt/views`, symlinks `/opt/view -> /opt/views/view`, and sets
  `ENTRYPOINT ["/entrypoint.sh"]` (sources `activate.sh`).
- With custom `build`/`final` images (apparadise: `cache/base-builder:<cuda>-<target>` and
  `cache/base-runtime:...`), *you* must ensure that Spack works in the build image and
  that the binaries run on the final image. `os_packages: {command: apt, build: [...],
  final: [...]}` installs distro packages, `labels:` adds OCI labels, and `template:`
  supplies a custom Jinja template.
- `spack containerize --last-stage {bootstrap,build,final}` cuts the output short.
  `--list-os` lists the bootstrappable OSes. `depfile` is deprecated (removed in v1.4).
- The manifest's own `view:` may be overridden by containerize's layout (unverified).
  Either way `/opt/view` exists in the final image.

**How an apparadise builder can run it** (`docs/operations/hand-build.md` step 3):

```bash
cd recipes/<name>/<version>/<target>            # holds spack.yaml
spack -e . containerize > Containerfile
podman build --platform <platform> -t <reg>/public/<name>:<version>-<target> .
```

Caveats for that path:
1. `podman build` does not see the job's registry credentials, so the authenticated `cache`
   OCI mirror cannot be read or written from inside the build stage (the `cache` project is
   not anonymous since Q8). Sources and the `spack-packages` clone also need network access
   during `RUN`.
2. Alternative given in the hand-build doc: run `spack -e . install` directly in the builder
   container, with the per-job credentials in `REG_USER`/`REG_TOKEN` env vars and the mirror
   readable, push to the cache with `spack buildcache push`, then copy `/opt/software` and
   the view into `FROM base-runtime`.
3. Or `spack buildcache push --base-image <base-runtime>@sha256:... --tag <name>-<version>-<target>
   <mirror> <root>`, which produces a runnable image with no Dockerfile and one layer per
   dependency. Check the 128-layer limit and that the Enroot `.sqsh` conversion and the
   SBOM/cosign steps still apply. Which of the three to use is an owner decision, not
   recorded yet.
- Build natively (CPU of the target arch, no GPU, never QEMU). Spack's builds need no GPU.
  Only the `test` stage does.

## 5. Cheat sheet

```bash
# Install / bootstrap (Python 3.6+, git, a C/C++ compiler, patch, tar, unzip, curl)
git clone --depth=2 --branch v1.2.2 https://github.com/spack/spack.git /opt/spack
. /opt/spack/share/spack/setup-env.sh          # or call /opt/spack/bin/spack directly
spack bootstrap now                             # fetch clingo etc. ahead of time
spack bootstrap status
spack isolate --self                            # keep caches/config out of ~/.spack (1.2+)

# Discover
spack list cuda                                 # names matching a pattern; -d searches descriptions
spack info --all gromacs                        # versions, variants (with when=), deps
spack versions gromacs                          # safe + remote versions
spack pkg grep -l CudaPackage                   # grep all package.py files
spack providers mpi
spack arch; spack arch --known-targets

# Solve and build
spack spec -lI 'gromacs@2026.3 +mpi +cuda cuda_arch=120 target=x86_64_v4 %gcc@13'
spack install -j 32 --fail-fast <spec>
spack install --use-buildcache only|never|auto <spec>
spack find -lvd; spack uninstall -a <spec>; spack gc -y; spack clean -a
spack location -i <spec>; spack load <spec>
spack -d install ...                            # debug; logs at $prefix/.spack/spack-build-out.txt

# Environments
spack env create -d .; spack -e . concretize -f; spack -e . install
spack env activate -p .; spack env deactivate; spack env status
spack -e . containerize > Containerfile

# Externals and compilers
spack external find --not-buildable cuda cmake perl
spack compiler find /usr/bin; spack compiler list

# Mirrors / build caches
spack mirror add [--unsigned] [--oci-username-variable U --oci-password-variable P] NAME URL
spack mirror list; spack mirror set --unsigned NAME
spack buildcache push [--unsigned] [--update-index] [--base-image IMG] [--tag T] NAME <specs>
spack buildcache list [-a]; spack buildcache check-index; spack buildcache update-index NAME

# Package repos (1.0+)
spack repo list; spack repo update [--commit SHA] builtin; spack repo set --destination DIR builtin

# Config
spack config get packages; spack config blame packages; spack config edit --scope site packages
spack -C /path/to/scope-dir <cmd>               # extra highest-precedence scope
```

**Config scopes (1.1+).** All scopes come from `$spack/etc/spack/include.yaml`. The
defaults (`$spack/etc/spack/defaults`, lowest) are followed by named included scopes:
`system` (`/etc/spack`), `site` (`$spack/etc/spack/site`) and `user` (`~/.spack`), then
`$spack/etc/spack` itself, which now has a **higher** priority than `~/.spack` (inverted in
1.1). On top come the active environment, then `-C` scopes. `include::[]` in an
environment drops all but the defaults. `SPACK_DISABLE_LOCAL_CONFIG=1` skips `user` and
`system`. Use `spack config blame` rather than reasoning about the order. Builders should
set `SPACK_DISABLE_LOCAL_CONFIG=1` (or `spack isolate`) so that the per-job `spack.yaml` is
the only input.

## 6. Common failures and fixes

| Symptom | Cause | Fix |
|---|---|---|
| `concretization failed`, `cannot satisfy`, `No version for 'x' satisfies` | conflicting `require:`, version not in the pinned spack-packages, variant that does not exist on a node | `spack spec` the root alone, then add constraints one at a time. Read the "condition(s) that caused an error" lines. Check `spack info` on the **pinned** repo, not `develop`. apparadise: stage `concretize`; the repair `spack-unsatisfiable` switches source. |
| `variant 'cuda' does not exist` on a dependency | spec order: `+cuda` landed after a `^dep`/`%dep` | put root variants before `%`/`^` (see section 2) |
| `conflicts with '%gcc@15:' when %cuda@:12.9` | host GCC too new for nvcc | see the matrix below |
| Concretizer picks a source build of `cuda` | missing `buildable: false` external, or the external's version is excluded by a package's `depends_on("cuda@...")` | keep `buildable: false`. Check `spack spec` for `[e]` on cuda. A package that needs a newer CUDA than the base image cannot build on that target. |
| `requirement ... target=x86_64_v4` cannot be satisfied | the external, or the compiler, does not support the target (GCC < 11.1 for `x86_64_v4`) | use a newer compiler, or relax `require` to `prefer` for that package |
| Build OOM, or killed with many `-j` | the v1.2 installer runs several builds at once | `spack install -j N`, `config: build_jobs: N`, `concurrent_packages` (unverified key name in 1.2) |
| `bool`/`true`/`false` typedef errors | GCC 15 defaults to C23 | `cflags==-std=gnu17` (apparadise repair `c23-old-code`) |
| gprofng errors building binutils | glibc 2.42+ `_Generic` macros | `^binutils~gprofng` (repair `binutils-gprofng`) |
| gnulib `memchr`/`bsearch` `_Generic` clash | glibc 2.42+ with old gnulib | `-std=gnu17` does **not** help. Needs a forced include (Q31). The Ubuntu 24.04 base (glibc 2.39) does not hit it. |
| First run hangs or fails offline | clones spack-packages and bootstraps clingo from the network | pre-clone the repo, `spack bootstrap now` and `spack bootstrap mirror` in the image |
| GPG trust prompt in CI (1.2) | `spack gpg` now asks | `--yes-to-all`, or use unsigned mirrors |

**CUDA / host GCC matrix** (from `CudaPackage` conflicts, spack-packages `develop`;
`releases/v2026.06` stops at the `gcc@16` row):

| CUDA | max GCC | max Clang |
|---|---|---|
| ≤ 12.3 | 12 | 16 |
| 12.4–12.5 | 13 | 17 |
| 12.6 | 13 | 18 |
| 12.7–12.9 | 14 | 19 |
| 13.0 | 15 | 20 |
| 13.1–13.3 | 15 | 21 |
| 13.4 | 16 | 22 |

Also `%gcc@13.2.0` conflicts with `cuda@:12.4` on aarch64. CUDA 13 drops `%intel` (classic).
(Values derived from `conflicts("%gcc@N:", when="%cuda@:X")`. The NVIDIA install guide is
the primary source; spot-check it before relying on an edge row.)

**GCC 15 on Ubuntu 26.04 hosts.** Ubuntu 26.04 ships GCC 15 (15.2) as the default, GCC
11–14 as alternatives and 16 optionally, plus glibc 2.43 (secondary sources and the repo's
own Q31 check; not from Ubuntu release notes). The owner wants DGX OS 8.1 / Ubuntu 26.04
(Q16 answer), while the current base images are Ubuntu 24.04 (GCC 13, glibc 2.39).
Consequences if the build base moves to 26.04:
- `generic` (CUDA 12.9) with the system GCC 15 is a **concretize-time conflict**. Register
  `gcc-14` as the compiler external (`apt install gcc-14 g++-14 gfortran-14`,
  `spack compiler find /usr/bin/gcc-14 ...`, then `%gcc@14` or
  `packages: all: require: ['%gcc@14']`, unverified syntax for compiler requirements in
  v1.2) or keep `generic` on a 24.04 base.
- `rtx`/`vr` on CUDA 13.0: GCC 15 is allowed. GCC 16 is not (needs CUDA 13.4).
- C23 default and glibc 2.42+ `_Generic` failures become common (see the table).
  Spack's `cflags==` propagation reaches dependencies; EasyBuild's does not (Q30).

## 7. How apparadise uses Spack

- **Resolver position:** rule 3 of `requester's choice → pixi (CUDA builds on both
  linux-64 and linux-aarch64) → Spack with a cuda variant → EasyBuild CUDA easyconfig →
  CUDA port → no-recipe` (`web/apparadise/resolver.py`). Detail: a conda package that
  wraps compiled HPC code loses to Spack when Spack has a non-`py-` CUDA recipe
  (`compiled_in_spack`), so LAMMPS/GROMACS go to Spack. In rule 5 (CUDA port), Spack is
  first in `PORT_ORDER`.
- **Index:** `web/apparadise/index.py:scan_spack` reads a `spack-packages` checkout
  (`repos/spack_repo/builtin/packages/*/package.py`, falling back to the pre-1.0
  `var/spack/repos/builtin/packages`). `has_cuda` = `CudaPackage` or `variant("cuda"`.
  Versions come from `version("...")` (the newest 12, branches dropped). `py-` is stripped
  for name matching. Spack counts as available on both arches. *Open point:* the index
  scans whatever checkout `tools/refresh-index` uses, while the builder's Spack v1.2.2
  defaults to `releases/v2026.06`, so the index can advertise versions or `cuda_arch`
  values the builder cannot see. Pinning both to the same spack-packages ref is not
  decided (unverified which ref the nightly job uses).
- **Generate:** `python -m apparadise.recipes spack <name> <version> <target> --spec
  '<spec>'` renders `recipes/_templates/spack.yaml.in` with values from
  `containers/targets.yml` into `recipes/<name>/<version>/<target>/spack.yaml`.
  `SPEC_RE` allows `[A-Za-z0-9_.@%+~=:,^ -]`, max 200, no `/` or quotes (so no paths or
  quoted flags in specs). `vr` refuses to render while `cuda_arch` is null (Q13).

Rendered example (`gromacs 2026.3 rtx --spec gromacs@2026.3+mpi`):

```yaml
spack:
  specs:
  - "gromacs@2026.3+mpi +cuda cuda_arch=120 target=x86_64_v4"
  packages:
    all:
      require: ["target=x86_64_v4"]
      variants: [+cuda, "cuda_arch=120"]
    cuda:
      buildable: false
      externals:
      - spec: cuda@13.0.3
        prefix: /usr/local/cuda
    rdma-core:
      buildable: false
      externals:
      - spec: rdma-core@50
        prefix: /usr
  concretizer:
    unify: true
  view: /opt/view
  config:
    install_tree:
      root: /opt/software
  mirrors:
    apparadise-cache:
      url: oci://$REGISTRY/cache/spack-rtx
      signed: false
  container:
    format: docker
    images:
      build: $REGISTRY/cache/base-builder:13.0-rtx
      final: $REGISTRY/cache/base-runtime:13.0-rtx
```

  (`$REGISTRY` is filled in at render time.) `generic` renders
  `cuda_arch=70,75,80,86,89,90,100,120` on CUDA 12.9.2, with `x86_64_v3` or `aarch64`.
  `packages: all: variants:` is only a *preference*. Dependencies without a `cuda` variant
  ignore it.
- **Failure stages** (fixed set): `no-recipe` (resolver rule 6), **`concretize`** (`spack
  concretize`/`spack spec` fails: unsatisfiable, conflict, unknown version),
  **`build`** (`spack install` or the container build fails), `package` (final image,
  SBOM), `test` (GPU smoke test on a real node, e.g. `srun --container-image=...`),
  `publish` (cosign, `.sqsh` staging, catalog). Spack's own SBOMs
  (`$prefix/.spack/sbom`, v1.2) could feed the `package` stage SBOM (not decided).
- **Auto-repair** (`recipes/repairs.yml`, at most 2 attempts per release, never the same
  rule twice): Spack actions are `spec_append` (`^binutils~gprofng`,
  `cflags==-std=gnu17`) and `switch_source` on concretize failures. See the spec-order
  gotcha in section 2: appended `^dep` text currently breaks the template's trailing
  `+cuda ...`.
- **Logs:** `spack install` writes per-package `spack-build-out.txt` and
  `spack-build-env.txt` in the stage, kept in `$prefix/.spack/` on success. On failure
  the first `error:` line feeds the failure signature after scrubbing (paths, hostnames,
  tokens).

## 8. When to choose Spack

Choose Spack when:
- the package is compiled HPC code (MPI, CUDA kernels, FFT/BLAS choices) and Spack has a
  `cuda` variant (resolver rule 3), especially when conda-forge only wraps it;
- you need per-target CPU tuning (`x86_64_v4`, `neoverse_v2`) and an explicit
  `cuda_arch` list per target;
- a failure needs a dependency-level fix (`^dep~variant`, `flag==` propagation), which
  EasyBuild's `--try-amend` cannot express;
- the requester explicitly asks for Spack (rule 1), or a CUDA port is needed (rule 5,
  Spack first).

Do not choose Spack when:
- it is a Python/ML stack with conda-forge CUDA builds for both linux-64 and linux-aarch64
  (rule 2, pixi), e.g. PyTorch, JAX, RAPIDS: Spack's `py-torch` builds are slow and
  fragile;
- Spack has the package only without `cuda` while EasyBuild has a CUDA easyconfig
  (rule 4);
- the recipe needs a CUDA newer than the target's base image (cuda is never built by
  Spack here). Pick another source or wait for the base bump;
- the fix needs a file in the image (forced include, patch file): specs cannot carry paths
  (`SPEC_RE`);
- never mix Spack with EasyBuild or conda in one image, even for a single dependency.

## 9. Authoritative links

- Docs (latest): https://spack.readthedocs.io/en/latest/ (pick the versioned docs,
  e.g. `/en/v1.2.2/`, when behaviour differs; unverified that a v1.2.2 build exists)
  - Spec syntax: https://spack.readthedocs.io/en/latest/spec_syntax.html
  - Environments: https://spack.readthedocs.io/en/latest/environments.html
  - packages.yaml / externals / requirements: https://spack.readthedocs.io/en/latest/packages_yaml.html
  - Compilers: https://spack.readthedocs.io/en/latest/configuring_compilers.html
  - Configuration and scopes: https://spack.readthedocs.io/en/latest/configuration.html
  - Binary caches (OCI): https://spack.readthedocs.io/en/latest/binary_caches.html
  - Containers: https://spack.readthedocs.io/en/latest/containers.html
  - Package repositories: https://spack.readthedocs.io/en/latest/repositories.html
  - Package API: https://spack.readthedocs.io/en/latest/package_api.html
- Releases: https://github.com/spack/spack/releases (v1.0.0, v1.1.0, v1.2.0, v1.2.2)
- Packages: https://github.com/spack/spack-packages (and its releases);
  `CudaPackage`: `repos/spack_repo/builtin/build_systems/cuda.py`
- archspec: https://github.com/archspec/archspec-json (`cpu/microarchitectures.json`),
  https://archspec.readthedocs.io
- NVIDIA host-compiler support: CUDA Installation Guide for Linux, "System Requirements".
- Ubuntu 26.04 toolchain (secondary): https://linuxconfig.org/ubuntu-26-04-release-date-and-new-features-in-resolute-raccoon
- Repo: `recipes/_templates/spack.yaml.in`, `containers/targets.yml`,
  `web/apparadise/{index,resolver,recipes,buildqueue,repair}.py`, `recipes/repairs.yml`,
  `docs/operations/hand-build.md`, `QUESTIONS.md` (Q8, Q13, Q16, Q29–Q31).
