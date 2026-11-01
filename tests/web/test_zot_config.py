"""tools/zot-config: the Zot config apparadise expects."""

import json
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[2] / "tools" / "zot-config"
ARGS = ["--storage", "/d", "--realm", "https://h.example/registry/token",
        "--verify-key", "/k.pem", "--htpasswd", "/h"]


def run(*extra):
    return subprocess.run([sys.executable, "-I", str(TOOL), *ARGS, *extra],
                          capture_output=True, text=True)


def test_test_tags_expire_and_everything_else_is_kept():
    cfg = json.loads(run().stdout)
    assert cfg["storage"]["gc"] is True  # retention needs gc
    first, last = cfg["storage"]["retention"]["policies"]
    assert first["repositories"] == ["cache/test-*"]
    assert first["keepTags"] == [{"patterns": [".*"], "pushedWithin": "48h"}]  # Q19
    # the catch-all comes last and keeps every tag and untagged manifest
    assert last == {"repositories": ["**"], "deleteUntagged": False,
                    "keepTags": [{"patterns": [".*"]}]}


def test_refuses_plain_http_realm():
    r = subprocess.run([sys.executable, "-I", str(TOOL), "--storage", "/d", "--realm",
                        "http://h.example/t", "--verify-key", "/k", "--htpasswd", "/h"],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "https" in r.stderr
