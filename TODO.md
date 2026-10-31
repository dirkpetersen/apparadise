# TODO

The phased plan from the architecture doc. Tick items as they land. Questions go in
`QUESTIONS.md`.

## Phase 0: foundations (shared by all phases)

- [x] README, CLAUDE.md, QUESTIONS.md, TODO.md
- [x] Lessons from the EasyBuild bare-metal session (`docs/lessons/easybuild-host-build.md`)
- [ ] `web/` skeleton: settings + `.env.default`, `create_app`, `/health`, CSP + security headers
- [ ] SQLite `Database` with migrations (full data model from the architecture doc + audit)
- [ ] AD/LDAPS login, sessions, CSRF, throttle, `require_user` / `require_sysadmin`
- [ ] `mock/` LDAP directory + `mock/demo.py`
- [ ] Frontend shell: tokens.css, dom.js `h()`, api.js, theme, login view, showcase.html
- [ ] Tests enforcing CSP/static rules, `.env.default` parity, audit coverage
- [ ] Zensical docs skeleton, `docs/stack.md`

## Phase 1: hand-built catalog

- [ ] Per-target base images (`containers/base/`): builder + runtime for `vr`, `rtx`, `generic` (Q5, Q6, Q10)
- [ ] Recipe layout `recipes/<name>/<version>/{spack.yaml|easystack.yml|pixi.toml}` + `meta.yml`
- [ ] Hand-build runbook `docs/operations/hand-build.md` (build → package → test → sign → push → sqsh)
- [ ] `tools/stage-sqsh`: write the published image as read-only `.sqsh` on CephFS (Enroot import)
- [ ] Module generator: Lmod file per image + Enroot wrapper per executable, arch-based tag pick
- [ ] Catalog read model: `catalog/*.yml` → DB `package` rows; read-only `/api/catalog` (Q3)
- [ ] Catalog UI: list/search + package page with `module load` and `srun --container-image` lines
- [ ] Harbor runbook (`docs/operations/harbor.md`): projects public/restricted/cache, nvcr.io proxy cache (Q8)
- [ ] Top 20 packages built on both targets (Q4)

## Phase 2: automated pipeline

- [ ] Three-way index loader (Spack, EasyBuild, conda-forge linux-64 + linux-aarch64)
- [ ] Resolver (fixed rule order) with tests per rule
- [ ] Recipe generators: spack.yaml, easystack, pixi.toml (all pinned, base digest pinned)
- [ ] Coordinator: job queue (`UPDATE ... RETURNING` claim), stages, per-tree EasyBuild lock (Q9)
- [ ] Builder API `/api/builder/...` (register, claim, log stream, report) + per-builder tokens
- [ ] `builder/` agent: pull loop, local-container launcher, Slurm launcher
- [ ] Harbor client: robot token per job, push, sign
- [ ] Test stage as low-priority Slurm job via Pyxis

## Phase 3: community layer

- [ ] Requests, upvotes, quota
- [ ] Public failure DB: signature grouping, log scrubbing, restricted filtering
- [ ] Nightly retries when dependencies change upstream
- [ ] Live build logs (SSE)

## Phase 4: chatbot and auto-repair

- [ ] Chatbot page with the five tools (Q12)
- [ ] Bounded auto-repair with a known-patch library (seeded from `docs/lessons/`)
- [ ] Upstream PR offers
