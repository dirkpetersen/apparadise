"""The pull loop: register once, claim a job, run its stages, stream the log, report.

Builders pull and the server never pushes: everything here is an outbound HTTPS call to
the coordinator. The command runner is injectable, so tests run without podman/srun.

Stages run by the builder (resolve and generate already happened on the coordinator):

    build    podman build --target build      (the source's own tool inside base-builder)
    package  podman build                     (final image FROM base-runtime, labelled)
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

# Containerfiles per source. Build stage named `build`, final stage FROM the runtime base.
CONTAINERFILES = {
    "spack": """\
FROM {registry}/{base_builder} AS build
COPY spack.yaml /work/spack.yaml
RUN spack -e /work concretize --fresh && spack -e /work install --fail-fast \\
 && spack -e /work gc -y
FROM {registry}/{base_runtime}
COPY --from=build /opt/software /opt/software
COPY --from=build /opt/view /opt/view
ENV PATH=/opt/view/bin:$PATH
""",
    "easybuild": """\
FROM {registry}/{base_builder} AS build
COPY easystack.yml /work/easystack.yml
RUN eb --easystack /work/easystack.yml --installpath=/opt/apps --robot
FROM {registry}/{base_runtime}
COPY --from=build /opt/apps /opt/apps
ENV MODULEPATH=/opt/apps/modules/all
""",
    "pixi": """\
FROM {registry}/{base_builder} AS build
COPY pixi.toml /work/pixi.toml
RUN cd /work && pixi install && pixi shell-hook -s bash > /work/activate.sh
FROM {registry}/{base_runtime}
COPY --from=build /work/.pixi/envs/default /work/.pixi/envs/default
COPY --from=build /work/activate.sh /opt/apparadise/activate.sh
ENV PATH=/work/.pixi/envs/default/bin:$PATH
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
    if stage == "build":
        return [[tool, "build", "--platform", job["platform"], "--target", "build",
                 "-t", f"{local}-build", "-f", "Containerfile", "."]]
    if stage == "package":
        return [[tool, "build", "--platform", job["platform"], *labels, "-t", local,
                 "-f", "Containerfile", "."]]
    if stage == "test":
        if s.test_mode == "none":
            return []
        test_ref = f"{s.registry_host}/cache/test-{job['package']}:{job['id']}"
        return [
            [tool, "push", *auth, local, f"docker://{test_ref}"],
            ["srun", "-p", s.test_partition, "--gpus=1", "--nice=1000",
             f"--container-image={s.registry_host}#cache/test-{job['package']}:{job['id']}",
             "sh", "-c", job["smoke"]],
        ]
    if stage == "publish":
        cmds = [[tool, "push", *auth, "--digestfile", str(workdir / "digest"), local,
                 f"docker://{ref}"]]
        if s.sign_key:
            cmds.append(["cosign", "sign", "--yes", "--key", s.sign_key, ref])
        return cmds
    raise ValueError(stage)


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
            raise SystemExit(f"Registration refused ({r.status_code}): {r.text[:200]}")
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
        (workdir / "Containerfile").write_text(CONTAINERFILES[job["source"]].format(
            registry=self.s.registry_host, base_builder=job["baseBuilder"],
            base_runtime=job["baseRuntime"]))
        buf = bytearray()
        tail = bytearray()

        async def flush() -> None:
            if buf:
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
                    await self.report(job["id"], "publish", True, digest=digest)
                elif stage == "test":
                    await self.report(job["id"], "test", True, test=job["smoke"][:500])
                else:
                    await self.report(job["id"], stage, True)
            return "published"
        finally:
            authfile.unlink(missing_ok=True)
            self.current = None

    # -- the loop ---------------------------------------------------------------------------

    async def run(self) -> None:
        await self.ensure_token()
        while not self.stopping.is_set():
            try:
                job = await self.claim()
            except httpx.HTTPError as e:
                log.warning("claim failed: %s", e)
                job = None
            if job is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.stopping.wait(), self.s.poll_interval)
                continue
            log.info("job %s: %s %s %s", job["id"], job["package"], job["version"], job["target"])
            try:
                status = await self.execute(job)
                log.info("job %s: %s", job["id"], status)
            except Exception:
                log.exception("job %s crashed", job["id"])
