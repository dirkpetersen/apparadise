"""A fake Harbor admin API (robots only), for tests and the demo. Never imported by the app."""

from __future__ import annotations

import secrets

from fastapi import FastAPI, HTTPException, Request


def create_app(username: str = "admin", password: str = "harbor") -> FastAPI:
    app = FastAPI()
    app.state.robots = {}
    app.state.fail = None  # set to a status code to make every call fail

    def check(request: Request) -> None:
        import base64

        if app.state.fail:
            raise HTTPException(app.state.fail, "forced failure")
        auth = request.headers.get("authorization", "")
        want = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        if auth != want:
            raise HTTPException(401, "unauthorized")

    @app.post("/api/v2.0/robots", status_code=201)
    async def create(request: Request):
        check(request)
        body = await request.json()
        rid = len(app.state.robots) + 1
        robot = {"id": rid, "name": f"robot${body['name']}", "secret": secrets.token_urlsafe(16),
                 "body": body}
        app.state.robots[rid] = robot
        return {k: robot[k] for k in ("id", "name", "secret")}

    @app.delete("/api/v2.0/robots/{rid}")
    async def delete(rid: int, request: Request):
        check(request)
        if app.state.robots.pop(rid, None) is None:
            raise HTTPException(404, "not found")
        return {}

    return app
