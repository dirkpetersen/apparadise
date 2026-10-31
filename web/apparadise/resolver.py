"""Pick exactly one source for a request, with the fixed rule order from the architecture doc:

1. the requester names a source                          -> that source
2. Python/ML package with conda-forge CUDA builds for BOTH linux-64 and linux-aarch64
                                                          -> pixi
3. Spack has it with a cuda variant                       -> spack
4. EasyBuild has a CUDA easyconfig                        -> easybuild
5. only CPU builds exist anywhere                         -> CUDA port attempt in the source
                                                             with the closest recipe
6. nothing exists                                         -> failure, stage `no-recipe`

One image, one source, one solver: the result never mixes sources.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .db import SOURCES
from .index import CONDA_SUBDIRS, normalise

# Rule 5 prefers the source with the most control over arch flags and MPI.
PORT_ORDER = ("spack", "easybuild", "pixi")


@dataclass(frozen=True)
class Resolution:
    rule: int                # 1..6, as in the module docstring
    source: str | None       # None only for rule 6
    upstream_name: str | None
    versions: tuple[str, ...]
    cuda_port: bool          # rule 5: the recipe has no CUDA build yet
    reason: str

    @property
    def failed(self) -> bool:
        return self.source is None

    @property
    def stage(self) -> str | None:
        return "no-recipe" if self.failed else None


def candidates(name: str, aliases: dict[str, str] | None = None) -> list[str]:
    """Normalised names to look up: the request itself plus any per-source aliases."""
    names = {normalise(name)}
    for source, upstream in (aliases or {}).items():
        names.add(normalise(upstream, source))
    return sorted(names)


def resolve(entries: list[dict[str, Any]], requested_source: str | None = None) -> Resolution:
    """`entries`: index rows matching the request (any source), from `IndexRepo.lookup`."""
    if requested_source is not None and requested_source not in SOURCES:
        raise ValueError(f"unknown source {requested_source!r}")
    by_source: dict[str, dict[str, Any]] = {}
    for e in entries:  # several upstream names may normalise alike: prefer one with CUDA
        cur = by_source.get(e["source"])
        if cur is None or (e["has_cuda"] and not cur["has_cuda"]):
            by_source[e["source"]] = e

    def pick(rule: int, e: dict[str, Any], reason: str, port: bool = False) -> Resolution:
        return Resolution(rule, e["source"], e["upstream_name"], tuple(e["versions"]), port,
                          reason)

    if requested_source:
        e = by_source.get(requested_source)
        if e is None:
            return Resolution(6, None, None, (), False,
                              f"{requested_source} has no recipe for this package")
        return pick(1, e, f"requested source {requested_source}", port=not e["has_cuda"])
    conda = by_source.get("pixi")
    if conda and conda["has_cuda"] and conda["is_python"] \
            and set(CONDA_SUBDIRS) <= set(conda["arches"]):
        return pick(2, conda, "Python/ML package with conda-forge CUDA builds for both arches")
    spack = by_source.get("spack")
    if spack and spack["has_cuda"]:
        return pick(3, spack, "Spack recipe with a cuda variant")
    eb = by_source.get("easybuild")
    if eb and eb["has_cuda"]:
        return pick(4, eb, "EasyBuild CUDA easyconfig")
    for source in PORT_ORDER:
        if e := by_source.get(source):
            return pick(5, e, f"only a CPU build exists; attempting a CUDA port in {source}",
                        port=True)
    return Resolution(6, None, None, (), False, "no recipe in Spack, EasyBuild or conda-forge")


def resolve_name(db, name: str, aliases: dict[str, str] | None = None,
                 requested_source: str | None = None) -> Resolution:
    return resolve(db.index.lookup(candidates(name, aliases)), requested_source)
