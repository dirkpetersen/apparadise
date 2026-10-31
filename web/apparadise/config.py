"""Runtime configuration, read from the environment (and `./.env`).

Every setting in `.env.default` has a field here. A field without a default is
REQUIRED: the app refuses to start while it is unset or empty. Field defaults
must equal the values in `.env.default` (tests/test_config.py enforces this).
"""

import re
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LOG_LEVELS = ("debug", "info", "warning", "error")
# A registry host as it appears in an image reference: host[:port], no scheme.
REGISTRY_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?\Z")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_ignore_empty=False)

    # Server
    port: int = Field(8000, ge=1, le=65535)
    host: str = "127.0.0.1"
    app_name: str = "apparadise"
    health_check_path: str = "/health"
    log_level: str = "info"
    data_dir: str = "data"

    # Sessions
    session_secret: SecretStr = Field(min_length=1)
    session_max_age: int = Field(28800, gt=0)
    session_cookie_secure: bool = True

    # Active Directory login
    ldap_url: Annotated[list[str], NoDecode] = Field(min_length=1)
    ldap_base_dn: str = Field(min_length=1)
    ldap_bind_dn: str = Field(min_length=1)
    ldap_bind_password: SecretStr = Field(min_length=1)
    ldap_user_filter: str = (
        "(&(objectCategory=person)(objectClass=user)"
        "(!(userAccountControl:1.2.840.113556.1.4.803:=2))"
        "(|(sAMAccountName={login})(mail={login})(userPrincipalName={login})))"
    )
    ldap_username_attribute: str = "sAMAccountName"
    ldap_display_name_attribute: str = "displayName"
    ldap_email_attribute: str = "mail"
    # Empty: every enabled account in the directory may sign in (QUESTIONS.md Q2).
    ldap_required_group_dn: str = ""
    # Members of this group (RCO-Admins) are sysadmins: no build quota, see restricted software.
    ldap_admin_group_dn: str = Field(min_length=1)
    ldap_ca_cert_file: str = ""
    ldap_ca_sha256: str = ""
    ldap_tls_verify: bool = True
    ldap_timeout: int = Field(5, gt=0)

    # Catalog and delivery (phase 1: hand-maintained catalog files)
    catalog_dir: str = "../catalog"
    registry_host: str = Field(min_length=1)
    sqsh_dir: str = "/shared/containers"
    module_dir: str = "/shared/modules/apparadise"
    wrapper_dir: str = "/shared/apps/apparadise/bin"

    @field_validator("ldap_url", mode="before")
    @classmethod
    def _split_urls(cls, v):
        if isinstance(v, str):
            return [u.strip() for u in v.split(",") if u.strip()]
        return v

    @field_validator("ldap_url")
    @classmethod
    def _ldaps_only(cls, v: list[str]) -> list[str]:
        # Passwords travel over this connection: plain ldap:// is never allowed.
        for u in v:
            if not u.lower().startswith("ldaps://") or len(u) <= len("ldaps://"):
                raise ValueError("every URL must be ldaps://host[:port] (plain ldap:// is refused)")
        return v

    @field_validator("ldap_user_filter")
    @classmethod
    def _filter_has_login(cls, v: str) -> str:
        if "{login}" not in v:
            raise ValueError("must contain the {login} placeholder")
        return v

    @field_validator("ldap_ca_sha256")
    @classmethod
    def _check_pin(cls, v: str) -> str:
        v = v.replace(":", "").replace(" ", "").strip()
        if v and not re.fullmatch(r"[0-9A-Fa-f]{64}", v):
            raise ValueError("must be a SHA-256 fingerprint: 64 hex digits (colons allowed)")
        return v.upper()

    @field_validator("log_level")
    @classmethod
    def _check_level(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in LOG_LEVELS:
            raise ValueError(f"must be one of {', '.join(LOG_LEVELS)}")
        return v

    @field_validator("health_check_path")
    @classmethod
    def _check_path(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("must start with /")
        return v

    @field_validator("registry_host")
    @classmethod
    def _check_registry(cls, v: str) -> str:
        v = v.strip()
        if not REGISTRY_RE.match(v):
            raise ValueError("must be a registry host such as registry.example.org[:port], "
                             "without https://")
        return v

    @field_validator("sqsh_dir", "module_dir", "wrapper_dir")
    @classmethod
    def _abs_path(cls, v: str) -> str:
        if not v.startswith("/") or any(c in v for c in " \"'$`\\;\n"):
            raise ValueError("must be an absolute path without spaces, quotes or shell characters")
        return v.rstrip("/")


def _ca_file_problems(s: Settings) -> list[str]:
    if not s.ldap_ca_cert_file:
        return []
    try:
        ok = "BEGIN CERTIFICATE" in Path(s.ldap_ca_cert_file).read_text(errors="replace")
    except OSError:
        ok = False
    if ok:
        return []
    return [f"  LDAP_CA_CERT_FILE: {s.ldap_ca_cert_file} is missing, unreadable or not a PEM "
            "certificate file (the path must exist on THIS server)"]


def load_settings() -> Settings:
    """Load ./.env (cwd only, never searched upward; never overriding the environment).

    Exits with one message listing every missing or invalid setting.
    """
    load_dotenv(Path.cwd() / ".env", override=False)
    try:
        settings = Settings()
    except ValidationError as e:
        lines = []
        for err in e.errors():
            if not err["loc"]:
                lines.append("  " + err["msg"].removeprefix("Value error, "))
                continue
            name = str(err["loc"][0]).upper()
            missing = err["type"] in ("missing", "string_too_short", "too_short")
            lines.append(f"  {name}: {'REQUIRED, not set' if missing else err['msg']}")
        raise SystemExit(
            "Invalid configuration (see .env.default):\n" + "\n".join(sorted(lines))
        ) from None
    from .ldapca import ensure_ca_file

    ensure_ca_file(settings)
    if problems := _ca_file_problems(settings):
        raise SystemExit("Invalid configuration (see .env.default):\n" + "\n".join(problems))
    return settings
