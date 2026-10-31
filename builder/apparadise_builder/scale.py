"""Start builders when the queue grows. Run it from cron every few minutes, per platform:

    */5 * * * * cd /srv/apparadise/builder && .venv/bin/python3 -m apparadise_builder.scale \\
        --launcher sbatch --platform linux/arm64 --max 4 --partition build-arm

It asks the coordinator for queue depth (counts only) with this builder's own token,
remembers what it started in a small state file, and starts at most `--max` builders per
platform. It never stops builders: started builders set IDLE_EXIT_POLLS and exit by
themselves when the queue is empty; entries older than --max-age are forgotten.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from .config import load_settings
from .launchers import Ec2Spot, Launcher, LocalContainer, Sbatch

CSRF = {"X-Requested-With": "apparadise"}


def plan(queued: int, active: int, maximum: int) -> int:
    """How many builders to start: one per queued job, up to `maximum` in total."""
    return max(0, min(queued, maximum) - active)


def load_state(path: Path, now: float, max_age: float) -> list[dict[str, Any]]:
    try:
        entries = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return [e for e in entries if now - e.get("started", 0) < max_age]


def save_state(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(entries, fh)
    os.replace(tmp, path)


def scale_once(counts: dict[str, dict[str, int]], platform: str, launcher: Launcher,
               state: Path, maximum: int, max_age: float, now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    entries = load_state(state, now, max_age)
    mine = [e for e in entries if e["platform"] == platform]
    started = []
    for _ in range(plan(counts.get("queued", {}).get(platform, 0), len(mine), maximum)):
        lid = launcher.start()
        entries.append({"id": lid, "platform": platform, "started": now})
        started.append(lid)
    save_state(state, entries)
    return started


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Start builders from queue depth.")
    p.add_argument("--launcher", choices=("local", "sbatch", "ec2"), required=True)
    p.add_argument("--platform", choices=("linux/amd64", "linux/arm64"), required=True)
    p.add_argument("--max", type=int, default=2)
    p.add_argument("--max-age", type=float, default=8 * 3600, help="seconds to remember a launch")
    p.add_argument("--image", default="")
    p.add_argument("--partition", default="build")
    p.add_argument("--workdir", default=".")
    p.add_argument("--launch-template", default="")
    p.add_argument("--region", default="")
    a = p.parse_args(argv)
    s = load_settings()
    token = s.token_file.read_text().strip()
    r = httpx.get(f"{s.coordinator_url}/api/builder/queue", timeout=30,
                  headers={**CSRF, "Authorization": f"Bearer {token}"})
    r.raise_for_status()
    if a.launcher == "local":
        launcher: Launcher = LocalContainer(a.image, ".env", s.data_dir, s.work_dir)
    elif a.launcher == "sbatch":
        launcher = Sbatch(a.partition, a.workdir)
    else:
        launcher = Ec2Spot(a.launch_template, a.region)
    started = scale_once(r.json(), a.platform, launcher,
                         Path(s.data_dir) / f"launches-{a.platform.replace('/', '-')}.json",
                         a.max, a.max_age)
    for lid in started:
        print(lid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
