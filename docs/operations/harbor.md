# Harbor registry

Harbor is the source of truth for every image. It runs from its own installer on the
apparadise server, outside appmotel. Traefik routes the registry hostname to it, so the web
app and the registry share one front door and one certificate. (QUESTIONS.md Q8: a test
install exists, no production install yet.)

## Projects

| Project      | Access | Holds |
|--------------|--------|-------|
| `public`     | Pull without login | Redistributable software |
| `restricted` | Sysadmins only (RCO-Admins via OIDC/LDAP group) | Licence- or weight-gated software |
| `cache`      | Robot accounts only, not browsable | Base images, Spack build caches, intermediate layers |

Add a **proxy-cache** project for `nvcr.io`, so nodes and builders never pull from the
internet. Base images are derived from NGC through it.

## Robot accounts

- **One per build job**, created by the coordinator when a builder claims the job. It is
  a system robot with push/pull on the image's project (`public` or `restricted`) plus
  `cache`, lives `HARBOR_ROBOT_DAYS` (default 1), and is deleted when the job finishes. If
  Harbor is down, the job goes back in the queue. Set `HARBOR_URL`, `HARBOR_USERNAME` and
  `HARBOR_PASSWORD` in `web/.env` to an admin account that may create system robots (Q20).
- **Phase 1 hand builds:** one robot account per operator, with push to `public/*` and
  `cache/*`. Use `restricted/*` only from on-prem builders.

## Registry page

The web app's Registry page (`#/registry`) reads repositories and artifacts through the same
`HARBOR_*` account, server-side, and caches each answer for 30 seconds. Everyone sees
`public`. Only RCO-Admins see `restricted` and `cache`. A signature shows as "Signed" when
Harbor lists a cosign accessory on the artifact. Without `HARBOR_URL` the page says the
registry is not configured (Q23).

## Signing and scanning

Enable Trivy scanning on push. Sign every image after its smoke test passes
(`cosign sign --key ... REGISTRY/public/NAME@DIGEST`), and enforce signature verification on
`public` and `restricted`.

## Install

Tested with Harbor **v2.14.6** and Docker 29 with Compose v2.

1. Download the online installer
   (`harbor-online-installer-v<version>.tgz` from the goharbor/harbor GitHub releases) and
   unpack it.
2. Copy `harbor.yml.tmpl` to `harbor.yml` and set:
   - `hostname` to the registry name. Harbor refuses an IP address here; use `localhost` for
     a local test.
   - `external_url: https://<registry name>`, because Traefik terminates TLS.
   - Remove the whole `https:` block, and set `http.port` to a free local port (e.g. `8085`).
   - Random `harbor_admin_password` (it needs upper case, lower case and a digit) and
     `database.password`. Keep both out of the repo.
   - `data_volume` (image layers, Redis, job logs) on large storage, and `log.local.location`
     off `/var` if `/var` is small.
3. Run `./prepare`. It writes `docker-compose.yml` and `common/config/`.
4. Edit `docker-compose.yml` before starting:
   - publish nginx as `127.0.0.1:<port>:8080`, not `<port>:8080`, so only Traefik reaches it;
   - optionally move the database volume (`.../database:/var/lib/postgresql/data`) to local
     disk, and `chown 999:999` the new directory (Postgres runs as uid 999).
5. Start with `docker compose up -d` in the installer directory. Don't use `./install.sh`
   after step 4: it runs `prepare` again and overwrites your edits. Add `--with-trivy`
   (re-run `prepare --with-trivy`) once there is disk space for the scanner (~1 GB of images).
6. Route the registry hostname in Traefik to `http://127.0.0.1:<port>`, with long
   timeouts for multi-GB pushes.
7. Create the projects with `tools/harbor-setup` (below).

**Gotchas seen on the first install:**

- `./prepare` runs as root (in a container), so the `common/config/*/env` files are root-owned
  with mode 0640. If you run `docker compose` as a non-root member of the `docker` group, it
  fails with `open .../env: permission denied`. `chown` those four files to that user.
- After restarting `core` or `jobservice` on their own, restart `proxy` (nginx) too. nginx
  resolves its upstreams only at startup, and the containers may swap IP addresses. The
  symptom is a 401 on every API call, even `/api/v2.0/health`, with the message
  `'Authorization' should start with 'Harbor-Secret'` (jobservice answering).
- Harbor's images go to Docker's data root (usually `/var/lib/docker`): about 2 GB without
  Trivy.
- All containers use `restart: always`, so Harbor comes back after a reboot with Docker.

## Projects and proxy cache: `tools/harbor-setup`

```bash
HARBOR_URL=https://registry.example.org HARBOR_USERNAME=admin HARBOR_PASSWORD=... \
    tools/harbor-setup --dry-run      # report what is missing
HARBOR_URL=... HARBOR_USERNAME=... HARBOR_PASSWORD=... tools/harbor-setup
```

It creates `public` (anonymous pull), `restricted` and `cache` (private), a registry endpoint
for `https://nvcr.io`, and the proxy-cache project **`nvcr`** on it. Pull NGC images through
it as `<registry>/nvcr/nvidia/cuda:<tag>`. Running it again only adds what is missing.

## Checklist

1. Install Harbor and point its hostname at Traefik (above).
2. Run `tools/harbor-setup`.
3. Connect auth to AD (LDAP) or OIDC, and map RCO-Admins to the `restricted` project.
4. Set `REGISTRY_HOST`, `HARBOR_URL`, `HARBOR_USERNAME` and `HARBOR_PASSWORD` in the web
   app's `.env`.

## Client note: no cookies

Harbor sets a `sid` session cookie on every API answer. A later POST, PUT or DELETE that sends
it back is treated as a browser session and refused with `403 CSRF token not found`. The web
app's Harbor client therefore keeps no cookies (`apparadise/harbor.py`). Without that, the
per-job robots were created but never deleted. Any other script that talks to the Harbor API
with basic auth must do the same.
