"""Launchers start and stop builders. Each implements exactly two calls: start and stop.

    python -m apparadise_builder.launchers local start --image REG/cache/builder-agent:latest
    python -m apparadise_builder.launchers sbatch start --partition build --env-file builder.env
    python -m apparadise_builder.launchers ec2 start --launch-template apparadise-builder-arm
    python -m apparadise_builder.launchers <kind> stop ID

Every launcher only starts a builder process; the builder then registers (or reuses its
token) and pulls work. Slurm and EC2 builders should set IDLE_EXIT_POLLS so they release
their node when the queue is empty. Commands are passed as argv lists, never via a shell.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from typing import Protocol

Run = Callable[[Sequence[str]], str]
SAFE = re.compile(r"^[A-Za-z0-9./][A-Za-z0-9._:/@+=-]{0,255}\Z")  # never a leading "-"


def run(argv: Sequence[str]) -> str:
    return subprocess.run(list(argv), check=True, capture_output=True, text=True).stdout.strip()


def _safe(name: str, value: str) -> str:
    if not SAFE.match(value):
        raise ValueError(f"{name}: unsafe value {value!r}")
    return value


class Launcher(Protocol):
    def start(self) -> str: ...
    def stop(self, launch_id: str) -> None: ...


class LocalContainer:
    """A builder container on this server (the single-server setup's x86 builder)."""

    def __init__(self, image: str, env_file: str, data_dir: str, scratch: str,
                 tool: str = "podman", runner: Run = run):
        self.image, self.env_file = _safe("image", image), _safe("env_file", env_file)
        self.data_dir, self.scratch = _safe("data_dir", data_dir), _safe("scratch", scratch)
        self.tool, self.run = tool, runner

    def start(self) -> str:
        return self.run([self.tool, "run", "-d", "--rm", "--env-file", self.env_file,
                         "-v", f"{self.data_dir}:/app/data:Z", "-v", f"{self.scratch}:/scratch:Z",
                         "--label", "io.apparadise.role=builder", self.image])

    def stop(self, launch_id: str) -> None:
        self.run([self.tool, "stop", _safe("id", launch_id)])


class Sbatch:
    """A builder as a Slurm job on a CPU node of the right architecture."""

    def __init__(self, partition: str, workdir: str, time: str = "08:00:00", cpus: int = 32,
                 mem: str = "128G", python: str = ".venv/bin/python3", runner: Run = run):
        self.partition, self.workdir = _safe("partition", partition), _safe("workdir", workdir)
        self.time, self.mem = _safe("time", time), _safe("mem", mem)
        self.cpus, self.python, self.run = int(cpus), _safe("python", python), runner

    def start(self) -> str:
        out = self.run(["sbatch", "--parsable", "--job-name=apparadise-builder",
                        f"--partition={self.partition}", f"--time={self.time}",
                        f"--cpus-per-task={self.cpus}", f"--mem={self.mem}", "--nice=100",
                        f"--chdir={self.workdir}", f"--wrap={self.python} app.py"])
        return out.split(";")[0]

    def stop(self, launch_id: str) -> None:
        self.run(["scancel", _safe("id", launch_id)])


class Ec2Spot:
    """An EC2 spot instance from a launch template (user data starts the builder)."""

    def __init__(self, launch_template: str, region: str = "", runner: Run = run):
        self.template = _safe("launch_template", launch_template)
        self.region, self.run = region, runner

    def _aws(self) -> list[str]:
        return ["aws", "ec2"] + (["--region", _safe("region", self.region)] if self.region else [])

    def start(self) -> str:
        return self.run([*self._aws(), "run-instances",
                         "--launch-template", f"LaunchTemplateName={self.template}",
                         "--instance-market-options", "MarketType=spot",
                         "--count", "1", "--query", "Instances[0].InstanceId", "--output", "text"])

    def stop(self, launch_id: str) -> None:
        self.run([*self._aws(), "terminate-instances", "--instance-ids", _safe("id", launch_id)])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Start or stop an apparadise builder.")
    p.add_argument("kind", choices=("local", "sbatch", "ec2"))
    p.add_argument("action", choices=("start", "stop"))
    p.add_argument("id", nargs="?")
    p.add_argument("--image", default="")
    p.add_argument("--env-file", default=".env")
    p.add_argument("--data-dir", default="/srv/apparadise-builder/data")
    p.add_argument("--scratch", default="/scratch/apparadise")
    p.add_argument("--partition", default="build")
    p.add_argument("--workdir", default=".")
    p.add_argument("--launch-template", default="")
    p.add_argument("--region", default="")
    a = p.parse_args(argv)
    if a.kind == "local":
        launcher: Launcher = LocalContainer(a.image, a.env_file, a.data_dir, a.scratch)
    elif a.kind == "sbatch":
        launcher = Sbatch(a.partition, a.workdir)
    else:
        launcher = Ec2Spot(a.launch_template, a.region)
    if a.action == "start":
        print(launcher.start())
    else:
        if not a.id:
            p.error("stop needs the ID that start printed")
        launcher.stop(a.id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
