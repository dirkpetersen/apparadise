# Software stack

These are the versions in use. Keep this page in step with `web/requirements*.txt`,
`containers/base/` and the frontend layout.

## Web app (`web/`)

| Component | Version | Notes |
|---|---|---|
| Python | 3.12 (server) | Tests also pass on the dev venv's 3.12 |
| FastAPI | >= 0.115 (0.162 tested) | App factory `create_app`, strict CSP |
| uvicorn[standard] | >= 0.30 | Bound to 127.0.0.1 behind Traefik, `proxy_headers` on |
| pydantic-settings, python-dotenv | >= 2.4, >= 1.0 | `.env` only, never overriding the environment |
| httpx | >= 0.27 | Async clients for Harbor, Slurm, Run:ai (phase 2) |
| anthropic | >= 2.3 | Request chatbot (phase 4): Claude via the official SDK, server-side only |
| ldap3 | >= 2.9 | AD over LDAPS, nested groups |
| itsdangerous | >= 2.2 | Signed stateless session cookie |
| PyYAML | >= 6 | Catalog files and `containers/targets.yml` |
| SQLite | stdlib | WAL, STRICT tables, `PRAGMA user_version` migrations |
| pytest, ruff, cryptography | dev only | `cryptography` makes test certificates |

## Builder (`builder/`)

| Component | Version | Notes |
|---|---|---|
| httpx | >= 0.27 | Outbound calls to the coordinator only |
| FastAPI + uvicorn | as `web/` | Serves only `/health` for appmotel |
| podman, srun, cosign | on the host | Called as argv lists, never through a shell |

## Frontend (`web/static/`)

Plain HTML, CSS and vanilla JS ES modules, with no framework and no build step.
- `css/tokens.css` holds all design values. There is one accent (indigo `#4f46e5`), and
  light and dark both use `light-dark()`.
- `js/dom.js` provides `h()`. `js/api.js` is the only way to call `/api`.
- Components (`js/components/`): `code-panel`, `toast`, `menu-button`, `confirm-dialog`.
- Views (`js/views/`): `shell`, `catalog`, `package`, `login`, `admin` (the build console).
- Fonts are self-hosted, all under the OFL: Inter, Fraunces and JetBrains Mono.
- `showcase.html` shows every component.

**Rules that tests enforce:** strict CSP (no inline script or style), no `innerHTML` with
dynamic data, and every `/api` call goes through `api.js`.

## Build images (`containers/base/`)

| Component | Version | Notes |
|---|---|---|
| Base OS | Ubuntu 24.04 (NGC `nvcr.io/nvidia/cuda`) | Q5 |
| CUDA | 13.0.3 (`vr`, `rtx`), 12.9.2 (`generic`) | Q6; NGC tags checked 2026-10-31 |
| Spack | `v1.2.2` (`SPACK_REF`) | Latest release on 2026-10-31 |
| EasyBuild | 5.4.2 | Non-editable install. Latest on PyPI on 2026-10-31 |
| pixi | 0.83.0 | Latest release on 2026-10-31; musl static binary |
| Lmod | Ubuntu package | Not bootstrapped by hand |
