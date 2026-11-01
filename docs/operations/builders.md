# Builders

Builders are pull-based: they make outbound HTTPS calls to the coordinator, and the server
never connects to them. A builder can be a container on the apparadise server, a Slurm job,
or an EC2 spot instance. Restricted builds only go to on-prem builders
(`BUILDER_ONPREM=true`).

## Register a builder

1. Sign in as a sysadmin (RCO-Admins), open **Admin → Builders**, and create a registration
   token. It works once and expires after 24 hours.
2. On the builder host, `cd builder`, then
   `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
3. Copy `.env.default` to `.env` and set `COORDINATOR_URL`, `BUILDER_NAME`, `REGISTRY_HOST`
   and `REGISTRATION_TOKEN`.
4. Start it with `.venv/bin/python3 app.py`, or deploy it as its own appmotel app from the
   subfolder (`appmo add apparadise-builder dirkpetersen/apparadise/tree/main/builder`) with a
   large `MEMORY_LIMIT`. A plain deploy of the repo root installs the web app only. On first start it trades the token for a builder token, which it keeps in
   `data/builder.token` (mode 0600). After that you can remove `REGISTRATION_TOKEN`.

To retire a builder, use **Disable** on the admin page. It can no longer claim jobs.

## What a builder does per job

| Stage | Command |
|---|---|
| concretize | the solver alone in `base-builder`: `spack concretize`, `eb --robot --dry-run`, `pixi lock`. A failure here is a `concretize` failure, which the toolchain fallback and source switch repair |
| build | `podman build --target build`, using the source's own tool inside `base-builder` |
| package | `podman build`, the final image `FROM base-runtime`, labelled; then run it as uid 54321 and fail on unreadable files ([user identity](user-identity.md)) |
| test | push `cache/test-<name>:<job>`, then `srun --gpus=1 --nice` to run the smoke test on a GPU node |
| publish | push the release tag, record the digest, `cosign sign` if `SIGN_KEY` is set |

- **Credentials:** each job gets its own one-time registry login (`job-<id>`), valid only
  while the job runs ([Registry](registry.md)). The builder
  writes it to a 0600 `auth.json` in the job directory, never to the command line or the
  log, and deletes the directory when the job ends. For the test stage it also writes an
  Enroot `.credentials` file (0600, in a 0700 `enroot/` dir) and runs `srun` with
  `ENROOT_CONFIG_PATH` pointing at it, so Pyxis pulls `cache/test-<name>` with the job's
  own login (Q19). `WORK_DIR` must therefore be on storage the GPU nodes mount.
- **Logs:** output streams to the coordinator in 64 KiB chunks. A heartbeat every minute
  keeps a quiet job alive. A builder that stays silent for `BUILDER_STALE_MINUTES` has its
  job failed as "builder lost".
- **No untested images:** `TEST_MODE=none` makes every job fail at the test stage.

## Launchers and autoscaling

```bash
.venv/bin/python3 -m apparadise_builder.launchers sbatch start --partition build-arm --workdir /srv/apparadise/builder
.venv/bin/python3 -m apparadise_builder.launchers sbatch stop 4242
.venv/bin/python3 -m apparadise_builder.launchers ec2 start --launch-template apparadise-builder-arm
.venv/bin/python3 -m apparadise_builder.launchers local start --image $REGISTRY/cache/builder-agent:latest
```

Each launcher implements only `start` and `stop`. For queue-driven scaling, run the
autoscaler from cron on a host that has a registered builder token:

```cron
*/5 * * * * cd /srv/apparadise/builder && .venv/bin/python3 -m apparadise_builder.scale --launcher sbatch --platform linux/arm64 --max 4 --partition build-arm
```

The autoscaler reads only the job counts per platform (`GET /api/builder/queue`), starts at
most `--max` builders, and never stops any. Set `IDLE_EXIT_POLLS` in the `.env` of the
builders it starts, so they exit when the queue is empty.

## Cross-architecture caveat

Graviton instances can compile aarch64 code, but a binary tuned for Vera may use
instructions they lack. Builds that run what they compile (configure checks, unit tests)
need generic flags or a Vera/Grace builder. GPU tests always run on-premises.
