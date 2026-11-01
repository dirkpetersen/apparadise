"""Zot's token service: GET/POST /registry/token (the Docker registry token protocol).

Zot (docs/operations/registry.md) stores the images and trusts only tokens signed with this
app's key. Every OCI client (podman, docker, Enroot/Pyxis, skopeo) asks here for a token that
names exactly which repositories it may pull or push. Who gets what:

    anonymous            pull  public/<name>
    signed-in AD user    pull  public/<name>
    sysadmin (RCO-Admins) pull+push public/*, restricted/*, cache/*; pull nvcr/*; the catalog
    build job (job-<id>) pull+push its own image, cache/test-<package>, its build cache;
                         pull the base images, nvcr/* and public/*; only while it runs

Zot matches a token's grants to the exact repository name, so a job token is good for one
image repository, not a whole project. Passwords go through the same AD sign-in and throttle
as the login page. This route sits outside /api (no CSRF header: OCI clients cannot send one)
and is audited for every credentialed request.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qs

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from . import coordinator
from .auth import PasswordSignInError, password_sign_in
from .db import Database, get_db

log = logging.getLogger(__name__)
router = APIRouter(tags=["registry"])  # mounted at /registry in main.create_app (not /api)

SIGNING_KEY = "registry-signing.pem"
VERIFY_KEY = "registry-verify.pem"
# OCI distribution repository names; "" is the catalog (`repository::pull`).
_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
NAME_RE = re.compile(rf"^{_COMPONENT}(?:/{_COMPONENT})*$")
JOB_RE = re.compile(r"^job-([0-9]{1,12})$")
MAX_SCOPES = 20
ACTIONS = ("pull", "push")


# -- signing key ---------------------------------------------------------------------------


def load_key(data_dir: str) -> ec.EllipticCurvePrivateKey:
    """The ES256 signing key in DATA_DIR, created on first use (mode 0600). The public half
    is written next to it as registry-verify.pem for Zot's `http.auth.bearer.cert`."""
    d = Path(data_dir)
    key_path, pub_path = d / SIGNING_KEY, d / VERIFY_KEY
    if key_path.exists():
        key = serialization.load_pem_private_key(key_path.read_bytes(), None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError(f"{key_path} is not an EC private key")
    else:
        d.mkdir(parents=True, exist_ok=True)
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        tmp = key_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(pem)
        tmp.replace(key_path)
        log.info("created the registry token signing key")
    pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                        serialization.PublicFormat.SubjectPublicKeyInfo)
    if not pub_path.exists() or pub_path.read_bytes() != pub:
        pub_path.write_bytes(pub)
    return key


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign(key: ec.EllipticCurvePrivateKey, claims: dict[str, Any]) -> str:
    """A compact ES256 JWT."""
    head = _b64(json.dumps({"alg": "ES256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"{head}.{body}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


# -- who may do what -----------------------------------------------------------------------


def parse_scopes(raw: list[str]) -> list[tuple[str, str, list[str]]]:
    """`repository:<name>:<a,b>` entries (space-separated within one parameter, or
    repeated) -> [(type, name, actions)]. Malformed entries are dropped."""
    out = []
    for part in " ".join(raw).split():
        typ, _, rest = part.partition(":")
        name, _, acts = rest.rpartition(":")
        if typ != "repository" or not (name == "" or NAME_RE.match(name)) or len(name) > 255:
            continue
        out.append((typ, name, [a for a in acts.split(",") if a]))
        if len(out) >= MAX_SCOPES:
            break
    return out


def grant(principal: dict[str, Any], name: str, actions: list[str]) -> list[str]:
    """The subset of `actions` (pull/push) `principal` gets on repository `name`."""
    wanted = [a for a in ACTIONS if a in actions]
    kind = principal["kind"]
    project = name.split("/", 1)[0]
    if name == "":  # the catalog
        return ["pull"] if kind == "admin" and "pull" in wanted else []
    if kind == "admin":
        if project in ("public", "restricted", "cache"):
            return wanted
        return [a for a in wanted if a == "pull"] if project == "nvcr" else []
    if kind == "job":
        if name in principal["own"]:
            return wanted
        if name in principal["pull"] or project == "nvcr":
            return [a for a in wanted if a == "pull"]
        # otherwise what anyone gets: the client sends the job login for every request
    # anonymous and signed-in users
    return [a for a in wanted if a == "pull"] if project == "public" and "/" in name else []


def _job(db: Database, job_id: int, secret: str, hours: int) -> dict[str, Any] | None:
    row = db.query_one(
        "SELECT b.id, b.status, b.started_at, b.restricted, b.source, b.target, "
        "b.registry_secret, p.name AS package FROM build b JOIN package p "
        "ON p.id = b.package_id WHERE b.id = ?", (job_id,))
    if row is None or row["status"] != "running" or not row["registry_secret"]:
        return None
    if not hmac.compare_digest(row["registry_secret"], coordinator.registry_secret_hash(secret)):
        return None
    started = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
    if datetime.now(UTC) - started > timedelta(hours=hours):
        return None
    own, pull = coordinator.job_repositories(row, row["package"])
    return {"kind": "job", "name": f"job-{row['id']}", "own": own, "pull": pull}


def _basic(request: Request) -> tuple[str, str] | None:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("basic "):
        return None
    try:
        user, sep, pw = base64.b64decode(h[6:], validate=True).decode().partition(":")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(401, "Bad credentials.") from None
    if not sep:
        raise HTTPException(401, "Bad credentials.")
    return user, pw


async def _principal(request: Request, db: Database, creds: tuple[str, str] | None
                     ) -> dict[str, Any]:
    if creds is None:
        return {"kind": "anonymous", "name": "anonymous"}
    login, password = creds
    settings = request.app.state.settings
    m = JOB_RE.match(login)
    if m:
        job = await db.run(_job, db, int(m.group(1)), password, settings.registry_job_hours)
        if job is None:
            await db.run(db.audit.add, login, "registry.token.denied", "build", m.group(1), {})
            raise HTTPException(401, "Invalid or expired job credential.")
        return job
    try:
        user = await password_sign_in(request, db, login, password)
    except PasswordSignInError as e:
        raise HTTPException(e.status, e.message, headers=e.headers) from None
    return {"kind": "admin" if user.is_admin else "user", "name": user.username}


async def _issue(request: Request, db: Database, creds, service: str, scopes: list[str]):
    settings = request.app.state.settings
    if service and service != settings.registry_service:
        raise HTTPException(400, "Unknown service.")
    principal = await _principal(request, db, creds)
    access = []
    for typ, name, actions in parse_scopes(scopes):
        ok = grant(principal, name, actions)
        if ok:
            access.append({"type": typ, "name": name, "actions": ok})
    now = int(time.time())
    ttl = settings.registry_token_minutes * 60
    key = request.app.state.registry_key
    if key is None:
        key = request.app.state.registry_key = load_key(settings.data_dir)
    token = sign(key, {"iss": "apparadise", "sub": principal["name"],
                       "aud": settings.registry_service, "iat": now, "nbf": now,
                       "exp": now + ttl, "jti": secrets.token_hex(12), "access": access})
    if principal["kind"] != "anonymous":
        await db.run(db.audit.add, principal["name"], "registry.token", "registry", "",
                     {"access": [f"{a['name']}:{','.join(a['actions'])}" for a in access]})
    issued = datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return JSONResponse({"token": token, "access_token": token, "expires_in": ttl,
                         "issued_at": issued}, headers={"Cache-Control": "no-store"})


@router.get("/token")
async def token_get(request: Request, db: Annotated[Database, Depends(get_db)]):
    q = request.query_params
    return await _issue(request, db, _basic(request), q.get("service", ""),
                        q.getlist("scope"))


@router.post("/token")
async def token_post(request: Request, db: Annotated[Database, Depends(get_db)]):
    """The OAuth2 form flow docker/containerd use when they hold credentials."""
    body = await request.body()
    if len(body) > 16384:
        raise HTTPException(413, "Request too large.")
    form = parse_qs(body.decode(errors="replace"))
    one = lambda k: (form.get(k) or [""])[0]  # noqa: E731
    if one("grant_type") not in ("password", ""):
        # refresh_token is not offered: clients fall back to the password grant
        raise HTTPException(400, "Unsupported grant_type.")
    creds = (one("username"), one("password")) if one("username") else _basic(request)
    return await _issue(request, db, creds, one("service"), form.get("scope", []))
