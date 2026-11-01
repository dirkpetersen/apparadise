"""The Registry page API: read-only browsing of Zot, with restricted, cache and the NGC mirror
hidden from everyone but sysadmins."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from apparadise.config import Settings
from apparadise.db import Database
from apparadise.main import create_app
from apparadise.zot import ZotClient
from mock.directory import DemoDirectory
from mock.zot_mock import create_app as create_zot
from mock.zot_mock import image, seed_from_catalog

from .conftest import REQUIRED

H = {"X-Requested-With": "apparadise"}
CATALOG = Path(__file__).resolve().parents[2] / "mock" / "catalog"
ZOT = {"registry_url": "https://zot.example.org", "registry_username": "apparadise-web",
       "registry_password": "zot"}


class Clock:
    t = 1_900_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    s = Settings(_env_file=None, data_dir=str(tmp_path), catalog_dir=str(CATALOG),
                 session_cookie_secure=False, **REQUIRED, **ZOT)
    mock = create_zot()
    seed_from_catalog(mock, CATALOG)
    clock = Clock()
    db = Database(":memory:")
    app = create_app(s, db=db, authenticator=DemoDirectory(), clock=clock, background=False,
                     zot=ZotClient(s, transport=httpx.ASGITransport(app=mock)))
    with TestClient(app) as c:
        yield c, mock, clock
    db.close()


def login(c, user="dana"):
    r = c.post("/api/login", json={"login": user, "password": "demo"}, headers=H)
    assert r.status_code == 200


def one(tag="1", day="2026-01-01"):
    return [image("sha256:" + "b" * 64, tag, arches=["amd64"], pushed=f"{day}T00:00:00Z",
                  size=1)]


def test_public_overview_hides_restricted_and_cache(env):
    c, _, _ = env
    r = c.get("/api/registry").json()
    assert r["configured"] and r["host"] == "registry.example.org"
    assert [p["name"] for p in r["projects"]] == ["public"]
    names = [x["name"] for x in r["projects"][0]["repositories"]]
    assert names == ["gromacs", "pytorch", "relion"] and "alphafold3" not in str(r)
    gromacs = r["projects"][0]["repositories"][0]
    assert gromacs["newest"] == "2026.3-rtx" and gromacs["package"] == "gromacs"


@pytest.mark.parametrize("user", [None, "alice"])
def test_restricted_cache_and_mirror_are_404_for_non_sysadmins(env, user):
    c, _, _ = env
    if user:
        login(c, user)
    for path in ("restricted/alphafold3", "nvcr/nvidia/cuda", "cache/base-builder", "nope/x"):
        r = c.get(f"/api/registry/{path}")
        assert r.status_code == 404 and r.json()["detail"] == "No such repository."


def test_sysadmin_sees_all_projects_and_nested_mirror_repos(env):
    c, _, _ = env
    login(c)
    r = c.get("/api/registry").json()
    assert [p["name"] for p in r["projects"]] == ["public", "restricted", "cache", "nvcr"]
    mirror = r["projects"][3]["repositories"]
    assert mirror[0]["name"] == "nvidia/cuda" and mirror[0]["package"] is None
    a = c.get("/api/registry/nvcr/nvidia/cuda").json()["artifacts"][0]
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


def test_tags_of_one_digest_are_one_artifact(env):
    c, mock, _ = env
    d = "sha256:" + "a" * 64
    mock.state.repos["public/twice"] = [
        image(d, t, arches=["amd64"], pushed="2026-01-01T00:00:00Z", size=1)
        for t in ("latest", "1.0")]
    arts = c.get("/api/registry/public/twice").json()["artifacts"]
    assert len(arts) == 1 and arts[0]["tags"] == ["1.0", "latest"]
    assert arts[0]["commands"]["enroot"].endswith("#public/twice:1.0")


def test_answers_are_cached_briefly(env):
    c, mock, clock = env
    assert len(c.get("/api/registry").json()["projects"][0]["repositories"]) == 3
    mock.state.repos["public/new"] = one()
    assert len(c.get("/api/registry").json()["projects"][0]["repositories"]) == 3
    clock.t += 31
    assert len(c.get("/api/registry").json()["projects"][0]["repositories"]) == 4


def test_pagination_reads_every_page(env):
    c, mock, _ = env
    for i in range(130):
        mock.state.repos[f"public/pkg{i:03d}"] = one()
    assert len(c.get("/api/registry").json()["projects"][0]["repositories"]) == 133


def test_repo_name_is_a_safe_graphql_string(env):
    c, mock, _ = env
    login(c)
    c.get("/api/registry/public/gromacs")
    assert mock.state.queries[-1].startswith('{ImageList(repo:"public/gromacs" ')


def test_outage_is_a_clean_502_without_hostnames(env):
    c, mock, _ = env
    mock.state.fail = 500
    r = c.get("/api/registry")
    assert r.status_code == 502 and "zot.example.org" not in r.text


def test_unreachable_registry_is_503(tmp_path):
    def boom(request):
        raise httpx.ConnectError("no route to zot.example.org")

    s = Settings(_env_file=None, data_dir=str(tmp_path), catalog_dir=str(tmp_path),
                 **REQUIRED, **ZOT)
    db = Database(":memory:")
    app = create_app(s, db=db, authenticator=DemoDirectory(), background=False,
                     zot=ZotClient(s, transport=httpx.MockTransport(boom)))
    with TestClient(app) as c:
        r = c.get("/api/registry/public/gromacs")
        assert r.status_code == 503 and "zot.example.org" not in r.text
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


def test_registry_settings_validated():
    with pytest.raises(ValueError):
        Settings(_env_file=None, **REQUIRED, registry_url="http://zot.example.org")
    with pytest.raises(ValueError):
        Settings(_env_file=None, **REQUIRED, registry_url="https://zot.example.org")
    s = Settings(_env_file=None, **REQUIRED, registry_url="http://127.0.0.1:5000/",
                 registry_username="u", registry_password="p")
    assert s.registry_url == "http://127.0.0.1:5000"
