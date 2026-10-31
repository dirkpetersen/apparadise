"""The demo hook stays dev-only: create_app never enables it, and the app never imports mock/."""

import ast
from pathlib import Path

from fastapi.testclient import TestClient

from apparadise.config import Settings
from apparadise.main import create_app

from .conftest import REQUIRED

PKG = Path(__file__).resolve().parents[2] / "web" / "apparadise"


def test_create_app_never_enables_demo(tmp_path):
    s = Settings(_env_file=None, data_dir=str(tmp_path), catalog_dir=str(tmp_path), **REQUIRED)
    with TestClient(create_app(s)) as c:
        assert c.get("/api/demo").json() == {"demo": False}


def test_app_never_imports_mock():
    for f in PKG.rglob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(a.name.split(".")[0] == "mock" for a in node.names), f
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "mock", f


def test_no_setting_mentions_demo():
    assert not [n for n in Settings.model_fields if "demo" in n]


def test_demo_builds_and_flags(tmp_path, monkeypatch):
    from mock.demo import build_demo_app, demo_settings

    monkeypatch.setenv("LDAP_URL", "ldaps://real.example.org")  # must be ignored
    s = demo_settings(port=8000, data_dir=str(tmp_path / "d"))
    assert s.ldap_url == ["ldaps://directory.demo.invalid:636"]
    with TestClient(build_demo_app(s)) as c:
        assert c.get("/api/demo").json() == {"demo": True}
        assert c.get("/api/catalog").json()["packages"]
