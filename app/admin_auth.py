"""
Admin authentication for the web management console.

A single admin password is supplied via the ``ADMIN_PASSWORD`` environment variable
-- there is no config.yaml entry for it. The password is only accepted by the login
endpoint: a successful login mints a short-lived, HMAC-signed session cookie, and
protected admin routes depend on ``require_admin``.

``require_admin`` accepts exactly one credential: the signed session cookie minted by
the login endpoint. It deliberately does **not** accept a gateway API key. Those keys
are handed to every caller of the gateway; accepting one on the management API would
give each caller the ability to read the full config (including upstream credentials),
export everybody's request bodies, rewrite upstreams and run the server-side testers.
The admin password itself is not a request credential either.

``require_admin_session`` is the equivalent cookie-only dependency used by the key
mint/revoke routes; both now resolve to the same rule, kept as two names so the
intent stays visible at each call site.

Design notes:
  * No external dependencies and no server-side session table. The token is
    ``<sid>.<expiry>.<signature>``. ``sid`` is a fresh random session id minted at
    login, and the signing key is ``HMAC-SHA256(key=process_seed, msg=password + sid)``
    where ``process_seed`` is 32 random bytes generated at import time and never
    leaves the process. The cookie therefore carries no secret: a stolen cookie
    cannot be used to brute-force ADMIN_PASSWORD offline, and every session has its
    own signing key. Changing the password OR restarting the process invalidates all
    outstanding sessions (the seed is deliberately not persisted).
  * The cookie is HttpOnly + SameSite=Lax and only marked Secure on HTTPS, so it works
    over plain HTTP during local development but is locked down in production.
  * The compatibility helper ``_gateway_key_authorized`` only checks the SQLite key
    store and is NEVER an admin credential: management routes require the signed
    console cookie, so an anonymous internal zone cannot open the management API.
  * Session lifetime is ``ADMIN_SESSION_TTL`` seconds (default 12h, clamped to
    5 min .. 7 days). A successful request past half the TTL re-issues the cookie
    with a fresh session id (sliding renewal), so an active session does not
    hard-expire. Logout only clears the browser cookie - there is no server-side
    session table, so a copied token stays valid until its expiry, an
    ADMIN_PASSWORD change, or a process restart (which rotates the seed).
  * A valid console session cookie is also accepted on business API routes
    (see ``auth.has_admin_session``), so the web UI can call the gateway directly.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import time

from auth import extract_api_key
from fastapi import Cookie, HTTPException, Request, Response, status
from key_store import get_store

logger = logging.getLogger("llm_hub")

ADMIN_COOKIE_NAME = "llmhub_admin"
ADMIN_USERNAME = "admin"
#: Default session lifetime (12h). Override with ADMIN_SESSION_TTL (seconds).
ADMIN_SESSION_TTL = 12 * 3600
_MIN_SESSION_TTL = 300
_MAX_SESSION_TTL = 7 * 24 * 3600


def session_ttl() -> int:
    """Session lifetime in seconds; ADMIN_SESSION_TTL is clamped to 5 min .. 7 days."""
    raw = (os.getenv("ADMIN_SESSION_TTL") or "").strip()
    if not raw:
        return ADMIN_SESSION_TTL
    try:
        return max(_MIN_SESSION_TTL, min(_MAX_SESSION_TTL, int(raw)))
    except ValueError:
        logger.warning("Ignoring invalid ADMIN_SESSION_TTL=%r; using %ds", raw, ADMIN_SESSION_TTL)
        return ADMIN_SESSION_TTL


def get_admin_password() -> str | None:
    """Return the configured admin password, or None when not set (admin disabled)."""
    pw = (os.getenv("ADMIN_PASSWORD") or "").strip()
    return pw or None


#: Process-scoped random seed. It is never written to a cookie, a config file or a
#: log; rotating it (a process restart) invalidates every outstanding session.
_SESSION_SEED = secrets.token_bytes(32)
_SID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def _sign(msg: str, key: str) -> str:
    return hmac.new(key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def _session_key(password: str, sid: str) -> str:
    """Per-session signing key: the admin password plus a session-specific salt.

    The salt is derived from a secret process seed, so knowing a cookie (which
    exposes ``sid`` but not the seed) is not enough to verify a password guess.
    """
    return hmac.new(_SESSION_SEED, f"{password}\x00{sid}".encode("utf-8"), hashlib.sha256).hexdigest()


def _mint(expiry: int, key: str, sid: str | None = None) -> str:
    """Sign one session. ``sid`` is injectable so tests can mint aged tokens."""
    sid = sid or secrets.token_urlsafe(16)
    payload = f"{sid}.{expiry}"
    return f"{payload}.{_sign(payload, _session_key(key, sid))}"


def create_admin_token(key: str) -> str:
    return _mint(int(time.time()) + session_ttl(), key)


def session_remaining(token: str | None, key: str) -> int | None:
    """Seconds left on a valid token, or None when forged / malformed / expired."""
    if not token or token.count(".") != 2:
        return None
    sid, expiry_str, sig = token.split(".")
    if not _SID_RE.fullmatch(sid):
        return None
    try:
        expiry = int(expiry_str)
    except ValueError:
        return None
    expected = _sign(f"{sid}.{expiry_str}", _session_key(key, sid))
    if not hmac.compare_digest(expected, sig):
        return None
    remaining = expiry - int(time.time())
    return remaining if remaining > 0 else None


def verify_admin_token(token: str | None, key: str) -> bool:
    return session_remaining(token, key) is not None


def refresh_due(token: str | None, key: str) -> bool:
    """Sliding renewal: extend a valid session once it is past half its lifetime."""
    remaining = session_remaining(token, key)
    return remaining is not None and remaining < session_ttl() // 2


def set_admin_cookie(response: Response, request: Request, token: str) -> None:
    """Single place for the cookie flags, shared by login and sliding renewal."""
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=session_ttl(),
        # A TLS-terminating reverse proxy speaks plain HTTP to uvicorn, so the URL
        # scheme alone would leave the cookie without Secure. X-Forwarded-Proto is
        # safe to trust *for this direction only*: forging it can only make the
        # browser stricter, never looser.
        secure=request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https",
    )


def _gateway_key_authorized(request: Request) -> bool:
    """Retained for compatibility with callers that imported it; NOT an admin credential.

    A valid caller key is still a valid *caller* credential on business APIs (handled by
    ``auth.require_api_key``), but the management API must never treat it as an admin
    session: every caller would otherwise be an administrator.
    """
    store = get_store()
    if store is None or store.count() == 0:
        return False
    candidate = extract_api_key(request)
    return bool(candidate) and store.verify(candidate)


async def require_admin(
    request: Request,
    admin_cookie: str | None = Cookie(default=None, alias=ADMIN_COOKIE_NAME),
) -> None:
    """FastAPI dependency that gates admin routes: console session cookie only.

    Raises 503 when admin is not configured (no ADMIN_PASSWORD), 401 when the cookie
    is missing, forged or expired. A gateway API key is intentionally *not* accepted.
    """
    key = get_admin_password()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin is not configured: set the ADMIN_PASSWORD environment variable.",
        )
    if verify_admin_token(admin_cookie, key):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Console sign-in required: management endpoints accept a session cookie only.",
    )


async def require_admin_session(
    admin_cookie: str | None = Cookie(default=None, alias=ADMIN_COOKIE_NAME),
) -> None:
    """FastAPI dependency for the routes that mint or revoke API keys.

    Only the signed session cookie counts here: a gateway API key is what any client already
    holds, so accepting one would let that client issue itself more access. A request that
    arrives with a key but no console session is told to sign in.
    """
    key = get_admin_password()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin is not configured: set the ADMIN_PASSWORD environment variable.",
        )
    if verify_admin_token(admin_cookie, key):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Console sign-in required: this endpoint creates or revokes credentials.",
    )
