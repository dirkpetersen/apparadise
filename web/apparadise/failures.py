"""Failure signatures and log scrubbing for the public failure database.

A signature groups builds that failed the same way. It hashes the first error line after
normalising versions, paths, hex ids and numbers, so the gprofng failure that hit three
binutils easyconfigs in the EasyBuild session (FAILURES.md #13, #17, #19) is one group.
"""

from __future__ import annotations

import hashlib
import re

# The first line that looks like an error, in priority order.
ERROR_LINE_RES = [
    re.compile(r"^.*\berror\b[: ].*$", re.I | re.M),
    re.compile(r"^.*\b(fatal|failed|cannot|undefined reference)\b.*$", re.I | re.M),
]
_NORMALISE = [
    (re.compile(r"(/[\w.+-]+)+/?"), "<path>"),
    (re.compile(r"\b[0-9a-f]{7,64}\b", re.I), "<hex>"),
    (re.compile(r"\b\d+(\.\d+)+([-_.]?[a-z][\w.]*)?\b", re.I), "<ver>"),
    (re.compile(r"\b\d+\b"), "<n>"),
    (re.compile(r"\s+"), " "),
]
_SCRUB = [
    # tokens and credentials first, before paths eat parts of them
    (re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"), r"\1[redacted]"),
    (re.compile(r"(?i)\b(token|password|passwd|secret|api[_-]?key)=\S+"), r"\1=[redacted]"),
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1[redacted]@"),
    (re.compile(r"/(home|users|scratch|storage|gpfs|fh|fast|tmp)/[^\s:'\"]*"), "/<path>"),
    (re.compile(r"\b[\w-]+\.(?:fhcrc|fredhutch|osu|internal|local|lan)\.\w+\b", re.I), "<host>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
]
EXCERPT_LINES = 30


def first_error_line(log: str) -> str:
    for rx in ERROR_LINE_RES:
        if m := rx.search(log):
            return m.group(0).strip()
    lines = [ln for ln in log.strip().splitlines() if ln.strip()]
    return lines[-1].strip() if lines else ""


def normalise_error(line: str) -> str:
    out = line.lower()
    for rx, repl in _NORMALISE:
        out = rx.sub(repl, out)
    return out.strip()


def signature(stage: str, error_line: str, restricted: bool = False) -> str:
    key = f"{stage}\n{normalise_error(error_line)}\n{'restricted' if restricted else 'public'}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def scrub(text: str) -> str:
    for rx, repl in _SCRUB:
        text = rx.sub(repl, text)
    return text


def excerpt(log: str, around: str | None = None, lines: int = EXCERPT_LINES) -> str:
    """Up to `lines` lines around the error line (or the tail), scrubbed."""
    all_lines = log.splitlines()
    idx = len(all_lines)
    if around:
        for i, ln in enumerate(all_lines):
            if around in ln:
                idx = i + 1
                break
    start = max(0, idx - lines + 5)
    return scrub("\n".join(all_lines[start:start + lines]))
