# CLAUDE.md

This file gives Claude Code (claude.ai/code) guidance for working with code in this repository.

## Project

apparadise is a CUDA software platform for HPC. It builds software once per hardware target and
ships every build as a signed OCI image. That image is the only build product: it is served
from Harbor and exposed both as a container (Pyxis/Enroot on Slurm, Run:ai on Kubernetes) and as
`module load <name>/<version>`. Software comes from **Spack**, **EasyBuild** or
**pixi/conda-forge**. Users browse a catalog, upvote packages, request software through a
chatbot, and see every failed build in a public failure database.

The repo is greenfield. The design source of truth is the architecture document *CUDA Software
Platform — Architecture* (Claude Docs). `README.md` summarizes it. The planned work is in
`TODO.md`, and questions for the owner go in `QUESTIONS.md`. **Don't guess at product
decisions.** Ask in `QUESTIONS.md` and work on something else meanwhile. Label decisions with
their question ID and date, for example "(Q4, owner, 2026-11-02)".

## Decisions already made (do not re-litigate)

- **Name:** the repo, Python package and product are all `apparadise`. The architecture doc
  keeps its title.
- **Licence:** MIT (the repo `LICENSE`) covers everything the platform publishes: code,
  recipes, failure records, catalog text and docs.
- **One image, one source, one solver.** Never mix Spack, EasyBuild and conda-forge in one image.
- **The resolver follows a fixed order:** requester's choice → pixi (only if conda-forge has CUDA
  builds for *both* linux-64 and linux-aarch64) → Spack with a `cuda` variant → EasyBuild CUDA
  easyconfig → CUDA port attempt → `no-recipe` failure.
- **Targets:** `vr` (Vera Rubin NVL72, aarch64), `rtx` (x86-64, RTX PRO 6000 Blackwell, `sm_120`),
  `generic` (`sm_70`–`sm_120`). Always embed PTX for the highest arch. Prefer family (`f`) targets
  over arch-specific (`a`). Tag images `<name>:<version>-<target>` under a multi-arch manifest.
- **Build natively, never under QEMU.** Building needs a CPU of the matching arch, but no GPU.
  GPU tests run on-premises on real target nodes as low-priority Slurm or Run:ai jobs.
- **The pipeline has six stages:** `resolve → generate → build → package → test → publish`.
  Failure stage names are fixed: `no-recipe`, `concretize`, `build`, `package`, `test`,
  `publish`. Do not add new ones without updating the data model and docs.
- **Builders pull, and the server never pushes.** Builders register with a one-time token, poll
  `/api/builder/...`, stream logs, and report stage, status and image digest. Each job gets one
  short-lived Harbor robot token for one repository and never site credentials. Restricted
  builds go to on-prem builders only. A launcher implements exactly `start` and `stop`.
- **Slurm runtime:** Pyxis + Enroot as shipped by BCM. Do not add another container runtime to
  Slurm. Base images derive from NGC (`nvcr.io`), proxy-cached by Harbor.
- **Module delivery:** an shpc-*style* generator of our own. It writes one Lmod file per image
  and one wrapper script per exposed executable, takes `module help` text from the catalog, and
  picks the `vr` or `rtx` image from the node's architecture. The wrappers run **Enroot**
  against the pre-staged `.sqsh` on CephFS. Do not use shpc itself or Apptainer.
- **Registry:** Harbor, with projects `public`, `restricted` and `cache`. The registry is the
  source of truth. The `.sqsh` files on CephFS are a rebuildable cache.
- **Permissions:** anyone signed in may trigger builds within a quota, and sysadmins have no
  quota. Sysadmins are members of the AD group **RCO-Admins** (`LDAP_ADMIN_GROUP_DN`, nested
  membership counts). Restricted packages, and their failures, are visible and pullable only by
  sysadmins.
- **Audience and chatbot:** apparadise is for advanced HPC users. The request chatbot is a
  standalone page inside apparadise, not a yeetl mode. yeetl (the front end for non-advanced
  users) may use apparadise later, so keep the catalog, request and build endpoints a clean,
  documented JSON API that another app could call. Do not build any yeetl integration now.
- **Hosting:** one server runs the web app, DB, Harbor, coordinator and an x86 builder. Harbor
  runs outside appmotel, behind the same Traefik.
- **Still open:** the Rubin compute capability and the Vera CPU target, to be read from the
  hardware (`nvidia-smi --query-gpu=compute_cap`) and checked against Spack archspec.

## Repo layout

Two appmotel apps live in one repo, and they share the root-level `docs/`, `mock/` and `tests/`:

```
web/            FastAPI app + build coordinator + static frontend (appmotel app #1)
  app.py          uvicorn entry point (calls uvicorn.run itself, reads PORT)
  requirements.txt
  .env.default
  apparadise/     package: config.py, main.py (create_app), db.py, auth.py, ldapauth.py,
                  resolver.py, index.py, coordinator.py, builder_api.py, catalog.py,
                  votes.py, failures.py, chatbot.py, harbor.py, slurm.py, runai.py, modules.py
  static/         frontend, served at /
builder/        pull-based builder agent (appmotel app #2, big MEMORY_LIMIT)
  app.py          /health + the pull loop
  apparadise_builder/   config, agent, launchers (start/stop), scale (autoscaler)
  requirements.txt
  .env.default
catalog/        hand-maintained catalog/<name>.yml (phase 1), loaded at startup
containers/     targets.yml (all arch flags: single source of truth) + base/ Containerfiles
recipes/        _templates/ + rendered recipes/<name>/<version>/<target>/
tools/          stage-sqsh and other single-file operator scripts (bash or stdlib Python)
mock/           fake directory, fake Harbor, sample catalog, demo (mock/demo.py); later fake Slurm
tests/web/      tests for web/; tests/builder/ drives the builder against the real app
                (ASGI, fake command runner). Run both from web/: .venv/bin/pytest
docs/           Markdown for Zensical; docs/stack.md lists versions
TODO.md  QUESTIONS.md  zensical.toml  ruff.toml (lint config for mock/ and tests/)
```

Existing modules in `web/apparadise/`: config, main (app factory + lost-builder reaper), db
(migrations 1-9), auth, ldapauth, ldapca, catalog, modules (Lmod + Enroot wrappers), recipes
(template rendering), index (three-way index), resolver, coordinator (queue, claim, report),
builder_api (`/api/builder/*`, `/api/admin/*`), failures (signatures, scrubbing), harbor
(per-job robots, read-only repository/artifact listing), failures also serves `/api/failures`
(public failure DB), votes (`/api/votes/{package|failure|request}/{key}`), buildqueue (the one
resolve/render/queue path + quota), requests_api (`/api/requests`, `/api/index`, `/api/quota`,
`/api/admin/policy`), live (`/api/builds/{id}/events`, SSE live logs), retry (nightly re-queue of failed builds),
registry (`/api/registry`, the
Registry page: `public` for everyone, `restricted` and `cache` for sysadmins, 30 s cache). `builder/apparadise_builder/`: config, agent, launchers, scale.
chatbot, slurm and runai are still to come. Frontend views: catalog, package, registry,
requests, failures, admin, login; `js/vote.js` is the upvote button; use `replace(el, [...])` from
`dom.js`, not native `replaceChildren`, when children may be `cond && node`.
**appmotel gotcha:** a top-level directory named `frontend`, `backend`, `api`, `worker`, `db` or
`cache` that holds a `requirements.txt` switches appmotel into multi-component mode. Never use
those names. `builder/` must never import from `web/`. It talks to the coordinator only over
HTTPS.

## Commands

Run these from `web/` (or `builder/`). The commands mirror Bucketeer's.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # setup
cp .env.default .env                     # then fill in the REQUIRED values
.venv/bin/python3 app.py                 # run locally, same as appmotel
.venv/bin/python3 ../mock/demo.py        # local demo with mocks + fake users, http://127.0.0.1:8000
.venv/bin/ruff check . ../mock ../tests ../builder   # lint
.venv/bin/pytest                         # all tests (tests/web + tests/builder, from web/)
.venv/bin/pytest ../tests/web/test_catalog.py::test_search   # single test
.venv/bin/python -m apparadise.modules --catalog ../catalog --module-dir /m --wrapper-dir /w --sqsh-dir /s --dry-run
.venv/bin/python -m apparadise.recipes spack gromacs 2026.3 rtx --spec gromacs@2026.3+mpi --stdout
zensical serve                           # docs (pip install zensical); build → site/ (git-ignored)
```

## Stack (same as [Bucketeer](https://github.com/dirkpetersen/bucketeer))

Python 3.12, FastAPI>=0.115, uvicorn[standard], pydantic-settings, python-dotenv, httpx, ldap3,
itsdangerous. Use pytest and ruff for development. `docs/stack.md` lists exact versions: keep it
in step with `requirements*.txt` and the frontend layout.

### Backend

- `create_app(...)` is the app factory. Tests inject `authenticator=`, `clock=`,
  `db=Database(":memory:")` and httpx `transport=` through it. Do not monkeypatch globals.
- JSON lives under `/api/...`. All `/api` routers are included into **one**
  `APIRouter(prefix="/api", dependencies=[Depends(csrf_protect)])`. Never register `/api` routes
  directly on `app`. The static mount at `/` is registered **last**. `GET /health` is cheap and
  touches neither the DB nor any upstream.
- Bind uvicorn to `127.0.0.1` with `proxy_headers=True` and `forwarded_allow_ips="127.0.0.1"`.
- Each external client (Harbor, Slurm REST, Run:ai, the LLM) is one async httpx client per app
  on `app.state.X`, exposed through a `get_X` dependency and closed on shutdown. Escape every
  path parameter (`quote(v, safe="")`), and never build URLs by hand.
- Blocking libraries (ldap3, subprocess-heavy work) run via `asyncio.to_thread`.
- Background work (job queue, nightly index refresh, nightly failure retries, nightly `.sqsh`
  re-staging) runs as asyncio tasks in the single uvicorn process. Use no Celery or Redis.
- Errors are typed exceptions such as `HarborError(status, code, message, details)` or
  `HarborUnavailable`, mapped to HTTP codes. Never put URLs, tokens or secrets in exception text
  or logs. Pin noisy third-party loggers to WARNING. Redact token paths in `uvicorn.access`.
- Live build logs use server-sent events, not WebSockets.
- The chatbot runs on the backend only, and the browser never sees the LLM API key. Its tools
  are exactly `search_catalog`, `search_index`, `create_request`, `start_build` and
  `explain_failure`. Use the latest Claude model unless configured otherwise.

### Configuration

- All runtime config comes from env vars loaded from `./.env` only. Use python-dotenv **without
  override**, so systemd-set variables win.
- `.env` is git-ignored. The committed `.env.default` documents **every** variable as either
  **REQUIRED** (left empty, so the app refuses to start without it, with no placeholder fallback)
  or **Default** (the value must equal the code default). Whenever you add a setting, add it to
  `.env.default`. `tests/test_config.py` checks that the two match.
- `PORT` is required. Put comments on their own lines, because systemd treats a trailing `# ...`
  as part of the value. Quote values containing `$(`, a backtick, `;`, `&&`, `||` or `|`, which
  appmotel's parser otherwise skips.
- Tests never read a real `.env`. Use `Settings(_env_file=None, **REQUIRED)`.
- Runtime policy (quotas, repair-attempt cap, retry schedule) is stored in the DB and edited on
  the admin page. `.env` holds only infrastructure and secrets.

### Database

- SQLite in WAL mode at `DATA_DIR/apparadise.sqlite3` (`data/`, git-ignored). The file mode is
  0600 and the data directory is 0700. Use one connection behind an RLock, with sync
  repositories that return plain dicts. Call them as `await db.run(...)`, and wrap
  multi-statement writes in `with db.tx():`.
- **Migrations:** the `MIGRATIONS` list in `db.py` is tracked by `PRAGMA user_version`, and each
  migration is applied in one transaction. Append new ones and never edit an applied one. The
  app refuses to start on a DB newer than the code.
- Use STRICT tables with `PRAGMA foreign_keys=ON`. **Stay PostgreSQL-portable:** no arrays, no
  JSONB operators, no `SKIP LOCKED`. Claim jobs with a single `UPDATE ... RETURNING`. Logs are
  files, never rows. The move to PostgreSQL will be done later with pgloader.
- Tables: `user`, `package`, `index_entry`, `request`, `build`, `failure`, `vote`, plus an
  append-only `audit` table. A failure is a failed build plus its grouped error signature (a hash
  of the normalised first error line).
- Only the build service and the chatbot write to catalog data. The catalog pages and the public
  read API only read.
- **Audit:** every POST/PUT/PATCH/DELETE route maps to an audit action named
  `area.verb[.detail]`, and `tests/test_audit_coverage.py` enforces this. The actor is the AD
  username, or `system` for background jobs. Never put secrets in audit details.

### Auth

- AD over LDAPS only (`ldap://` is refused). Bind as the service account, then search
  `LDAP_USER_FILTER` with an RFC 4515-escaped `{login}`, matching `sAMAccountName`, `mail` or
  `userPrincipalName`. Require **exactly one** match, and check the nested group with OID
  `1.2.840.113556.1.4.1941`. Then bind as the user, rejecting empty passwords. Take the identity
  from `sAMAccountName`, never from the typed login.
- Show one uniform failure message to the user. Throttle to 10 attempts per login or account
  and 20 per IP per 15 minutes, returning 429. Never log a password.
- The session is a stateless itsdangerous cookie named `__Host-apparadise`, set `HttpOnly`,
  `Secure` and `SameSite=Strict`, with expiry also checked server-side. Get the user with
  `Depends(require_user)` or `Depends(require_sysadmin)`, and take identity from the session,
  never from the request.
- **CSRF:** every state-changing `/api` request needs `X-Requested-With: apparadise`. Add no
  CORS middleware for that header, and never let a GET change state.
- **Builder API** (`/api/builder/...`) is the exception: builders send a per-builder bearer token
  instead of a cookie, and never get a session.

### Frontend

- Plain HTML, CSS and vanilla JS ES modules, with **no framework, no bundler and no build step**.
  **It must look exceptionally polished.** Treat visual quality as a top-level requirement.
- Native custom elements in `static/js/components/`, rendered into the light DOM. Use native
  `<dialog>` and popover.
- **CSP:** `script-src 'self'; style-src 'self'`. No inline `<script>`, `<style>`, `style=`
  attributes or `on*` handlers. Build DOM with `h()` from `js/dom.js`, never `innerHTML` with
  dynamic data. `tests/test_static.py` enforces this.
- **Always call `/api` through `js/api.js`**, never bare `fetch`. It sends the CSRF header and
  fires `apparadise:unauthorized` on 401.
- **Tokens:** all colours, spacing, radii, shadows, type sizes and motion values come from
  `css/tokens.css` (`light-dark()` plus a `[data-theme]` override). Use one accent colour, check
  AA contrast, and honour `prefers-reduced-motion`. Fonts are self-hosted in `static/fonts/`.
- `static/showcase.html` (not linked) demonstrates every component in light and dark.
- Never put a secret in the DOM, a URL, browser storage or the console.

### mock/ and demo

- `mock/` never imports from `web/` or `builder/`, and the app never imports `mock/`.
- Demo code lives only in `mock/demo.py`. It builds its own Settings, never reads the real
  `.env`, uses a temp `DATA_DIR`, and serves on 127.0.0.1. The only production hook is
  `app.state.demo` behind `GET /api/demo`.
- Tests must never call real Harbor, Slurm, Run:ai, LDAP, an LLM, Spack, EasyBuild or pixi.

## Security notes

Anyone can trigger a build through the chatbot, so treat recipes, package names, chatbot input
and log content as untrusted. Builders run in fresh, network-restricted sandboxes with only a
per-job push token. Auto-repair attempts are capped and logged. Scrub paths, hostnames and tokens
from log excerpts before they reach the public failure database. Restricted packages never
appear in public catalog, index or failure responses. Test that.

## Deployment (appmotel)

- appmotel can deploy an app from a subfolder of the repo. It starts each app as
  `.venv/bin/python3 app.py` with that app's folder (`web/` or `builder/`) as the working
  directory, and installs from that folder's `requirements.txt` (not `pyproject.toml`).
- Set `HEALTH_CHECK_PATH=/health` in `.env.default`. appmotel replaces a `PORT` below 10001 with
  one it assigns itself, so `PORT=8000` stays fine for local development.
- `MEMORY_LIMIT` (default 512M) covers every process of an app. Set a much larger value in the
  builder's `.env.default`.

## Working conventions

- Follow the roadmap in `TODO.md`: (1) hand-built catalog, (2) automated pipeline,
  (3) community layer, (4) chatbot and auto-repair. Don't build phase-N+1 features ahead of
  phase N.
- When a design decision changes, update the architecture doc, `README.md`, `docs/` and this
  file together.
