# Registry (Zot)

The registry holds every image apparadise builds and is the source of truth. It is
[Zot](https://zotregistry.dev): one Go binary with no database, storing images as files
(Q8, owner, 2026-11-01: "switch to zot"). An earlier Harbor test install was removed.

The apparadise web app is Zot's **token service**. Zot itself knows no users. Every
client (podman, docker, Pyxis/Enroot, skopeo) is sent to `https://<web app>/registry/token`,
signs in there (or not), and gets a short-lived token signed by the web app. The token names
exactly which repositories it may pull or push. Zot checks the signature with the web app's
public key and matches each grant to the exact repository name.

## Who may do what

| Who | Signs in with | Gets |
|---|---|---|
| Anyone | nothing | pull `public/<name>` |
| AD user | AD login and password (same throttle as the login page) | pull `public/<name>` |
| Sysadmin (RCO-Admins, nested groups count) | AD login and password | pull and push `public`, `restricted`, `cache`; pull `nvcr`; the catalog |
| Build job | `job-<id>` and a one-time secret from `/api/builder/claim` | pull and push its own image, `cache/test-<package>` and `cache/<source>-<target>`; pull the base images, `nvcr` and `public` |

A job's login works only while the job is running, and at most `REGISTRY_JOB_HOURS` (24 h).
The coordinator stores only its SHA-256 and clears it when the job ends or its builder is
lost. Every credentialed token is audited (`registry.token`). Anonymous ones are not.

Restricted packages (very few) live in `restricted/` in the same Zot. If that ever needs
stronger separation, run a second Zot for it with its own storage (owner, 2026-11-01).

## Projects

| Path | Holds |
|---|---|
| `public/<package>` | Published images, `<version>-<target>` tags, signed |
| `restricted/<package>` | Licence- or weight-gated images |
| `cache/base-builder`, `cache/base-runtime` | The per-target base images |
| `cache/test-<package>` | Test tags the GPU smoke test pulls |
| `cache/<source>-<target>` | Build caches |
| `nvcr/nvidia/...` | On-demand mirror of `nvcr.io/nvidia/...`: fetched on first pull, then kept |

## Install

Tested with Zot **v2.1.22** (2026-10-06) on Ubuntu 26.04.

1. Download `zot-linux-amd64` (or `-arm64`) and `checksums.sha256.txt` from the
   project-zot/zot GitHub release, and check the checksum.
2. Create the read-only account for the web app's Registry page (bcrypt is required):
   ```bash
   htpasswd -nbB apparadise-web "$(openssl rand -base64 24)" > htpasswd   # apache2-utils
   ```
   Put the same user and password in the web app's `.env` as `REGISTRY_USERNAME` and
   `REGISTRY_PASSWORD`.
3. Start the web app once and request any token, for example
   `curl https://<web app>/registry/token`. This creates `DATA_DIR/registry-signing.pem`
   (mode 0600) and `DATA_DIR/registry-verify.pem`. Back up the signing key: a new key means
   Zot has to be given the new `registry-verify.pem`.
4. Write Zot's config:
   ```bash
   tools/zot-config --storage /data/zot --realm https://<web app>/registry/token \
       --verify-key <web DATA_DIR>/registry-verify.pem --htpasswd /path/to/htpasswd \
       --port 5000 --log-dir /path/to/logs > config.json
   zot verify config.json
   ```
5. Run `zot serve config.json` as a service (systemd user unit, or appmotel), bound to
   127.0.0.1.
6. Route the registry hostname to it in Traefik (below), and set `REGISTRY_HOST` (image
   references) and `REGISTRY_URL` (`http://127.0.0.1:5000`) in the web app's `.env`.

**Gotchas:**

- NGC publishes Docker v2 manifest lists. Zot syncs them only with `http.compat:
  ["docker2s2"]`, which `tools/zot-config` sets.
- Zot only accepts bcrypt entries in the htpasswd file.
- `REGISTRY_SERVICE` (default `apparadise-registry`) must equal `service` in Zot's bearer
  config.

## Hostname and Traefik

OCI clients always talk to `/v2/` at the **root** of a host, so the registry cannot live
under a path prefix. Either:

- give it its own hostname (`registry.example.org` → Zot), or
- route `PathPrefix(/v2/)` on the web app's host to Zot. Then `REGISTRY_HOST` is that host.

The token URL (`/registry/token`) belongs to the web app. With the web app under a path
prefix (`https://host/apparadise/`), the realm is `https://host/apparadise/registry/token`.

## Clients

```bash
podman pull registry.example.org/public/gromacs:2026.3-rtx            # anonymous
podman login registry.example.org                                     # AD account
podman pull registry.example.org/restricted/alphafold3:3.0.1-rtx      # RCO-Admins only
srun --container-image=registry.example.org#public/gromacs:2026.3-rtx ...
```

Builders write the job login to a 0600 auth file. podman then runs the token flow by
itself.

## Registry page

The web app reads Zot's search extension with the read-only account and caches answers for
30 s. Everyone sees `public`. Sysadmins also see `restricted`, `cache` and `nvcr`.

## Signing

`cosign sign` pushes signatures next to the image (the job's token covers its own
repository). Zot's search reports them, and the Registry page shows "Signed".
