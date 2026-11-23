# Requirements

This file lists the requirements that apparadise meets today (2026-11-23). Each requirement
has an ID, so that issues, tests and commits can refer to it. Owner decisions have their
question ID from `QUESTIONS.md`.

Status values:

- **Done:** the feature exists and has tests. All tests run offline, with mocks.
- **Done, local:** the feature also ran for real on the x86 build host (podman, Spack,
  EasyBuild).
- **Open:** the feature is not done. `TODO.md` has the plan.

Nothing has run on a GPU, on Slurm, on Run:ai or on the Vera Rubin (`vr`) hardware yet.

## 1. Product scope

| ID | Requirement | Status |
|---|---|---|
| SCOPE-1 | apparadise builds CUDA software for HPC once per hardware target and ships each build as an OCI image. The image is the only build product. | Done, local |
| SCOPE-2 | Each image is available as a container (Pyxis/Enroot on Slurm, Run:ai on Kubernetes) and as `module load <name>/<version>`. | Done (code), not run on a cluster |
| SCOPE-3 | Software comes from exactly one source per image: Spack, EasyBuild or pixi/conda-forge. Sources are never mixed in one image. | Done |
| SCOPE-4 | The users are advanced HPC users. Users never need root. | Done |
| SCOPE-5 | The catalog, request and build endpoints are a documented JSON API that another app (for example yeetl) can call. There is no yeetl integration. | Done |
| SCOPE-6 | Everything the platform publishes (code, recipes, failure records, catalog text, docs) is under the MIT licence. | Done |

## 2. Hardware targets

| ID | Requirement | Status |
|---|---|---|
| TGT-1 | There are three targets: `vr` (Vera Rubin NVL72, aarch64), `rtx` (x86-64, RTX PRO 6000 Blackwell, `sm_120`) and `generic` (`sm_75` to `sm_120`, x86-64 and aarch64). Volta is not supported (Q38). | Done |
| TGT-2 | All CPU and GPU architecture flags come from one file, `containers/targets.yml`. | Done |
| TGT-3 | Every build embeds PTX for the highest architecture. Family (`f`) targets are preferred over arch-specific (`a`) targets. | Done |
| TGT-4 | Images are tagged `<name>:<version>-<target>`. A `generic` release has one tag per CPU architecture and a multi-arch manifest. | Done |
| TGT-5 | Builds run natively on a CPU of the matching architecture, never under QEMU. A build needs no GPU. | Done |
| TGT-6 | Only CUDA 13.0 is supported (Q38). | Done |
| TGT-7 | The Rubin compute capability and the Vera CPU target are read from the hardware and filled in to `targets.yml` (Q13). | Open |

## 3. Catalog

| ID | Requirement | Status |
|---|---|---|
| CAT-1 | Sysadmins maintain the catalog as one YAML file per package in `catalog/<name>.yml`. The app loads and validates them at startup and syncs them to the database. | Done |
| CAT-2 | An entry has the fields `name`, `title`, `description`, `homepage`, `source`, `visibility`, `tags`, `aliases`, `executables`, `versions`, `test` and `spec`. Per target, a version has `status`, `digest`, `test`, `built` and `stage`. Unknown fields fail with a message that names the file and the field. | Done |
| CAT-3 | Executable names, digests and tags are checked against strict patterns. | Done |
| CAT-4 | An optional `test:` block gives a GPU smoke test: `command` (one line), `minutes` (1 to 60) and optional `data` (an `https` URL and a sha256) (Q51). | Done (schema); data entries Open |
| CAT-5 | Everyone can browse the catalog without an account (Q26). The catalog page lists packages, search, and per package its targets, status, source, test result, votes, and the `module load` and `srun --container-image` commands. | Done |
| CAT-6 | A failed target links to its failure record. | Done |
| CAT-7 | Search also covers packages that exist upstream but are not built yet. | Done |
| CAT-8 | The catalog holds the top 20 packages. Each is built on all targets (Q4). | Open: 15 of 20 built on `rtx` and `generic`; `vr` needs an aarch64 builder |

## 4. Three-way index and resolver

| ID | Requirement | Status |
|---|---|---|
| IDX-1 | An index lists every package of Spack, EasyBuild and conda-forge (linux-64 and linux-aarch64), with the versions and whether a CUDA build exists. `python -m apparadise.index` loads it. | Done |
| IDX-2 | `tools/refresh-index` refreshes the index every night (clone or download the upstreams, then load). | Done |
| IDX-3 | The index records the EasyBuild CUDA easyconfig per version, and the resolver picks it automatically (Q22). | Done |
| RES-1 | The resolver uses this fixed order: the requester's choice, then pixi (only if conda-forge has CUDA builds for both linux-64 and linux-aarch64), then Spack with a `cuda` variant, then an EasyBuild CUDA easyconfig, then a CUDA port attempt, then a `no-recipe` failure. | Done |

## 5. Recipes

| ID | Requirement | Status |
|---|---|---|
| REC-1 | Templates for Spack (`spack.yaml`), EasyBuild (easystack) and pixi (`pixi.toml` and lock) live in `recipes/_templates/`. Rendered recipes live in `recipes/<name>/<version>/<target>/`. `python -m apparadise.recipes` renders them. | Done |
| REC-2 | The Spack template uses Spack 1.2.2, `unify: true`, the per-target `cuda_arch`, and builds LLVM without CUDA (LLVM before 20 has no `sm_120` OpenMP runtime). | Done, local |
| REC-3 | EasyBuild recipes use the `foss,2025b` toolchain with `filter-deps: CUDA`, so the base image's CUDA is used (Q22). | Done, local |
| REC-4 | A Spack overlay repo (`containers/base/spack-repo/`, namespace `apparadise`) holds package fixes. It fixes relion for CUDA 13 (removes `-D__INTEL_COMPILER`). | Done, local |
| REC-5 | EasyBuild hooks (`containers/base/eb-hooks.py`) fix known CUDA 13 and container problems: GCCcore without nvptx, no Perl test suite, FlexiBLAS mirror, LLVM lit tests, UCX-CUDA typedef, Bundle source URLs, NCCL 2.27.7 minimum, magma `clockRate`, Clang/LLVM before 20 without OpenMP GPU offload, Triton CUPTI path. | Done, local |
| REC-6 | A Spack catalog entry may carry a default `spec` (the text after `name@version`, for example `+cuda build_type=Release ^mesa~llvm`). It is used when a request names no spec (Q53). | Done |

## 6. Build pipeline

| ID | Requirement | Status |
|---|---|---|
| PIPE-1 | Each build runs six stages: `resolve`, `generate`, `build`, `package`, `test`, `publish`. A failure is recorded with one of the fixed stage names `no-recipe`, `concretize`, `build`, `package`, `test`, `publish`. | Done |
| PIPE-2 | `concretize` runs the solver alone in `base-builder` (`spack concretize`, `eb --robot --dry-run`, `pixi lock`). | Done, local |
| PIPE-3 | `build` is one `podman build`: the source's tool in `base-builder`, then the final image `FROM base-runtime`. Build trees go to a per-job `/scratch` mount and never into an image layer. | Done, local |
| PIPE-4 | A Spack image links every executable of its run environment into `/opt/view/bin`, so tools that Spack installs under `libexec` are on `PATH`. | Done, local |
| PIPE-5 | An EasyBuild image holds only its runtime closure (the modules named by `depends_on`), not build-only tools, and puts their `bin/` on `PATH` (there is no Lmod in the image). | Done, local |
| PIPE-6 | `package` runs the image as uid `54321:54321` and fails if `id -u` is wrong or any file under `/opt` or `/work` is not readable (Q36). | Done, local |
| PIPE-7 | `test` pushes `cache/test-<name>:<job>` and runs the smoke test as a low-priority Slurm job through Pyxis (`srun --gpus=1 --nice --container-image`). `TEST_MODE=none` fails every job at the test stage, so no untested image is published. | Done (code), not run on Slurm |
| PIPE-8 | `publish` pushes the release tag, records the digest, and signs the image with `cosign` when `SIGN_KEY` is set. | Done (code) |
| PIPE-9 | An SBOM is made for each image. | Open |

## 7. Base images

| ID | Requirement | Status |
|---|---|---|
| BASE-1 | There is a `base-builder` and a `base-runtime` image per target, derived from NGC (`nvcr.io`) CUDA images (Q5, Q6, Q10). | Done, local (rtx, generic) |
| BASE-2 | `base-builder` holds Spack 1.2.2 with the `apparadise` overlay repo and EasyBuild 5.4.2 with the hooks and extra easyconfigs. | Done, local |
| BASE-3 | Base images are pinned by digest in `targets.yml` (`base_digests`) after the first push. | Done (code); digests Open |
| BASE-5 | The runtime base clears NGC's entrypoint, so no image has one (Q36). | Done, local |
| BASE-4 | `libcuda.so.1` is a stub in the builder, so builds that link against the driver work without a GPU. | Done, local |

## 8. Image rules (user identity)

| ID | Requirement | Status |
|---|---|---|
| IMG-1 | Every container runs as the submitting user's own UID and GID, inside and outside (Q36). | Done |
| IMG-2 | Every image works as any UID: install trees are `a+rX`, there is no entrypoint and no setuid file, and the default user is `USER 65534:65534`. | Done, local |
| IMG-3 | Nothing writes into the image at run time. Software lives in `/opt` and `/work`. | Done |

## 9. Builders

| ID | Requirement | Status |
|---|---|---|
| BLD-1 | Builders pull work. They make outbound HTTPS calls to the coordinator, and the server never connects to them. | Done |
| BLD-2 | A builder registers once with a one-time token (valid 24 h, made by a sysadmin on the admin page) and then uses its own bearer token, stored 0600. | Done |
| BLD-3 | A builder polls `/api/builder/claim`, streams the log in 64 KiB chunks, sends a heartbeat every minute, and reports stage, status and image digest. | Done |
| BLD-4 | A builder that is silent for `BUILDER_STALE_MINUTES` has its job failed as "builder lost". | Done |
| BLD-5 | Each job gets one registry login (`job-<id>`) for its own repositories only. The builder writes it to a 0600 `auth.json` and never to the command line or the log, and deletes the job directory when the job ends. | Done |
| BLD-6 | Restricted builds go only to on-prem builders (`BUILDER_ONPREM=true`). | Done |
| BLD-7 | Launchers implement exactly `start` and `stop`: local container, `sbatch`, EC2 spot. `IDLE_EXIT_POLLS` makes an idle builder exit. | Done (code) |
| BLD-8 | An autoscaler (`python -m apparadise_builder.scale`, from cron) reads only the job counts per platform and starts at most `--max` builders. It never stops any. | Done (code) |
| BLD-9 | Shared caches under `CACHE_DIR`: a Spack binary cache and an EasyBuild install tree per `<target>-<arch>`, and one shared EasyBuild source dir. Only builds a sysadmin started write to them; requests only read the Spack cache and the source dir (Q9, Q47). | Done, local |
| BLD-10 | The coordinator runs at most one EasyBuild job per install tree. | Done |
| BLD-11 | A sysadmin can disable a builder, so it can no longer claim jobs. | Done |

## 10. Registry

| ID | Requirement | Status |
|---|---|---|
| REG-1 | The registry is Zot with the projects `public`, `restricted`, `cache` and the on-demand `nvcr` mirror. It is the source of truth (Q8). | Done (tested on 127.0.0.1); real install Open |
| REG-2 | The web app is Zot's token service (`/registry/token`, ES256 key in `DATA_DIR`). Zot holds no users. Grants match exact repository names. | Done |
| REG-3 | Anyone may pull `public`. Sysadmins may pull and push `public`, `restricted` and `cache`. A build job may push only its own image, its test tag and its build caches. | Done |
| REG-4 | A job's login works only while the job runs, and at most `REGISTRY_JOB_HOURS`. The coordinator stores only its SHA-256. Every credentialed token is audited. | Done |
| REG-5 | Test tags in `cache/test-<package>` are deleted 48 h after push by a Zot retention policy (`tools/zot-config`). | Done (code) |
| REG-6 | The Registry page (`#/registry`) shows images, tags, platforms, digest, size and signature, with Slurm, Enroot and Podman commands and a digest-pinned reference. Only sysadmins see `restricted` and `cache`. | Done |

## 11. Delivery: squashfs and modules

| ID | Requirement | Status |
|---|---|---|
| DEL-1 | `tools/stage-sqsh` and `python -m apparadise_builder.stage` write each published image by digest as a read-only `.sqsh` on CephFS. A node of each architecture does this from cron. | Done (code) |
| DEL-2 | `python -m apparadise.deliver` pushes the multi-arch `generic` manifest once both architectures are published, and regenerates the modules from the database. | Done (code) |
| DEL-3 | The module generator writes one Lmod file per image and one Enroot wrapper per executable. `module help` text comes from the catalog. Modules note which targets exist (Q4). | Done |
| DEL-4 | A wrapper picks its image: `APPARADISE_TARGET` if set, else `vr` on aarch64, `rtx` on x86-64 with compute capability 12 or higher, else `generic`. It falls back to `generic`, and exits with a clear message when no image exists. | Done |
| DEL-5 | A release appears in a module only once its `.sqsh` exists. | Done |
| DEL-6 | Restricted `.sqsh` files live under `SQSH_DIR/restricted/` (0750 dirs, 0440 files, group `RESTRICTED_GROUP`). Their modules are made only with `--include-restricted`, into a directory only sysadmins can read. | Done |

## 12. Requests, quota and votes

| ID | Requirement | Status |
|---|---|---|
| REQ-1 | A signed-in user can search the index on `#/requests`, see which sources have a package and which source the resolver picks, and request a version and targets (default `vr` and `rtx`). | Done |
| REQ-2 | Each user has a quota of build jobs per 7 days, set by sysadmins on the admin page (Q25). A `generic` build counts as two jobs. Sysadmins have no quota. | Done |
| REQ-3 | A release that is already available or queued is skipped and does not use quota. | Done |
| REQ-4 | The *Mine* tab lists a user's requests, and each build links to its live log. | Done |
| VOTE-1 | A signed-in user has one upvote per package, request and failure group. Votes order the build queue. | Done |

## 13. Failure database, retries and repair

| ID | Requirement | Status |
|---|---|---|
| FAIL-1 | Every failed build is public (`#/failures`, `#/f/<id>`), grouped by stage and a signature (hash of the normalised first error line). | Done |
| FAIL-2 | Logs are scrubbed of paths, host names, IP addresses and tokens before they reach the failure database or a browser. | Done |
| FAIL-3 | Restricted packages and their failures never appear in public catalog, index or failure responses. Tests check this. | Done |
| RETRY-1 | `python -m apparadise.retry` re-queues a failed build every night when its recipe changed upstream, or after 14 days, with at most 3 attempts per release. | Done |
| REPAIR-1 | A failure that matches the known-patch library (`recipes/repairs.yml`) gets one rebuild with the fix. There are at most 2 repairs per release, and never the same rule twice. Each attempt is listed on the failure page. | Done |
| REPAIR-2 | The admin page lists published repairs with a drafted upstream issue (`/api/admin/upstream`). Filing it upstream stays a manual step. | Done |

## 14. Live build logs

| ID | Requirement | Status |
|---|---|---|
| LOG-1 | Build logs stream to the browser over server-sent events (`GET /api/builds/{id}/events`, `#/b/<id>`). The view follows the end and pauses when the user scrolls up. | Done |
| LOG-2 | Logs are files, never database rows. | Done |

## 15. Chatbot

| ID | Requirement | Status |
|---|---|---|
| CHAT-1 | The request chatbot is a standalone page (`#/ask`) for signed-in users (Q12, Q27). | Done |
| CHAT-2 | It runs on the backend only, uses Claude through the Anthropic SDK (`CHAT_MODEL`, `CHAT_FALLBACK_MODEL`), and the browser never sees the API key. | Done |
| CHAT-3 | Its tools are exactly `search_catalog`, `search_index`, `create_request`, `start_build` and `explain_failure`. `create_request` and `start_build` only propose; nothing is queued until the user confirms. | Done |
| CHAT-4 | Without an API key (the demo), a keyword-rule stand-in answers. | Done |

## 16. Authentication and permissions

| ID | Requirement | Status |
|---|---|---|
| AUTH-1 | Login is AD over LDAPS only. The app binds as a service account, searches with an RFC 4515-escaped login, requires exactly one match, checks nested group membership, then binds as the user. Empty passwords are rejected. The identity is `sAMAccountName`. | Done |
| AUTH-2 | Login shows one uniform failure message and is throttled (10 per login, 20 per IP per 15 minutes, HTTP 429). Passwords are never logged. | Done |
| AUTH-3 | The session is a stateless signed cookie `__Host-apparadise` (`HttpOnly`, `Secure`, `SameSite=Strict`), with expiry checked on the server. | Done |
| AUTH-4 | Every state-changing `/api` request needs `X-Requested-With: apparadise`. No GET changes state. | Done |
| AUTH-5 | Sysadmins are members of AD group RCO-Admins (nested membership counts). Only sysadmins see and pull restricted packages, use the admin console and register builders. | Done |
| AUTH-6 | Builders use a per-builder bearer token and never get a session. | Done |

## 17. Admin console

| ID | Requirement | Status |
|---|---|---|
| ADM-1 | The admin page (`#/admin`) lets a sysadmin queue builds (`POST /api/admin/builds`: resolve, render and queue), watch the live build list with log tails, and manage builders and one-time tokens. | Done |
| ADM-2 | Runtime policy (quota, repair cap, retry schedule) is stored in the database and edited on the admin page (`/api/admin/policy`). `.env` holds only infrastructure and secrets. | Done |

## 18. Web app, data and operations

| ID | Requirement | Status |
|---|---|---|
| WEB-1 | The web app is FastAPI on Python 3.12, bound to `127.0.0.1` behind Traefik, deployed with appmotel. The repo-root deploy is the web app only; the builder deploys from `builder/`. | Done |
| WEB-2 | `GET /health` is cheap and touches neither the database nor any upstream. | Done |
| WEB-3 | All config comes from `.env` (python-dotenv, no override). `.env.default` documents every variable as REQUIRED or Default, and a test checks this. | Done |
| WEB-4 | The database is SQLite in WAL mode with STRICT tables, foreign keys and `PRAGMA user_version` migrations. The file is 0600 and its directory 0700. It stays PostgreSQL-portable. | Done |
| WEB-5 | Every POST, PUT, PATCH and DELETE route writes an audit record `area.verb[.detail]` with the AD user or `system`. A test enforces coverage. No secrets go into audit details, logs or exception text. | Done |
| WEB-6 | Background work (job queue, nightly index refresh, retries, re-staging, lost-builder reaper) runs as asyncio tasks or cron jobs. There is no Celery or Redis. | Done |

## 19. Frontend

| ID | Requirement | Status |
|---|---|---|
| UI-1 | Plain HTML, CSS and vanilla JS ES modules with native custom elements. No framework, bundler or build step. | Done |
| UI-2 | Strict CSP (`script-src 'self'; style-src 'self'`): no inline script, style or handler, no `innerHTML` with dynamic data. A test enforces this. | Done |
| UI-3 | Colours, spacing, type and motion come from `css/tokens.css`, with light and dark themes, AA contrast, `prefers-reduced-motion` and self-hosted fonts. | Done |
| UI-4 | Views: catalog, package, registry, requests, failures, build log, chatbot, admin, login. `static/showcase.html` shows every component. | Done |
| UI-5 | Visual check in a normal desktop browser. | Open |

## 20. Demo and tests

| ID | Requirement | Status |
|---|---|---|
| DEMO-1 | `mock/demo.py` runs the real app with a fake directory, a fake Zot search and a sample index, on 127.0.0.1, with a temp `DATA_DIR`. Users `alice` and `bob` and sysadmin `dana` (password `demo`). | Done |
| TEST-1 | Tests never call real Zot, Slurm, Run:ai, LDAP, an LLM, Spack, EasyBuild or pixi. `tests/builder/` drives the builder against the real app with a fake command runner. | Done |

## 21. Open requirements (summary)

- Real Zot install, base images pushed and pinned (REG-1, BASE-3).
- Rubin compute capability and Vera CPU target (TGT-7).
- The remaining top-20 packages and all `vr` builds (CAT-8). Blocked: alphafold3 (Q52), and
  packages with no CUDA recipe on our sources.
- GPU smoke tests on real nodes, and test data (PIPE-7, CAT-4, Q51).
- SBOM per image (PIPE-9).
- Slurm REST and Run:ai clients (`slurm.py`, `runai.py`).
- Visual check in a normal desktop browser (UI-5).
