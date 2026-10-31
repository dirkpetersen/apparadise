"""Catalog files, DB sync and the public read API (restricted filtering, commands)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apparadise.catalog import CatalogError, load_dir, parse_entry, sync, version_key
from apparadise.config import Settings
from apparadise.db import Database
from apparadise.main import create_app
from mock.directory import DemoDirectory

from .conftest import REQUIRED

ROOT = Path(__file__).resolve().parents[2]
H = {"X-Requested-With": "apparadise"}
D = "sha256:" + "a" * 64


def entry(**kw):
    base = {"name": "gromacs", "title": "GROMACS", "source": "spack",
            "versions": {"2026.3": {"rtx": {"status": "published", "digest": D}}}}
    base.update(kw)
    return base


def test_repo_catalogs_are_valid():
    assert len(load_dir(ROOT / "catalog")) >= 20
    names = {e["name"] for e in load_dir(ROOT / "mock" / "catalog")}
    assert {"gromacs", "pytorch", "alphafold3"} <= names


@pytest.mark.parametrize("bad", [
    {"name": "Gromacs"},
    {"name": "../x"},
    {"source": "conan"},
    {"visibility": "secret"},
    {"homepage": "http://insecure.example"},
    {"homepage": "javascript:alert(1)"},
    {"executables": ["gmx; rm -rf /"]},
    {"tags": ["Has Space"]},
    {"extra": 1},
    {"versions": {"2026.3": {"mars": {}}}},
    {"versions": {"2026.3": {"rtx": {"status": "published"}}}},  # no digest
    {"versions": {"2026.3": {"rtx": {"status": "published", "digest": "sha256:xyz"}}}},
    {"versions": {"2026.3": {"rtx": {"status": "failed"}}}},  # no stage
    {"versions": {"bad version": {"rtx": {}}}},
])
def test_invalid_entries_rejected(bad):
    with pytest.raises(CatalogError):
        parse_entry(entry(**bad), "x.yml")


def test_planned_package_without_versions():
    e = parse_entry(entry(versions={}))
    assert e["builds"] == []


def test_file_name_must_match(tmp_path):
    (tmp_path / "other.yml").write_text("name: gromacs\nsource: spack\nversions: {}\n")
    with pytest.raises(CatalogError, match="file name"):
        load_dir(tmp_path)


def test_version_key_is_natural():
    got = sorted(["2026.3", "2026.10", "2025.4"], key=version_key)
    assert got == ["2025.4", "2026.3", "2026.10"]


def test_sync_replaces_and_removes():
    db = Database(":memory:")
    sync(db, [parse_entry(entry()), parse_entry(entry(name="lammps", title="LAMMPS"))])
    assert db.packages.names("catalog") == ["gromacs", "lammps"]
    sync(db, [parse_entry(entry(versions={"2027.1": {"vr": {"status": "planned"}}}))])
    assert db.packages.names("catalog") == ["gromacs"]
    builds = db.builds.for_package(db.packages.get("gromacs")["id"])
    got = [(b["version"], b["target"], b["status"]) for b in builds]
    assert got == [("2027.1", "vr", "planned")]


@pytest.fixture
def client(tmp_path):
    s = Settings(_env_file=None, data_dir=str(tmp_path / "data"),
                 catalog_dir=str(ROOT / "mock" / "catalog"),
                 session_cookie_secure=False, **REQUIRED)
    db = Database(":memory:")
    app = create_app(s, db=db, authenticator=DemoDirectory())
    with TestClient(app) as c:
        yield c
    db.close()


def login(c, user):
    r = c.post("/api/login", json={"login": user, "password": "demo"}, headers=H)
    assert r.status_code == 200, r.text


def names(c, q=""):
    r = c.get("/api/catalog", params={"q": q} if q else None)
    return {p["name"] for p in r.json()["packages"]}


def test_catalog_is_public_and_hides_restricted(client):
    assert "gromacs" in names(client)
    assert "alphafold3" not in names(client)
    assert client.get("/api/catalog/alphafold3").status_code == 404
    login(client, "alice")
    assert "alphafold3" not in names(client)
    assert client.get("/api/catalog/alphafold3").status_code == 404


def test_sysadmin_sees_restricted(client):
    login(client, "dana")
    assert "alphafold3" in names(client)
    r = client.get("/api/catalog/alphafold3").json()
    cmds = r["versions"][0]["targets"]["rtx"]["commands"]
    assert "#restricted/alphafold3:3.0.1-rtx" in cmds["srunRegistry"]


def test_restricted_never_matches_search_for_public(client):
    assert names(client, "alphafold") == set()
    assert names(client, "structural") == set()


def test_search(client):
    assert names(client, "cryo") == {"relion"}
    assert names(client, "py-torch") == {"pytorch"}  # aliases are searched
    assert names(client, "%") == set()  # LIKE wildcards are escaped


def test_package_detail_and_commands(client):
    r = client.get("/api/catalog/gromacs").json()
    assert [v["version"] for v in r["versions"]] == ["2026.3", "2025.4"]
    rtx = r["versions"][0]["targets"]["rtx"]
    assert rtx["status"] == "published"
    reg = "registry.example.org"
    assert rtx["commands"] == {
        "module": "module load gromacs/2026.3",
        "srunRegistry": f"srun --container-image={reg}#public/gromacs:2026.3-rtx gmx_mpi",
        "srunSqsh": "srun --container-image=/shared/containers/rtx/gromacs-2026.3-rtx.sqsh gmx_mpi",
        "pull": "registry.example.org/public/gromacs:2026.3-rtx",
        "sqsh": "/shared/containers/rtx/gromacs-2026.3-rtx.sqsh",
    }
    vr = r["versions"][0]["targets"]["vr"]
    assert vr["status"] == "running" and "commands" not in vr
    assert r["targets"] == {"rtx": "published", "vr": "running"}


def test_failed_build_shows_stage(client):
    r = client.get("/api/catalog/relion").json()
    assert r["versions"][0]["targets"]["vr"] == {
        "status": "failed", "stage": "build", "digest": "", "test": "", "built": None,
        "failureId": None}


def test_list_summary(client):
    pk = {p["name"]: p for p in client.get("/api/catalog").json()["packages"]}
    assert pk["vllm"]["status"] == "queued" and pk["vllm"]["published"] is False
    assert pk["gromacs"]["latest"] == "2026.3" and pk["gromacs"]["source"] == "spack"


def test_unknown_package_404(client):
    assert client.get("/api/catalog/nope").status_code == 404


def test_removing_catalog_file_keeps_pipeline_history():
    from apparadise import coordinator

    db = Database(":memory:")
    sync(db, [parse_entry(entry())])
    pid = db.packages.get("gromacs")["id"]
    coordinator.enqueue(db, pid, "2026.4", "spack", "rtx", "linux/amd64", "r",
                        requested_by="dana", restricted=False)
    sync(db, [])
    pkg = db.packages.get("gromacs")
    assert pkg is not None and pkg["origin"] == "request"
    builds = db.builds.for_package(pkg["id"])
    assert [(b["version"], b["origin"]) for b in builds] == [("2026.4", "pipeline")]
