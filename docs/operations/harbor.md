# Harbor registry

Harbor is the source of truth for every image. It runs from its own installer on the
apparadise server, outside appmotel. Traefik routes the registry hostname to it, so the web
app and the registry share one front door and one certificate. (QUESTIONS.md Q8: not yet
installed.)

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

## Checklist

1. Install Harbor and point its hostname at Traefik.
2. Create the projects above and the `nvcr.io` proxy cache.
3. Connect auth to AD (LDAP) or OIDC, and map RCO-Admins to the `restricted` project.
4. Set `REGISTRY_HOST` in `web/.env` to the registry hostname.
