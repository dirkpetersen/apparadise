# pixi and conda-forge (with conda/mamba as background)

Reference for the `pixi` source in apparadise. Facts were checked on 2026-11-01 against the
pixi docs (pixi.prefix.dev, which pixi.sh now redirects to), the pixi CHANGELOG, conda-forge.org,
the conda docs and conda-forge feedstock files on GitHub. Anything not confirmed against a
source is marked **(unverified)**.

## 1. What they are, and what they're good at

- **conda-forge** is the community conda channel: one GitHub "feedstock" repo per package,
  built by CI into `.conda` binaries for `linux-64`, `linux-aarch64`, `osx-*`, `win-64` and
  `noarch`. It ships prebuilt CUDA variants of the Python/ML stack (PyTorch, JAX, CuPy, ...)
  plus NVIDIA's split CUDA toolkit (`cuda-*`, `libcublas`, ...). It is binary-first: you
  consume the builds as published, and **you don't choose arch flags**. The feedstock does.
- **pixi** (prefix.dev, Rust, built on the `rattler` libraries) is a project/workspace manager
  for conda packages. It solves with `resolvo`, resolves PyPI packages through `uv`, writes a
  multi-platform lockfile (`pixi.lock`) and installs into `.pixi/envs/<env>`. It ships as one
  static binary.
- **conda / mamba** are the classic per-environment CLIs. They share the same channels,
  MatchSpec syntax and virtual packages (`__cuda`, `__glibc`, ...). pixi adds per-project
  manifests, lockfiles and multi-environment workspaces.
- **Best at:** reproducible Python/ML stacks with CUDA, locked for several platforms from one
  host, without root and without compiling anything. **Weak at:** per-target arch tuning, MPI
  linked against the site fabric, and big compiled HPC codes (use Spack or EasyBuild for those).

**Versions:** pixi **0.83.0**, released 2026-10-22 (GitHub releases). apparadise pins
`PIXI_VERSION=0.83.0` in `containers/base/Containerfile.builder`, and `docs/stack.md` lists it.
conda-forge's global pinning (checked 2026-11-01) builds CUDA packages for
`cuda_compiler_version` **None, 12.9 and 13.4** on linux-64, linux-aarch64 and win-64. The
newest CUDA toolkit on conda-forge is **13.4** (`cuda-nvcc` 13.4.92). The conda-forge CUDA page
says CUDA 13.3 is the last CTK release on the `nvidia` channel. From 13.4 on, conda-forge is the
only source.

## 2. Core concepts

### Manifest

A manifest is `pixi.toml`, or a `[tool.pixi.*]` section in `pyproject.toml`. `[workspace]` is the
current name for the table, and `[project]` is a deprecated alias.

```toml
[workspace]
name = "pytorch"
channels = ["conda-forge"]            # order = priority
platforms = [                          # "rich platforms" (pixi >= 0.71.0)
  { platform = "linux-64", cuda = "12" },
  { platform = "linux-aarch64", cuda = "12" },
]
channel-priority = "strict"            # default; also "flexible", "disabled" (discouraged)
solve-strategy = "highest"             # default; "lowest", "lowest-direct"
requires-pixi = ">=0.83"

[dependencies]                         # conda MatchSpecs
python = "3.12.*"
pytorch = { version = "==2.14.1", build = "cuda*" }
cuda-version = "12.9.*"                # pins the CUDA minor of every CUDA package

[pypi-dependencies]                    # solved by uv AFTER the conda solve
some-pure-python-pkg = "*"

[feature.test.dependencies]
pytest = "*"

[environments]
test = { features = ["test"], solve-group = "default" }

[target.linux-aarch64.dependencies]    # platform-specific additions
# ...

[activation]
env = { OMP_NUM_THREADS = "1" }
```

- **Features and environments:** a feature is a named bundle of dependencies, tasks,
  activation and platforms. An environment is a list of features plus the default feature,
  unless `no-default-feature = true`. A `solve-group` makes several environments share one
  solve, so the same package gets the same version in each.
- **Platforms** are conda subdirs (`linux-64`, `linux-aarch64`, ...). The lockfile holds a
  solution for **every** listed platform. `pixi lock` can solve the aarch64 part on an x86 host,
  because solving only needs repodata and the declared virtual packages.
- **Lockfile:** `pixi.lock` (YAML) pins exact URLs and hashes for each environment and
  platform. Commit it. `pixi install --locked` aborts if the lockfile is out of date with the
  manifest. `pixi install --frozen` installs from the lockfile as-is, without checking it.

### System requirements and virtual packages

- **`[system-requirements]` is deprecated since pixi 0.71.0 (2026-06-24)** in favour of rich
  platform entries. Old manifests are still parsed and migrated automatically. The old form
  `[system-requirements] cuda = "12"` becomes
  `platforms = [{ platform = "linux-64", cuda = "12" }]`.
  CLI: `pixi workspace platform add linux-64 --cuda 12`.
- Recognised keys: `cuda`, `archspec`, `glibc`, `linux`, `macos`, `windows`, plus a raw
  `__name = "version"` escape hatch. A named entry,
  `{ name = "linux-64-cuda", platform = "linux-64", cuda = "12" }`, lets a feature bind to
  `platforms = ["linux-64-cuda"]` (this replaces `[feature.X.system-requirements]`).
- Defaults for a bare `"linux-64"` entry: `__linux = "4.18"`, `__glibc = "2.28"`. Defaults for
  `linux-aarch64` are not listed on the page, but are presumably the same **(unverified)**.
- pixi 0.71 also added **`__cuda_arch`** (CEP 46, "the GPU's compute capability"). Since 0.83.0,
  channels can register their own virtual-package detectors.
- Since pixi 0.42, a declared `cuda` requirement no longer blocks a GPU-less machine **unless
  the installed packages actually depend on `__cuda`**. CUDA builds of PyTorch do
  (`run: __cuda`), so they still need an override on GPU-less builders (see section 6).

### Virtual packages

| Virtual pkg | Meaning | Override env var |
|---|---|---|
| `__cuda` | max CUDA version the **driver** supports | `CONDA_OVERRIDE_CUDA=12.9` (`""` = no driver) |
| `__cuda_arch` | GPU compute capability `{major}.{minor}` (pixi) | `CONDA_OVERRIDE_CUDA_ARCH` |
| `__glibc` | system glibc | `CONDA_OVERRIDE_GLIBC=2.28` |
| `__linux` | kernel version | `CONDA_OVERRIDE_LINUX` |
| `__archspec` | CPU microarch (build string, e.g. `x86_64_v3`) | `CONDA_OVERRIDE_ARCHSPEC` (`0` = unknown) |
| `__unix`, `__osx`, `__win`, `__conda` | OS / conda version | `CONDA_OVERRIDE_OSX`, `_WIN` |

Setting an override to an empty string disables that virtual package in pixi. Run `pixi info`
(or `conda info`) to see what was detected. conda's `.condarc` equivalent is
`override_virtual_packages:`.

**Microarch:** conda-forge says recipes must not depend on `__archspec` directly. They use the
`x86_64-microarch-level` helper packages (levels 1/3/4), giving the preferred level a higher
build number, and the solver then picks the best variant for the host. **No aarch64 equivalent
is documented**, so on `vr`, conda-forge binaries are generic armv8 builds (unverified for
individual packages). TensorFlow's feedstock, for example, builds `microarch_level` 1/3/4 on
linux-64.

### Channels and priority

- Use `conda-forge` only. Never mix in `defaults` (anaconda): the conda-forge tips page warns
  about library mismatches. Don't add the legacy `pytorch` channel either. CUDA 12.5+ packages
  on `nvidia` and conda-forge are identical, but there is no reason to add `nvidia`.
- With `strict` priority, a package found in a higher channel is never taken from a lower one.
- Mirrors: `https://prefix.dev/conda-forge` is a full mirror that the pixi docs use in
  examples. A channel can also be an URL to an internal proxy, configured with `mirrors` in
  `.pixi/config.toml` (details unverified).

### Build variants and build strings

A feedstock builds a matrix (`.ci_support/*.yaml`) over `cuda_compiler_version`, python,
blas, `c_stdlib_version`, `arm_variant_type`, and so on. Variants differ in the **build
string**. PyTorch's recipe, for example:

```
cuda{{ cuda_compiler_version | replace('.', '') }}_{{ blas_impl }}_py{{ CONDA_PY }}_h<hash>_<n>
cpu_{{ blas_impl }}_py{{ CONDA_PY }}_h<hash>_<n>
```

This gives `cuda129_generic_py312_h..._0` and `cuda134_mkl_py312_h..._0`. To select a variant:
`build = "cuda*"` in pixi, `"pytorch=*=cuda*"` in conda, or a metapackage (`pytorch-gpu`,
`pytorch-cpu`). Build-string conventions are **per feedstock**: many use `cuda*`, but some
mark CUDA only through dependencies such as `cuda-version` or `libcublas`.

## 3. CUDA specifics

### Does a package have CUDA builds for linux-64 and linux-aarch64?

You can check in four ways, from most to least authoritative:

1. **repodata:** `https://conda.anaconda.org/conda-forge/<subdir>/repodata.json` (plus a
   `.zst` variant). The prefix.dev mirror serves the same paths **(unverified)**. Under
   `packages` and `packages.conda`, look for records whose `build` contains `cuda` **or** whose
   `depends` has `cuda-version`, `cudatoolkit`, `cuda-cudart*` or `libcublas*`, or `__cuda`.
   Check both `linux-64` and `linux-aarch64`.
2. **`pixi search`** (MatchSpec argument, `--platform`/`-p` takes a subdir, `--json`,
   `-l/--limit` versions, default 5):
   ```bash
   pixi search -c conda-forge -p linux-aarch64 -l -1 'pytorch[build=cuda*]'   # bracket form (unverified)
   pixi search -c conda-forge -p linux-64 --json pytorch | jq '.[].build'
   ```
   With conda or mamba:
   `conda search -c conda-forge --subdir linux-aarch64 'pytorch=*=cuda*'`.
3. **Feedstock `.ci_support/`:** in `github.com/conda-forge/<name>-feedstock`, look for files
   named `linux_aarch64_...cuda_compiler_version12.9...yaml`. These show what the **current
   recipe** builds, which is not the same as what has been published for an old version.
4. **`cuda_compiler_version` in the recipe** (`# [cuda_compiler_version != "None"]`
   selectors). CUDA builds only happen when the feedstock sets `CF_CUDA_ENABLED` in
   `conda-forge.yml` and builds on Linux x86_64/aarch64 or win64 (pinning file).

Feedstock state on 2026-11-01:

| Package | linux-64 CUDA | linux-aarch64 CUDA | CUDA versions | archs compiled |
|---|---|---|---|---|
| pytorch (`pytorch-cpu-feedstock`, 2.14.1) | yes | yes (sbsa; tegra on 12.9) | 12.9, 13.4 | 12.9: `5.0;6.0;7.0;7.5;8.0;8.6;9.0;10.0;12.0+PTX`; 13.x: `7.5;8.0;8.6;9.0;10.0;11.0;12.0+PTX` (aarch64 adds 8.7) |
| jaxlib | yes | yes | 12.9, 13.0 | 12: `sm_60,70,75,80,86,89,90,100,120,compute_120`; 13: `sm_75,80,86,89,90,100,110,120,compute_120` |
| cupy | yes | yes | 12.9, 13.4 | (not checked) |
| vllm | yes | yes | 12.9, 13.0, 13.4 | (not checked) |
| tensorflow | yes | **no** (no linux-aarch64 CUDA job) | 12.9, 13.0 | (not checked) |

So TensorFlow **fails the both-platforms rule** on conda-forge. **sm_120** (RTX PRO 6000
Blackwell) is covered as SASS by PyTorch and JAX, with PTX for 12.0 on top. **sm_70 (Volta)
is only in the CUDA 12.x builds**, because CUDA 13 dropped it. The `generic` target therefore
needs `cuda-version = "12.9.*"`, which matches `containers/targets.yml`. The Rubin compute
capability is still open (Q13). Whether it can JIT from `compute_120` PTX is unverified.

**Default arch list of the conda-forge `nvcc`** (cuda-nvcc 13.4). It is used whenever a recipe
doesn't set its own: the activation script exports `CUDAARCHS`, unless it is already set, to
`75-real;80-real;86-real;89-real;90a-real;100f-real;103f-real;120a-real;121f-real;121-virtual`
on linux-64. linux-aarch64 adds `87-real` and `110-real`. That is, `sm_120a` SASS plus
`compute_121` PTX, and no sm_70.

### Selecting CUDA builds

- Declare `cuda` on the platform (or `[system-requirements] cuda`). Without it, pixi solves
  **CPU-only** builds, because no `__cuda` exists and the CUDA builds require it.
- `cuda-version = "12.9.*"` (or `cuda-version=13` in conda) pins the CUDA major or minor across
  all `cuda-*` and `lib*` packages. This is the metapackage everything CUDA depends on.
- Require the variant explicitly with `build = "cuda*"`, or with a metapackage such as
  `pytorch-gpu`.
- The `__cuda` value means "the driver supports at least X". The CUDA user libraries
  (`cuda-cudart`, `libcublas`, ...) come **from conda-forge inside the env**, not from the base
  image's toolkit. Only `libcuda.so`, the driver, comes from the host. Conda can never install
  the driver, NVML, Fabric Manager, IMEX or Nsight Systems.
- Split packages: `libX` holds the runtime libraries, `libX-dev` the headers and CMake files,
  and `libX-static` the static libraries. The metapackages are `cuda-runtime`,
  `cuda-libraries`, `cuda-libraries-dev`, `cuda-compiler`, `cuda-tools` and `cuda`. The
  monolithic `cudatoolkit` is CUDA 11 and older.
- **ARM:** CUDA 12.9 has `arm-variant=*=sbsa` (servers) and `*=tegra` (Jetson). From CUDA 13
  on, there is no separate Tegra variant. Grace and Vera servers use **sbsa**.

### Building without a GPU

```bash
CONDA_OVERRIDE_CUDA=12.9 pixi install --locked            # GPU-less builder
CONDA_OVERRIDE_CUDA=13.0 pixi lock                         # lock on a laptop
CONDA_OVERRIDE_CUDA=12.9 conda create -n t -c conda-forge "pytorch=*=cuda*"
```

conda-forge documents `CONDA_OVERRIDE_CUDA` for exactly this case ("HPC login nodes or CI
runners"). The packages still **install** on a GPU-less host. They just can't run until they
reach a GPU node.

### PyTorch, JAX and TensorFlow notes

- **PyTorch:** on conda-forge it is `pytorch`, built from `pytorch-cpu-feedstock`.
  `pytorch-gpu` / `pytorch-cpu` are selector metapackages, and `libtorch` is the C++ library.
  PyTorch's own `pytorch` conda channel is legacy. Don't mix it in. The pixi docs offer a PyPI
  route (`[pypi-dependencies] torch` with `index = "https://download.pytorch.org/whl/cu1xx"`),
  but that puts CUDA wheels in the env next to conda CUDA libs. Pick one source.
- **JAX:** `jax` is noarch Python, `jaxlib` is compiled (CUDA builds on both linux subdirs).
  Pin `jaxlib` with `build = "cuda*"` **(unverified exact build-string prefix)**.
- **TensorFlow:** conda-forge has CUDA builds for linux-64 only. On apparadise that sends it to
  Spack (rule 3), or to a port attempt.
- The resolver's alias table maps `torch` and `pytorch-gpu` to `pytorch`
  (`web/apparadise/index.py:normalise`).

## 4. Containers

The official pattern (pixi docs, "Container" deployment page) is a multi-stage build that
copies only the env into a slim runtime image:

```dockerfile
FROM ghcr.io/prefix-dev/pixi:0.83.0 AS build
WORKDIR /app
COPY pixi.toml pixi.lock ./
RUN pixi install --locked -e prod
RUN pixi shell-hook -e prod -s bash > /shell-hook
RUN echo "#!/bin/bash" > /app/entrypoint.sh && cat /shell-hook >> /app/entrypoint.sh \
 && echo 'exec "$@"' >> /app/entrypoint.sh      # entrypoint lines paraphrased from the docs
FROM ubuntu:24.04 AS production
COPY --from=build /app/.pixi/envs/prod /app/.pixi/envs/prod
COPY --from=build --chmod=0755 /app/entrypoint.sh /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
```

- `ghcr.io/prefix-dev/pixi` tags include `latest` (ubuntu noble), `focal`, `bullseye`,
  `noble-cuda-12.9.1` and `noble-cuda-13.0.0` (based on `nvidia/cuda:*-base`). apparadise
  doesn't use them: it installs the static pixi binary into its own NGC-derived
  `cache/base-builder`.
- **Keep the env path identical** in both stages. Conda prefixes contain hard-coded absolute
  paths in text files and scripts.
- `pixi shell-hook -s bash` writes the activation script (PATH, `CONDA_PREFIX`, and the
  packages' `etc/conda/activate.d` scripts). Activation matters: some packages set env vars in
  `activate.d`, so a bare `PATH=` is not always enough.
- pixi isn't needed in the runtime image.
- **Other platforms without QEMU:** `pixi lock` solves for all manifest platforms on any host.
  `pixi install --platform linux-aarch64` on x86 is allowed ("a warning is printed"). The
  result can't be tested there, though, and steps that run code, such as post-link scripts
  (off by default) and `.pyc` compilation, presumably can't run **(unverified)**. Under
  apparadise rules, use this only for solving and dry runs. **Real builds run natively** on a
  CPU of the matching arch.
- `pixi-pack` / `pixi-unpack` (prefix.dev) pack a locked env into a tarball for air-gapped use
  **(unverified current CLI)**.

## 5. Cheat sheet

```bash
curl -fsSL https://pixi.sh/install.sh | sh             # -> ~/.pixi/bin; PIXI_VERSION=v0.83.0 to pin, PIXI_HOME
pixi init myenv -c conda-forge -p linux-64 -p linux-aarch64
pixi init --format pyproject                           # manifest inside pyproject.toml
pixi workspace platform add linux-64 --cuda 12         # rich platform with __cuda
pixi add python=3.12 "pytorch[build=cuda*]" cuda-version=12.9   # add + lock + install (matchspec form unverified)
pixi add --pypi requests                               # PyPI dep via uv
pixi add --feature test pytest
pixi search -c conda-forge -p linux-aarch64 -l -1 jaxlib
pixi lock                                              # solve all platforms, write pixi.lock
pixi install --locked                                  # fail if lock is stale  (CI)
pixi install --frozen                                  # use lock as-is
pixi install -e test --platform linux-aarch64          # other env / platform
pixi run python -c 'import torch; print(torch.cuda.is_available())'
pixi shell / pixi shell-hook -s bash                   # interactive / scriptable activation
pixi list [--platform linux-aarch64] [--json]          # what's installed or locked
pixi tree [-i libcublas]                               # dependency tree / inverted
pixi info                                              # detected virtual packages (__cuda, __glibc, __archspec)
pixi update [pkg]                                      # re-solve within manifest constraints
pixi upgrade [pkg]                                     # loosen manifest pins to newest (unverified semantics)
pixi global install ripgrep                            # tools in isolated global envs (~/.pixi)
pixi exec -s numpy python                              # throwaway env (unverified flag)
pixi config set default-channels '["conda-forge"]'     # global channel default
```

Config lives in `~/.pixi/config.toml` (global), `.pixi/config.toml` (project), or a file passed
with `--config-file`. Use `--no-config` in CI so user config can't change the result.
`--concurrent-downloads` defaults to 50.

conda/mamba equivalents: `conda create -n x -c conda-forge --override-channels ...`,
`conda config --set channel_priority strict`, `mamba` (same CLI, faster solver; conda now uses
the libmamba solver by default **(unverified current default)**).

## 6. Common failures and fixes

| Symptom | Cause | Fix |
|---|---|---|
| CPU-only `pytorch` gets installed, or `torch.cuda.is_available()` is False with a GPU present | no `cuda` declared on the platform, so the solver skips builds that need `__cuda` | declare `cuda = "12"` on the platform, add `build = "cuda*"` or `pytorch-gpu`, and pin `cuda-version` |
| `pixi install` on a GPU-less builder refuses: virtual package `__cuda` missing / machine doesn't match lockfile | the lock contains packages that depend on `__cuda` | `CONDA_OVERRIDE_CUDA=<major.minor>`, e.g. `12.9` or `13.0`, matching the target's `cuda_version` |
| "nothing provides `__glibc >=2.28`" | CUDA 13 builds use `c_stdlib_version` 2.28 (pinning zips 13.4 with 2.28; 12.9 builds keep 2.17) | Ubuntu 24.04 bases are fine (glibc 2.39). Declare `glibc = "2.28"` on the platform if needed. Never claim a newer glibc than the runtime image has. |
| solver conflict involving `cuda-version` | two packages pinned to different CUDA minors, or `__cuda` lower than a package requires | pin one `cuda-version`. Check `pixi tree -i cuda-version`. Lower the CUDA minor rather than overriding `__cuda` upward |
| conflict mentioning the `pytorch` / `nvidia` / `defaults` channel | mixed channels | conda-forge only, `channel-priority = "strict"` |
| wanted version exists for linux-64 but the lock fails on linux-aarch64 | the feedstock lacks an aarch64 CUDA job (TensorFlow), or the release isn't built yet | check `.ci_support` and repodata for aarch64. The package doesn't qualify for rule 2 |
| Python version has no build | new CPython not yet in the matrix, or one recently dropped | pin `python = "3.12.*"` (matrices run roughly 3.10–3.15 in late 2026) |
| ImportError / duplicate `libcudart` / `libstdc++` mismatch after adding PyPI packages | pip/uv wheels (e.g. `torch+cu12x`, `nvidia-*-cu12`) next to conda CUDA libs | pixi solves conda first, then PyPI. Don't put a conda package on top of a PyPI dependency. Keep CUDA-bearing packages all-conda. Never `pip install` into a pixi env by hand, since the lock won't know about it |
| env works in the build stage but not in the runtime image | env copied to a different path, or activation skipped | same prefix path in both stages. Source the `shell-hook` output |
| arch "no kernel image is available" on a GPU | the feedstock didn't compile for that sm and has no suitable PTX | check the feedstock's arch list (section 3). Rebuild via Spack or EasyBuild |

## 7. How apparadise uses it

- **Resolver rule 2** (`web/apparadise/resolver.py`): if the requester named no source, the
  package is picked from conda-forge (source name `pixi`) when **all** of these hold:
  `has_cuda`, `is_python`, conda-forge CUDA builds in **both** `linux-64` and `linux-aarch64`
  (`CONDA_SUBDIRS <= arches`), and **not** `compiled_in_spack`. That last condition means Spack
  doesn't have a non-`py-` CUDA recipe. Compiled HPC codes that only ship Python bindings
  (GROMACS, LAMMPS) therefore go to Spack and are rebuilt per target. In rule 5 (CUDA port
  attempts), pixi comes last (`PORT_ORDER = spack, easybuild, pixi`).
- **Index** (`web/apparadise/index.py:scan_conda`) reads the `repodata.json` for both subdirs
  (decompress the `.zst` first; `tools/refresh-index`). A subdir counts as CUDA if a build
  string contains `cuda` or a dependency matches
  `^(cuda-version|cudatoolkit|cuda-cudart|cuda-nvrtc|libcublas)`. `is_python` means a dependency
  on `python` or `python_abi`, or a name matching `PYTHON_HINT`. It records
  `details.cuda_build_marker` (whether `cuda` appears in a build string). The last
  `MAX_VERSIONS=12` releases are kept. The `noarch` subdir isn't scanned.
- **Recipe** (`recipes/_templates/pixi.toml.in`, rendered by `web/apparadise/recipes.py`):
  channel `conda-forge`, one platform (`linux-64` for `linux/amd64`, `linux-aarch64` for
  `linux/arm64`), `[system-requirements] cuda = "<major of targets.yml cuda_version>"`, and one
  dependency `name = { version = "==X", build = "*cuda*" }`. The `build` part is added only when
  `cuda_build_marker` is set. conda-forge binaries are used as-is, **not rebuilt per target**.
  Hand-build docs say to run `pixi lock` and commit `pixi.lock`.
- **Builder** (`builder/apparadise_builder/agent.py`): a two-stage Containerfile.
  `pixi install && pixi shell-hook -s bash > /work/activate.sh` runs in `base-builder`, then
  `/work/.pixi/envs/default` and `activate.sh` are copied into `base-runtime`, with
  `PATH=/work/.pixi/envs/default/bin:$PATH`. A failure to lock or solve has stage
  `concretize`. A failure to install has stage `build`.
- Targets: `rtx` and `vr` use CUDA 13.0, so `cuda = "13"`. `generic` uses 12.9, so
  `cuda = "12"` (CUDA 13 dropped sm_70, per Q6).

**Observations to check (not decisions; ask in QUESTIONS.md before changing):**
- The template uses the deprecated `[system-requirements]` table. It still works on 0.83.0;
  the replacement is a rich platform entry.
- The builder Containerfile copies only `pixi.toml` and runs `pixi install` without
  `--locked`/`--frozen`, while `docs/operations/hand-build.md` says `pixi install --locked` with
  a committed `pixi.lock`. As written, the builder re-solves at build time.
- The builder sets no `CONDA_OVERRIDE_CUDA`. On GPU-less builders, installing a lock with
  `__cuda`-dependent packages is expected to need it (see section 6).
- The template sets only the CUDA **major**, not `cuda-version`. `rtx` and `vr` can therefore
  get 13.4 user-space libraries on a 13.0 base image. That's fine if the node driver supports
  13.x minor compatibility, which is what the 13.0 `__cuda` claims **(unverified on real
  nodes)**.

## 8. When to choose pixi/conda-forge

**Choose it when:**
- it's a Python/ML stack (PyTorch, JAX, CuPy, RAPIDS-style, vLLM) and conda-forge has CUDA
  builds for **both** linux-64 and linux-aarch64 (rule 2)
- the feedstock's arch list covers the target: sm_120 for `rtx`, sm_70–sm_120 for `generic`
  (so CUDA 12.9 builds), and Rubin for `vr` once its compute capability is known
- speed matters more than per-target tuning: no compile, locked in minutes
- the dependency tree is large and mostly Python, which would be painful to build in Spack

**Don't choose it when:**
- CUDA builds exist on only one arch (TensorFlow today). Use Spack or EasyBuild
- it's a compiled HPC code that Spack builds with CUDA (GROMACS, LAMMPS, ...), even if
  conda-forge has it
- you need MPI tied to the site fabric (UCX/OFI, Slurm PMIx), or specific `-march`/sm flags.
  conda-forge builds generic binaries (no aarch64 microarch levels) and fixes its own arch list
- the wanted sm is missing from the feedstock's list and no usable PTX exists
- the requester asked for another source (rule 1 always wins)
- you'd need to mix pip CUDA wheels with conda CUDA libs to make it work

## 9. Authoritative docs

- pixi docs: https://pixi.prefix.dev/latest/ (pixi.sh redirects there)
  - Manifest reference: https://pixi.prefix.dev/latest/reference/pixi_manifest/
  - System requirements / rich platforms: https://pixi.prefix.dev/latest/workspace/system_requirements/
  - PyTorch guide: https://pixi.prefix.dev/latest/python/pytorch/
  - Containers: https://pixi.prefix.dev/latest/deployment/container/
  - CLI: https://pixi.prefix.dev/latest/reference/cli/pixi/install/ (and `.../search/`)
  - Installation: https://pixi.prefix.dev/latest/installation/
  - CHANGELOG: https://github.com/prefix-dev/pixi/blob/main/CHANGELOG.md ;
    releases: https://github.com/prefix-dev/pixi/releases
  - Docker images: https://github.com/prefix-dev/pixi-docker
- conda-forge CUDA: https://conda-forge.org/docs/user/cuda/ (updated 2026-10-27)
- conda-forge knowledge base (microarch, glibc/`c_stdlib_version`):
  https://conda-forge.org/docs/maintainer/knowledge_base/
- conda-forge tips (channel mixing, external MPI):
  https://conda-forge.org/docs/user/tipsandtricks/
- Global pinning (`cuda_compiler_version`, `c_stdlib_version`):
  https://github.com/conda-forge/conda-forge-pinning-feedstock/blob/main/recipe/conda_build_config.yaml
- nvcc default arches: https://github.com/conda-forge/cuda-nvcc-feedstock/blob/main/recipe/conda_build_config.yaml
- Feedstocks: https://github.com/conda-forge/pytorch-cpu-feedstock ,
  https://github.com/conda-forge/jaxlib-feedstock , https://github.com/conda-forge/tensorflow-feedstock
- conda virtual packages: https://docs.conda.io/projects/conda/en/latest/user-guide/tasks/manage-virtual.html
- CEP 46 (`__cuda_arch`): https://conda.org/learn/ceps/cep-0046/
- Repodata: https://conda.anaconda.org/conda-forge/linux-64/repodata.json ,
  https://conda.anaconda.org/conda-forge/linux-aarch64/repodata.json
