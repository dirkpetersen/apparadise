"""The builder agent against the real coordinator (in-process, ASGI), with a fake runner."""

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from apparadise.config import Settings as WebSettings
from apparadise.db import Database
from apparadise.index import _item
from apparadise.main import create_app
from apparadise_builder.agent import Agent, plan
from apparadise_builder.config import Settings
from mock.directory import DemoDirectory

H = {"X-Requested-With": "apparadise"}
WEB_REQUIRED = {
    "session_secret": "s", "ldap_url": "ldaps://dc.example.org", "ldap_base_dn": "DC=x",
    "ldap_bind_dn": "svc", "ldap_bind_password": "p", "ldap_admin_group_dn": "CN=A",
    "registry_host": "registry.example.org",
}
DIGEST = "sha256:" + "e" * 64


class FakeRunner:
    def __init__(self, fail_on: str | None = None):
        self.calls: list[list[str]] = []
        self.fail_on = fail_on

    async def __call__(self, argv, cwd, on_output):
        self.calls.append(argv)
        await on_output(f"running {argv[0]} {argv[1]}\n".encode())
        if self.fail_on and self.fail_on in argv:
            await on_output(b"src/foo.c:12: error: expected identifier before '_Generic'\n")
            return 2
        if "--digestfile" in argv:
            Path(argv[argv.index("--digestfile") + 1]).write_text(DIGEST)
        return 0


@pytest.fixture
def world(tmp_path):
    ws = WebSettings(_env_file=None, data_dir=str(tmp_path / "web"), catalog_dir=str(tmp_path),
                     session_cookie_secure=False, **WEB_REQUIRED)
    db = Database(":memory:")
    db.index.replace_source("spack", [_item("spack", "gromacs", ["2026.3"], True, [], False)])
    db.packages.upsert({"name": "gromacs", "title": "GROMACS", "executables": ["gmx_mpi"]})
    web = create_app(ws, db=db, authenticator=DemoDirectory(), load_catalog=False)
    tc = TestClient(web)
    tc.post("/api/login", json={"login": "dana", "password": "demo"}, headers=H)
    token = tc.post("/api/admin/builder-tokens", headers=H).json()["token"]

    def settings(**kw):
        return Settings(_env_file=None, coordinator_url="http://127.0.0.1",
                        builder_name="x86-1", builder_platform="linux/amd64",
                        registration_token=token, registry_host="registry.example.org",
                        data_dir=str(tmp_path / "builder"), work_dir=str(tmp_path / "work"), **kw)

    def agent(runner, **kw):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=web),
                                   base_url="http://127.0.0.1", headers=H)
        return Agent(settings(**kw), client=client, runner=runner)

    def queue():
        r = tc.post("/api/admin/builds", headers=H,
                    json={"package": "gromacs", "version": "2026.3", "targets": ["rtx"]})
        assert r.status_code == 200, r.text
        return r.json()["queued"][0]["id"]

    yield tc, db, agent, queue, tmp_path
    db.close()


async def one_job(agent):
    await agent.ensure_token()
    job = await agent.claim()
    return job, (await agent.execute(job) if job else None)


def test_happy_path_publishes(world):
    tc, db, make_agent, queue, tmp = world
    bid = queue()
    runner = FakeRunner()
    agent = make_agent(runner)
    job, status = asyncio.run(one_job(agent))
    assert job["id"] == bid and status == "published"
    tools = [c[:2] for c in runner.calls]
    assert tools == [["podman", "build"], ["podman", "build"], ["podman", "run"],
                     ["podman", "push"], ["srun", "-p"], ["podman", "push"]]
    assert runner.calls[2][runner.calls[2].index("--user") + 1] == "54321:54321"
    srun = runner.calls[4]
    assert f"--container-image=registry.example.org#cache/test-gromacs:{bid}" in srun
    assert srun[-1] == "nvidia-smi -L && gmx_mpi --version"
    row = db.query_one("SELECT status, image_digest, log_path FROM build WHERE id = ?", (bid,))
    assert row["status"] == "published" and row["image_digest"] == DIGEST
    assert "running podman build" in Path(row["log_path"]).read_text()
    # the token file is private and reused on the next start
    tf = tmp / "builder" / "builder.token"
    assert tf.stat().st_mode & 0o777 == 0o600
    assert not (tmp / "work" / str(bid)).exists()  # cleaned up after the job


def test_failure_reports_stage_and_error(world):
    tc, db, make_agent, queue, _ = world
    bid = queue()
    job, status = asyncio.run(one_job(make_agent(FakeRunner(fail_on="--target"))))
    assert status == "failed"
    row = db.query_one("SELECT status, stage, failure_id FROM build WHERE id = ?", (bid,))
    assert (row["status"], row["stage"]) == ("failed", "build")
    f = db.failures.get(row["failure_id"])
    assert "_Generic" in f["log_excerpt"]


def test_test_mode_none_fails_closed(world):
    tc, db, make_agent, queue, _ = world
    bid = queue()
    runner = FakeRunner()
    job, status = asyncio.run(one_job(make_agent(runner, test_mode="none")))
    assert status == "failed"
    assert not any("push" in c for c in runner.calls)  # nothing pushed, nothing published
    assert db.query_one("SELECT stage FROM build WHERE id = ?", (bid,))["stage"] == "test"


def test_empty_queue(world):
    _, _, make_agent, _, _ = world
    job, status = asyncio.run(one_job(make_agent(FakeRunner())))
    assert job is None


def test_plan_has_no_shell_except_smoke(world, tmp_path):
    _, _, make_agent, _, _ = world
    s = make_agent(FakeRunner()).s
    job = {"id": 7, "image": "public/x:1-rtx", "package": "x", "version": "1", "target": "rtx",
           "source": "spack", "platform": "linux/amd64", "smoke": "nvidia-smi -L"}
    for stage in ("build", "package", "test", "publish"):
        for argv in plan(job, stage, s, tmp_path):
            assert argv[0] in {"podman", "srun", "cosign"}
            if argv[0] == "srun":
                continue
            if "sh" in argv:  # only the constant any-uid check, never job data in a shell
                from apparadise_builder.agent import ANY_UID_CHECK

                assert argv[-2:] == ["-c", ANY_UID_CHECK] and argv[argv.index("--user") + 1] \
                    == "54321:54321"


def test_settings_validation():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, coordinator_url="http://coordinator.example.org",
                 builder_name="b", registry_host="r")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, coordinator_url="https://c.example", builder_name="Bad Name",
                 registry_host="r")


def test_registry_token_goes_to_authfile_not_argv(world):
    tc, db, make_agent, queue, tmp = world
    queue()
    seen = {}

    class Spy(FakeRunner):
        async def __call__(self, argv, cwd, on_output):
            if "push" in argv and "--authfile" in argv:
                f = Path(argv[argv.index("--authfile") + 1])
                seen["mode"] = f.stat().st_mode & 0o777
                seen["auth"] = f.read_text()
            return await super().__call__(argv, cwd, on_output)

    runner = Spy()
    agent = make_agent(runner)

    async def go():
        await agent.ensure_token()
        job = await agent.claim()
        job["registryToken"] = {"username": "robot$x", "password": "s3cret"}
        return job, await agent.execute(job)

    job, status = asyncio.run(go())
    assert status == "published" and seen["mode"] == 0o600
    assert "registry.example.org" in seen["auth"]
    assert not any("s3cret" in a for argv in runner.calls for a in argv)
    assert not (tmp / "work" / str(job["id"])).exists()  # work tree and auth file removed


def test_idle_exit(world):
    _, _, make_agent, _, _ = world
    agent = make_agent(FakeRunner(), idle_exit_polls=2, poll_interval=1)
    asyncio.run(asyncio.wait_for(agent.run(), 10))
    assert agent.stopping.is_set()


def test_containerfile_uses_job_base_images(world, tmp_path):
    from apparadise_builder.agent import CONTAINERFILES

    text = CONTAINERFILES["spack"].format(registry="r.example", base_builder="cache/b:13.0-rtx",
                                          base_runtime="cache/r:13.0-rtx")
    assert text.startswith("FROM r.example/cache/b:13.0-rtx AS build")
    assert "FROM r.example/cache/r:13.0-rtx" in text


def test_signs_the_pushed_digest_not_the_tag(world):
    _, _, make_agent, queue, _ = world
    queue()
    runner = FakeRunner()
    job, status = asyncio.run(one_job(make_agent(runner, sign_key="cosign.key")))
    assert status == "published"
    sign = [c for c in runner.calls if c[0] == "cosign"]
    assert sign and sign[0][-1] == f"registry.example.org/public/gromacs@{DIGEST}"


def test_missing_digest_file_reports_failure_not_lost(world):
    _, db, make_agent, queue, _ = world
    bid = queue()

    class NoDigest(FakeRunner):
        async def __call__(self, argv, cwd, on_output):
            await on_output(b"ok\n")
            return 0  # push "succeeds" but writes no digest file

    job, status = asyncio.run(one_job(make_agent(NoDigest())))
    assert status == "failed"
    row = db.query_one("SELECT status, stage, failure_id FROM build WHERE id = ?", (bid,))
    assert (row["status"], row["stage"]) == ("failed", "publish")
    assert "FileNotFoundError" in db.failures.get(row["failure_id"])["log_excerpt"]


def test_containerfiles_expose_base_cuda_to_the_build_tools():
    from apparadise_builder.agent import CONTAINERFILES

    fmt = {"registry": "r.example", "base_builder": "b", "base_runtime": "r"}
    eb = CONTAINERFILES["easybuild"].format(**fmt)
    # easyblocks only build GPU code with EBROOTCUDA; root needs EasyBuild's explicit opt-in
    assert "EBROOTCUDA=/usr/local/cuda EBVERSIONCUDA=${CUDA_VERSION}" in eb
    assert "EASYBUILD_ALLOW_USE_AS_ROOT_AND_ACCEPT_CONSEQUENCES=1" in eb
    assert "EASYBUILD_CUDA_SANITY_CHECK_ERROR_ON_FAILED_CHECKS=1" in eb
    pixi = CONTAINERFILES["pixi"].format(**fmt)
    # GPU-less builders: override __cuda; solve once, install that exact lock, ship it
    assert 'CONDA_OVERRIDE_CUDA="${CUDA_VERSION%.*}"' in pixi
    assert "pixi lock && pixi install --frozen" in pixi
    assert "COPY --from=build /work/pixi.lock /opt/apparadise/pixi.lock" in pixi


def test_images_run_as_any_uid():
    from apparadise_builder.agent import CONTAINERFILES

    fmt = {"registry": "r.example", "base_builder": "b", "base_runtime": "r"}
    for source, paths in (("spack", "/opt/software /opt/view"), ("easybuild", "/opt/apps"),
                          ("pixi", "/work/.pixi/envs/default")):
        text = CONTAINERFILES[source].format(**fmt)
        build, final = text.split("FROM r.example/r")
        assert f"chmod -R a+rX {paths}" in build  # in the build stage: no second copy
        assert final.rstrip().endswith("USER 65534:65534")
        assert "ENTRYPOINT" not in text and "chmod" not in final


def test_any_uid_check_flags_unreadable_files(tmp_path):
    import subprocess

    from apparadise_builder.agent import ANY_UID_CHECK

    # The check's logic, without a container: fake `id` and `find`
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "id").write_text("#!/bin/sh\necho 54321\n")
    for out, code in (("", 0), ("/opt/view/lib/secret.so", 1)):
        (bin_ / "find").write_text(f"#!/bin/sh\necho -n '{out}'\n")
        for f in bin_.iterdir():
            f.chmod(0o755)
        r = subprocess.run(["sh", "-c", ANY_UID_CHECK], env={"PATH": f"{bin_}:/usr/bin:/bin"},
                           capture_output=True, text=True)
        assert r.returncode == code, r.stderr
        assert (out in r.stderr) if out else r.stderr == ""
