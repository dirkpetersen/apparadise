"""Stage published images as squashfs on this node's architecture. Run from cron on a
cluster node with Enroot and the shared CephFS mounts, one per architecture:

    */10 * * * * cd /srv/apparadise/builder && .venv/bin/python3 -m apparadise_builder.stage

Uses this builder's token and the coordinator API only (never the server's database). Each
image is imported by digest and replaces an older file of the same name (FORCE=1), so a
rebuild never leaves a stale squashfs behind. A failure is reported and retried next run;
it never blocks the other deliveries.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import httpx

from .config import Settings, load_settings

CSRF = {"X-Requested-With": "apparadise"}
STAGE = Path(__file__).resolve().parents[2] / "tools" / "stage-sqsh"
Run = Callable[[Sequence[str], dict[str, str]], None]


def run(argv: Sequence[str], env: dict[str, str]) -> None:
    subprocess.run(list(argv), check=True, capture_output=True, env={**os.environ, **env})


def stage_all(s: Settings, client: httpx.Client, runner: Run = run,
              stage_tool: str = str(STAGE)) -> list[dict[str, Any]]:
    token = s.token_file.read_text().strip()
    auth = {**CSRF, "Authorization": f"Bearer {token}"}
    r = client.get("/api/builder/deliveries", headers=auth)
    r.raise_for_status()
    results = []
    arch = s.builder_platform.split("/", 1)[1]
    for d in r.json()["deliveries"]:
        try:
            runner([stage_tool, s.registry_host, d["project"], d["package"], d["version"],
                    d["target"], d["image_digest"], s.sqsh_dir],
                   {"ARCH": arch, "FORCE": "1"})
            ok, err = True, ""
        except (subprocess.CalledProcessError, OSError) as e:
            ok, err = False, f"stage-sqsh failed: {type(e).__name__}"
        try:  # a failed report never stops the other deliveries; it is retried next run
            client.post(f"/api/builder/deliveries/{d['id']}", headers=auth,
                        json={"ok": ok, "error": err}).raise_for_status()
        except httpx.HTTPError:
            ok = False
        results.append({"id": d["id"], "ok": ok})
    return results


def main() -> int:
    s = load_settings()
    with httpx.Client(base_url=s.coordinator_url, timeout=60) as client:
        results = stage_all(s, client)
    for r in results:
        print(r)
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
