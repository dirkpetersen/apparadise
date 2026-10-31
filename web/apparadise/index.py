"""The three-way package index: which CUDA software Spack, EasyBuild and conda-forge offer.

Scanners read local copies (the nightly job clones/downloads them first; nothing here
touches the network), so they are fast to test:

- Spack: a checkout of spack/spack-packages (`repos/spack_repo/builtin/packages/*/package.py`,
  or the older `var/spack/repos/builtin/packages/*/package.py`). CUDA = the recipe mixes in
  `CudaPackage` or declares a `cuda` variant.
- EasyBuild: a checkout of easybuilders/easybuild-easyconfigs
  (`easybuild/easyconfigs/<l>/<Name>/*.eb`). CUDA = a `-CUDA-` version suffix or a CUDA
  dependency.
- conda-forge: `repodata.json` for linux-64 and linux-aarch64 (decompress the `.zst` first).
  CUDA = a build string containing `cuda`; arches = the subdirs that have such a build.

Names are normalised (`norm_name`) so `py-torch`, `PyTorch` and `pytorch` meet, which is
what the resolver and the catalog search use.

    python -m apparadise.index --db data/apparadise.sqlite3 --spack ~/src/spack-packages \
        --easybuild ~/src/easybuild-easyconfigs --conda linux-64=repodata-64.json \
        --conda linux-aarch64=repodata-aarch64.json [--csv cuda_spack_easybuild_pixi.csv]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .catalog import version_key

MAX_VERSIONS = 12  # newest first; the index is a lookup table, not an archive
CONDA_SUBDIRS = ("linux-64", "linux-aarch64")
SPACK_VERSION_RE = re.compile(r"""^\s*version\(\s*["']([^"']+)["']""", re.M)
SPACK_CUDA_RE = re.compile(r"\bCudaPackage\b|variant\(\s*[\"']cuda[\"']")
EB_VERSION_RE = re.compile(r"""^version\s*=\s*['"]([^'"]+)['"]""", re.M)
EB_CUDA_DEP_RE = re.compile(r"""\(\s*['"]CUDA['"]\s*,""")
# Conda packages that count as Python/ML even though their name has no python marker.
PYTHON_HINT = re.compile(r"^(py-|python-)|^(pytorch|tensorflow|jax|jaxlib|cupy|vllm|rapids)")


def normalise(name: str, source: str = "") -> str:
    n = name.strip().lower().replace("_", "-")
    if source == "spack" and n.startswith("py-"):
        n = n[3:]
    aliases = {"torch": "pytorch", "pytorch-gpu": "pytorch"}
    return aliases.get(n, n)


# Branch names and templates are not releases (spack: develop/main/master; EasyBuild: %s).
NOT_A_RELEASE = re.compile(r"^(develop|main|master|stable|head|trunk|latest|nightly)$|%", re.I)
CUDA_DEPS = re.compile(r"^(cuda-version|cudatoolkit|cuda-cudart|cuda-nvrtc|libcublas)\b")


def releases(versions: Iterable[str]) -> list[str]:
    vs = {v for v in versions if v and not NOT_A_RELEASE.search(v)}
    return sorted(vs, key=version_key, reverse=True)[:MAX_VERSIONS]


def _item(source: str, name: str, versions: Iterable[str], has_cuda: bool,
          arches: list[str], is_python: bool) -> dict[str, Any]:
    vs = releases(versions)
    return {"source": source, "upstream_name": name, "norm_name": normalise(name, source),
            "versions": vs, "has_cuda": has_cuda, "arches": arches, "is_python": is_python}


def scan_spack(root: str | Path) -> list[dict[str, Any]]:
    root = Path(root)
    for rel in ("repos/spack_repo/builtin/packages", "var/spack/repos/builtin/packages"):
        pkgdir = root / rel
        if pkgdir.is_dir():
            break
    else:
        raise FileNotFoundError(f"{root}: no Spack builtin packages directory")
    out = []
    for f in sorted(pkgdir.glob("*/package.py")):
        text = f.read_text(errors="replace")
        name = f.parent.name.replace("_", "-")
        out.append(_item("spack", name, SPACK_VERSION_RE.findall(text),
                         bool(SPACK_CUDA_RE.search(text)),
                         # Spack builds from source for whatever target it is given.
                         list(CONDA_SUBDIRS), name.startswith("py-")))
    return out


def scan_easybuild(root: str | Path) -> list[dict[str, Any]]:
    root = Path(root)
    ecdir = root / "easybuild" / "easyconfigs"
    if not ecdir.is_dir():
        raise FileNotFoundError(f"{root}: no easybuild/easyconfigs directory")
    versions: dict[str, set[str]] = defaultdict(set)
    cuda: dict[str, bool] = defaultdict(bool)
    python: dict[str, bool] = defaultdict(bool)
    for f in ecdir.glob("*/*/*.eb"):
        name = f.parent.name
        text = f.read_text(errors="replace")
        if m := EB_VERSION_RE.search(text):
            versions[name].add(m.group(1))
        if "-CUDA-" in f.name or EB_CUDA_DEP_RE.search(text):
            cuda[name] = True
        if "PythonPackage" in text or "PythonBundle" in text:
            python[name] = True
    return [_item("easybuild", n, versions[n], cuda[n], list(CONDA_SUBDIRS), python[n])
            for n in sorted(versions)]


def scan_conda(repodata: dict[str, str | Path]) -> list[dict[str, Any]]:
    """`repodata` maps a subdir (linux-64, linux-aarch64) to its repodata.json."""
    versions: dict[str, set[str]] = defaultdict(set)
    cuda_arches: dict[str, set[str]] = defaultdict(set)
    python: dict[str, bool] = defaultdict(bool)
    for subdir, path in repodata.items():
        if subdir not in CONDA_SUBDIRS:
            raise ValueError(f"unknown conda subdir {subdir!r}")
        with open(path) as fh:
            data = json.load(fh)
        for section in ("packages", "packages.conda"):
            for rec in (data.get(section) or {}).values():
                name = rec["name"]
                versions[name].add(rec["version"])
                deps = rec.get("depends") or []
                if "cuda" in rec.get("build", "").lower() or any(CUDA_DEPS.match(d) for d in deps):
                    cuda_arches[name].add(subdir)
                if any(d == "python" or d.startswith("python ") or d.startswith("python_abi")
                       for d in deps) or PYTHON_HINT.match(name):
                    python[name] = True
    return [_item("pixi", n, versions[n], bool(cuda_arches[n]), sorted(cuda_arches[n]),
                  python[n]) for n in sorted(versions)]


def write_csv(path: str | Path, items: list[dict[str, Any]]) -> int:
    """The three-way comparison table: one row per normalised name, CUDA packages only."""
    rows: dict[str, dict[str, str]] = defaultdict(dict)
    for it in items:
        if it["has_cuda"]:
            rows[it["norm_name"]][it["source"]] = it["upstream_name"]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "spack", "easybuild", "pixi", "sources"])
        for name in sorted(rows):
            r = rows[name]
            w.writerow([name, r.get("spack", ""), r.get("easybuild", ""), r.get("pixi", ""),
                        len(r)])
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    from .db import Database

    p = argparse.ArgumentParser(description="Refresh the three-way package index.")
    p.add_argument("--db", required=True, help="path to apparadise.sqlite3")
    p.add_argument("--spack", help="spack-packages checkout")
    p.add_argument("--easybuild", help="easybuild-easyconfigs checkout")
    p.add_argument("--conda", action="append", default=[], metavar="SUBDIR=REPODATA.json")
    p.add_argument("--csv", help="also write the comparison CSV here")
    a = p.parse_args(argv)
    db = Database(a.db)
    everything = []
    try:
        if a.spack:
            items = scan_spack(a.spack)
            print("spack", db.index.replace_source("spack", items))
            everything += items
        if a.easybuild:
            items = scan_easybuild(a.easybuild)
            print("easybuild", db.index.replace_source("easybuild", items))
            everything += items
        if a.conda:
            items = scan_conda(dict(c.split("=", 1) for c in a.conda))
            print("pixi", db.index.replace_source("pixi", items))
            everything += items
        if a.csv:
            print("csv rows", write_csv(a.csv, everything))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
