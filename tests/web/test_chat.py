"""The request chatbot: the real Anthropic SDK against a scripted fake API (httpx2 mock
transport). Covers the tool loop, permissions inside tools, proposals instead of actions,
conversations, refusals and errors."""

import json
import logging

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

from apparadise import chatbot
from apparadise.db import Database
from apparadise.main import create_app
from mock.demo import build_demo_app, demo_settings
from mock.directory import DemoDirectory

H = {"X-Requested-With": "apparadise"}


class FakeClaude:
    """Answers /v1/messages from a script and records what it was sent."""

    def __init__(self):
        self.script = []
        self.sent = []

    def handler(self, request):
        self.sent.append({"body": json.loads(request.content), "headers": request.headers})
        status, payload = self.script.pop(0)
        return httpx2.Response(status, json=payload)

    def reply(self, *content, stop="end_turn"):
        self.script.append((200, {
            "id": f"msg_{len(self.script)}", "type": "message", "role": "assistant",
            "model": "claude-opus-5-5", "content": list(content), "stop_reason": stop,
            "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}))

    def tool(self, name, args, tid="tu_1"):
        self.reply({"type": "thinking", "thinking": "", "signature": "sig-" + tid},
                   {"type": "tool_use", "id": tid, "name": name, "input": args},
                   stop="tool_use")

    def text(self, text):
        self.reply({"type": "text", "text": text})


def text_of(block):
    return block["content"] if isinstance(block["content"], str) else json.dumps(block)


@pytest.fixture
def env(tmp_path):
    fake = FakeClaude()
    settings = demo_settings(port=8000, data_dir=str(tmp_path))
    client = anthropic.AsyncAnthropic(
        api_key="test-key", max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(
            transport=httpx2.MockTransport(fake.handler)))
    app = build_demo_app(settings, anthropic_client=client)
    logging.disable(logging.INFO)
    with TestClient(app) as c:
        c.app_db = app.state.db
        yield c, fake
    logging.disable(logging.NOTSET)
    app.state.db.close()


def login(c, user="alice"):
    assert c.post("/api/login", json={"login": user, "password": "demo"},
                  headers=H).status_code == 200


def say(c, message, conversation=None):
    return c.post("/api/chat", json={"message": message, "conversation": conversation},
                  headers=H)


def test_switched_off_without_a_key(settings):
    db = Database(":memory:")
    settings = settings.model_copy(update={"session_cookie_secure": False})  # plain http
    app = create_app(settings, db=db, authenticator=DemoDirectory(), background=False)
    with TestClient(app) as c:
        assert c.get("/api/chat").json() == {"enabled": False, "model": None}
        login(c)
        assert say(c, "hi").status_code == 503
    db.close()


def test_demo_uses_the_keyword_stand_in(tmp_path):
    app = build_demo_app(demo_settings(port=8000, data_dir=str(tmp_path)))
    with TestClient(app) as c:
        login(c)
        r = say(c, "Is GROMACS available on the Vera Rubin nodes?").json()
        assert "module load gromacs/" in r["reply"] and r["tools"] == ["search_catalog"]
        r = say(c, "I need LAMMPS for the RTX farm.").json()
        assert r["tools"] == ["search_index", "create_request"]
        assert r["proposals"][0]["targets"] == ["rtx"]
        r = say(c, "Why did the vLLM build fail on vr?").json()
        assert "compute_103f" in r["reply"]
    app.state.db.close()


def test_needs_sign_in_and_csrf(env):
    c, fake = env
    assert c.get("/api/chat").json() == {"enabled": True, "model": "claude-opus-5-5"}
    assert say(c, "hi").status_code == 401
    login(c)
    assert c.post("/api/chat", json={"message": "hi"}).status_code == 403
    assert fake.sent == []


def test_tool_loop_runs_tools_and_passes_history_back(env):
    c, fake = env
    login(c)
    fake.tool("search_catalog", {"query": "gromacs"})
    fake.text("Use module load gromacs/2026.3.")
    r = say(c, "is gromacs available?").json()
    assert r["reply"] == "Use module load gromacs/2026.3." and r["tools"] == ["search_catalog"]
    first, second = fake.sent
    assert first["body"]["model"] == "claude-opus-5-5"
    assert first["body"]["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in first["headers"]["anthropic-beta"]
    assert first["body"]["output_config"] == {"effort": "medium"}
    assert [t["name"] for t in first["body"]["tools"]] == chatbot.TOOL_NAMES
    assert "test-key" in first["headers"]["authorization"]  # the key goes from the server only
    msgs = second["body"]["messages"]
    assert msgs[1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig-tu_1"}
    result = msgs[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "tu_1"
    found = json.loads(result["content"])["packages"]
    assert found[0]["name"] == "gromacs" and found[0]["module"] == "module load gromacs"


def test_conversation_continues_and_is_private(env):
    c, fake = env
    login(c)
    fake.text("Hello.")
    cid = say(c, "hi").json()["conversation"]
    fake.text("Again.")
    assert say(c, "and again", cid).json()["conversation"] == cid
    assert [m["role"] for m in fake.sent[-1]["body"]["messages"]] == ["user", "assistant",
                                                                     "user"]
    login(c, "bob")
    fake.text("Who are you?")
    assert say(c, "hijack", cid).json()["conversation"] != cid
    assert len(fake.sent[-1]["body"]["messages"]) == 1


def test_create_request_only_proposes(env):
    c, fake = env
    login(c)
    before = c.app_db.query_one("SELECT COUNT(*) AS n FROM build")["n"]
    fake.tool("create_request", {"name": "lammps", "targets": ["rtx"]})
    fake.text("Confirm with the button below.")
    r = say(c, "please build lammps").json()
    assert r["proposals"] == [{"kind": "request", "name": "lammps", "version": "",
                               "targets": ["rtx"], "source": None,
                               "resolution": r["proposals"][0]["resolution"]}]
    assert r["proposals"][0]["resolution"]["source"] == "spack"
    assert c.app_db.query_one("SELECT COUNT(*) AS n FROM build")["n"] == before
    assert c.get("/api/quota").json()["used"] == 0


def test_bad_tool_input_is_an_error_result(env):
    c, fake = env
    login(c)
    fake.tool("create_request", {"name": "Not A Name!"})
    fake.text("Sorry.")
    say(c, "build it")
    result = fake.sent[1]["body"]["messages"][2]["content"][0]
    assert result["is_error"] is True and "invalid request" in result["content"]


def test_start_build_is_for_sysadmins(env):
    c, fake = env
    login(c)
    fake.tool("start_build", {"package": "gromacs", "version": "2026.3", "targets": ["rtx"]})
    fake.text("Only sysadmins.")
    assert say(c, "rebuild").json()["proposals"] == []
    assert "Only sysadmins" in text_of(fake.sent[1]["body"]["messages"][2]["content"][0])
    login(c, "dana")
    fake.tool("start_build", {"package": "gromacs", "version": "2026.3", "targets": ["rtx"]})
    fake.text("Confirm below.")
    assert say(c, "rebuild").json()["proposals"][0]["kind"] == "build"


def test_restricted_data_stays_hidden_inside_tools(env):
    c, fake = env
    login(c)
    fid = c.app_db.query_one("SELECT id FROM failure WHERE restricted = 1")["id"]
    fake.tool("explain_failure", {"failure_id": fid})
    fake.tool("search_catalog", {"query": "alphafold"}, tid="tu_2")
    fake.tool("search_index", {"query": "alphafold"}, tid="tu_3")
    fake.text("Nothing.")
    say(c, "alphafold?")
    results = [m["content"][0] for m in fake.sent[-1]["body"]["messages"]
               if m["role"] == "user" and isinstance(m["content"], list)]
    assert json.loads(results[0]["content"]) == {"error": "No such failure."}
    assert json.loads(results[1]["content"])["packages"] == []
    assert json.loads(results[2]["content"])["results"] == []


def test_explain_failure_returns_the_scrubbed_record(env):
    c, fake = env
    login(c)
    fake.tool("explain_failure", {"package": "vllm"})
    fake.text("nvcc does not know compute_103f yet.")
    say(c, "why did vllm fail?")
    out = json.loads(fake.sent[1]["body"]["messages"][2]["content"][0]["content"])
    assert "compute_103f" in out["failures"][0]["error"]


def test_refusal_and_errors(env):
    c, fake = env
    login(c)
    fake.reply({"type": "text", "text": ""}, stop="refusal")
    assert say(c, "x").json()["reply"] == "I can't help with that request."
    fake.script.append((500, {"type": "error", "error": {"type": "api_error",
                                                         "message": "secret detail"}}))
    r = say(c, "y")
    assert r.status_code == 502 and "secret" not in r.text


def test_failed_turn_leaves_history_untouched(env):
    c, fake = env
    login(c)
    fake.text("One.")
    cid = say(c, "first").json()["conversation"]
    fake.tool("search_catalog", {"query": "x"})
    fake.script.append((529, {"type": "error", "error": {"type": "overloaded_error",
                                                         "message": "busy"}}))
    assert say(c, "second", cid).status_code in (502, 503)
    fake.text("Three.")
    say(c, "third", cid)
    roles = [m["role"] for m in fake.sent[-1]["body"]["messages"]]
    assert roles == ["user", "assistant", "user"]  # the failed turn left nothing behind


def test_hourly_limit_and_audit_without_text(env, monkeypatch):
    c, fake = env
    monkeypatch.setattr(chatbot, "PER_HOUR", 2)
    login(c)
    for _ in range(2):
        fake.text("ok")
        assert say(c, "my secret project name").status_code == 200
    assert say(c, "more").status_code == 429
    entries = c.app_db.audit.query(action="chat.message")
    assert len(entries) == 2 and "secret project" not in json.dumps(entries)


def test_too_many_rounds_is_not_kept(env, monkeypatch):
    c, fake = env
    monkeypatch.setattr(chatbot, "TOOL_ROUNDS", 1)
    login(c)
    fake.tool("search_catalog", {"query": "a"})
    fake.tool("search_catalog", {"query": "b"}, tid="tu_2")
    r = say(c, "loop").json()
    assert "too many steps" in r["reply"]
    fake.text("fresh")
    say(c, "again", r["conversation"])
    assert len(fake.sent[-1]["body"]["messages"]) == 1


def test_cut_off_tool_use_is_closed_and_the_conversation_goes_on(env):
    c, fake = env
    login(c)
    fake.reply({"type": "text", "text": "Let me look"},
               {"type": "tool_use", "id": "tu_cut", "name": "search_catalog",
                "input": {"query": "x"}}, stop="max_tokens")
    r = say(c, "first").json()
    assert "cut short" in r["reply"] and r["tools"] == []
    fake.text("Second answer.")
    assert say(c, "second", r["conversation"]).json()["reply"] == "Second answer."
    msgs = fake.sent[-1]["body"]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    closing = msgs[2]["content"]
    assert closing[0]["type"] == "tool_result" and closing[0]["tool_use_id"] == "tu_cut"
    assert closing[0]["is_error"] is True and closing[-1] == {"type": "text", "text": "second"}


def test_tool_bugs_and_absurd_inputs_are_error_results(env):
    c, fake = env
    login(c)
    fake.tool("explain_failure", {"failure_id": 2**70})
    fake.text("ok")
    assert say(c, "x").status_code == 200
    result = fake.sent[1]["body"]["messages"][2]["content"][0]
    assert result["is_error"] is True and "positive integer" in result["content"]
    fake.tool("create_request", {"name": "lammps", "source": "evil"}, tid="tu_2")
    fake.text("ok")
    r = say(c, "y").json()
    assert r["proposals"] == []


def test_parallel_calls_are_capped(env):
    c, fake = env
    login(c)
    calls = [{"type": "tool_use", "id": f"tu_{i}", "name": "search_catalog",
              "input": {"query": "gromacs"}} for i in range(chatbot.CALLS_PER_ROUND + 2)]
    fake.reply(*calls, stop="tool_use")
    fake.text("ok")
    assert len(say(c, "x").json()["tools"]) == chatbot.CALLS_PER_ROUND
    results = fake.sent[1]["body"]["messages"][2]["content"]
    assert len(results) == len(calls) and results[-1]["is_error"] is True


def test_long_conversations_must_start_over(env, monkeypatch):
    c, fake = env
    monkeypatch.setattr(chatbot, "CONV_LIMIT", 10)
    login(c)
    fake.text("A long enough answer.")
    cid = say(c, "first message").json()["conversation"]
    assert say(c, "more", cid).status_code == 409
    assert fake.sent[0]["body"]["cache_control"] == {"type": "ephemeral"}
