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

## Rootless podman on the builder host

Builders run podman as their own (non-root) account. What the first builder host (motuz)
needed:

1. **A sub-UID range.** Without one, podman maps a single UID, and only the slow `vfs`
   driver works. Every layer is then a full copy of the CUDA image (about 10 GB). An AD
   account is not in `/etc/passwd`, so `usermod --add-subuids` refuses it. Append the line
   directly, using the next free range, then run `podman system migrate` **as that
   account**, not as root:
   ```bash
   echo "<user>:<start>:65536" | sudo tee -a /etc/subuid /etc/subgid
   podman system migrate        # as <user>
   podman unshare cat /proc/self/uid_map   # two lines: 0 -> uid, 1 -> <start>
   ```
2. **Image storage on big shared storage**, when the local disk is small.
   `~/.config/containers/storage.conf`:
   ```toml
   [storage]
   driver = "overlay"
   graphroot = "/storage/.../podman/storage-overlay"
   runroot = "/run/user/<uid>/containers"
   [storage.options.overlay]
   force_mask = "700"
   ```
   On a network filesystem (CephFS, NFS) podman always applies `force_mask`: files are
   stored as mode 700 and the real mode is kept in an xattr, which images and pushes
   preserve. Point `TMPDIR` and `image_copy_tmp_dir` (`containers.conf`) at the same storage
   too, or big layers fill `/var/tmp`.
3. **No sub-UIDs and no way to get them:** `driver = "vfs"` with
   `[storage.options.vfs] ignore_chown_errors = "true"`, plus `tar --no-same-owner` in every
   `RUN` that unpacks an archive (the base Containerfiles already use it). This works but
   is slow, and every file in the image ends up owned by one user.

## What a builder does per job

| Stage | Command |
|---|---|
| concretize | the solver alone in `base-builder`: `spack concretize`, `eb --robot --dry-run`, `pixi lock`. A failure here is a `concretize` failure, which the toolchain fallback and source switch repair |
| build | one `podman build`: the source's own tool inside `base-builder`, then the final labelled image `FROM base-runtime`. Build trees go to the job's `/scratch` mount, never into a layer |
| package | run the final image as uid 54321 and fail on unreadable files ([user identity](user-identity.md)) |
| test | push `cache/test-<name>:<job>`, then `srun --gpus=1 --nice` to run the smoke test on a GPU node |
| publish | push the release tag, record the digest, `cosign sign` if `SIGN_KEY` is set |

- **Shared caches** (`CACHE_DIR`, for example `/opt/ap`): `spack/<target>-<arch>` is a
  Spack binary cache mounted at `/cache`, and `eb/<target>-<arch>` an EasyBuild install tree
  mounted at `/opt/apps`, so GCC and other dependencies are built once per target. Only
  builds a sysadmin started may write (`cachePush` in the job); user and chatbot requests
  mount the Spack cache read-only and get no EasyBuild tree. The coordinator runs at most one
  EasyBuild job per tree, and each EasyBuild image copies only its own dependency closure
  (the modules `eb --dry-run` lists) out of the tree. `eb/sources` holds EasyBuild's
  downloads for all targets (every file is checksum-checked): sysadmin builds download into
  it, requests read it. Seed `generic/eb_v<EB version>/ConfigureMake/config.guess` there once
  per EasyBuild version, mode 0755 (EasyBuild runs it in place): savannah.gnu.org refuses connections from here, and every
  ConfigureMake package otherwise loses 3 minutes to download retries. The same commit is on
  `gitlab.com/freedesktop-sdk/mirrors/savannah/config/-/raw/<commit>/config.guess`, and
  EasyBuild checks its sha256.
- **EasyBuild hooks** (`containers/base/eb-hooks.py`, set in `base-builder`): GCCcore is built
  without its nvptx GPU offload compiler (CUDA 13's `ptxas` rejects GCC's default `sm_50`;
  CUDA code uses nvcc), and Perl's own test suite is skipped (its Net-Ping ICMP tests need
  raw sockets, which a rootless build container does not have). FlexiBLAS (in every foss
  toolchain) is fetched from the MPI group's web server first, because their GitLab answers
  with an HTML anti-bot page that fails the checksum. LLVM ignores 4 lit tests that expect
  "Permission denied" and so fail when the build runs as root. UCX-CUDA gets a define for the
  one driver typedef that CUDA 13 renamed. A Bundle that sets its download URLs only in
  `default_component_specs` (SentencePiece) gets them at the top level too, because
  EasyBuild 5.4 otherwise tries only sources.easybuild.io.
  NCCL dependencies older than 2.27.7 move to 2.27.7, the first NCCL that supports CUDA 13;
  `containers/base/easyconfigs/` (on the robot path before EasyBuild's own) has it for
  toolchains EasyBuild ships none for. magma no longer reads the removed
  `cudaDeviceProp.clockRate`.
  Clang and LLVM before 20 build without the OpenMP GPU offload runtime, which knows no
  Blackwell architecture and stops configure.

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
