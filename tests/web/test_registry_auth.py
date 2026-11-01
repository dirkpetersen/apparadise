"""Zot's token service (/registry/token): who may pull or push what, signed tokens Zot can
verify, job credentials that end with the job, and AD logins with the login throttle."""

import base64
import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi.testclient import TestClient

from apparadise.config import Settings
from apparadise.db import Database
from apparadise.index import _item
from apparadise.main import create_app
from mock.directory import DemoDirectory

from .conftest import REQUIRED

H = {"X-Requested-With": "apparadise"}
SERVICE = "apparadise-registry"


@pytest.fixture
def env(tmp_path):
    s = Settings(_env_file=None, data_dir=str(tmp_path / "data"),
                 catalog_dir=str(Path(__file__).resolve().parents[2] / "mock" / "catalog"),
                 session_cookie_secure=False, **REQUIRED)
    db = Database(":memory:")
    db.index.replace_source("spack", [_item("spack", "gromacs", ["2026.3"], True, [], False)])
    app = create_app(s, db=db, authenticator=DemoDirectory())
    with TestClient(app) as c:
        yield c, db, tmp_path / "data"
    db.close()


def basic(user, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def token(c, scopes, auth=None, service=SERVICE):
    params = [("service", service)] + [("scope", s) for s in scopes]
    return c.get("/registry/token", params=params, headers=auth or {})


def claims(data_dir, tok):
    """Verify the ES256 signature with registry-verify.pem (what Zot does), return claims."""
    pub = serialization.load_pem_public_key((data_dir / "registry-verify.pem").read_bytes())
    head, body, sig = tok.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    raw = base64.urlsafe_b64decode(pad(sig))
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pub.verify(der, f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))
    assert json.loads(base64.urlsafe_b64decode(pad(head))) == {"alg": "ES256", "typ": "JWT"}
    return json.loads(base64.urlsafe_b64decode(pad(body)))


def access(c, data_dir, scopes, auth=None):
    r = token(c, scopes, auth)
    assert r.status_code == 200, r.text
    return {a["name"]: a["actions"] for a in claims(data_dir, r.json()["token"])["access"]}


def claimed_job(c):
    """Queue gromacs for rtx, register a builder, claim the job."""
    c.post("/api/login", json={"login": "dana", "password": "demo"}, headers=H)
    tok = c.post("/api/admin/builder-tokens", headers=H).json()["token"]
    assert c.post("/api/admin/builds", headers=H, json={
        "package": "gromacs", "version": "2026.3", "targets": ["rtx"]}).status_code == 200
    c.post("/api/logout", headers=H)
    bt = c.post("/api/builder/register", headers=H, json={
        "token": tok, "name": "b1", "platform": "linux/amd64", "onprem": True}).json()["token"]
    bh = {**H, "Authorization": f"Bearer {bt}"}
    job = c.post("/api/builder/claim", headers=bh).json()
    return job, bh


def test_anonymous_pulls_public_only(env):
    c, _, d = env
    got = access(c, d, ["repository:public/gromacs:pull,push", "repository:restricted/af:pull",
                        "repository:cache/base-builder:pull", "repository:nvcr/nvidia/cuda:pull",
                        "repository::pull"])
    assert got == {"public/gromacs": ["pull"]}


def test_token_claims_and_key_file(env):
    c, db, d = env
    r = token(c, ["repository:public/gromacs:pull"])
    cl = claims(d, r.json()["token"])
    assert cl["aud"] == SERVICE and cl["sub"] == "anonymous"
    assert cl["exp"] - cl["iat"] == 60 * 60 == r.json()["expires_in"]
    assert r.headers["cache-control"] == "no-store"
    assert (d / "registry-signing.pem").stat().st_mode & 0o777 == 0o600
    # anonymous tokens are not audited
    assert not db.query("SELECT 1 FROM audit_log WHERE action LIKE 'registry.%'")


def test_job_gets_exactly_its_own_repositories(env):
    c, db, d = env
    job, _ = claimed_job(c)
    cred = job["registryToken"]
    assert cred["username"] == f"job-{job['id']}" and len(cred["password"]) >= 40
    assert db.query_one("SELECT registry_secret FROM build WHERE id = ?",
                        (job["id"],))["registry_secret"] != cred["password"]  # hashed
    got = access(c, d, [
        "repository:public/gromacs:pull,push", "repository:cache/test-gromacs:push,pull",
        "repository:cache/spack-rtx:pull,push", "repository:cache/base-builder:pull,push",
        "repository:nvcr/nvidia/cuda:pull,push", "repository:public/relion:pull,push",
        "repository:restricted/gromacs:push", "repository::pull"],
        basic(cred["username"], cred["password"]))
    assert got == {"public/gromacs": ["pull", "push"], "cache/test-gromacs": ["pull", "push"],
                   "cache/spack-rtx": ["pull", "push"], "cache/base-builder": ["pull"],
                   "nvcr/nvidia/cuda": ["pull"], "public/relion": ["pull"]}
    row = db.query_one("SELECT actor, action FROM audit_log WHERE action = 'registry.token'")
    assert row == {"actor": f"job-{job['id']}", "action": "registry.token"}


def test_post_form_flow(env):
    c, _, d = env
    job, _ = claimed_job(c)
    cred = job["registryToken"]
    r = c.post("/registry/token", data={
        "grant_type": "password", "service": SERVICE, "client_id": "docker",
        "username": cred["username"], "password": cred["password"],
        "scope": "repository:public/gromacs:pull,push"})
    assert r.status_code == 200, r.text
    assert claims(d, r.json()["access_token"])["access"] == [
        {"type": "repository", "name": "public/gromacs", "actions": ["pull", "push"]}]
    assert c.post("/registry/token", data={"grant_type": "refresh_token"}).status_code == 400


def test_job_credential_ends_with_the_job(env):
    c, db, _ = env
    job, bh = claimed_job(c)
    cred = basic(job["registryToken"]["username"], job["registryToken"]["password"])
    assert token(c, ["repository:public/gromacs:push"], cred).status_code == 200
    r = c.post(f"/api/builder/jobs/{job['id']}/report", headers=bh,
               json={"stage": "build", "ok": False, "error": "boom"})
    assert r.json()["status"] == "failed"
    assert token(c, ["repository:public/gromacs:push"], cred).status_code == 401
    assert db.query_one("SELECT registry_secret FROM build WHERE id = ?",
                        (job["id"],))["registry_secret"] is None
    assert db.query_one("SELECT 1 FROM audit_log WHERE action = 'registry.token.denied'")


@pytest.mark.parametrize("user,pw", [("job-1", "wrong"), ("job-999", "x"), ("job-x", "y")])
def test_bad_job_credentials(env, user, pw):
    c, _, _ = env
    claimed_job(c)
    # job-x is not a job name, so it is an AD login (DemoDirectory): also refused
    assert token(c, ["repository:public/gromacs:pull"], basic(user, pw)).status_code == 401


def test_ad_users_and_sysadmins(env):
    c, db, d = env
    scopes = ["repository:public/gromacs:pull,push", "repository:restricted/alphafold3:pull",
              "repository:cache/test-x:push", "repository:nvcr/nvidia/cuda:pull,push",
              "repository::pull"]
    assert access(c, d, scopes, basic("alice", "demo")) == {"public/gromacs": ["pull"]}
    assert access(c, d, scopes, basic("dana", "demo")) == {
        "public/gromacs": ["pull", "push"], "restricted/alphafold3": ["pull"],
        "cache/test-x": ["push"], "nvcr/nvidia/cuda": ["pull"], "": ["pull"]}
    assert db.query_one(
        "SELECT 1 FROM audit_log WHERE actor = 'dana' AND action = 'registry.token'")


def test_wrong_password_and_throttle(env):
    c, _, _ = env
    codes = [token(c, ["repository:public/gromacs:pull"], basic("alice", "nope")).status_code
             for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429
    # malformed Basic header
    assert token(c, [], {"Authorization": "Basic !!!"}).status_code == 401


def test_wrong_service_and_junk_scopes(env):
    c, _, d = env
    assert token(c, [], service="other").status_code == 400
    got = access(c, d, ["repository:Public/X:pull", "repository:public/../x:pull",
                        "registry:catalog:*", "repository:public/ok:pull"])
    assert got == {"public/ok": ["pull"]}


def test_key_survives_restart(env, tmp_path):
    c, _, d = env
    token(c, [])
    first = (d / "registry-verify.pem").read_bytes()
    from apparadise.registry_auth import load_key

    load_key(str(d))
    assert (d / "registry-verify.pem").read_bytes() == first
    assert oct(os.stat(d / "registry-signing.pem").st_mode & 0o777) == "0o600"
