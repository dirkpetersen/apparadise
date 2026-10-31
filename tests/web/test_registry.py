"""The Registry page API: read-only Harbor browsing, with restricted and cache hidden from
everyone but sysadmins."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from apparadise.config import Settings
from apparadise.db import Database
from apparadise.harbor import HarborClient
from apparadise.main import create_app
from mock.directory import DemoDirectory
from mock.harbor_mock import artifact, seed_from_catalog
from mock.harbor_mock import create_app as create_harbor

from .conftest import REQUIRED

H = {"X-Requested-With": "apparadise"}
CATALOG = Path(__file__).resolve().parents[2] / "mock" / "catalog"
HARBOR = {"harbor_url": "https://harbor.example.org", "harbor_username": "admin",
          "harbor_password": "harbor"}


class Clock:
    t = 1_900_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    s = Settings(_env_file=None, data_dir=str(tmp_path), catalog_dir=str(CATALOG),
                 session_cookie_secure=False, **REQUIRED, **HARBOR)
    mock = create_harbor()
    seed_from_catalog(mock, CATALOG)
    clock = Clock()
    db = Database(":memory:")
    app = create_app(s, db=db, authenticator=DemoDirectory(), clock=clock, background=False,
                     harbor=HarborClient(s, transport=httpx.ASGITransport(app=mock)))
    with TestClient(app) as c:
        yield c, mock, clock
    db.close()


def login(c, user="dana"):
    r = c.post("/api/login", json={"login": user, "password": "demo"}, headers=H)
    assert r.status_code == 200


def test_public_overview_hides_restricted_and_cache(env):
    c, _, _ = env
    r = c.get("/api/registry").json()
    assert r["configured"] and r["host"] == "registry.example.org"
    assert [p["name"] for p in r["projects"]] == ["public"]
    names = [x["name"] for x in r["projects"][0]["repositories"]]
    assert names == ["gromacs", "pytorch", "relion"] and "alphafold3" not in str(r)
    gromacs = r["projects"][0]["repositories"][0]
    assert gromacs["artifacts"] == 3 and gromacs["package"] == "gromacs"


@pytest.mark.parametrize("user", [None, "alice"])
def test_restricted_and_cache_are_404_for_non_sysadmins(env, user):
    c, _, _ = env
    if user:
        login(c, user)
    for path in ("restricted/alphafold3", "cache/nvidia/cuda", "nope/x"):
        r = c.get(f"/api/registry/{path}")
        assert r.status_code == 404 and r.json()["detail"] == "No such repository."


def test_sysadmin_sees_all_projects_and_nested_cache_repos(env):
    c, _, _ = env
    login(c)
    r = c.get("/api/registry").json()
    assert [p["name"] for p in r["projects"]] == ["public", "restricted", "cache"]
    cache = r["projects"][2]["repositories"]
    assert cache[0]["name"] == "nvidia/cuda" and cache[0]["package"] is None
    a = c.get("/api/registry/cache/nvidia/cuda").json()["artifacts"][0]
    assert a["index"] and a["platforms"] == ["linux/amd64", "linux/arm64"] and not a["signed"]
    af = c.get("/api/registry/restricted/alphafold3").json()
    assert af["package"] == "alphafold3"


def test_artifacts_newest_first_with_commands(env):
    c, _, _ = env
    r = c.get("/api/registry/public/gromacs").json()
    tags = [a["tags"] for a in r["artifacts"]]
    assert tags[0] == ["2026.3-rtx"]  # pushed 2026-10-30, the newest
    a = r["artifacts"][0]
    assert a["signed"] and a["platforms"] == ["linux/amd64"]
    assert a["commands"]["srun"].startswith(
        "srun --container-image=registry.example.org#public/gromacs:2026.3-rtx")
    assert a["commands"]["podman"] == "podman pull registry.example.org/public/gromacs:2026.3-rtx"


def test_untagged_artifact_uses_digest(env):
    c, mock, _ = env
    d = "sha256:" + "a" * 64
    mock.state.repos["public"]["loose"] = [artifact(d, [], arch="amd64", pushed="2026-01-01",
                                                    size=1)]
    a = c.get("/api/registry/public/loose").json()["artifacts"][0]
    assert a["commands"]["enroot"].endswith(f"#public/loose@{d}")


def test_answers_are_cached_briefly(env):
    c, mock, clock = env
    assert len(c.get("/api/registry").json()["projects"][0]["repositories"]) == 3
    mock.state.repos["public"]["new"] = []
    assert len(c.get("/api/registry").json()["projects"][0]["repositories"]) == 3
    clock.t += 31
    assert len(c.get("/api/registry").json()["projects"][0]["repositories"]) == 4


def test_pagination_reads_every_page(env):
    c, mock, _ = env
    for i in range(130):
        mock.state.repos["public"][f"pkg{i:03d}"] = []
    assert len(c.get("/api/registry").json()["projects"][0]["repositories"]) == 133


def test_outage_is_a_clean_503_without_hostnames(env):
    c, mock, _ = env
    mock.state.fail = 500
    r = c.get("/api/registry")
    assert r.status_code == 502 and "harbor.example.org" not in r.text


def test_unreachable_harbor_is_503(tmp_path):
    def boom(request):
        raise httpx.ConnectError("no route to harbor.example.org")

    s = Settings(_env_file=None, data_dir=str(tmp_path), catalog_dir=str(tmp_path),
                 **REQUIRED, **HARBOR)
    db = Database(":memory:")
    app = create_app(s, db=db, authenticator=DemoDirectory(), background=False,
                     harbor=HarborClient(s, transport=httpx.MockTransport(boom)))
    with TestClient(app) as c:
        r = c.get("/api/registry/public/gromacs")
        assert r.status_code == 503 and "harbor.example.org" not in r.text
    db.close()


def test_not_configured(settings):
    db = Database(":memory:")
    with TestClient(create_app(settings, db=db, authenticator=DemoDirectory(),
                               background=False)) as c:
        assert c.get("/api/registry").json() == {
            "configured": False, "host": "registry.example.org", "projects": []}
        assert c.get("/api/registry/public/gromacs").status_code == 503
    db.close()


@pytest.mark.parametrize("repo", ["../restricted/alphafold3", "Gromacs", "a/../b"])
def test_bad_repository_names_are_404(env, repo):
    c, _, _ = env
    assert c.get(f"/api/registry/public/{repo}").status_code == 404
