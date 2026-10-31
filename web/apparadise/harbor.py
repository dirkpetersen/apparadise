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
# Our repositories are package names; `cache` (the NGC proxy) also has nvidia/cuda-style paths.
REPO_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,254}\Z")


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

    # --- read-only registry browsing (the Registry page) ---

    async def _pages(self, path: str, params: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while len(out) < limit:
            r = await self._call("GET", path, params={**params, "page": page, "page_size": 100})
            batch = r.json() or []
            out += batch
            if len(batch) < 100:
                break
            page += 1
        return out[:limit]

    async def list_repositories(self, project: str) -> list[dict[str, Any]]:
        """-> [{name, artifacts, pulls, updated}], `name` without the project prefix."""
        if not PROJECT_RE.match(project):
            raise ValueError(f"bad project {project!r}")
        rows = await self._pages("/" + _path("projects", project, "repositories"), {}, 1000)
        prefix = f"{project}/"
        return [{"name": r["name"].removeprefix(prefix),
                 "artifacts": r.get("artifact_count", 0), "pulls": r.get("pull_count", 0),
                 "updated": r.get("update_time")} for r in rows]

    async def list_artifacts(self, project: str, repo: str) -> list[dict[str, Any]]:
        """-> [{digest, tags, size, pushed, platforms, index, signed}], newest first."""
        if not PROJECT_RE.match(project) or not REPO_RE.match(repo) or ".." in repo:
            raise ValueError("bad repository")
        # Harbor wants a repository name with slashes encoded twice (a/b -> a%252Fb).
        path = "/" + _path("projects", project, "repositories", quote(repo, safe=""),
                           "artifacts")
        rows = await self._pages(path, {"with_tag": "true", "with_accessory": "true"}, 500)
        out = []
        for a in rows:
            refs = a.get("references") or []
            if refs:
                plats = [f"{(r.get('platform') or {}).get('os', 'linux')}/"
                         f"{(r.get('platform') or {}).get('architecture', '?')}" for r in refs]
            else:
                ex = a.get("extra_attrs") or {}
                plats = [f"{ex.get('os', 'linux')}/{ex['architecture']}"] \
                    if ex.get("architecture") else []
            out.append({
                "digest": a["digest"],
                "tags": sorted(t["name"] for t in (a.get("tags") or [])),
                "size": a.get("size", 0),
                "pushed": a.get("push_time"),
                "platforms": sorted(set(plats)),
                "index": bool(refs),
                "signed": any(str(x.get("type", "")).startswith("signature")
                              for x in (a.get("accessories") or [])),
            })
        out.sort(key=lambda a: a["pushed"] or "", reverse=True)
        return out

    async def delete_robot(self, robot_id: int) -> None:
        try:
            await self._call("DELETE", "/" + _path("robots", int(robot_id)))
        except HarborError as e:
            if e.status != 404:
                raise


def get_harbor(request) -> HarborClient | None:
    return request.app.state.harbor
