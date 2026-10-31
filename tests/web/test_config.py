from pathlib import Path

import pytest
from dotenv import dotenv_values
from pydantic import ValidationError

from apparadise.config import Settings, load_settings

from .conftest import REQUIRED

# conftest.REQUIRED also sets the optional sign-in group, so tests exercise group checks.
REQUIRED_NAMES = set(REQUIRED) - {"ldap_required_group_dn"}
ENV_DEFAULT = Path(__file__).resolve().parents[2] / "web" / ".env.default"


def test_env_default_keys_match_fields():
    keys = {k.lower() for k in dotenv_values(ENV_DEFAULT)}
    assert keys == set(Settings.model_fields)


def test_env_default_values_match_code_defaults(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name.upper(), value)
    from_file = Settings(_env_file=ENV_DEFAULT)
    from_code = Settings(_env_file=None)
    assert from_file.model_dump() == from_code.model_dump()


def test_required_are_exactly_the_empty_keys():
    required = {n for n, f in Settings.model_fields.items() if f.is_required()}
    assert required == REQUIRED_NAMES
    values = dotenv_values(ENV_DEFAULT)
    for name in required:
        assert values[name.upper()] == "", name
    text = ENV_DEFAULT.read_text()
    for name in required:  # the comment block right above each REQUIRED key says so
        block = text[: text.index(f"\n{name.upper()}=")].rsplit("\n\n", 1)[-1]
        last_comment = [line for line in block.splitlines() if line.startswith("#")][-1]
        assert "REQUIRED" in last_comment, name


def test_no_trailing_comments_in_env_default():
    for line in ENV_DEFAULT.read_text().splitlines():
        if line and not line.startswith("#"):
            assert " #" not in line, line


def test_missing_required_lists_all(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        load_settings()
    msg = str(e.value)
    for name in REQUIRED_NAMES:
        assert f"{name.upper()}: REQUIRED, not set" in msg


def test_ldap_plain_url_refused():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**REQUIRED, "ldap_url": "ldap://dc.example.org"})


@pytest.mark.parametrize("host", ["https://registry.example.org", "reg istry", "a/b", ""])
def test_registry_host_validated(host):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**REQUIRED, "registry_host": host})


@pytest.mark.parametrize("path", ["relative/dir", "/with space", "/x;rm", "/a$(b)"])
def test_delivery_paths_validated(path):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**REQUIRED, "sqsh_dir": path})
