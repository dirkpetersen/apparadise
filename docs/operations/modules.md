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
