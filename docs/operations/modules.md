# Modules and wrappers

Each published package version gets one Lmod file plus one wrapper script per executable.
Both are generated from the catalog:

```bash
cd web
.venv/bin/python -m apparadise.modules --catalog ../catalog \
  --module-dir /shared/modules/apparadise --wrapper-dir /shared/apps/apparadise/bin \
  --sqsh-dir /shared/containers --dry-run      # drop --dry-run to write
```

Users add `/shared/modules/apparadise` to `MODULEPATH` (once, site-wide). Then:

```bash
module load gromacs/2026.3
gmx_mpi mdrun ...
```

## Automatic delivery (phase 2)

Pipeline builds need no hand step. Run the delivery job from cron on one node of each
architecture that has Enroot and the CephFS mounts:

```cron
*/10 * * * * cd /srv/apparadise/web && .venv/bin/python3 -m apparadise.deliver --db data/apparadise.sqlite3 --registry registry.example.org
```

The job does three things:
1. It stages the published builds for this host's platform with `tools/stage-sqsh`, by
   digest.
2. Once both the `-generic-amd64` and `-generic-arm64` images exist, it joins them into the
   multi-arch `-generic` manifest.
3. It regenerates the modules from the database.

A build appears in a module only after its `.sqsh` exists. Generic images get one `.sqsh`
per architecture (`...-generic-amd64.sqsh`, `...-generic-arm64.sqsh`), and the wrapper
picks between them at run time.

## How a wrapper picks its image

| Node | Image |
|---|---|
| `APPARADISE_TARGET` is set | That target |
| aarch64 | `vr` |
| x86-64 with GPU compute capability ≥ 12 | `rtx` |
| anything else | `generic` |

If the preferred image was not published, the wrapper falls back to `generic`. With no usable
image it exits with a clear message. It then runs:

```
enroot start -- /shared/containers/<target>/<name>-<version>-<target>.sqsh <exe> args...
```

The working directory is kept. Enroot's system configuration (`/etc/enroot`) mounts home,
project and scratch, and injects the GPU driver and the host RDMA/NCCL libraries.

## Restricted packages

These are skipped by default. Generate them with `--include-restricted` into a module
directory that only sysadmins can read, never into the shared one.

## Open points

- Which host environment variables `enroot start` should pass through (Q14).
- Whether `.sqsh` images should start through `enroot start` directly or through
  `enroot create` with a per-user cache (Q14).
