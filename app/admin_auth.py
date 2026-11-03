"""
Admin authentication for the (future) web management console.

A single admin password is supplied via the ``ADMIN_PASSWORD`` environment variable
-- there is no config.json entry for it. A successful login mints a short-lived,
HMAC-signed session cookie; protected admin routes depend on ``require_admin``. The
same password also works as a ``Bearer`` / ``X-API-Key`` token for headless/API use.

Design notes:
  * No external dependencies. The session token is ``<expiry>.<signature>`` where the
    signature is HMAC-SHA256(key=ADMIN_PASSWORD, msg=expiry). Sessions are therefore
    automatically invalidated whenever the password changes (no shared signing secret
    to rotate), at the cost of sessions not surviving a password change -- acceptable
    for a single-password management console.
  * The cookie is HttpOnly + SameSite=Lax and only marked Secure on HTTPS, so it works
    over plain HTTP during local development but is locked down in production.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Optional

from fastapi import Cookie, HTTPException, Request, Response, status

ADMIN_COOKIE_NAME = "llmhub_admin"
ADMIN_SESSION_TTL = 12 * 3600  # seconds (12h)


def get_admin_password() -> Optional[str]:
    """Return the configured admin password, or None when not set (admin disabled)."""
    pw = (os.getenv("ADMIN_PASSWORD") or "").strip()
    return pw or None


def _sign(msg: str, key: str) -> str:
    return hmac.new(key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def create_admin_token(key: str) -> str:
    expiry = str(int(time.time()) + ADMIN_SESSION_TTL)
    return f"{expiry}.{_sign(expiry, key)}"


def verify_admin_token(token: Optional[str], key: str) -> bool:
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


def _password_from_request(request: Request) -> Optional[str]:
    """Reuse the gateway's header extraction so the admin password can be passed as
    ``Authorization: Bearer <pw>`` or ``X-API-Key: <pw>`` (headless/API use)."""
    from auth import extract_api_key  # local import avoids a circular import at module load

    return extract_api_key(request)


async def require_admin(
    request: Request,
    admin_cookie: Optional[str] = Cookie(default=None, alias=ADMIN_COOKIE_NAME),
) -> None:
    """FastAPI dependency that gates admin routes.

    Accepts either the signed session cookie or the admin password presented as a
    Bearer / X-API-Key token. Raises 503 when admin is not configured (no
    ADMIN_PASSWORD), 401 when authentication is missing or invalid.
    """
    key = get_admin_password()
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin is not configured: set the ADMIN_PASSWORD environment variable.",
        )
    if verify_admin_token(admin_cookie, key):
        return
    presented = _password_from_request(request)
    if presented and hmac.compare_digest(presented, key):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Admin authentication required.",
        headers={"WWW-Authenticate": "Bearer"},
    )
