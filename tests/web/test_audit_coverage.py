"""Every state-changing route is listed here with the audit action it writes.

A new POST/PUT/PATCH/DELETE route that is missing from ROUTES fails this test: decide
what it audits (scheme `area.verb[.detail]`) and add it, or list it under NOT_AUDITED
with the reason.
"""

from apparadise.config import Settings
from apparadise.main import create_app

from .conftest import REQUIRED

ROUTES = {
    ("POST", "/api/login"): "login.success",
    ("POST", "/api/logout"): "logout",
    ("POST", "/api/builder/register"): "builder.register",
    ("POST", "/api/builder/claim"): "build.claim",
    ("POST", "/api/builder/jobs/{build_id}/report"): "build.report",
    ("POST", "/api/builder/deliveries/{build_id}"): "build.deliver",
    ("POST", "/api/admin/builder-tokens"): "builder.token.create",
    ("POST", "/api/admin/builders/{builder_id}/disable"): "builder.disable",
    ("POST", "/api/admin/builds"): "build.queue",
    ("PUT", "/api/votes/{subject}/{key}"): "vote.add",
    ("DELETE", "/api/votes/{subject}/{key}"): "vote.remove",
    ("POST", "/api/requests"): "request.create",
    ("PUT", "/api/admin/policy"): "policy.update",
}
NOT_AUDITED = {
    # High-volume log streaming; the build row and the log file are the record.
    ("POST", "/api/builder/jobs/{build_id}/log"),
}
SOURCES = ["auth.py", "builder_api.py", "votes.py", "requests_api.py"]


def mutating_routes(tmp_path):
    s = Settings(_env_file=None, data_dir=str(tmp_path), catalog_dir=str(tmp_path), **REQUIRED)
    out = set()
    for path, ops in create_app(s).openapi()["paths"].items():
        for method in ops:
            if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                out.add((method.upper(), path))
    return out


def test_every_mutating_route_is_classified(tmp_path):
    routes = mutating_routes(tmp_path)
    assert routes == set(ROUTES) | NOT_AUDITED


def test_actions_follow_scheme_and_exist_in_code():
    import re
    from pathlib import Path

    pkg = Path(__file__).resolve().parents[2] / "web" / "apparadise"
    code = "".join((pkg / f).read_text() for f in SOURCES)
    for action in ROUTES.values():
        assert re.fullmatch(r"[a-z_]+(\.[a-z_]+){0,2}", action), action
        assert f'"{action}"' in code, action
