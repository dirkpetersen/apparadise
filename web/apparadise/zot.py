"""Read-only Zot client for the Registry page.

Uses Zot's search extension (GraphQL at /v2/_zot/ext/search) with a read-only htpasswd
account (REGISTRY_USERNAME/REGISTRY_PASSWORD, Zot's `accessControl.adminPolicy` with only
`read`). Writing to the registry is never done from here: builders push with their own
per-job token (registry_auth.py).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger(__name__)
PROJECT_RE = re.compile(r"^(public|restricted|cache|nvcr)$")
REPO_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,254}\Z")
INDEX_TYPES = ("application/vnd.oci.image.index.v1+json",
               "application/vnd.docker.distribution.manifest.list.v2+json")


class RegistryError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class RegistryUnavailable(Exception):
    """Zot could not be reached (network or timeout)."""


class ZotClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.client = httpx.AsyncClient(
            base_url=settings.registry_url.rstrip("/"),
            auth=(settings.registry_username, settings.registry_password.get_secret_value()),
            timeout=15, transport=transport, headers={"Accept": "application/json"})

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _search(self, query: str) -> dict[str, Any]:
        try:
            r = await self.client.get("/v2/_zot/ext/search", params={"query": query})
        except httpx.HTTPError as e:
            raise RegistryUnavailable(type(e).__name__) from None  # never the URL
        if r.status_code >= 400:
            raise RegistryError(r.status_code, f"Zot answered {r.status_code}")
        body = r.json()
        if body.get("errors"):
            msg = str(body["errors"][0].get("message", ""))
            log.info("zot search error: %s", msg[:200])
            raise RegistryError(404 if "not found" in msg.lower() else 502, "Zot search error")
        return body.get("data") or {}

    async def list_repositories(self, project: str) -> list[dict[str, Any]]:
        """-> [{name, newest, pulls, updated}], `name` without the project prefix."""
        if not PROJECT_RE.match(project):
            raise ValueError(f"bad project {project!r}")
        out, offset, prefix = [], 0, f"{project}/"
        while True:
            data = await self._search(
                "{RepoListWithNewestImage(requestedPage:{limit:100 offset:" + str(int(offset))
                + "}){Page{TotalCount} Results{Name LastUpdated DownloadCount NewestImage{Tag}}}}")
            page = data.get("RepoListWithNewestImage") or {}
            rows = page.get("Results") or []
            out += [{"name": r["Name"].removeprefix(prefix),
                     "newest": (r.get("NewestImage") or {}).get("Tag") or "",
                     "pulls": int(r.get("DownloadCount") or 0),
                     "updated": r.get("LastUpdated")}
                    for r in rows if r["Name"].startswith(prefix)]
            offset += len(rows)
            if not rows or offset >= int((page.get("Page") or {}).get("TotalCount") or 0) \
                    or offset >= 5000:
                return out

    async def list_artifacts(self, project: str, repo: str) -> list[dict[str, Any]]:
        """-> [{digest, tags, size, pushed, platforms, index, signed}], newest first."""
        if not PROJECT_RE.match(project) or not REPO_RE.match(repo) or ".." in repo:
            raise ValueError("bad repository")
        name = json.dumps(f"{project}/{repo}")  # a valid GraphQL string literal
        data = await self._search(
            "{ImageList(repo:" + name + " requestedPage:{limit:500 offset:0})"
            "{Results{Tag Digest MediaType Size PushTimestamp LastUpdated IsSigned "
            "Manifests{Platform{Os Arch}}}}}")
        rows = (data.get("ImageList") or {}).get("Results") or []
        if not rows:
            raise RegistryError(404, "no such repository")
        by_digest: dict[str, dict[str, Any]] = {}
        for r in rows:
            a = by_digest.setdefault(r["Digest"], {
                "digest": r["Digest"], "tags": [], "size": int(r.get("Size") or 0),
                "pushed": r.get("PushTimestamp") or r.get("LastUpdated"),
                "platforms": sorted({f"{(m.get('Platform') or {}).get('Os') or 'linux'}/"
                                     f"{(m.get('Platform') or {}).get('Arch') or '?'}"
                                     for m in r.get("Manifests") or []
                                     if (m.get("Platform") or {}).get("Arch")}),
                "index": r.get("MediaType") in INDEX_TYPES,
                "signed": bool(r.get("IsSigned"))})
            if r.get("Tag"):
                a["tags"].append(r["Tag"])
        out = list(by_digest.values())
        for a in out:
            a["tags"].sort()
        out.sort(key=lambda a: a["pushed"] or "", reverse=True)
        return out


def get_zot(request) -> ZotClient | None:
    return request.app.state.zot
