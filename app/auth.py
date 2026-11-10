"""API key authentication and security helpers.

Supported key transports:
  - Authorization: Bearer <key>
  - X-API-Key: <key>
  - ?api_key=<key>  (only when security.allow_query_api_key is enabled; disabled by default)

Multiple keys are supported. They live in the ``api_keys`` table of ``llmhub.db`` as
SHA-256 hashes (see ``key_store.py``) and are verified through that store's in-memory
set, so no client request touches sqlite. Keys are minted in the console; nothing seeds
them from the environment or from config.yaml.

Trust zones are decided by ONE anchor: the kernel-assigned local listening socket
(``scope["server"]``), which no request header can forge.

* ``internal`` - the ``server.port`` listener, reachable only from the LAN. Normal
  APIs are key-free when ``security.internal.allow_anonymous`` is true (the default);
  ``/`` and ``/health`` are always anonymous probes here so the container
  HEALTHCHECK works. ``/docs``, ``/admin`` and the cookie-only management API are
  reachable. Publish this port on the LAN only.
* ``public`` - the ``security.public.port`` listener. EVERY request here needs a
  valid caller key or signed console session; ``bridge.py`` has already reduced the
  visible surface to the method+path pairs in ``security.public.path_allowlist``.
  There is no switch that turns this requirement off.
* ``security.public.port == server.port`` makes a public-only instance: the single
  socket is public, so there is no anonymous probe at all (use a TCP port check).

``X-Forwarded-For`` is NEVER a trust anchor. uvicorn's ProxyHeadersMiddleware
rewrites ``scope["client"]`` from that header when the peer is in
``--forwarded-allow-ips`` (127.0.0.1 by default), so ``serve.py`` starts uvicorn with
``proxy_headers=False``. Application-level XFF parsing in :func:`client_ip` is used
only for declared ``security.trusted_proxies``, for IP fencing, rate-limit buckets
and audit rows - never to decide which zone a request is in.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Iterable

from fastapi import HTTPException, Request, status
from key_store import get_store, mask_key

logger = logging.getLogger("llm_hub")

# Anonymous liveness/info probes on the INTERNAL listener only. Kept independent of
# `internal.allow_anonymous` so a key-required LAN gateway still answers its own
# container HEALTHCHECK. The public zone never reaches this bypass: bridge.py's
# config allowlist decides what exists there, and /health is not listed by default.
PROBE_PATHS = {"/", "/health"}

# OpenAPI docs routes: served locally (vendored assets), internal zone only.
DOCS_PATHS = {"/docs", "/openapi.json"}

WWW_AUTHENTICATE = "Bearer"

#: Headers that mark a request as having traversed a proxy. Their presence alone is
#: never proof of a trusted proxy - that requires the TCP peer to be allowlisted.
PROXY_HEADERS = (
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-forwarded-host",
    "x-forwarded-port",
    "x-real-ip",
    "forwarded",
)

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
    # Values copied verbatim out of .env.example / config templates. They are long
    # enough to pass the length/entropy checks above, so a fresh deployment that
    # never opens the template would otherwise start with a publicly documented
    # password and zero warnings.
    "example-admin-password",
    "sk-gateway-example-key",
    "sk-gateway-example-key-2",
}

#: Substrings that mark a value as a template placeholder rather than a real secret.
EXAMPLE_MARKERS = ("example", "changeme", "change-me", "your-", "replace-me", "demo")


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
    if any(marker in low for marker in EXAMPLE_MARKERS):
        return "example/template placeholder"
    if len(set(key)) < 4:
        return "key has very low entropy"
    return None


def find_weak_keys(keys: Iterable[str]) -> list[str]:
    """Return a list of 'mask -> reason' warnings. Never put a secret in the log."""
    problems: list[str] = []
    for k in keys:
        reason = is_weak_key(k)
        if reason:
            problems.append(f"{mask_key(k)}: {reason}")
    return problems


# --------------------------------------------------------------------------- #
# Zone classification
# --------------------------------------------------------------------------- #
def _config(request: Request):
    manager = getattr(request.app.state, "manager", None)
    return getattr(manager, "config", None)


def local_port(request: Request) -> int | None:
    """The local listening socket this connection arrived on.

    uvicorn fills ``scope["server"]`` from the accepted socket, so no header can
    influence it. Mapping two Docker host ports to one container port does NOT
    create two zones - the container sees a single socket.
    """
    server = request.scope.get("server")
    if not isinstance(server, (tuple, list)) or len(server) < 2:
        return None
    try:
        return int(server[1])
    except (TypeError, ValueError):
        return None


def zone(request: Request) -> str:
    """Classify the listener by its kernel-assigned local port.

    Returns ``"internal"`` (the trusted ``server.port`` listener) or ``"public"``
    (``security.public.port``, including the public-only single-socket case). The
    public port comes from ``config.yaml``, never from an environment variable.
    A request with no socket information fails closed as ``"public"``.
    """
    cfg = _config(request)
    public_port = getattr(getattr(cfg, "security", None), "public", None)
    public_port = getattr(public_port, "port", None) if public_port is not None else None
    local = local_port(request)
    if local is None:
        return "public"
    if public_port is not None and local == public_port:
        return "public"
    return "internal"


def is_public(request: Request) -> bool:
    """True when the connection landed on the internet-facing listener."""
    return zone(request) == "public"


def is_bridge(request: Request) -> bool:
    """Backwards-compatible name for :func:`is_public`."""
    return is_public(request)


def has_proxy_headers(request: Request) -> bool:
    return any(request.headers.get(name) is not None for name in PROXY_HEADERS)


def _trusted_proxy_cidrs(request: Request) -> list[str]:
    cfg = _config(request)
    return list(getattr(getattr(cfg, "security", None), "trusted_proxies", None) or [])


def _xff_chain(request: Request) -> list[str]:
    chain: list[str] = []
    if raw := request.headers.get("x-forwarded-for"):
        chain.extend(part.strip() for part in raw.split(",") if part.strip())
    if real := request.headers.get("x-real-ip"):
        if real.strip():
            chain.append(real.strip())
    return chain


def client_ip(request: Request) -> str | None:
    """Caller address for IP fencing, rate-limit buckets and audit rows.

    XFF is honoured only when the **real TCP peer** is a declared trusted proxy.
    Every proxy appends to XFF, so the leftmost value is caller-controlled; take
    the rightmost hop that is not itself a trusted proxy. A forged
    ``X-Forwarded-For: 192.168.10.5`` followed by nginx appending the real public
    IP therefore resolves to the public IP, never to the forged one.
    """
    peer = peer_ip(request)
    trusted = _trusted_proxy_cidrs(request)
    if not (peer and trusted and ip_allowed(peer, trusted)):
        return peer
    for addr in reversed(_xff_chain(request) + [peer]):
        try:
            ipaddress.ip_address(addr)
        except ValueError:
            continue
        if not ip_allowed(addr, trusted):
            return addr
    return peer


def peer_ip(request: Request) -> str | None:
    """The real TCP peer address, or None when the ASGI scope has been polluted.

    uvicorn's ProxyHeadersMiddleware rewrites ``scope["client"]`` to
    ``(<leftmost X-Forwarded-For value>, 0)`` when the peer is in
    ``--forwarded-allow-ips``. A real TCP connection never has port 0. ``serve.py``
    sets ``proxy_headers=False``; this check is the fail-closed safety net for
    anyone still launching with plain ``uvicorn main:app``.
    """
    client = request.scope.get("client")
    if not client:
        return None
    host = client[0]
    port = client[1] if len(client) > 1 else None
    if port == 0 and has_proxy_headers(request):
        logger.warning(
            "ASGI client address %r was rewritten from forwarded headers; refusing to use it "
            "for an access-control decision. Start with `python serve.py` (proxy_headers=False).",
            client,
        )
        return None
    return host


def ip_allowed(ip: str | None, allowed: Iterable[str]) -> bool:
    """Check a client IP against an allowlist (supports CIDR, e.g. 10.0.0.0/8).

    An empty allowlist means "no IP fence", not "nobody".
    """
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


# --------------------------------------------------------------------------- #
# Authorization
# --------------------------------------------------------------------------- #
def docs_allowed(request: Request, cfg) -> bool:
    """Interactive docs and Swagger assets: internal zone, opt-in per config."""
    if cfg is None or not cfg.security.internal.expose_docs:
        return False
    return zone(request) == "internal"


async def authorize(request: Request, cfg) -> str | None:
    """Require a valid caller key (or console session handled by the caller).

    Also enforces the optional ``security.allowed_client_ips`` fence before
    spending time on key verification. Raised errors are turned into responses by
    the route dependency; ``bridge.public_auth_denial`` maps them into the
    middleware's denial envelope, so the rules live in exactly one place.
    """
    path = request.url.path.rstrip("/") or "/"
    ip = client_ip(request)
    if not ip_allowed(ip, cfg.security.allowed_client_ips):
        logger.warning("Client IP not allowed: %s %s", ip, path)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Client address is not permitted to access this gateway.",
        )

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


async def read_limited_body(request: Request, limit: int | None) -> bytes:
    """Read a request body under a hard byte cap.

    `Content-Length` is checked before any bytes are read, and chunked / streaming
    bodies are counted as they arrive, so an oversized POST never lands in memory.
    Callers must use the returned bytes instead of calling `request.body()` again:
    Starlette only caches the body for `body()`, not for `stream()`.
    """
    if not limit or limit <= 0:
        return await request.body()
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > limit:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Request body too large.",
                )
        except ValueError:
            pass  # malformed header: the streaming counter below still applies
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > limit:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Request body too large.",
            )
    return bytes(chunks)


async def require_api_key(request: Request) -> str | None:
    """FastAPI dependency used by every business endpoint.

    Credential priority: a signed console session, then a valid caller API key.
    Zone rules, checked in this order:

    * a console session cookie is accepted on either zone;
    * ``public`` (``security.public.port``) -> ALWAYS a valid key, with no
      anonymous exception. ``bridge.py`` has already hidden every unlisted path;
    * ``internal`` -> the liveness/info probes ``/`` and ``/health`` stay
      anonymous so the container HEALTHCHECK works, and every other API is
      anonymous only when ``security.internal.allow_anonymous`` is true.
    """
    manager = getattr(request.app.state, "manager", None)
    if manager is None:
        return "anonymous"
    if has_admin_session(request):
        return "admin-session"
    cfg = manager.config
    if zone(request) == "public":
        return await authorize(request, cfg)

    path = request.url.path.rstrip("/") or "/"
    if path in PROBE_PATHS or cfg.security.internal.allow_anonymous:
        return "anonymous"
    return await authorize(request, cfg)
