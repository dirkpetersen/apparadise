# Go-live checklist

This is the order to bring apparadise up on real infrastructure. Every step points to the
runbook or setting it needs. Open decisions are in `QUESTIONS.md`.

## 1. Answer the blocking questions

- **Q1:** the full DN of RCO-Admins (`LDAP_ADMIN_GROUP_DN`).
- **Q5/Q6:** the base OS and CUDA versions.
- **Q7:** the shared paths (`SQSH_DIR`, `MODULE_DIR`, `WRAPPER_DIR`).
- **Q8:** the registry hostname (Zot; owner, 2026-11-01).
- **Q13:** read Rubin's compute capability on the NVL72
  (`nvidia-smi --query-gpu=compute_cap`), put it into `containers/targets.yml`
  (`vr.cuda_arch`, `vr.ptx_arch`), and run the tests.

## 2. Registry

Install Zot with `tools/zot-config` ([Registry](registry.md)): token auth against the web
app, a read-only account for the Registry page, and the on-demand `nvcr.io` mirror.

## 3. Web app (appmotel app 1, `web/`)

Deploy the repo root: `appmo add apparadise dirkpetersen/apparadise`. The root `app.py`,
`requirements.txt` and `.env.default` (a link to `web/.env.default`) make this install and
run the web app only, from `web/`. It never installs the builder. Deploying the subfolder
(`.../tree/main/web`) gives the same result.

Fill in the app's `.env` (`appmo env apparadise`):
- the REQUIRED settings, and `LDAP_REQUIRED_GROUP_DN` if needed (Q2),
- `REGISTRY_HOST`,
- `REGISTRY_URL`, `REGISTRY_USERNAME` and `REGISTRY_PASSWORD` (Zot's read-only account).

Deploy with appmotel. Check `/health`, sign in as an RCO-Admin, and open `#/admin`.

## 4. Base images

Build `base-builder` and `base-runtime` natively for each target, push them, and record the
digests under `base_digests` in `containers/targets.yml`
([Base images](base-images.md)).

## 5. Index

Install `tools/refresh-index` as a nightly cron job on the server, then run it once by hand.
It also runs `python -m apparadise.retry`, which re-queues failed builds whose recipe changed
upstream (or whose failure is older than 14 days, with at most 3 attempts per release).

## 5b. Community settings

On `#/admin`, set the weekly request quota (default: 6 build jobs per user). The retry
interval and cap are stored in the database as `retry.after_days` and `retry.max_attempts`.

## 6. Builders

- Register at least one x86 builder (on the server) and one aarch64 builder
  ([Builders](builders.md)).
- On the GPU partitions, the test job's account needs Enroot credentials for `cache` (Q19).
- Optionally add the autoscaler cron job per platform.

## 7. Delivery

- On one node per architecture, with Enroot and CephFS, run
  `python -m apparadise_builder.stage` from cron. For restricted images, set
  `RESTRICTED_GROUP` to the RCO-Admins group.
- On the server, run `python -m apparadise.deliver` from cron
  ([Modules and wrappers](modules.md)).
- Add `MODULE_DIR` to the site `MODULEPATH`.

## 7b. Assistant (optional)

Set `ANTHROPIC_API_KEY` in `web/.env` to switch on the Ask page (`CHAT_MODEL` defaults to
`claude-opus-5-5`, Q12, Q27). Without the key, the page says the assistant is off and
everything else works.

## 8. First package, end to end

Queue `gromacs` for `rtx` from `#/admin`. Watch the log through
`build → package → test → publish`, then confirm that `module load gromacs/<version>`
works on an RTX node. Do the same on `vr` once Q13 is answered.

## 9. Things only a real system can show

These are untested on the development host:
- the base image builds,
- the per-source Containerfiles in `builder/apparadise_builder/agent.py`,
- `enroot start` from a `.sqsh` file (Q14),
- the EasyBuild toolchain bump (Q22),
- the auto-repair rules in `recipes/repairs.yml` against real build logs (Q29),
- the Ask page against the real Claude API (the tests and the demo use a fake),
- Spack concretization of the rendered `spack.yaml`.

Expect the first builds to fail at `concretize` or `build`. The failure records show where.
