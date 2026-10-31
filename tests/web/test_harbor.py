"""Per-job Harbor robots: created on claim, deleted when the job ends, released on outage."""

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from apparadise.config import Settings
from apparadise.db import Database
from apparadise.harbor import HarborClient
from apparadise.index import _item
from apparadise.main import create_app
from mock.directory import DemoDirectory
from mock.harbor_mock import create_app as create_harbor

from .conftest import REQUIRED

H = {"X-Requested-With": "apparadise"}
D = "sha256:" + "f" * 64
HARBOR = {"harbor_url": "https://harbor.example.org", "harbor_username": "admin",
          "harbor_password": "harbor"}


@pytest.fixture
def env(tmp_path):
    s = Settings(_env_file=None, data_dir=str(tmp_path), catalog_dir=str(tmp_path),
                 session_cookie_secure=False, **REQUIRED, **HARBOR)
    mock = create_harbor()
    harbor = HarborClient(s, transport=httpx.ASGITransport(app=mock))
    db = Database(":memory:")
    db.index.replace_source("spack", [_item("spack", "gromacs", ["1"], True, [], False)])
    with TestClient(create_app(s, db=db, authenticator=DemoDirectory(), harbor=harbor)) as c:
        c.post("/api/login", json={"login": "dana", "password": "demo"}, headers=H)
        tok = c.post("/api/admin/builder-tokens", headers=H).json()["token"]
        r = c.post("/api/builder/register", headers=H,
                   json={"token": tok, "name": "b1", "platform": "linux/amd64"})
        bh = {**H, "Authorization": f"Bearer {r.json()['token']}"}
        c.post("/api/admin/builds", headers=H,
               json={"package": "gromacs", "version": "1", "targets": ["rtx"]})
        yield c, db, mock, bh
    db.close()


def test_robot_created_scoped_and_deleted(env):
    c, db, mock, bh = env
    job = c.post("/api/builder/claim", headers=bh).json()
    tok = job["registryToken"]
    assert tok["username"].startswith(f"robot$apparadise-job-{job['id']}-") and tok["password"]
    robot = next(iter(mock.state.robots.values()))
    assert [p["namespace"] for p in robot["body"]["permissions"]] == ["public", "cache"]
    assert robot["body"]["duration"] == 1
    for stage in ("build", "package", "test"):
        c.post(f"/api/builder/jobs/{job['id']}/report", headers=bh,
               json={"stage": stage, "ok": True})
    assert mock.state.robots  # still alive mid-job
    r = c.post(f"/api/builder/jobs/{job['id']}/report", headers=bh,
               json={"stage": "publish", "ok": True, "digest": D})
    assert r.status_code == 200 and "harbor_robot_id" not in r.json()
    assert mock.state.robots == {}
    # the secret is never written to the audit log
    assert tok["password"] not in str(db.audit.query(limit=1000))


def test_failed_job_deletes_robot(env):
    c, _, mock, bh = env
    job = c.post("/api/builder/claim", headers=bh).json()
    c.post(f"/api/builder/jobs/{job['id']}/report", headers=bh,
           json={"stage": "build", "ok": False, "error": "error: boom"})
    assert mock.state.robots == {}


def test_harbor_outage_releases_job(env):
    c, db, mock, bh = env
    mock.state.fail = 500
    assert c.post("/api/builder/claim", headers=bh).status_code == 503
    row = db.query_one("SELECT status, builder_id FROM build")
    assert row == {"status": "queued", "builder_id": None}
    mock.state.fail = None
    assert c.post("/api/builder/claim", headers=bh).status_code == 200


def test_harbor_settings_validated():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **REQUIRED, harbor_url="https://h.example")  # no creds
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **REQUIRED, **{**HARBOR, "harbor_url": "http://h.example"})
