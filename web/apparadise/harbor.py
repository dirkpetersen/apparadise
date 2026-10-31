"""Harbor client: one short-lived robot account per build job.

The coordinator never hands a builder site credentials. On claim it creates a system-level
robot with push/pull on exactly two projects (the image's own `public` or `restricted`, and
`cache` for test tags and build caches), valid for HARBOR_ROBOT_DAYS; when the job finishes
the robot is deleted. Harbor scopes robots to projects, not single repositories, so the
short lifetime and the deletion are what keep the blast radius small.
"""

from __future__ import annotations

import logging
import re
import secrets
from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings

log = logging.getLogger(__name__)
PROJECT_RE = re.compile(r"^(public|restricted|cache)$")


class HarborError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class HarborUnavailable(Exception):
    """Harbor could not be reached (network or timeout)."""


def _path(*parts: str | int) -> str:
    return "/".join(quote(str(p), safe="") for p in parts)


class HarborClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.days = settings.harbor_robot_days
        self.client = httpx.AsyncClient(
            base_url=settings.harbor_url.rstrip("/") + "/api/v2.0",
            auth=(settings.harbor_username, settings.harbor_password.get_secret_value()),
            timeout=15, transport=transport, headers={"Accept": "application/json"})

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            r = await self.client.request(method, path, **kw)
        except httpx.HTTPError as e:
            # never include the URL: it names the internal host
            raise HarborUnavailable(type(e).__name__) from None
        if r.status_code >= 400:
            raise HarborError(r.status_code, f"Harbor answered {r.status_code}")
        return r

    async def create_robot(self, job_id: int, project: str) -> dict[str, Any]:
        """-> {id, name, secret}. `project` is public or restricted."""
        if not PROJECT_RE.match(project) or project == "cache":
            raise ValueError(f"bad project {project!r}")
        access = [{"resource": "repository", "action": a} for a in ("push", "pull")]
        body = {
            # unique per attempt: a robot left behind by a failed call never blocks a retry
            "name": f"apparadise-job-{int(job_id)}-{secrets.token_hex(4)}",
            "description": f"apparadise build job {int(job_id)}",
            "duration": self.days,
            "level": "system",
            "disable": False,
            "permissions": [{"kind": "project", "namespace": ns, "access": access}
                            for ns in (project, "cache")],
        }
        r = await self._call("POST", "/robots", json=body)
        data = r.json()
        return {"id": data["id"], "name": data["name"], "secret": data["secret"]}

    async def delete_robot(self, robot_id: int) -> None:
        try:
            await self._call("DELETE", "/" + _path("robots", int(robot_id)))
        except HarborError as e:
            if e.status != 404:
                raise


def get_harbor(request) -> HarborClient | None:
    return request.app.state.harbor
