"""
Admin authentication for the web management console.

A single admin password is supplied via the ``ADMIN_PASSWORD`` environment variable
-- there is no config.json entry for it. The password is only accepted by the login
endpoint: a successful login mints a short-lived, HMAC-signed session cookie, and
protected admin routes depend on ``require_admin``.

``require_admin`` accepts two credentials:
  * the signed session cookie (what the console uses -- it never sees the password);
  * a gateway API key from ``auth.api_keys``, presented as ``Authorization: Bearer`` or
    ``X-API-Key`` (what scripts and headless calls use).
The admin password itself is never a request credential: sending it in those headers is
a 401, because it would defeat the point of a login page.

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
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

from auth import extract_api_key, key_matches
from fastapi import Cookie, HTTPException, Request, status

ADMIN_COOKIE_NAME = "llmhub_admin"
ADMIN_USERNAME = "admin"
ADMIN_SESSION_TTL = 12 * 3600  # seconds (12h)


def get_admin_password() -> str | None:
    """Return the configured admin password, or None when not set (admin disabled)."""
    pw = (os.getenv("ADMIN_PASSWORD") or "").strip()
    return pw or None


def _sign(msg: str, key: str) -> str:
    return hmac.new(key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def create_admin_token(key: str) -> str:
    expiry = str(int(time.time()) + ADMIN_SESSION_TTL)
    return f"{expiry}.{_sign(expiry, key)}"


def verify_admin_token(token: str | None, key: str) -> bool:
    if not token or "." not in token:
        return False
    expiry_str, sig = token.rsplit(".", 1)
    try:
        expiry = int(expiry_str)
    except ValueError:
        return False
    if not hmac.compare_digest(_sign(expiry_str, key), sig):
        return False
    return expiry > int(time.time())


def _gateway_key_authorized(request: Request) -> bool:
    """True when the request carries a valid gateway API key.

    The query-string transport is deliberately excluded: a key in a URL ends up in
    access logs and browser history. With gateway auth disabled there are no keys to
    accept, so the management API stays cookie-only rather than becoming anonymous.
    """
    manager = getattr(request.app.state, "manager", None)
    cfg = getattr(manager, "config", None)
    if cfg is None or not cfg.auth.enabled or not cfg.auth.api_keys:
        return False
    candidate = extract_api_key(request)
    return bool(candidate) and key_matches(candidate, cfg.auth.api_keys)


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
