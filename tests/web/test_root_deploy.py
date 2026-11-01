"""A plain `appmo add apparadise <repo>` (no subfolder) must deploy the web app, and only it.

These checks mirror appmotel's own detection (bin/appmo: is_zensical_site,
detect_components, setup_python_env, setup_app_env_file).
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from .conftest import REQUIRED

ROOT = Path(__file__).resolve().parents[2]
APPMOTEL_COMPONENTS = ("frontend", "backend", "api", "worker", "db", "cache")


def test_root_is_a_python_app_not_the_docs_site():
    # appmotel serves zensical.toml as a static docs site unless the root has app.py.
    assert (ROOT / "zensical.toml").exists()
    assert (ROOT / "app.py").is_file()
    for name in APPMOTEL_COMPONENTS:  # would switch appmotel into multi-component mode
        assert not (ROOT / name / "requirements.txt").exists(), name
        assert not (ROOT / "src" / name).exists(), name


def test_root_requirements_install_the_web_app_only():
    lines = [ln.strip() for ln in (ROOT / "requirements.txt").read_text().splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    assert lines == ["-r web/requirements.txt"]


def test_root_env_template_is_the_web_one():
    # appmotel copies the root .env.default into the app's .env on first deploy.
    assert (ROOT / ".env.default").resolve() == (ROOT / "web" / ".env.default").resolve()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_root_app_py_starts_the_web_app(tmp_path):
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LDAP_", "REGISTRY_"))}
    env.update({k.upper(): v for k, v in REQUIRED.items()})
    env.update({"PORT": str(port), "DATA_DIR": str(tmp_path), "ANTHROPIC_API_KEY": "",
                "LOG_LEVEL": "warning"})
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except httpx.TransportError:
                assert proc.poll() is None, proc.stdout.read().decode()
                assert time.monotonic() < deadline, "web app did not come up"
                time.sleep(0.2)
        assert r.json() == {"status": "ok"}
        # Relative settings resolve from web/, as in a subfolder deploy: the catalog loads.
        names = {p["name"] for p in httpx.get(f"http://127.0.0.1:{port}/api/catalog").json()
                 ["packages"]}
        assert names
        assert (tmp_path / "apparadise.sqlite3").exists()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
