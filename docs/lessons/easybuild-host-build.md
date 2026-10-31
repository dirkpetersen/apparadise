# Lessons from the bare-metal EasyBuild session

In October 2026, a separate session set up EasyBuild + Lmod on an Ubuntu 26.04 host and tried
to build `PyTorch-2.9.1-foss-2025b`. It logged 21 failures in
`/storage/t3/test/motuz/eb/FAILURES.md`. This page turns them into design rules for
apparadise.

## What went wrong, grouped by root cause

| Root cause | Failures | What apparadise does instead |
|---|---|---|
| **Bleeding-edge host toolchain**: GCC 15 defaults to C23, glibc 2.42+ turns `memchr`/`bsearch`/... into `_Generic` macros that clash with old gnulib, and gprofng doesn't compile | #10, #11, #12, #13, #17, #19, #20, #21 | Never build on the host. Builds run inside pinned builder images (NGC CUDA devel on Ubuntu 24.04, see Q5), whose compiler and glibc match what the upstream recipes are tested against. |
| **Bootstrap gaps**: no Lmod easyconfig, broken namespace package with editable installs, Lua paths, luaposix version | #2–#7 | The builder image ships EasyBuild, Lmod and their dependencies preinstalled and tested once. EasyBuild is installed non-editable. |
| **Missing OS dependencies** fixed by `--ignore-osdeps`, which silently dropped InfiniBand from OpenMPI/UCX | #9 | Builder images include `rdma-core`/`libibverbs-dev`. `--ignore-osdeps` is forbidden. A missing OS dependency is a `concretize` failure (Q10). |
| **Blocked egress**: `config.guess` download retried with 10 s timeouts on every configure step | #14 | Builders get sources from a mirror and proxy caches. Images ship `config.guess`, and the download is disabled (Q11). |
| **Wrong flag spelling**: `--optarch=x86-64-v3` became `-x86-64-v3` | #15 | The recipe generator always writes the **full** flag: `optarch = '-march=x86-64-v4'` (or the `GCC:` prefixed form). A unit test checks it. |
| **Concurrent runs on one install tree**: queues raced, and a stale lock blocked the relaunch | #16, #18 | The coordinator allows one EasyBuild job per (target, toolchain-generation) install tree at a time (Q9). Builders are disposable, so a dead build never leaves an orphaned lock behind. |
| **Process lifetime**: background builds died with the shell | #8 | The builder agent runs as a systemd service (appmotel) or a Slurm job, never from an interactive shell. |
| **Disk**: the root filesystem had only 11 GB free, so `TMPDIR` was moved | settings | Builders put `TMPDIR` and the build path on a dedicated scratch volume, set per launcher. |

## Patterns for the failure database and auto-repair

- **One root cause produced many failures.** The gprofng break appeared in three binutils
  easyconfigs (2.44 SYSTEM, 2.44 GCCcore, 2.45). The error-signature grouping must normalise
  versions and paths, so these become one failure with three builds.
- **The known-patch library (phase 4) can be seeded from this log:**
  - `binutils`: add `--disable-gprofng` to `configopts` when gprofng fails with `_Generic`.
  - gnulib vs. glibc `_Generic`: a forced include that undefines `__glibc_const_generic`
    (`CPPFLAGS='-include glibc-generic-off.h'`).
  - old autoconf probes under C23: add `-std=gnu17`.
  - EasyBuild `optarch`: rewrite a bare arch name to `-march=<arch>`.
- **Fix once, at the right level.** Patching gnulib headers one at a time (#21, attempted) chased
  the error from header to header. A single forced include fixed all of them. Auto-repair
  should prefer toolchain-level fixes over per-file patches.
- **Keep a run log.** The session's `chain.log` (`<step> exit=<code>`) was the most useful
  artifact. The build record keeps the stage reached and the exit code for every step.
