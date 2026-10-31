"""Builder-side staging against the real coordinator over ASGI (no DB access from the node)."""

import subprocess

from fastapi.testclient import TestClient

from apparadise import coordinator
from apparadise.config import Settings as WebSettings
from apparadise.db import Database
from apparadise.main import create_app
from apparadise_builder.config import Settings
from apparadise_builder.stage import stage_all
from mock.directory import DemoDirectory

H = {"X-Requested-With": "apparadise"}
D = "sha256:" + "c" * 64
WEB = {"session_secret": "s", "ldap_url": "ldaps://dc.example.org", "ldap_base_dn": "DC=x",
       "ldap_bind_dn": "svc", "ldap_bind_password": "p", "ldap_admin_group_dn": "CN=A",
       "registry_host": "registry.example.org"}


def test_stage_reports_each_delivery(tmp_path):
    ws = WebSettings(_env_file=None, data_dir=str(tmp_path), catalog_dir=str(tmp_path),
                     session_cookie_secure=False, **WEB)
    db = Database(":memory:")
    pid = db.packages.upsert({"name": "g", "title": "G", "origin": "request"})
    ids = [coordinator.enqueue(db, pid, v, "spack", "vr", "linux/arm64", "r",
                               requested_by="d", restricted=False) for v in ("1", "2")]
    x86 = coordinator.enqueue(db, pid, "1", "spack", "rtx", "linux/amd64", "r",
                              requested_by="d", restricted=False)
    with db.tx() as c:
        c.execute("UPDATE build SET status = 'published', image_digest = ?", (D,))
    web = create_app(ws, db=db, authenticator=DemoDirectory(), load_catalog=False,
                     background=False)
    tc = TestClient(web)
    tc.post("/api/login", json={"login": "dana", "password": "demo"}, headers=H)
    tok = tc.post("/api/admin/builder-tokens", headers=H).json()["token"]
    bt = tc.post("/api/builder/register", headers=H, json={
        "token": tok, "name": "arm-stage", "platform": "linux/arm64"}).json()["token"]
    s = Settings(_env_file=None, coordinator_url="http://127.0.0.1", builder_name="arm-stage",
                 builder_platform="linux/arm64", registry_host="registry.example.org",
                 data_dir=str(tmp_path / "b"), sqsh_dir="/shared/c")
    s.token_file.parent.mkdir(parents=True)
    s.token_file.write_text(bt)
    calls = []

    def runner(argv, env):
        calls.append((argv, env))
        if argv[4] == "2":  # version 2 fails to stage
            raise subprocess.CalledProcessError(1, argv)

    client = tc  # TestClient is an httpx.Client bound to the app
    results = stage_all(s, client, runner=runner, stage_tool="/bin/stage")
    assert results == [{"id": ids[0], "ok": True}, {"id": ids[1], "ok": False}]
    assert calls[0][0][1:] == ["registry.example.org", "public", "g", "1", "vr", D, "/shared/c"]
    assert calls[0][1] == {"ARCH": "arm64", "FORCE": "1"}
    rows = {r["id"]: r for r in db.query("SELECT id, delivered_at, delivery_error FROM build")}
    assert rows[ids[0]]["delivered_at"] and not rows[ids[1]]["delivered_at"]
    assert "stage-sqsh failed" in rows[ids[1]]["delivery_error"]
    assert not rows[x86]["delivered_at"]  # other platform untouched
    # the failed one is retried next run; the delivered one is not offered again
    calls.clear()
    stage_all(s, client, runner=lambda a, e: calls.append(a), stage_tool="/bin/stage")
    assert [c[4] for c in calls] == ["2"]


def test_stage_keeps_going_when_a_report_fails(tmp_path):
    import httpx as _httpx

    from apparadise_builder.config import Settings as BS

    s = BS(_env_file=None, coordinator_url="http://127.0.0.1", builder_name="n",
           builder_platform="linux/arm64", registry_host="r", data_dir=str(tmp_path))
    s.token_file.parent.mkdir(parents=True, exist_ok=True)
    s.token_file.write_text("t")
    deliveries = [{"id": i, "project": "public", "package": "g", "version": str(i),
                   "target": "vr", "image_digest": D} for i in (1, 2)]

    def handler(req):
        if req.method == "GET":
            return _httpx.Response(200, json={"deliveries": deliveries})
        return _httpx.Response(404 if req.url.path.endswith("/1") else 200, json={})

    client = _httpx.Client(transport=_httpx.MockTransport(handler), base_url="http://x")
    res = stage_all(s, client, runner=lambda a, e: None, stage_tool="/bin/stage")
    assert res == [{"id": 1, "ok": False}, {"id": 2, "ok": True}]
