# apparadise

Manage applications for HPC, currently focused on CUDA software, containers, and integrating
**Spack**, **EasyBuild** and **pixi/conda-forge** behind one catalog.

apparadise builds CUDA software once per hardware target and ships each build as a signed OCI
container image. The same image is also exposed as `module load <name>/<version>`. Users never
need root, and never need to know whether a package came from Spack, EasyBuild or conda-forge.

> **Status (2026-10-31):**
> - **Phase 1:** the code and runbooks are done.
> - **Phase 2:** the pipeline code (index, resolver, coordinator, builder agent, Harbor
>   robots) is built and tested offline.
> - **Not yet:** nothing has run on real hardware, Harbor or a container runtime.
>
> See `docs/operations/go-live.md` for the order to bring it up, `TODO.md` for what's
> left, and `QUESTIONS.md` for open decisions and the defaults in use.

## Quick start

```bash
cd web
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python3 ../mock/demo.py     # http://127.0.0.1:8000
.venv/bin/pytest                      # web + builder tests
```

The demo runs the real app with a fake directory, a sample catalog and a small package
index. All users have the password `demo`. `alice` and `bob` are regular users. `dana` is a
sysadmin (RCO-Admins), who sees restricted packages and the build console at `#/admin`.

| Path | What it is |
|---|---|
| `web/` | FastAPI app: catalog, AD login, coordinator, builder API, build console (appmotel app) |
| `builder/` | Pull-based builder agent and launchers: local container, `sbatch`, EC2 spot (appmotel app) |
| `catalog/` | Hand-maintained catalog entries (phase 1) |
| `containers/` | `targets.yml` (every arch flag) and the base image Containerfiles |
| `recipes/_templates/` | Spack, EasyBuild and pixi recipe templates |
| `tools/` | `stage-sqsh`, `refresh-index` |
| `docs/` | Zensical docs: stack, runbooks, lessons |

## How it works

```
request / vote ──► resolver ──► build pipeline (per target) ──► Harbor registry ──► module / container / Run:ai
                       │                 │
                       │                 └─ failure ──► public failure DB ──► nightly retry / community fix
                       └─ three-way index (Spack × EasyBuild × conda-forge, refreshed nightly)
```

- **One artifact, two front doors.** The OCI image is the only build product. The modulefile
  is a thin wrapper that runs it, so containers and modules never drift apart.
- **Optimise where it pays.** Compiled HPC code is rebuilt per CPU and GPU architecture.
  Python/ML stacks reuse upstream conda-forge binaries.
- **Three sources, one catalog.** Spack for HPC libraries and MPI apps, EasyBuild for curated
  scientific applications, pixi/conda-forge for Python and ML. Each image uses exactly one source.
- **Failures are data.** A failed build becomes a public catalog entry with a stage, a log
  excerpt and an error signature, so it never just disappears.

## Hardware targets

| Tag       | Hardware                                  | CPU tuning                        | CUDA arch                    |
|-----------|-------------------------------------------|-----------------------------------|------------------------------|
| `vr`      | Vera Rubin NVL72 (Vera Armv9.2, Rubin GPU)| GCC 16.1+ / Clang 21+, Olympus    | Rubin CC (TBD on delivery) + PTX |
| `rtx`     | RTX farm (x86-64, RTX PRO 6000 Blackwell) | `x86_64_v4` / exact Zen           | `sm_120` + `compute_120` PTX |
| `generic` | Portable fallback                         | `x86_64_v3` / `armv8.2-a`         | `sm_70`–`sm_120` + PTX       |

Images are tagged by target (`gromacs:2026.3-vr`, `gromacs:2026.3-rtx`) under a multi-arch
manifest. Builds always run natively and never under emulation.

## Source resolution

Each request resolves to exactly one source, in this order:

1. The requester named a source: use it.
2. Python/ML package with conda-forge CUDA builds for **both** linux-64 and linux-aarch64: **pixi**.
3. Spack has it with a `cuda` variant: **Spack**.
4. EasyBuild has a CUDA easyconfig: **EasyBuild** (bumped to the platform toolchain).
5. Only a CPU build exists: attempt a CUDA port from the closest recipe.
6. Nothing exists: record a failure with stage `no-recipe`.

## Build pipeline

Each build runs through six stages. A failure is recorded with the name of the stage where it
happened.

| # | Stage     | Does                                                         | Failure stage |
|---|-----------|--------------------------------------------------------------|---------------|
| 1 | Resolve   | Pick source and targets                                      | `no-recipe`   |
| 2 | Generate  | Write pinned `spack.yaml` / easystack / `pixi.toml`+lock     | `concretize`  |
| 3 | Build     | Disposable, network-restricted builder of matching arch      | `build`       |
| 4 | Package   | OCI image on per-target NGC-derived CUDA base, plus SBOM     | `package`     |
| 5 | Test      | Unprivileged smoke test on a real GPU node (low-prio job)    | `test`        |
| 6 | Publish   | Sign, push to Harbor, write `.sqsh` to CephFS, modulefile    | `publish`     |

Builders **pull** jobs from the coordinator over HTTPS and get one short-lived Harbor robot
token per job. A builder can be a local container, a Slurm job or an EC2 spot instance.
Restricted builds run only on on-premises builders.

## Using the software

```bash
# Slurm + Pyxis/Enroot, from the registry
srun --container-image=registry.example#cuda/gromacs:2026.3-vr gmx mdrun ...

# Slurm, from the pre-staged squashfs on CephFS
srun --container-image=/shared/containers/gromacs-2026.3-vr.sqsh gmx mdrun ...

# Lmod: Enroot-backed wrappers pick vr/rtx from the node architecture
module load gromacs/2026.3
gmx mdrun ...
```

On Run:ai/Kubernetes, select the image in the workload form.

## Community layer

- **Catalog:** one page per package, listing targets, module and container commands, source,
  test result and votes. Search also covers packages that exist upstream but are not built yet.
- **Upvotes:** one per user per package, request or failure. Votes order the build queue.
- **Request chatbot:** a standalone page in apparadise, built as a narrowly scoped agent (`search_catalog`, `search_index`,
  `create_request`, `start_build`, `explain_failure`).
- **Public failure database:** failures are grouped by error signature and logs are scrubbed.
  Failures are retried nightly, and working fixes are sent upstream as pull requests.

Restricted packages (licence or gated weights) are built but are visible only to sysadmins
(AD group `RCO-Admins`).

apparadise is aimed at advanced users. The JSON API is kept clean so that yeetl, the front end for
non-advanced users, can use it later.

## Stack

This project uses the same stack as [Bucketeer](https://github.com/dirkpetersen/bucketeer):

- Python 3.12, FastAPI + uvicorn (`127.0.0.1:$PORT`, behind Traefik)
- SQLite (WAL, STRICT tables, `PRAGMA user_version` migrations), with PostgreSQL planned later
- AD login over LDAPS, signed stateless cookie sessions, CSRF header
- Plain HTML/CSS/vanilla JS ES modules, with no framework and no bundler, under a strict CSP
- pytest + ruff, and a `mock/` folder with fake Harbor, Slurm and builder APIs
- Docs in `docs/`, built with Zensical
- Deployed with [appmotel](https://github.com/dirkpetersen/appmotel). Harbor runs alongside
  on the same server.

## Repository layout

- `web/`: FastAPI app, build coordinator and static frontend (appmotel app)
- `builder/`: pull-based builder agent (separate appmotel app)
- `mock/`, `tests/`, `docs/`: shared mocks, tests and Zensical docs

See `CLAUDE.md` for development conventions and commands.

## Roadmap

1. **Hand-built catalog:** Harbor, per-target base images, top 20 packages on both targets.
2. **Automated pipeline:** resolver, three-way index, six-stage pipeline, pull-based builders.
3. **Community layer:** catalog pages, upvotes, public failure DB, nightly retries.
4. **Chatbot and auto-repair:** request agent, bounded repair, upstream PRs.

The full design is in the architecture document, *CUDA Software Platform — Architecture*.

## Prior art

- [Singularity Registry HPC (shpc)](https://singularityhub.github.io/singularity-hpc/): modulefiles that wrap containers
- [E4S](https://e4s.io/): Spack-built CUDA containers, including on Arm
- [EESSI](https://www.eessi.io/): EasyBuild stack over CernVM-FS with Arm and CUDA support

## License

MIT, see [LICENSE](LICENSE). It covers the code and everything the platform publishes:
recipes, failure records, catalog text and docs.
