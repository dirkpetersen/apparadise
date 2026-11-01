"""Render a phase 1 recipe from `recipes/_templates/` with the flags of one target.

Target flags live in `containers/targets.yml` (the single source of truth). This is the
hand-build helper; phase 2's recipe generators will reuse `target_vars`.

    python -m apparadise.recipes spack gromacs 2026.3 rtx --spec gromacs@2026.3+mpi
    python -m apparadise.recipes easybuild relion 5.0.1 rtx --easyconfig RELION-5.0.1.eb
    python -m apparadise.recipes pixi pytorch 2.9.1 rtx --conda-name pytorch

Output goes to recipes/<name>/<version>/<target>/ (or stdout with --stdout).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from string import Template
from typing import Any

import yaml

from .catalog import NAME_RE, VERSION_RE

REPO = Path(__file__).resolve().parents[2]
TARGETS_FILE = REPO / "containers" / "targets.yml"
TEMPLATES = REPO / "recipes" / "_templates"
TARGET_NAMES = ("vr", "rtx", "generic")
TEMPLATE_FOR = {"spack": "spack.yaml.in", "easybuild": "easystack.yml.in", "pixi": "pixi.toml.in"}
OUTPUT_FOR = {"spack": "spack.yaml", "easybuild": "easystack.yml", "pixi": "pixi.toml"}
CONDA_PLATFORM = {"linux/amd64": "linux-64", "linux/arm64": "linux-aarch64"}
SPEC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@%+~=:,^ -]{0,200}\Z")
EASYCONFIG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,200}\.eb\Z")


class RecipeError(ValueError):
    pass


def load_targets(path: Path = TARGETS_FILE) -> dict[str, Any]:
    with path.open() as fh:
        return yaml.safe_load(fh)


def _per_platform(value: Any, platform: str) -> Any:
    return value[platform] if isinstance(value, dict) else value


TOOLCHAIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,30},20\d\d(?:[ab]|\.\d{1,2})")


def dotted_cc(arch: str) -> str:
    """Spack/nvcc style (120, 90a, 120f) to EasyBuild style (12.0, 9.0a, 12.0f)."""
    m = re.fullmatch(r"(\d+)(\d)([af]?)", arch)
    if not m:
        raise RecipeError(f"bad cuda_arch {arch!r} in containers/targets.yml")
    return f"{m[1]}.{m[2]}{m[3]}"


def split_spec(spec: str, cuda_arch_csv: str) -> dict[str, str]:
    """Split a Spack spec into the root part and its ^dep / %compiler part, so the CUDA
    variants land on the root package (in Spack 1.x anything after ^x or %x applies to x)."""
    cut = min((i for i in (spec.find("^"), spec.find("%")) if i >= 0), default=len(spec))
    root, deps = spec[:cut].rstrip(), spec[cut:].strip()
    words = root.replace("+", " +").replace("~", " ~").split()
    cuda = ""
    if "+cuda" not in words:
        cuda += " +cuda"
    if not any(w.startswith("cuda_arch=") for w in words):
        cuda += f" cuda_arch={cuda_arch_csv}"
    return {"SPEC_ROOT": root, "SPEC_CUDA": cuda, "SPEC_DEPS": f" {deps}" if deps else ""}


def target_vars(target: str, platform: str | None = None,
                targets: dict[str, Any] | None = None) -> dict[str, str]:
    """The template variables of one (target, platform). Refuses incomplete targets."""
    targets = targets or load_targets()
    if target not in TARGET_NAMES:
        raise RecipeError(f"unknown target {target!r}")
    t = targets[target]
    platforms = t["platform"] if isinstance(t["platform"], list) else [t["platform"]]
    platform = platform or platforms[0]
    if platform not in platforms:
        raise RecipeError(f"{target} builds for {', '.join(platforms)}, not {platform}")
    if not t.get("cuda_arch") or not t.get("ptx_arch"):
        raise RecipeError(f"{target}: cuda_arch/ptx_arch are not set in containers/targets.yml "
                          "(read them from the hardware first, QUESTIONS.md Q13)")
    arches = [str(a) for a in t["cuda_arch"]]
    return {
        "TARGET": target,
        "PLATFORM": platform,
        "CONDA_PLATFORM": CONDA_PLATFORM[platform],
        "CUDA_VERSION": str(t["cuda_version"]),
        "CUDA_FULL": str(t["cuda_full"]),
        "CUDA_MAJOR": str(t["cuda_version"]).split(".")[0],
        "CUDA_ARCH_CSV": ",".join(arches),
        # EasyBuild wants dotted compute capabilities: 120 -> 12.0, 89 -> 8.9, 120f -> 12.0f
        "CUDA_CC_CSV": ",".join(dotted_cc(a) for a in arches),
        "PTX_ARCH": str(t["ptx_arch"]),
        "SPACK_TARGET": str(_per_platform(t["spack_target"], platform)),
        "EASYBUILD_OPTARCH": str(_per_platform(t["easybuild_optarch"], platform)),
        "EASYBUILD_TOOLCHAIN": str(targets["easybuild_toolchains"][0]),
    }


# EasyBuild options a repair may set (recipes/repairs.yml): nothing that turns a check off.
EB_OPTIONS = {"try-amend", "try-toolchain"}


def _eb_options(options: dict[str, Any]) -> str:
    """Extra easystack option lines. Values are JSON-quoted, so YAML reads them verbatim."""
    out = []
    for key, value in sorted(options.items()):
        if key not in EB_OPTIONS:
            raise RecipeError(f"easystack option {key!r} is not allowed")
        if value is None or key == "try-toolchain":
            continue  # try-toolchain replaces the template's line (render), never adds one
        if not isinstance(value, str) or len(value) > 300 or "\n" in value or "," in value:
            # EasyBuild splits option values on commas: one value, one line, no commas
            raise RecipeError(f"easystack option {key}: one line of text without commas")
        out.append(f"\n        {key}: {json.dumps(value)}")
    return "".join(out)


def render(source: str, name: str, version: str, target: str, *, registry: str,
           platform: str | None = None, spec: str = "", easyconfig: str = "",
           conda_name: str = "", cuda_build_marker: bool = True,
           targets: dict[str, Any] | None = None,
           eb_options: dict[str, Any] | None = None) -> str:
    if source not in TEMPLATE_FOR:
        raise RecipeError(f"unknown source {source!r}")
    if not NAME_RE.match(name) or not VERSION_RE.match(version):
        raise RecipeError("invalid name or version")
    v = {**target_vars(target, platform, targets), "NAME": name, "VERSION": version,
         "REGISTRY": registry}
    if source == "spack":
        if not SPEC_RE.match(spec or ""):
            raise RecipeError("--spec: a Spack spec such as gromacs@2026.3+mpi")
        v.update(split_spec(spec, v["CUDA_ARCH_CSV"]))
    elif source == "easybuild":
        if not EASYCONFIG_RE.match(easyconfig or ""):
            raise RecipeError("--easyconfig: an easyconfig file name ending in .eb")
        v["EASYCONFIG"] = easyconfig
        v["EXTRA_OPTIONS"] = _eb_options(eb_options or {})
        tc = (eb_options or {}).get("try-toolchain", "")
        if tc:  # the toolchain-fallback repair picked an older generation
            if not TOOLCHAIN_RE.fullmatch(tc):
                raise RecipeError("try-toolchain: <name>,<version>, e.g. foss,2025b")
            v["EASYBUILD_TOOLCHAIN"] = tc
        if tc is None:  # the chain is used up: build with the easyconfig's own toolchain
            text = (TEMPLATES / TEMPLATE_FOR[source]).read_text()
            text = re.sub(r"(?m)^ *try-toolchain: .*\n", "", text)
            return Template(text).substitute(v)
    else:
        conda = conda_name or name
        if not NAME_RE.match(conda):
            raise RecipeError("--conda-name: a conda-forge package name")
        # Pin a CUDA build only when conda-forge marks it in the build string; otherwise the
        # [system-requirements] cuda entry steers the solver to the CUDA variant.
        build = ', build = "*cuda*"' if cuda_build_marker else ""
        v["PIXI_DEPENDENCY"] = f'{conda} = {{ version = "=={version}"{build} }}'
    text = (TEMPLATES / TEMPLATE_FOR[source]).read_text()
    return Template(text).substitute(v)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render a recipe template for one target.")
    p.add_argument("source", choices=sorted(TEMPLATE_FOR))
    p.add_argument("name")
    p.add_argument("version")
    p.add_argument("target")
    p.add_argument("--platform", help="linux/amd64 or linux/arm64 (generic builds both)")
    p.add_argument("--registry", default="registry.example.org")
    p.add_argument("--spec", default="")
    p.add_argument("--easyconfig", default="")
    p.add_argument("--conda-name", default="")
    p.add_argument("--stdout", action="store_true")
    a = p.parse_args(argv)
    try:
        text = render(a.source, a.name, a.version, a.target, registry=a.registry,
                      platform=a.platform, spec=a.spec, easyconfig=a.easyconfig,
                      conda_name=a.conda_name)
    except RecipeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.stdout:
        sys.stdout.write(text)
        return 0
    suffix = a.target if not a.platform else f"{a.target}-{CONDA_PLATFORM[a.platform]}"
    out = REPO / "recipes" / a.name / a.version / suffix / OUTPUT_FOR[a.source]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
