"""Builder settings, from the environment and ./.env (never overriding the environment)."""

import ipaddress
import platform as _platform
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLATFORMS = {"x86_64": "linux/amd64", "aarch64": "linux/arm64"}


def host_platform() -> str:
    return PLATFORMS.get(_platform.machine(), "linux/amd64")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Health endpoint for appmotel (the builder itself only makes outbound calls)
    port: int = Field(8001, ge=1, le=65535)
    host: str = "127.0.0.1"
    health_check_path: str = "/health"
    log_level: str = "info"
    data_dir: str = "data"

    # Coordinator
    coordinator_url: str = Field(min_length=1)
    builder_name: str = Field(min_length=1)
    builder_platform: str = ""  # empty: this host's architecture
    builder_onprem: bool = True
    # One-time token from a sysadmin; only needed until the builder has registered.
    registration_token: SecretStr = SecretStr("")
    poll_interval: int = Field(30, ge=1)
    # Exit after this many consecutive empty claims (0: run forever). Slurm and EC2
    # builders set it, so they release their node when the queue is empty.
    idle_exit_polls: int = Field(0, ge=0)

    # Building
    registry_host: str = Field(min_length=1)
    container_tool: str = "podman"
    work_dir: str = "/scratch/apparadise"
    # How the test stage runs: `slurm` (srun on a GPU node of the target) or `none`
    # (fail closed: an untested image is never published).
    test_mode: Literal["slurm", "none"] = "slurm"
    test_partition: str = "gpu"
    sign_key: str = ""  # cosign key reference; empty: images are not signed here

    @field_validator("coordinator_url")
    @classmethod
    def _https(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        u = urlsplit(v)
        if u.scheme not in ("https", "http") or not u.hostname:
            raise ValueError("must be https://host[:port]")
        if u.scheme == "http":
            loop = u.hostname == "localhost"
            try:
                loop = loop or ipaddress.ip_address(u.hostname).is_loopback
            except ValueError:
                pass
            if not loop:
                raise ValueError("plain http is only allowed for a loopback coordinator")
        return v

    @field_validator("builder_name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,62}", v):
            raise ValueError("lowercase letters, digits, '.' and '-'")
        return v

    @field_validator("builder_platform")
    @classmethod
    def _platform(cls, v: str) -> str:
        v = v or host_platform()
        if v not in PLATFORMS.values():
            raise ValueError("linux/amd64 or linux/arm64")
        return v

    @field_validator("registry_host")
    @classmethod
    def _registry(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*(:[0-9]{1,5})?", v.strip()):
            raise ValueError("a registry host such as registry.example.org, without https://")
        return v.strip()

    @property
    def token_file(self) -> Path:
        return Path(self.data_dir) / "builder.token"


def load_settings() -> Settings:
    load_dotenv(Path.cwd() / ".env", override=False)
    try:
        return Settings()
    except ValidationError as e:
        lines = []
        for err in e.errors():
            name = str(err["loc"][0]).upper() if err["loc"] else "?"
            missing = err["type"] in ("missing", "string_too_short")
            lines.append(f"  {name}: {'REQUIRED, not set' if missing else err['msg']}")
        msg = "Invalid configuration (see .env.default):\n" + "\n".join(sorted(lines))
        raise SystemExit(msg) from None
