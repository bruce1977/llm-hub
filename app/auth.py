"""
API key authentication and security helpers.

Supported key transports:
  - Authorization: Bearer <key>
  - X-API-Key: <key>
  - ?api_key=<key>  (only when security.allow_query_api_key is enabled; disabled by default)

Multiple keys are supported and compared in constant time to avoid timing side channels.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
from collections.abc import Iterable

from fastapi import HTTPException, Request, status

logger = logging.getLogger("llm_hub")

# Routes always anonymous when auth.allow_anonymous_health is true.
HEALTH_PATHS = {"/", "/health", "/healthz"}
# OpenAPI docs routes (hidden on a public gateway unless security.allow_docs).
DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}

WWW_AUTHENTICATE = "Bearer"

# Placeholder / trivially guessable keys rejected at startup.
WEAK_KEY_PATTERNS = {
    "sk-change-me-001",
    "sk-change-me",
    "change-me",
    "changeme",
    "changeme123",
    "sk-gateway-admin-2026",
    "test",
    "demo",
    "password",
    "123456",
    "secret",
    "admin",
    "key",
    "token",
    "example",
    "letmein",
}


def extract_api_key(request: Request, allow_query: bool = False) -> str | None:
    """Extract the API key from the request headers (or optionally the query string)."""
    auth_header = request.headers.get("authorization")
    if auth_header:
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
        # also accept a bare key without the Bearer prefix
        if not scheme and auth_header.strip():
            return auth_header.strip()

    api_key = request.headers.get("x-api-key")
    if api_key and api_key.strip():
        return api_key.strip()

    if allow_query:
        query_key = request.query_params.get("api_key")
        if query_key:
            return query_key.strip()

    return None


def key_matches(candidate: str, valid_keys: Iterable[str]) -> bool:
    """Constant-time comparison across all configured keys."""
    matched = False
    for key in valid_keys:
        if hmac.compare_digest(candidate, key):
            matched = True
    return matched


def is_weak_key(key: str) -> str | None:
    """Return a human-readable reason if the key is too weak, otherwise None."""
    if not key:
        return "empty key"
    if len(key) < 16:
        return f"key is too short ({len(key)} < 16 characters)"
    low = key.lower()
    if low in WEAK_KEY_PATTERNS:
        return "placeholder / guessable key"
    if len(set(key)) < 4:
        return "key has very low entropy"
    return None


def find_weak_keys(keys: Iterable[str]) -> list[str]:
    """Return a list of 'key -> reason' warnings for weak keys."""
    problems: list[str] = []
    for k in keys:
        reason = is_weak_key(k)
        if reason:
            problems.append(f"{k!r}: {reason}")
    return problems


def client_ip(request: Request) -> str | None:
    """Best-effort client IP: prefer X-Forwarded-For, fall back to the TCP peer."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def ip_allowed(ip: str | None, allowed: Iterable[str]) -> bool:
    """Check a client IP against an allowlist (supports CIDR, e.g. 10.0.0.0/8)."""
    if not allowed:
        return True
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip in allowed
    for a in allowed:
        try:
            if addr in ipaddress.ip_network(a, strict=False):
                return True
        except ValueError:
            if ip == a:
                return True
    return False


async def _authorize(request: Request, cfg) -> str | None:
    """Core authorization: health paths may be anonymous; everything else needs a valid key.

    Also enforces an optional client IP allowlist before spending time on auth.
    """
    path = request.url.path.rstrip("/") or "/"

    # optional client IP allowlist (evaluated before auth)
    if not ip_allowed(client_ip(request), cfg.security.allowed_client_ips):
        logger.warning("Client IP not allowed: %s %s", client_ip(request), path)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client address is not permitted to access this gateway.",
        )

    if cfg.auth.allow_anonymous_health and path in HEALTH_PATHS:
        return None

    key = extract_api_key(request, allow_query=cfg.security.allow_query_api_key)
    if not key:
        logger.warning("Missing API key: %s %s", request.method, path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide it via 'Authorization: Bearer <key>' or 'X-API-Key: <key>' header.",
            headers={"WWW-Authenticate": WWW_AUTHENTICATE},
        )

    if not key_matches(key, cfg.auth.api_keys):
        logger.warning("Invalid API key: %s %s", request.method, path)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )

    return key


async def require_api_key(request: Request) -> str | None:
    """FastAPI dependency used by the catch-all gateway route."""
    manager = getattr(request.app.state, "manager", None)
    if manager is None:
        return "anonymous"
    cfg = manager.config
    if not cfg.auth.enabled:
        return "anonymous"
    return await _authorize(request, cfg)
