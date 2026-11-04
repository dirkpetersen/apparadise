"""The pull loop: register once, claim a job, run its stages, stream the log, report.

Builders pull and the server never pushes: everything here is an outbound HTTPS call to
the coordinator. The command runner is injectable, so tests run without podman/srun.

Stages run by the builder (resolve and generate already happened on the coordinator):

    build    podman build                     (the source's own tool inside base-builder, then
                                              the final image FROM base-runtime, labelled)
    package  podman run --user <any uid>      (every installed file readable to any user)
    test     push a test tag, srun it on a GPU node of the target, run the smoke test
    publish  push the release tag, record the digest, sign (if a key is configured)
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import re
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger(__name__)

Runner = Callable[[list[str], Path, Callable[[bytes], Awaitable[None]]], Awaitable[int]]
CSRF = {"X-Requested-With": "apparadise"}
RECIPE_FILE = {"spack": "spack.yaml", "easybuild": "easystack.yml", "pixi": "pixi.toml"}
ERROR_RE = re.compile(rb"^.*\b(error|fatal|failed)\b.*$", re.I | re.M)
LOG_FLUSH_BYTES = 64 * 1024
HEARTBEAT_SECONDS = 60  # well under the coordinator's BUILDER_STALE_MINUTES
# The concretize stage: the solver alone, in the job's builder image, on the rendered recipe.
# Constant commands: no job data goes through a shell.
CONCRETIZE = {
    "spack": ["spack", "-e", "/work", "concretize", "--fresh"],
    "easybuild": ["env", "EASYBUILD_ALLOW_USE_AS_ROOT_AND_ACCEPT_CONSEQUENCES=1",
                  "eb", "--easystack", "/work/easystack.yml", "--robot", "--dry-run"],
    "pixi": ["sh", "-c", 'export CONDA_OVERRIDE_CUDA="${CUDA_VERSION%.*}" && cd /work '
             '&& pixi lock'],
}
# A uid/gid no image knows: the package must work for it (user-identity.md).
ANY_UID = "54321:54321"
# Symlinks are skipped: a link has no mode of its own, and a broken one (EasyBuild's OpenSSL
# wrapper links include/openssl to the builder's /usr/include) is no permission problem.
ANY_UID_CHECK = ('test "$(id -u)" = 54321 && bad="$(find /opt /work -xdev ! -type l ! -readable '
                 '-print 2>/dev/null | head -5)" && { [ -z "$bad" ] || '
                 '{ echo "unreadable as a non-root user: $bad" >&2; exit 1; }; }')

# Containerfiles per source. Build stage named `build`, final stage FROM the runtime base.
# Every image must run as ANY uid (docs/operations/user-identity.md): Pyxis/Enroot run it as
# the submitting user, Run:ai as the uid/gid from AD. So the build stage makes everything
# readable by all (a+rX; done there, so the final image gets no second copy of each file),
# the final stage has no entrypoint and a numeric non-root default user, and nothing needs
# to be written inside the image at runtime.
CONTAINERFILES = {
    "spack": """\
FROM {registry}/{base_builder} AS build
COPY spack.yaml /work/spack.yaml
# /cache: this builder's Spack binary cache (plan()), read-only unless the job may push.
# shortcut: unsigned and per builder host; move to the signed OCI cache once Zot runs (Q47).
RUN spack -e /work buildcache keys --install --trust --yes-to-all \\
 && if [ -d /cache ]; then spack -e /work mirror add --unsigned apparadise-local \\
      file:///cache; fi \\
 && spack -e /work concretize --fresh && spack -e /work install --fail-fast \\
 && if [ -w /cache ]; then spack -e /work buildcache push --unsigned --update-index \\
      --with-build-dependencies apparadise-local || echo "apparadise: cache push failed"; fi \\
 && spack -e /work gc -y && chmod -R a+rX /opt/software /opt/view
FROM {registry}/{base_runtime}
COPY --from=build /opt/software /opt/software
COPY --from=build /opt/view /opt/view
ENV PATH=/opt/view/bin:$PATH
USER 65534:65534
""",
    "easybuild": """\
FROM {registry}/{base_builder} AS build
COPY easystack.yml /work/easystack.yml
# CUDA comes from the base image (filter-deps: CUDA). Easyblocks such as GROMACS, PyTorch
# and CMakeMake only build GPU code when $EBROOTCUDA is set, so point them at it; and fail
# the CUDA sanity check (device code, PTX) instead of only warning. The build runs as root
# inside a throw-away container, which EasyBuild refuses without the explicit opt-in.
# CUDA_HOME/CUDA_ROOT/CUDA_PATH: what EasyBuild's CUDA module would set; makefile builds
# (MotionCor3: CUDAHOME=$CUDA_HOME) otherwise miss cufft.h.
# EasyBuild's own build-dir cleanup chmods every path first and fails on M4's deliberately
# over-long confdir3/confdir3/... test tree; the build dir is the job's /scratch mount (plan()).
ENV EBROOTCUDA=/usr/local/cuda EBVERSIONCUDA=${{CUDA_VERSION}} \
    CUDA_HOME=/usr/local/cuda CUDA_ROOT=/usr/local/cuda CUDA_PATH=/usr/local/cuda \
    EASYBUILD_ALLOW_USE_AS_ROOT_AND_ACCEPT_CONSEQUENCES=1 \
    EASYBUILD_CUDA_SANITY_CHECK_ERROR_ON_FAILED_CHECKS=1 \
    EASYBUILD_DISABLE_CLEANUP_BUILDDIR=1 \
    EASYBUILD_ACCEPT_EULA_FOR=cuDNN \
    TAR_OPTIONS=--no-same-owner \
    EASYBUILD_SOURCEPATH=/scratch/sources:/opt/eb-sources
# EasyBuild downloads into the first source path and finds files in any (plan() mounts
# the shared source dir at one of them).
# --no-same-owner: tar as (namespaced) root restores the tarball's owners, which fails for
# UIDs outside the user namespace (Perl 5.36.1: uid 197609) and leaves sub-UID files behind.
# /opt/apps may be the builder's shared install tree (plan()): the coordinator runs one
# EasyBuild job per tree at a time, so a lock left there is stale. The image gets only this
# package's dependency closure (the modules the dry run lists), not the whole tree.
RUN rm -rf /opt/apps/software/.locks \\
 && eb --easystack /work/easystack.yml --installpath=/opt/apps --robot \\
 && eb --easystack /work/easystack.yml --installpath=/opt/apps --robot --dry-run \\
    | sed -n 's/.*(module: \\(.*\\))$/\\1/p' > /tmp/closure && test -s /tmp/closure \\
 && mkdir -p /opt/image-apps && cd /opt/apps \\
 && while read -r m; do cp -a --parents "software/$m" "modules/all/$m.lua" /opt/image-apps/ \\
    || exit 1; done < /tmp/closure \\
 && chmod -R a+rX /opt/image-apps
FROM {registry}/{base_runtime}
COPY --from=build /opt/image-apps /opt/apps
ENV MODULEPATH=/opt/apps/modules/all
USER 65534:65534
""",
    "pixi": """\
FROM {registry}/{base_builder} AS build
COPY pixi.toml /work/pixi.toml
# Builders have no GPU: tell the solver which CUDA the target's driver offers (the base
# image's major.minor), or packages that need __cuda (PyTorch...) are refused. Solve once,
# install exactly that lock, and ship the lock in the image as the record of what is in it.
RUN cd /work && : "${{CUDA_VERSION:?base image sets CUDA_VERSION}}" \
 && export CONDA_OVERRIDE_CUDA="${{CUDA_VERSION%.*}}" \
 && pixi lock && pixi install --frozen && pixi shell-hook -s bash > /work/activate.sh \
 && chmod -R a+rX /work/.pixi/envs/default /work/activate.sh /work/pixi.lock
FROM {registry}/{base_runtime}
COPY --from=build /work/.pixi/envs/default /work/.pixi/envs/default
COPY --from=build /work/activate.sh /opt/apparadise/activate.sh
COPY --from=build /work/pixi.lock /opt/apparadise/pixi.lock
ENV PATH=/work/.pixi/envs/default/bin:$PATH
USER 65534:65534
""",
}


def plan(job: dict[str, Any], stage: str, s: Settings, workdir: Path) -> list[list[str]]:
    """The commands for one stage. Every value comes from the job or validated settings and
    is passed as an argv element, never through a shell, except the smoke test, which runs
    inside the container."""
    tool = s.container_tool
    ref = f"{s.registry_host}/{job['image']}"
    authfile = workdir / "auth.json"
    auth = ["--authfile", str(authfile)] if authfile.exists() else []
    local = f"apparadise-local/{job['id']}"
    labels = [f"--label=io.apparadise.{k}={job[k]}" for k in ("package", "version", "target",
                                                                "source")]
    if stage == "concretize":
        # pixi writes pixi.lock and Spack its .spack-env lock and spack.lock; only the
        # EasyBuild dry run leaves the recipe alone. The dir is the job's own, deleted after.
        mode = "ro" if job["source"] == "easybuild" else "rw"
        return [[tool, "run", "--rm", "--platform", job["platform"], *auth,
                 "-v", f"{workdir}:/work:{mode}", f"{s.registry_host}/{job['baseBuilder']}",
                 *CONCRETIZE[job["source"]]]]
    if stage == "build":
        # Build trees, Spack stages and downloads go to the job's own dir, never into a layer:
        # gnulib's deliberately deep confdir3/confdir3/... test trees (M4, bison, tar) can be
        # neither committed nor removed on overlayfs over CephFS. Deleted with the workdir.
        scratch = workdir / "scratch"
        for d in ("tmp", "eb-build", "sources"):
            (scratch / d).mkdir(parents=True, exist_ok=True)
        (scratch / "tmp").chmod(0o1777)
        vol = ["-v", f"{scratch}:/scratch"]
        if s.cache_dir:
            key = f"{job['target']}-{job['platform'].split('/')[-1]}"
            push = job.get("cachePush", False)
            if job["source"] == "spack":
                cache = Path(s.cache_dir) / "spack" / key
                cache.mkdir(parents=True, exist_ok=True)
                vol += ["-v", f"{cache}:/cache:{'rw' if push else 'ro'}"]
            elif job["source"] == "easybuild":
                # Sources are arch-independent and EasyBuild checks every checksum, so all
                # builds read one shared dir; only sysadmin builds download into it.
                # shortcut: two targets fetching one file at once can clash; the checksum then
                # fails one build, which a retry fixes. Download to a temp name if that recurs.
                src = Path(s.cache_dir) / "eb" / "sources"
                src.mkdir(parents=True, exist_ok=True)
                vol += ["-v", f"{src}:/scratch/sources" if push else f"{src}:/opt/eb-sources:ro"]
                if push:
                    # shortcut: user requests still build their whole EasyBuild tree (a
                    # writable shared tree would let them plant modules); give them a
                    # read-only lower tree if that gets too slow.
                    tree = Path(s.cache_dir) / "eb" / key
                    tree.mkdir(parents=True, exist_ok=True)
                    vol += ["-v", f"{tree}:/opt/apps"]
        # One podman build makes the final, labelled image. A second build in `package`
        # would resolve the base-builder tag again and rebuild everything if the base was
        # updated meanwhile (layer-cache miss).
        return [[tool, "build", "--platform", job["platform"], *vol, *labels, "-t", local,
                 "-f", "Containerfile", "."]]
    if stage == "package":
        return [# Runs as an arbitrary uid, as on Slurm or Run:ai, and fails if anything
                # installed is unreadable to it. No GPU needed.
                [tool, "run", "--rm", "--platform", job["platform"], "--user", ANY_UID,
                 "--entrypoint", "sh", local, "-c", ANY_UID_CHECK]]
    if stage == "test":
        if s.test_mode == "none":
            return []
        test_ref = f"{s.registry_host}/cache/test-{job['package']}:{job['id']}"
        # Q19: Pyxis/Enroot on the GPU node pulls the test tag with the job's own login,
        # from a 0600 .credentials file in the job's work dir (WORK_DIR must be on storage
        # the GPU nodes mount). Never a site account.
        enroot = workdir / "enroot"
        env = (["--export=ALL,ENROOT_CONFIG_PATH=" + str(enroot)]
               if (enroot / ".credentials").exists() else [])
        return [
            [tool, "push", *auth, local, f"docker://{test_ref}"],
            ["srun", "-p", s.test_partition, "--gpus=1", "--nice=1000", *env,
             f"--container-image={s.registry_host}#cache/test-{job['package']}:{job['id']}",
             "sh", "-c", job["smoke"]],
        ]
    if stage == "publish":
        # Signing happens in execute(), after the push, on ref@<pushed digest>: never on the
        # mutable tag, which a concurrent rebuild could move.
        return [[tool, "push", *auth, "--digestfile", str(workdir / "digest"), local,
                 f"docker://{ref}"]]
    raise ValueError(stage)


def write_enroot_credentials(path: Path, host: str, tok: dict[str, str]) -> None:
    """The job login in Enroot's netrc format, in a 0700 dir, file 0600 (Q19)."""
    path.mkdir(mode=0o700, exist_ok=True)
    for v in (host, tok["username"], tok["password"]):
        if not v or any(c.isspace() for c in v):
            raise ValueError("registry login must not contain whitespace")
    fd = os.open(path / ".credentials", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"machine {host} login {tok['username']} password {tok['password']}\n")


async def subprocess_runner(argv: list[str], cwd: Path,
                            on_output: Callable[[bytes], Awaitable[None]]) -> int:
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL, start_new_session=True)
    assert proc.stdout is not None
    while chunk := await proc.stdout.read(8192):
        await on_output(chunk)
    return await proc.wait()


class Agent:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None,
                 runner: Runner = subprocess_runner):
        self.s = settings
        self.client = client or httpx.AsyncClient(base_url=settings.coordinator_url,
                                                  timeout=60, headers=CSRF)
        self.runner = runner
        self.token: str | None = None
        self.stopping = asyncio.Event()
        self.current: int | None = None

    # -- coordinator calls ----------------------------------------------------------------

    def _auth(self) -> dict[str, str]:
        return {**CSRF, "Authorization": f"Bearer {self.token}"}

    async def ensure_token(self) -> None:
        f = self.s.token_file
        if f.exists():
            self.token = f.read_text().strip()
            return
        reg = self.s.registration_token.get_secret_value()
        if not reg:
            raise SystemExit("Not registered yet: set REGISTRATION_TOKEN (from a sysadmin, "
                             "POST /api/admin/builder-tokens) for the first start.")
        r = await self.client.post("/api/builder/register", headers=CSRF, json={
            "token": reg, "name": self.s.builder_name, "platform": self.s.builder_platform,
            "onprem": self.s.builder_onprem})
        if r.status_code != 200:
            raise SystemExit(f"Registration refused (HTTP {r.status_code}): the token is "
                             "invalid, used or expired; ask a sysadmin for a new one.")
        self.token = r.json()["token"]
        f.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(self.token)
        log.info("registered as %s (%s)", self.s.builder_name, self.s.builder_platform)

    async def claim(self) -> dict[str, Any] | None:
        r = await self.client.post("/api/builder/claim", headers=self._auth())
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()

    async def report(self, job_id: int, stage: str, ok: bool, **extra: str) -> None:
        r = await self.client.post(f"/api/builder/jobs/{job_id}/report", headers=self._auth(),
                                   json={"stage": stage, "ok": ok, **extra})
        r.raise_for_status()

    # -- one job ----------------------------------------------------------------------------

    async def execute(self, job: dict[str, Any]) -> str:
        """Run all stages of `job`. Returns the final status: published or failed."""
        self.current = job["id"]
        workdir = Path(self.s.work_dir) / str(job["id"])
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / RECIPE_FILE[job["source"]]).write_text(job["recipe"])
        authfile = workdir / "auth.json"
        if job.get("registryToken"):
            # The job's robot credentials go to a 0600 auth file, never into argv or the log.
            tok = job["registryToken"]
            cred = base64.b64encode(f"{tok['username']}:{tok['password']}".encode()).decode()
            fd = os.open(authfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump({"auths": {self.s.registry_host: {"auth": cred}}}, fh)
            write_enroot_credentials(workdir / "enroot", self.s.registry_host, tok)
        (workdir / "Containerfile").write_text(CONTAINERFILES[job["source"]].format(
            registry=self.s.registry_host, base_builder=job["baseBuilder"],
            base_runtime=job["baseRuntime"]))
        buf = bytearray()
        tail = bytearray()
        lock = asyncio.Lock()  # one log POST at a time, so chunks arrive in order

        async def flush() -> None:
            async with lock:
                data = bytes(buf)
                buf.clear()
                await self.client.post(f"/api/builder/jobs/{job['id']}/log",
                                       headers=self._auth(), content=data)

        async def on_output(chunk: bytes) -> None:
            buf.extend(chunk)
            tail.extend(chunk)
            del tail[:-65536]
            if len(buf) >= LOG_FLUSH_BYTES:
                await flush()

        async def heartbeat() -> None:
            # A quiet compile can run for an hour without output; keep the coordinator's
            # last_seen fresh so the job is not reaped as lost.
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                with contextlib.suppress(httpx.HTTPError):
                    await flush()  # an empty POST still refreshes last_seen

        beat = asyncio.create_task(heartbeat())
        stage = job["stages"][0]
        try:
            for stage in job["stages"]:
                if stage == "test" and self.s.test_mode == "none":
                    await flush()
                    await self.report(job["id"], "test", False,
                                      error="test stage disabled (TEST_MODE=none): not publishing")
                    return "failed"
                for argv in plan(job, stage, self.s, workdir):
                    buf.extend(f"\n$ {shlex.join(argv)}\n".encode())
                    rc = await self.runner(argv, workdir, on_output)
                    if rc != 0:
                        await flush()
                        errs = ERROR_RE.findall(bytes(tail))
                        line = (errs[-1] if errs else f"{argv[0]} exited with {rc}".encode())
                        await self.report(job["id"], stage, False,
                                          error=line.decode(errors="replace")[:2000])
                        return "failed"
                await flush()
                if stage == "publish":
                    digest = (workdir / "digest").read_text().strip()
                    if self.s.sign_key:
                        ref = f"{self.s.registry_host}/{job['image'].rsplit(':', 1)[0]}@{digest}"
                        argv = ["cosign", "sign", "--yes", "--key", self.s.sign_key, ref]
                        if await self.runner(argv, workdir, on_output) != 0:
                            await flush()
                            await self.report(job["id"], "publish", False,
                                              error="cosign sign failed")
                            return "failed"
                    await self.report(job["id"], "publish", True, digest=digest)
                elif stage == "test":
                    await self.report(job["id"], "test", True, test=job["smoke"][:500])
                else:
                    await self.report(job["id"], stage, True)
            return "published"
        except (httpx.HTTPError, OSError) as e:
            # A network error or a missing file: report the real cause when we can, so the
            # job does not sit "running" until the reaper calls it "builder lost".
            log.warning("job %s: %s at stage %s", job["id"], type(e).__name__, stage)
            with contextlib.suppress(httpx.HTTPError):
                await self.report(job["id"], stage, False,
                                  error=f"builder error at {stage}: {type(e).__name__}")
            return "failed"
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)
            authfile.unlink(missing_ok=True)
            (workdir / "enroot" / ".credentials").unlink(missing_ok=True)
            # The log lives on the coordinator; the work tree only fills the scratch disk.
            # rm, not shutil.rmtree: Python 3.12's rmtree recurses per directory level and
            # raises RecursionError (ignore_errors does not cover it) on gnulib's confdir3 trees.
            # Rootless podman: sources a build unpacked as root keep their tarball owners,
            # which map to sub-UIDs the builder user cannot delete outside the user namespace.
            rm = ["rm", "-rf", "--", str(workdir)]
            for argv in (rm, [self.s.container_tool, "unshare", *rm]):
                proc = await asyncio.create_subprocess_exec(
                    *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                if await proc.wait() == 0 or os.geteuid() == 0:
                    break
            self.current = None

    # -- the loop ---------------------------------------------------------------------------

    async def run(self) -> None:
        await self.ensure_token()
        idle = 0
        while not self.stopping.is_set():
            try:
                job = await self.claim()
            except httpx.HTTPError as e:
                log.warning("claim failed: %s", type(e).__name__)  # never the URL
                job = None
            if job is None:
                idle += 1
                if self.s.idle_exit_polls and idle >= self.s.idle_exit_polls:
                    log.info("idle for %d polls: exiting", idle)
                    self.stopping.set()
                    break
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.stopping.wait(), self.s.poll_interval)
                continue
            idle = 0
            log.info("job %s: %s %s %s", job["id"], job["package"], job["version"], job["target"])
            try:
                status = await self.execute(job)
                log.info("job %s: %s", job["id"], status)
            except Exception:
                log.exception("job %s crashed", job["id"])
