#!/usr/bin/env python3
"""Write a demo catalog: catalog/*.yml plus the build state of this host. Dev tooling only.

    python3 mock/real_catalog.py OUT_DIR [--logs DIR]

A local image `apparadise-local/<name>-<version>-<target>` is a finished build (the local image
ID stands in for the registry digest; nothing is pushed or GPU-tested here). A build log
`pkg-<name>-<version>-<target>.log` that ends in `EXIT=n STAGE=s` is a failure, unless an image
of that package and target exists. A log without EXIT whose localbuild process still runs is a
running build. Everything else stays as catalog/ has it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LOGS = Path("/storage/t3/test/peterdir/apparadise/podman/logs")
TARGETS = ("rtx", "generic", "vr")
UNTESTED = "built on an x86 host without a GPU; GPU test pending"


def local_images() -> dict[str, dict]:
    out = subprocess.run(["podman", "images", "--format", "json"], capture_output=True,
                         text=True, check=True).stdout
    found = {}
    for img in json.loads(out or "[]"):
        for n in img.get("Names") or []:
            m = re.fullmatch(r"localhost/apparadise-local/(.+):latest", n)
            if m:
                found[m.group(1)] = {"id": img["Id"], "created": img.get("CreatedAt", "")}
    return found


def split(key: str, name: str) -> tuple[str, str] | None:
    """`nccl-tests-2.17.6-rtx` -> ("2.17.6", "rtx") for package `nccl-tests`."""
    if not key.startswith(name + "-"):
        return None
    version, _, target = key[len(name) + 1:].rpartition("-")
    return (version, target) if target in TARGETS and re.match(r"\d", version) else None


def build_state(name: str, images: dict, logs: Path, running: str) -> dict:
    versions: dict[str, dict] = {}
    built_targets = set()
    for key, img in images.items():
        vt = split(key, name)
        if vt:
            versions.setdefault(vt[0], {})[vt[1]] = {
                "status": "published", "digest": f"sha256:{img['id'][-64:]}",
                "test": UNTESTED, "built": str(img["created"])[:10] or None}
            built_targets.add(vt[1])
    for log in sorted(logs.glob(f"pkg-{name}-*.log")):
        vt = split(log.name[4:-4], name)
        if not vt or vt[1] in built_targets:
            continue
        tail = log.read_text(errors="replace").splitlines()[-3:]
        end = next((ln for ln in reversed(tail) if ln.startswith("EXIT=")), "")
        m = re.match(r"EXIT=(\d+) STAGE=(\w+)", end)
        if m:
            versions.setdefault(vt[0], {})[vt[1]] = {"status": "failed", "stage": m.group(2)}
        elif not end and f"{name} {vt[0]} {vt[1]}" in running:
            versions.setdefault(vt[0], {})[vt[1]] = {"status": "running"}
    for targets in versions.values():
        for info in targets.values():
            if info.get("built") is None:
                info.pop("built", None)
    return versions


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("out", type=Path)
    p.add_argument("--logs", type=Path, default=LOGS)
    a = p.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    images = local_images()
    running = subprocess.run(["pgrep", "-af", "localbuild.py"], capture_output=True,
                             text=True).stdout
    for f in sorted((ROOT / "catalog").glob("*.yml")):
        doc = yaml.safe_load(f.read_text())
        doc["versions"] = build_state(doc["name"], images, a.logs, running) or {}
        (a.out / f.name).write_text(yaml.safe_dump(doc, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
