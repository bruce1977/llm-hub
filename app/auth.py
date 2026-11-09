"""
API key authentication and security helpers.

Supported key transports:
  - Authorization: Bearer <key>
  - X-API-Key: <key>
  - ?api_key=<key>  (only when security.allow_query_api_key is enabled; disabled by default)

Multiple keys are supported. They live in the ``api_keys`` table of ``llmhub.db`` as SHA-256
hashes (see ``key_store.py``) and are verified through that store's in-memory set, so no client
request touches sqlite; the environment only seeds an empty table on first start.

`auth.allow_access_ip_whitelist` (regexes) and `auth.trusted_networks` (CIDR blocks) define
the trusted network. Callers on it are exempt from the key check - even with an invalid key -
and may open /docs; `trusted_peer()` is the single judgement used by both. It is matched
against the TCP peer, never X-Forwarded-For. Declaring `trusted_networks` also turns
`auth.enabled: false` into an internal/outside split: external callers still need a key.
A valid console session cookie is a credential in its own right.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Iterable

from fastapi import HTTPException, Request, status
from key_store import get_store

logger = logging.getLogger("llm_hub")

# Liveness / system-info probes, anonymous by default for container health checks
# and load balancers. These are the ONLY paths an untrusted caller may reach
# without a key; every business API (including /v1/models) needs one. Set
# auth.allow_anonymous_health=false to require a key here too.
PUBLIC_PATHS = {"/", "/health"}
# OpenAPI docs routes: served only to auth.allow_access_ip_whitelist addresses.
# Served locally (vendored assets) so they work without any public CDN.
DOCS_PATHS = {"/docs", "/openapi.json"}

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


def peer_ip(request: Request) -> str | None:
    """The ASGI client address, used by `auth.allow_access_ip_whitelist`.

    Our code never reads X-Forwarded-For for this decision, because a caller can put any
    address in that header. Note that uvicorn's ProxyHeadersMiddleware may already have
    rewritten `request.client` from forwarded headers when the peer is a trusted proxy
    (`--forwarded-allow-ips`, default 127.0.0.1); a direct connection is always the TCP peer.
    """
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


def ip_pattern_allowed(ip: str | None, patterns: Iterable[str]) -> bool:
    """True when the address fully matches one of the regexes (empty list = nobody)."""
    if not patterns or not ip:
        return False
    for pattern in patterns:
        try:
            if re.fullmatch(pattern, ip):
                return True
        except re.error as exc:
            logger.warning("Ignoring malformed auth.allow_access_ip_whitelist pattern %r: %s", pattern, exc)
    return False


def trusted_peer(request: Request, cfg) -> bool:
    """Single source of truth for "this caller is on the trusted network".

    The `/docs` gate and the anonymous API-key bypass both use this, so they can
    never disagree. `allow_access_ip_whitelist` keeps its regex semantics;
    `auth.trusted_networks` adds CIDR blocks for the internal/outside split.
    Only the ASGI client address is consulted - `X-Forwarded-For` is forgeable.
    """
    if cfg is None:
        return False
    peer = peer_ip(request)
    if ip_pattern_allowed(peer, cfg.auth.allow_access_ip_whitelist):
        return True
    networks = cfg.auth.trusted_networks
    return bool(networks) and ip_allowed(peer, networks)


def docs_allowed(request: Request, cfg) -> bool:
    """`/docs`, `/openapi.json` and the Swagger assets: trusted network only."""
    return trusted_peer(request, cfg)


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

    # A trusted-network address is exempt from the key check on EVERY path, even
    # when the caller sends an invalid key. The /docs gate reads the same
    # judgement, so a hidden /docs can never coexist with a key-free API.
    # Empty whitelist + empty trusted_networks means nobody is exempt.
    if trusted_peer(request, cfg):
        return None

    # The only anonymous paths for untrusted callers are the liveness / info
    # probes used by the container HEALTHCHECK and load balancers. Every
    # business API needs a key; auth.allow_anonymous_health=false closes these too.
    if cfg.auth.allow_anonymous_health and path in PUBLIC_PATHS:
        return None

    key = extract_api_key(request, allow_query=cfg.security.allow_query_api_key)
    if not key:
        logger.warning("Missing API key: %s %s", request.method, path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide it via 'Authorization: Bearer <key>' or 'X-API-Key: <key>' header.",
            headers={"WWW-Authenticate": WWW_AUTHENTICATE},
        )

    store = get_store()
    if store is None or not store.verify(key):
        logger.warning("Invalid API key: %s %s", request.method, path)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )

    return key


def has_admin_session(request: Request) -> bool:
    """A valid, unexpired console session cookie is a credential for API calls.

    Local import: admin_auth imports extract_api_key from this module, so a
    module-level import here would be circular.
    """
    from admin_auth import ADMIN_COOKIE_NAME, get_admin_password, verify_admin_token

    password = get_admin_password()
    if not password:
        return False
    return verify_admin_token(request.cookies.get(ADMIN_COOKIE_NAME), password)


async def require_api_key(request: Request) -> str | None:
    """FastAPI dependency used by every business endpoint.

    Credential priority: a signed console session, then the trusted-network
    bypass, then a valid caller API key. An untrusted caller with neither gets
    401 (no key) or 403 (invalid key), including on `/`.
    """
    manager = getattr(request.app.state, "manager", None)
    if manager is None:
        return "anonymous"
    if has_admin_session(request):
        return "admin-session"
    cfg = manager.config
    if not cfg.auth.enabled:
        # trusted_networks turns "auth off" into an explicit internal/outside
        # split: only those networks stay anonymous. Without it keep the legacy
        # behaviour (all anonymous) - the startup self-check warns loudly.
        if cfg.auth.trusted_networks and not trusted_peer(request, cfg):
            return await _authorize(request, cfg)
        return "anonymous"
    return await _authorize(request, cfg)
