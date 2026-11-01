"""Live build logs over SSE: who may watch, scrubbing, resume, and the end of a stream."""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from apparadise.coordinator import log_path
from mock.demo import build_demo_app, demo_settings

H = {"X-Requested-With": "apparadise"}
LOG = ("==> Installing lammps\n"
       "cd /home/alice/stage && make\n"
       "token=s3cr3t\n"
       "half a line")


@pytest.fixture
def env(tmp_path):
    settings = demo_settings(port=8000, data_dir=str(tmp_path))
    app = build_demo_app(settings)
    app.state.sse_poll_seconds = 0.01
    app.state.sse_max_seconds = 0.2
    logging.disable(logging.INFO)
    with TestClient(app) as c:
        c.post("/api/login", json={"login": "alice", "password": "demo"}, headers=H)
        bid = c.post("/api/requests", json={"name": "lammps", "targets": ["rtx"]},
                     headers=H).json()["queued"][0]["id"]
        path = log_path(settings.data_dir, bid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(LOG)
        yield c, app.state.db, bid
    logging.disable(logging.NOTSET)
    app.state.db.close()


def events(c, url, headers=None):
    with c.stream("GET", url, headers=headers or {}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = r.read().decode()
    out = []
    for block in body.split("\n\n"):
        lines = block.splitlines()
        kind = next((x[7:] for x in lines if x.startswith("event: ")), None)
        if kind:
            out.append({"event": kind,
                        "id": next((x[4:] for x in lines if x.startswith("id: ")), None),
                        "data": "\n".join(x[6:] for x in lines if x.startswith("data: "))})
    return out


def finish(db, bid, status="failed"):
    with db.tx() as x:
        x.execute("UPDATE build SET status = ?, stage = 'build' WHERE id = ?", (status, bid))


def test_requester_gets_scrubbed_lines_then_end(env):
    c, db, bid = env
    finish(db, bid)
    ev = events(c, f"/api/builds/{bid}/events")
    assert ev[0]["event"] == "status" and json.loads(ev[0]["data"])["status"] == "failed"
    text = "\n".join(e["data"] for e in ev if e["event"] == "log")
    assert "alice" not in text and "s3cr3t" not in text and "Installing lammps" in text
    assert text.endswith("half a line")  # the unterminated tail is flushed at the end
    assert ev[-1] == {"event": "end", "id": None, "data": "failed"}


def test_running_build_holds_back_partial_line_and_times_out(env):
    c, db, bid = env
    ev = events(c, f"/api/builds/{bid}/events")
    logs = [e for e in ev if e["event"] == "log"]
    assert logs and "half a line" not in logs[-1]["data"]
    assert int(logs[-1]["id"]) == len(LOG) - len("half a line")
    assert ev[-1]["event"] != "end"  # the stream just ends; EventSource reconnects


def test_resume_from_last_event_id(env):
    c, db, bid = env
    finish(db, bid)
    offset = len(LOG) - len("half a line")
    ev = events(c, f"/api/builds/{bid}/events", {"Last-Event-ID": str(offset)})
    assert [e["data"] for e in ev if e["event"] == "log"] == ["half a line"]


def test_sysadmin_sees_raw_log(env):
    c, db, bid = env
    finish(db, bid)
    c.post("/api/login", json={"login": "dana", "password": "demo"}, headers=H)
    text = "\n".join(e["data"] for e in events(c, f"/api/builds/{bid}/events")
                     if e["event"] == "log")
    assert "/home/alice/stage" in text


@pytest.mark.parametrize("who", [None, "bob"])
def test_everyone_gets_the_scrubbed_log(env, who):
    # Q26: everyone can watch a public build, scrubbed; unknown builds are 404
    c, db, bid = env
    c.cookies.clear()
    if who:
        c.post("/api/login", json={"login": who, "password": "demo"}, headers=H)
    text = "\n".join(e["data"] for e in events(c, f"/api/builds/{bid}/events")
                     if e["event"] == "log")
    assert text and "/home/alice/stage" not in text
    assert c.get("/api/builds/99999/events").status_code == 404


def test_restricted_build_is_sysadmin_only_even_for_its_requester(env):
    c, db, bid = env
    with db.tx() as x:
        x.execute("UPDATE build SET restricted = 1 WHERE id = ?", (bid,))
    assert c.get(f"/api/builds/{bid}/events").status_code == 404
