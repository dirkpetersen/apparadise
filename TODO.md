# TODO

The phased plan from the architecture doc. Tick items as they land. Questions go in
`QUESTIONS.md`.

## Phase 0: foundations (shared by all phases)

- [x] README, CLAUDE.md, QUESTIONS.md, TODO.md
- [x] Lessons from the EasyBuild bare-metal session (`docs/lessons/easybuild-host-build.md`)
- [x] `web/` skeleton: settings + `.env.default`, `create_app`, `/health`, CSP + security headers
- [x] SQLite `Database` with migrations (full data model from the architecture doc + audit)
- [x] AD/LDAPS login, sessions, CSRF, throttle, `require_user` / `require_sysadmin`
- [x] `mock/` LDAP directory + `mock/demo.py`
- [x] Registry page (`#/registry`): read-only Zot browser with pull commands; fake Zot search in the demo
- [x] Frontend shell: tokens.css, dom.js `h()`, api.js, theme, login view, showcase.html
- [x] Tests enforcing CSP/static rules, `.env.default` parity
- [x] `tests/web/test_audit_coverage.py`
- [x] Zensical docs skeleton, `docs/stack.md`

## Phase 1: hand-built catalog

Code and runbooks are done. What remains needs the hardware, the registry host and a container runtime.


- [x] Per-target base images (`containers/base/`): builder + runtime for `vr`, `rtx`, `generic` (Q5, Q6, Q10)
- [x] Recipe layout `recipes/<name>/<version>/<target>/` + templates + `python -m apparadise.recipes`
- [x] Hand-build runbook `docs/operations/hand-build.md` (build → package → test → sign → push → sqsh)
- [x] `tools/stage-sqsh`: write the published image as read-only `.sqsh` on CephFS (Enroot import)
- [x] Module generator: Lmod file per image + Enroot wrapper per executable, arch-based tag pick
- [x] Catalog read model: `catalog/*.yml` → DB `package` rows; read-only `/api/catalog` (Q3)
- [x] Catalog UI: list/search + package page with `module load` and `srun --container-image` lines
- [x] Harbor runbook (`docs/operations/harbor.md`): projects public/restricted/cache, nvcr.io proxy cache (Q8)
- [ ] Install Zot for real (Q8; tested on 127.0.0.1), build and push base images (Q5, Q6, Q16)
- [ ] Read Rubin's compute capability on the NVL72 and fill in `containers/targets.yml` (Q13)
- [ ] Concretize a rendered `spack.yaml` with real Spack (`spack -e <dir> concretize`); not run here, since executing a freshly cloned Spack was outside this session's permissions
- [ ] Top 20 packages built on both targets (Q4)
- [x] Visual check in Chromium (catalog, package, login, admin; light/dark; desktop/phone). Found and fixed the CSRF header and 401 event name left over from Bucketeer (browser sign-in was broken)
- [ ] Re-check in a normal desktop browser: this host's sandboxed Chromium crashes shaping the Fraunces display font and drops monospace text at phone width, both most likely a library mismatch in the sandbox

## Phase 2: automated pipeline

Offline-testable code is being built ahead of phase 1 hardware (Q17).


- [x] Three-way index loader (Spack, EasyBuild, conda-forge linux-64 + linux-aarch64), `python -m apparadise.index`
- [x] Nightly refresh job (`tools/refresh-index`: clone/download upstreams, then run the loader)
- [x] Resolver (fixed rule order) with tests per rule
- [x] Base images pinned by digest via `containers/targets.yml` `base_digests` (filled in after the first push)
- [x] EasyBuild: CUDA easyconfig per version recorded in the index and picked automatically; recipes bumped to `foss,2025b` with `filter-deps: CUDA` (Q22)
- [x] Coordinator: job queue (`UPDATE ... RETURNING` claim), stages, per-tree EasyBuild lock (Q9)
- [x] Builder API `/api/builder/...` (register, claim, log stream, report) + per-builder tokens
- [x] Sysadmin routes: registration tokens, builders, `POST /api/admin/builds` (resolve + render + queue)
- [x] Failure signatures + log scrubbing (groups e.g. gprofng across binutils versions)
- [x] `builder/` agent: register, pull loop, podman build/package, srun smoke test, push + digest, log streaming
- [x] Sysadmin build console (`#/admin`): queue builds, live build list with log tails, builders + one-time tokens
- [x] Launchers (start/stop): local container, `sbatch`, EC2 spot; IDLE_EXIT_POLLS releases nodes
- [x] Lost-builder reaper (BUILDER_STALE_MINUTES) + agent heartbeat; work trees removed after each job
- [x] Autoscaler: `python -m apparadise_builder.scale` from cron, counts-only `GET /api/builder/queue`
- [x] Test stage as low-priority Slurm job via Pyxis (`srun --nice --container-image`); TEST_MODE=none fails closed
- [x] Registry: Zot with the web app as token service; a one-time login per job for exactly its repositories, void when the job ends; builder uses a 0600 authfile (Q8, Q19, Q20)

- [x] Delivery job (`python -m apparadise.deliver`): stage .sqsh by digest, multi-arch generic manifest, modules from the DB
- [x] generic builds push per-arch tags; .sqsh and wrappers are per-arch for generic

## Phase 3: community layer

- [x] Requests and quota (`#/requests`, `POST /api/requests`, weekly quota on the admin page, Q25)
- [x] Public failure DB: signature grouping, log scrubbing, restricted filtering (`#/failures`, `#/f/<id>`, Q24)
- [x] Upvotes on packages and failure groups (`PUT/DELETE /api/votes/...`)
- [x] Nightly retries (`python -m apparadise.retry`, run by `tools/refresh-index`): recipe changed upstream or failure older than 14 days, max 3 attempts per release
- [x] Live build logs (SSE): `GET /api/builds/{id}/events`, `#/b/<id>`, admin console (Q26)

## Phase 4: chatbot and auto-repair

- [x] Chatbot page with the five tools (`#/ask`; Q12, Q27): Claude via the SDK, proposals confirmed by the user, keyword stand-in in the demo
- [x] Bounded auto-repair with a known-patch library (`recipes/repairs.yml`, `apparadise.repair`): one fix per failure, max 2 per release, never the same rule twice
- [x] Upstream PR offers: `#/admin` lists published repairs with a drafted upstream issue (`/api/admin/upstream`); filing stays manual
