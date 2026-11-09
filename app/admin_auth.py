"""
Admin authentication for the web management console.

A single admin password is supplied via the ``ADMIN_PASSWORD`` environment variable
-- there is no config.yaml entry for it. The password is only accepted by the login
endpoint: a successful login mints a short-lived, HMAC-signed session cookie, and
protected admin routes depend on ``require_admin``.

``require_admin`` accepts two credentials:
  * the signed session cookie (what the console uses -- it never sees the password);
  * a gateway API key from the ``api_keys`` table, presented as ``Authorization: Bearer`` or
    ``X-API-Key`` (what scripts and headless calls use).
The admin password itself is never a request credential: sending it in those headers is
a 401, because it would defeat the point of a login page.

``require_admin_session`` is the cookie-only variant, used by the routes that mint or revoke
those keys: a client key is what an already-issued caller holds, so accepting one there would
let any client hand out more of itself.

Design notes:
  * No external dependencies. The session token is ``<expiry>.<signature>`` where the
    signature is HMAC-SHA256(key=ADMIN_PASSWORD, msg=expiry). Sessions are therefore
    automatically invalidated whenever the password changes (no shared signing secret
    to rotate), at the cost of sessions not surviving a password change -- acceptable
    for a single-password management console.
  * The cookie is HttpOnly + SameSite=Lax and only marked Secure on HTTPS, so it works
    over plain HTTP during local development but is locked down in production.
  * The API-key path is skipped when gateway auth is disabled or no key is configured,
    so ``auth.enabled: false`` cannot silently open the management API.
  * Session lifetime is ``ADMIN_SESSION_TTL`` seconds (default 12h, clamped to
    5 min .. 7 days). A successful request past half the TTL re-issues the cookie
    (sliding renewal), so an active session does not hard-expire. Logout only clears
    the browser cookie - there is no server-side session table, so a copied token
    stays valid until its expiry or an ADMIN_PASSWORD change.
  * A valid console session cookie is also accepted on business API routes
    (see ``auth.has_admin_session``), so the web UI can call the gateway directly.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
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


def _sign(msg: str, key: str) -> str:
    return hmac.new(key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def create_admin_token(key: str) -> str:
    expiry = str(int(time.time()) + session_ttl())
    return f"{expiry}.{_sign(expiry, key)}"


def session_remaining(token: str | None, key: str) -> int | None:
    """Seconds left on a valid token, or None when forged / malformed / expired."""
    if not token or "." not in token:
        return None
    expiry_str, sig = token.rsplit(".", 1)
    try:
        expiry = int(expiry_str)
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(expiry_str, key), sig):
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
        secure=request.url.scheme == "https",
    )


def _gateway_key_authorized(request: Request) -> bool:
    """True when the request carries a valid gateway API key.

    The query-string transport is deliberately excluded: a key in a URL ends up in
    access logs and browser history. With gateway auth disabled there are no keys to
    accept, so the management API stays cookie-only rather than becoming anonymous.
    """
    manager = getattr(request.app.state, "manager", None)
    cfg = getattr(manager, "config", None)
    store = get_store()
    if cfg is None or not cfg.auth.enabled or store is None or store.count() == 0:
        return False
    candidate = extract_api_key(request)
    return bool(candidate) and store.verify(candidate)


async def require_admin(
    request: Request,
    admin_cookie: str | None = Cookie(default=None, alias=ADMIN_COOKIE_NAME),
) -> None:
    """FastAPI dependency that gates admin routes.

    Accepts the signed session cookie minted by the login endpoint, or a gateway API key
    in ``Bearer`` / ``X-API-Key``. The admin password itself is not a request credential,
    so sending it in those headers does not match any key. Raises 503 when admin is not
    configured (no ADMIN_PASSWORD), 401 when neither credential is valid.
    """
    key = get_admin_password()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin is not configured: set the ADMIN_PASSWORD environment variable.",
        )
    if verify_admin_token(admin_cookie, key) or _gateway_key_authorized(request):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Admin authentication required: sign in, or send a gateway API key.",
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
