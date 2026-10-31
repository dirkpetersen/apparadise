# Using apparadise

apparadise is for advanced HPC users. Browsing needs no account. Signing in with your AD
account lets you request software and upvote.

## Catalog (`#/`)

Every package, with its builds per target: `vr` (Vera Rubin NVL72), `rtx` (RTX PRO 6000
farm) and `generic` (Volta and newer, x86-64 and Arm). Pick a published build to get its
commands:

```bash
module load gromacs/2026.3                                         # Lmod, picks vr or rtx
srun --container-image=registry.example.org#public/gromacs:2026.3-rtx gmx_mpi mdrun ...
```

A failed target links to its failure record.

## Registry (`#/registry`)

The registry shows what Harbor holds: every image, its tags, platforms, digest, size, and
whether it is signed. Each image comes with Slurm, Enroot and Podman commands, plus a
digest-pinned reference for builds that must never change. Everyone sees the `public`
project. Only RCO-Admins see `restricted` and `cache`.

## Ask (`#/ask`)

Sign in and ask in plain words, for example "Is GROMACS available on the Vera Rubin
nodes?", "I need LAMMPS for the RTX farm" or "Why did the vLLM build fail on vr?". The
assistant (Claude) searches the catalog and the upstream indexes and reads failure records,
then answers with the exact `module load` or `srun` line. It never builds anything by
itself: when a build is the answer, it drafts a request, and nothing is queued until you
press the button under it.

## Requests (`#/requests`)

Search for a package. The search covers Spack, EasyBuild and conda-forge. Each result shows
which sources have it, whether they have a CUDA build, and which source the resolver would
build from:

1. the source you picked;
2. conda-forge (via pixi) for Python/ML stacks with CUDA builds for both x86-64 and Arm;
3. Spack, if its recipe has a CUDA variant;
4. EasyBuild, if it has a CUDA easyconfig;
5. otherwise a CUDA port attempt of a CPU-only recipe.

Choose a version (newest by default) and targets (`vr` and `rtx` by default; `generic` costs
two build jobs), then request the build.

- **Quota:** each user has a number of build jobs per 7 days. Sysadmins set it on the admin
  page. A generic build counts as two jobs.
- **Duplicates are free:** a release that is already available or queued is skipped. If
  someone already asked for what you need, upvote their request instead.
- **Your requests:** the *Mine* tab lists them. Each build job links to its live log.

## Live build logs (`#/b/<id>`)

You can watch the log of a build you requested while it runs. Paths, internal host names,
IP addresses and tokens are replaced before the log reaches your browser. The view follows
the end of the log. Scroll up to pause it, and scroll back down to resume.

## Failure database (`#/failures`)

Every failed build is public, grouped with others that broke the same way: same pipeline
stage, same first error line once versions, paths and numbers are normalised. A group shows
the scrubbed error, a log excerpt, the packages and builds it hit, and a fix link once there
is one. Upvote the failures that block your work. Restricted packages' failures are visible
to RCO-Admins only.

Failed builds are retried nightly when their recipe changes upstream, or after 14 days,
with at most three failed attempts per release.

## For other apps

Every page reads the same JSON API under `/api/`: `catalog`, `registry`, `index`,
`requests`, `failures`, `votes` and `builds/{id}/events`. Requests that change state need
the header `X-Requested-With: apparadise` and a session.
