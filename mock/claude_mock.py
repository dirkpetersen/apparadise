"""A fake Claude Messages API for the demo: keyword rules instead of a model, so the chat
page can be tried without an API key. It still drives the real tool loop: it asks for the
same five tools, reads their results, and answers from them. Never imported by the app.

    transport = httpx2.MockTransport(FakeClaude().handler)
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx2

STOP = set("""a an and any are available build built can could did do does for from get have
help how i i'd if in install is it me my need needed of on or please run the there this to us
use want we what when where which why with would you vera rubin rtx farm nodes node gpu gpus
fail failed failing error errors broken version versions newest latest package software
""".split())


def _name(text: str) -> str | None:
    for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9._+-]*", text):
        w = word.lower().strip(".")
        if len(w) >= 3 and w not in STOP and not w.isdigit():
            return w
    return None


def _question(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m["role"] == "user" and isinstance(m["content"], str):
            return m["content"]
    return ""


def _message(content: list[dict[str, Any]], stop: str) -> dict[str, Any]:
    return {"id": "msg_demo", "type": "message", "role": "assistant", "model": "demo",
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}}


def _tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return _message([{"type": "tool_use", "id": f"tu_{name}", "name": name, "input": args}],
                    "tool_use")


def _say(text: str) -> dict[str, Any]:
    return _message([{"type": "text", "text": text}], "end_turn")


class FakeClaude:
    def handler(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        return httpx2.Response(200, json=self.answer(body["messages"]))

    def answer(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        q = _question(messages)
        name = _name(q)
        last = messages[-1]
        if isinstance(last["content"], str):  # a new question: pick the first tool
            if not name:
                return _say("Which package do you mean? Give me its name, e.g. `gromacs`.")
            if re.search(r"fail|error|broke", q, re.I):
                return _tool("explain_failure", {"package": name})
            if re.search(r"need|build|request|install|want", q, re.I):
                return _tool("search_index", {"query": name})
            return _tool("search_catalog", {"query": name})
        call = next(b for b in messages[-2]["content"] if b.get("type") == "tool_use")
        result = last["content"][0]
        data = json.loads(result["content"]) if not result.get("is_error") else {}
        tool = call["name"]
        if tool == "search_catalog":
            pkgs = data.get("packages", [])
            if not pkgs:
                return _tool("search_index", {"query": name})
            p = pkgs[0]
            targets = ", ".join(f"{t}: {s}" for t, s in p["targets"].items()) or "none yet"
            if p["module"]:
                return _say(f"{p['title']} {p['latest']} is in the catalog ({targets}).\n\n"
                            f"```\nmodule load {p['name']}/{p['latest']}\n```\n\n"
                            "The module picks the vr or rtx image for the node it runs on.")
            return _say(f"{p['title']} is in the catalog but not built yet ({targets}).")
        if tool == "search_index":
            hit = next((r for r in data.get("results", []) if r["name"] == name), None)
            if not hit or not hit["resolution"]["source"]:
                return _say(f"No source has a recipe for `{name}`: not Spack, EasyBuild or "
                            "conda-forge. Check the spelling, or ask the research computing "
                            "team.")
            targets = ["rtx"] if re.search(r"\brtx\b", q, re.I) else ["vr", "rtx"]
            return _tool("create_request", {"name": name, "targets": targets})
        if tool == "create_request":
            p = data.get("proposal", {})
            res = p.get("resolution") or {}
            return _say(f"`{name}` would build from **{res.get('source')}**: {res.get('reason')}."
                        "\n\nI drafted the request below. Nothing is queued until you press "
                        "the button, and it counts against your weekly quota.")
        if tool == "explain_failure":
            fails = data.get("failures", [])
            if not fails:
                return _say(f"I found no failed builds of `{name}`.")
            f = fails[0]
            hint = ("nvcc doesn't know this GPU architecture yet: the CUDA toolkit in the "
                    "base image is older than the target needs. A newer CUDA, or the "
                    "`generic` target, avoids it." if "gpu architecture" in f["error"].lower()
                    else "Open the failure record for the log excerpt.")
            return _say(f"The latest failure of `{name}` was at the **{f['stage']}** stage:"
                        f"\n\n```\n{f['error']}\n```\n\n{hint} See `#/f/{f['id']}`.")
        return _say("Done.")
