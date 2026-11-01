# EasyBuild troubleshooting

!!! note "Acknowledgement"
    Most of this page comes from a hard-won attempt to install **PyTorch 2.9.1
    (`PyTorch-2.9.1-foss-2025b`) with EasyBuild** on a bare Ubuntu 26.04 host in October 2026.
    It was not easy. Before PyTorch itself could start, the session had to bootstrap Lmod by
    hand, write a compiler wrapper, override easyconfigs and invent a forced include for glibc.
    It recorded every failure and workaround as it went. That record is the reason the fixes
    below are known, and not just guessed. Thank you.

    The working tree is `/storage/t3/test/motuz/eb`. Its main record is
    [`FAILURES.md`](#where-the-record-is) (entries 1–21). This page adds two later failures
    (22 and 23) found in the build logs.

The design rules apparadise took from the same session are in
[EasyBuild on a bare host](../lessons/easybuild-host-build.md). This page is the hands-on
side: symptom, cause, fix. Use it when you hand-build with EasyBuild
([Hand-building a package](hand-build.md)), or when an EasyBuild failure lands in the failure
database.

## How far the attempt got

| Step (from `bootstrap/chain.log`) | Result |
|---|---|
| Lmod 9.4.5 bootstrap (Lua 5.4.8, LuaRocks, luaposix 36.3) | built by hand, works |
| ncurses 6.6, gettext 0.26 (forced include), Julia 1.13.2 | exit 0 |
| GCC 16.2.0, 16.1.0, 15.3.0 | exit 0 |
| **PyTorch 2.9.1-foss-2025b** (150 modules planned, `bootstrap/dryrun.txt`) | 68 modules completed, then **failed at AOCL-BLAS 5.1** (#22) |
| R 4.5.2-gfbf-2025b | **failed at util-linux 2.41** (#23) |

EasyBuild 5 has granular exit codes (`EasyBuildExit`): 4 missing easyconfig, 15 configure
step, **16 build step**, 22 sanity check. The code comes from the first easyconfig in the
dependency tree that failed, so `PyTorch exit=16` in `chain.log` means "some package's build
step failed" (here a dependency's), not necessarily PyTorch's. The module that actually
failed is in the `== FAILED:` line of the build log. Its compiler output
is in the `run-shell-cmd-output/<step>-*/out.txt` directory named on the line above it.

## The host

- Ubuntu 26.04, **GCC 15.2** (defaults to `-std=gnu23`), **glibc 2.43**
- AMD EPYC 9534 (Zen 4, AVX-512), 8 build cores, about 11 GB free on `/`
- No root, no SSH key for GitHub, `git.savannah.gnu.org` blocked

Almost every hard failure below comes from the first two lines: a compiler and C library newer
than the 2025b recipes were tested with. apparadise builders avoid this by building in pinned
Ubuntu 24.04 images (GCC 13, glibc 2.39). Check the host first.

## Symptoms and fixes

### Setup and bootstrap

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 1 | `git@github.com: Permission denied (publickey)` | no SSH key | Clone the EasyBuild repos over HTTPS. They are public. |
| 2 | `Lmod modules tool can not be used, 'lmod' command is not available` | EasyBuild needs a modules tool first, there is no Lmod easyconfig, and `--modules-tool=None` is rejected | Build Lua, LuaRocks, luafilesystem, luaposix and Lmod by hand into `software/`, then source Lmod's `init/bash`. |
| 3 | `avail_repositories: FileRepository missing from list of repositories` | editable installs (`pip install -e`) break the `easybuild` namespace package | Install framework, easyblocks and easyconfigs **non-editable**. |
| 4 | Lmod configure: `lua module(s) are missing: posix` / `luaposix is too old` | `LUA_PATH`/`LUA_CPATH` unset | Export both, including the `?/init.lua` and `?/init.so` patterns. |
| 5, 7 | Lmod `make install` fails in `man_pages` | configure left `SYS_LUA_PATH` empty | Pass `SYS_LUA_PATH=... SYS_LUA_CPATH=...` to `make install`. |
| 6 | `posix.stat was removed after luaposix 37.0` | LuaRocks installed luaposix 38 | Pin `luaposix 36.3-1`, the version the easyconfigs use. |
| 8 | a background build stops with no error | the job died with the shell that started it | `setsid nohup eb ... < /dev/null & disown`, or run it as a service or batch job. |

The session's `env.sh` sets every path in one place (`EASYBUILD_PREFIX`, `BUILDPATH`,
`SOURCEPATH`, `INSTALLPATH`, `ROBOT_PATHS`, `MODULES_TOOL=Lmod`, `TMPDIR`) and is a good
template.

### EasyBuild options

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 9 | `One or more OS dependencies were not found: libibverbs-dev` | headers not installed, no root | The session used `--ignore-osdeps`, which **silently builds OpenMPI/UCX without InfiniBand**. Have an admin install `libibverbs-dev` (or `rdma-core-devel`) instead. |
| 14 | `Failed to download recent config.guess`, many 10 s retries | savannah blocked | Still open in that session. Provide a local `config.guess` (apparadise base images ship one, Q11). |
| 15 | `gcc: error: language 86-64-v3 not recognized` | `--optarch=x86-64-v3` is a bare name, so EasyBuild prepends `-`, giving `-x86-64-v3` | Write the full flag: `--optarch=-march=x86-64-v3` (or `GCC:-march=...`). Modules built before the fix have no `-march` at all. |
| 16, 18 | `Lock .../.locks/<module>.lock already exists, aborting!`, two builds racing | several queues in one install tree, and an orphaned process holding the lock | Run **one EasyBuild at a time per install tree**, in one sequential script (`bootstrap/build-chain.sh`). Kill stray processes by PID, not `pkill -f` (it matched and killed its own shell). Remove the lock only after no `eb` is left running. |

### Host compiler (GCC 15, C23)

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 10 | M4: `Cannot find a type to use in place of socklen_t` | GCC 15 defaults to C23, and old autoconf probes fail | A `gcc`/`cc` wrapper that adds `-std=gnu17`, on `PATH` only for the build run. |
| 11 | `Failed to determine the GCC version from: gcc-15 (...)` | EasyBuild wants `gcc --version` to start with `gcc` | The wrapper rewrites only the first line of `--version` output. |
| 12 | `cannot execute 'cc1': posix_spawnp` | `exec -a gcc` renamed argv[0], and the GCC driver finds `cc1` from argv[0] | Don't rename it. Call the real `gcc-15` for everything except `--version`. |

The final wrapper (`bootstrap/cc-wrapper/gcc`, identical `cc`):

```bash
#!/bin/bash
if [ "$1" = "--version" ]; then
  /usr/bin/gcc-15 --version | sed "1s/^gcc-15 /gcc /"
  exit 0
fi
exec /usr/bin/gcc-15 -std=gnu17 "$@"
```

### glibc 2.42+ `_Generic` macros

glibc 2.42 and later turn `memchr`, `strchr`, `strrchr`, `wmemchr`, `bsearch` and others into
`_Generic` macros, defined through `__glibc_const_generic` in `sys/cdefs.h`. They are on
whenever `__GLIBC_USE (ISOC23)` is set, and **`_GNU_SOURCE` sets it in every language mode**.
Old gnulib and util-linux define `_GNU_SOURCE`, so `-std=gnu17` does not help. Checked on this
host: under `-std=gnu17`, `bsearch` is a macro as soon as `_GNU_SOURCE` is defined.

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 13, 17, 19 | binutils: `iolib.c: error: expected identifier before '_Generic'`, `all-gprofng Error 2` | gprofng vs. the glibc macros | Local easyconfig copies with `configopts = '--disable-gprofng'`. **Every** binutils in the plan needs one: 2.44 (SYSTEM), 2.44-GCCcore-14.3.0 and 2.45 each failed in turn. In Spack: `^binutils~gprofng`. |
| 20, 21 | gettext: `./string.h:777: error: expected identifier or '(' before '_Generic'`, then the same in `wchar.h`, `stdlib.h` | bundled gnulib redeclares the names after glibc's macros | A **forced include** that switches the macros off (below), via `CPPFLAGS='-include .../glibc-generic-off.h'` in local gettext easyconfigs. Patching gnulib headers one by one only moved the error to the next header. Switching to gettext 0.26 alone doesn't help either. |
| 23 | util-linux 2.41: `lsfd.c:1839: error: macro "bsearch" passed 6 arguments, but takes just 5` | same clash: `bsearch` is now a function-like macro, and the call passes a compound literal `&(struct pollfd){.fd = fd,}`, whose comma splits it into an extra macro argument | **Not yet tried.** The same forced include should fix it. Add it to a local copy of `util-linux-2.41-GCCcore-14.3.0.eb`. |

`bootstrap/glibc-generic-off.h`:

```c
#define _GNU_SOURCE 1
#include <features.h>
#include <sys/cdefs.h>
#undef __glibc_const_generic
```

Fix it once, where it starts. One forced include replaced a whole series of per-header patches.

### CPU target

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 22 | AOCL-BLAS 5.1: `bli_x86_asm_macros.h: error: the register 'k2' cannot be clobbered in 'asm'`, then `inlining failed in call to 'always_inline' '_mm512_storeu_pd': target specific option mismatch` | BLIS's `auto` configuration detects the Zen 4 host and compiles AVX-512 kernels. EasyBuild's `CFLAGS` carry `-march=x86-64-v3` from `--optarch`, and x86-64-v3 has no AVX-512, so those kernels can't compile. | **Not yet tried.** Use an optarch that matches the host for this build (`--optarch=-march=znver4`), or keep x86-64-v3 and give AOCL-BLAS a BLIS configuration without AVX-512 kernels. Either way, the library must not select kernels newer than `-march` allows. |

Generic `optarch` values and auto-detecting libraries (BLIS, OpenBLAS `DYNAMIC_ARCH`, FFTW)
disagree in exactly this way. In apparadise the target's `-march` comes from
`containers/targets.yml`, and auto-detection must be pinned to the same level.

## A checklist before the next EasyBuild run

1. Check `gcc --version` and `ldd --version`. On GCC ≥ 15 or glibc ≥ 2.42, expect #10–13,
   #17 and #19–23, or build in an Ubuntu 24.04 container instead.
2. Install EasyBuild non-editable, and make sure Lmod works (`module --version`).
3. Have `libibverbs-dev` installed. Don't use `--ignore-osdeps` for MPI software.
4. Write `--optarch` as a full flag, and match it to what auto-detecting libraries will pick.
5. Put `TMPDIR` and the build path on a volume with room (PyTorch needs tens of GB).
6. Run `eb --dry-run` first (`bootstrap/dryrun.txt` listed 150 modules for PyTorch) and scan it
   for every binutils, gettext and util-linux version that needs a local override.
7. Run one `eb` at a time per install tree, from a script that logs `<step> exit=<code>`.

## Where the record is

All under `/storage/t3/test/motuz/eb`:

| Path | What |
|---|---|
| `FAILURES.md` | failures and workarounds 1–21, the main record |
| `bootstrap/chain.log` | exit code of each step of the sequential chain |
| `bootstrap/build6.log` | the PyTorch run (failed at AOCL-BLAS, #22) |
| `bootstrap/build-r.log` | the R run (failed at util-linux, #23) |
| `bootstrap/build-GCC-*.log`, `build-gettext26.log`, `build-ncurses66.log`, `build-julia.log` | the successful runs. `build.log` to `build5.log` are earlier PyTorch attempts. |
| `bootstrap/build-chain.sh`, `build-gettext26.sh`, `build-julia.sh` | the scripts that ran each step |
| `bootstrap/cc-wrapper/` | the `gcc`/`cc` wrapper (#10–12) |
| `bootstrap/glibc-generic-off.h` | the forced include (#21) |
| `bootstrap/dryrun.txt` | the planned PyTorch module list |
| `bootstrap/docs-notes/group-0.md` … `group-3.md` | condensed notes on the EasyBuild docs, **not checked** against the source pages |
| `env.sh` | every path and EasyBuild setting |
| `easyconfigs/` | local overrides: binutils without gprofng, gettext with the forced include |
| `tmp/eb-*/run-shell-cmd-output/` | full compiler output of each failed step |
