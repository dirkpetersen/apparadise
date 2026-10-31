import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "web", ROOT):  # the app package and mock/, wherever pytest is started
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

REQUIRED = {
    "session_secret": "test-secret",
    "ldap_url": "ldaps://dc.example.org:636",
    "ldap_base_dn": "DC=example,DC=org",
    "ldap_bind_dn": "svc@example.org",
    "ldap_bind_password": "test-password",
    "ldap_required_group_dn": "CN=S3-Users,OU=Groups,DC=example,DC=org",
    "ldap_admin_group_dn": "CN=S3-Admins,OU=Groups,DC=example,DC=org",
    "registry_host": "registry.example.org",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Keep the developer's real environment and .env out of the tests."""
    from apparadise.config import Settings

    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture
def settings(tmp_path):
    from apparadise.config import Settings

    return Settings(_env_file=None, data_dir=str(tmp_path / "data"),
                    catalog_dir=str(tmp_path / "no-catalog"), **REQUIRED)


@pytest.fixture(scope="session", autouse=True)
def repo_db_untouched():
    """Fail the session if any test creates or changes the repo's real database."""
    from pathlib import Path

    db = Path(__file__).resolve().parents[2] / "web" / "data" / "apparadise.sqlite3"

    def stamp():
        return (db.stat().st_mtime_ns, db.stat().st_size) if db.exists() else None

    before = stamp()
    yield
    assert stamp() == before, f"tests created or modified {db}"
