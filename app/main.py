"""
LLM Hub - the unified entrypoint FastAPI application.

Intercepts and handles:
  GET  /                —— system information (gateway name + version)
  GET  /v1/models     —— aggregates the model list of all upstreams
  POST /v1/rerank     —— reranking endpoint (not supported by Ollama natively)
  POST /v1/tools/web-search —— web search, proxied to an OpenCode API Gateway upstream
  GET  /api/tags      —— aggregates the model list in Ollama format
  GET  /health        —— gateway health status

All other requests (/api/*, /v1/*) are routed to the matching Ollama instance by model name.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from admin_auth import require_admin
from auth import (
    DOCS_PATHS,
    client_ip,
    docs_allowed,
    extract_api_key,
    find_weak_keys,
    ip_allowed,
    require_api_key,
)
from config import CONFIG_PATH, ConfigManager
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from key_store import ApiKeyStore, set_store
from management import AdminLogin
from management import login as admin_login
from management import logout as admin_logout
from management import router as management_router
from proxy import ProxyService
from bench_store import BenchStore
from messages import MessagesService
from request_log import RequestLog
from rerank import RerankService
from systemone import SystemOneService
from websearch import WebSearchService

logger = logging.getLogger("llm_hub")

#: Gateway version (was previously exposed via the app package __init__)
__version__ = "1.2.0"

HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def setup_logging(level: str) -> None:
    numeric = getattr(logging, str(level).upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("llm_hub").setLevel(numeric)


# --------------------------------------------------------------------------- #
# Lifespan
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    manager = ConfigManager()
    cfg = manager.config
    setup_logging(cfg.server.log_level)

    limits = httpx.Limits(
        max_connections=max(16, cfg.server.max_concurrency),
        max_keepalive_connections=min(32, max(8, cfg.server.max_concurrency)),
        keepalive_expiry=cfg.server.upstream_keepalive_expiry,
    )
    # trust_env=False: the gateway always talks to explicitly-configured upstreams
    # (Ollama / LM Studio / System One backend — all internal addresses). Inheriting ambient
    # HTTP(S)_PROXY from the host breaks pooled keep-alive connections to LAN hosts
    # (first request succeeds, later ones 404 through the proxy). Connect directly.
    client = httpx.AsyncClient(
        limits=limits,
        timeout=httpx.Timeout(cfg.server.request_timeout, connect=10.0),
        follow_redirects=True,
        trust_env=False,
    )

    app.state.manager = manager
    app.state.client = client
    app.state.request_log = RequestLog()
    await app.state.request_log.start()
    app.state.proxy = ProxyService(manager, client, app.state.request_log)
    app.state.rerank = RerankService(manager, client)
    app.state.systemone = SystemOneService(manager, client)
    app.state.websearch = WebSearchService(manager, client)
    app.state.messages = MessagesService(manager, client)
    app.state.bench_store = BenchStore()
    await app.state.bench_store.start()
    app.state.api_keys = ApiKeyStore()
    await app.state.api_keys.start()
    set_store(app.state.api_keys)
    app.state.started_at = time.time()

    # Caller keys are rows of `api_keys` in llmhub.db, kept as SHA-256 hashes. The environment
    # (GATEWAY_API_KEYS / GATEWAY_API_KEYS_FILE / data/.keys) only seeds an *empty* table, so an
    # existing deployment upgrades itself on its first run and, from then on, the file is never
    # read again: a key revoked in the console stays revoked across restarts.
    env_keys = [key for key in cfg.auth.api_keys if key]
    if app.state.api_keys.count() == 0 and env_keys:
        seeded = await app.state.api_keys.import_plaintext(env_keys)
        logger.info(
            "Seeded %d API key(s) from the environment into %s; manage them at /admin from now on",
            seeded,
            app.state.api_keys.db_path,
        )
    elif stale := app.state.api_keys.unserved_env_keys(env_keys):
        logger.warning(
            "Ignoring %d environment key(s) (%s): the api_keys table in %s became the source of "
            "truth at first start, so create or revoke keys in the console",
            len(stale),
            ", ".join(stale),
            app.state.api_keys.db_path,
        )

    # Security self-check: never serve an authenticated gateway without any key - unless an
    # IP whitelist is configured, which is an explicit "trusted network" deployment: those
    # addresses skip the key check anyway (see auth._authorize), so refusing to start would
    # make a keys-free / docs-only setup impossible.
    if cfg.auth.enabled and app.state.api_keys.count() == 0:
        if cfg.auth.allow_access_ip_whitelist:
            logger.warning(
                "auth.enabled with no API key: running in trusted-network mode, only "
                "auth.allow_access_ip_whitelist addresses (%d pattern(s)) may call the API; "
                "every other client gets 401",
                len(cfg.auth.allow_access_ip_whitelist),
            )
        else:
            raise RuntimeError(
                "Refusing to start: auth.enabled is true but the api_keys table holds no key. "
                "Seed the empty table with the GATEWAY_API_KEYS environment variable (comma "
                "separated) or GATEWAY_API_KEYS_FILE (a secret file such as data/.keys) and "
                "restart, then manage keys at /admin; or turn auth off with auth.enabled=false."
            )

    # Security self-check: refuse to start with weak/placeholder keys on a public gateway.
    # Console-generated keys are 238-bit random, so only the environment ones can be weak.
    weak = find_weak_keys(env_keys)
    if weak and cfg.security.fail_on_weak_keys:
        raise RuntimeError(
            "Refusing to start: weak/placeholder API keys detected -> "
            + "; ".join(weak)
            + ". Use stronger keys (>=16 random chars) or set security.fail_on_weak_keys=false."
        )
    elif weak:
        logger.warning("Weak/placeholder API keys detected: %s", "; ".join(weak))

    logger.info(
        "LLM Hub started | config=%s | upstreams=%d | aliases=%d | listening=%s:%d",
        CONFIG_PATH,
        len(cfg.upstreams),
        len(cfg.aliases),
        cfg.server.host,
        cfg.server.port,
    )
    try:
        yield
    finally:
        await app.state.request_log.stop()
        if getattr(app.state, "bench_store", None) is not None:
            await app.state.bench_store.stop()
        await client.aclose()
        logger.info("LLM Hub stopped")


app = FastAPI(
    title="LLM Hub",
    description="Unified entrypoint for Ollama / LM Studio / OpenCode-gateway instances: model routing / API key auth / Rerank API / Web Search / alias and parameter injection",
    version=__version__,
    lifespan=lifespan,
    # Disable FastAPI's built-in doc routes: they are registered lazily in
    # setup() (after our catch-all route), so the catch-all would otherwise
    # intercept /docs and require an API key. We register them explicitly below,
    # before the catch-all, and keep them key-free.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# The console shows the gateway build in its sidebar; /overview reads it off the state
# instead of importing main back into management (which would be circular).
app.state.gateway_version = __version__


# --------------------------------------------------------------------------- #
# OpenAPI security: shared Authorize schemes + an inline per-request API key
# --------------------------------------------------------------------------- #
# Two ways to pass a key in /docs:
#   1. the classic Authorize dialog, driven by the shared schemes below
#      (Authorization: Bearer <key> or X-API-Key: <key>)
#   2. an `X-API-Key` header *parameter* on every operation, so the input box
#      sits in the request area itself (Parameters -> Try it out -> Execute)
#      and is attached to that single call.
# FastAPI declares neither for require_api_key, so both are injected below.
# Either transport is accepted by auth.extract_api_key().
_API_KEY_HEADER = "X-API-Key"

_API_KEY_PARAM: dict[str, Any] = {
    "name": _API_KEY_HEADER,
    "in": "header",
    "required": False,
    "schema": {"type": "string"},
}

# Shared schemes powering the standard Authorize dialog.
API_KEY_SCHEMES: dict[str, Any] = {
    "BearerAuth": {
        "type": "http",
        "scheme": "bearer",
        "description": "Authorization: Bearer <api-key>",
    },
    "ApiKeyAuth": {
        "type": "apiKey",
        "in": "header",
        "name": _API_KEY_HEADER,
        "description": "X-API-Key: <api-key>",
    },
}
# Global OR requirement: a caller may authenticate with either scheme.
_OPENAPI_SECURITY: list[dict[str, list[str]]] = [{"BearerAuth": []}, {"ApiKeyAuth": []}]


def _openapi_with_security() -> dict[str, Any]:
    """FastAPI's schema + shared auth schemes + an inline `X-API-Key` parameter per operation.

    FastAPI only emits `securitySchemes` for routes declaring a `Security()`
    dependency, and it never surfaces a header as a parameter; the gateway
    authenticates through `require_api_key`, so both are injected here — the
    schemes feed the Authorize dialog, while the parameter puts an API-key
    input box into every endpoint's request section.
    """
    schema = FastAPI.openapi(app)
    schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schemes.update(API_KEY_SCHEMES)
    schema["security"] = _OPENAPI_SECURITY
    for path, item in schema.get("paths", {}).items():
        for method, operation in item.items():
            if not isinstance(operation, dict):
                continue
            params = operation.setdefault("parameters", [])
            if not any(p.get("name") == _API_KEY_HEADER and p.get("in") == "header" for p in params):
                param = dict(_API_KEY_PARAM)
                param["description"] = (
                    f"API key for `{method.upper()} {path}` — sent as the `{_API_KEY_HEADER}` header. "
                    'Click "Try it out" and type it here; it is used for this request only.'
                )
                params.append(param)
    return schema


app.openapi = _openapi_with_security  # type: ignore[method-assign]


# --------------------------------------------------------------------------- #
# Security response hardening
# --------------------------------------------------------------------------- #
# Defensive headers attached to every response (see OWASP Secure Headers Project).
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}

# CSP for the Swagger UI document (/docs, /openapi.json). The blanket
# `default-src 'none'` above would block the page's own stylesheet, bundle and
# inline bootstrap script, leaving /docs a blank white page. The docs routes
# therefore opt in to first-party scripts/styles + their inline bootstrapper
# (the vendored, integrity-checked assets under /static/swagger) while every
# other response keeps the locked-down default.
DOCS_CSP = (
    "default-src 'none'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self' data:; "
    "form-action 'self'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)

# CSP for the (future) web management console under /admin. Unlike the locked-down
# default and the docs scope, the admin SPA needs first-party scripts/styles plus
# inline bootstrap (most single-file UIs embed inline JS/CSS). It stays locked to
# 'self' origins and forbids framing by other sites.
ADMIN_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self' data:; "
    "form-action 'self'; "
    "base-uri 'self'; "
    "frame-ancestors 'self'"
)

# Compiled path-allowlist regexes, cached by the pattern tuple.
_PATH_ALLOWLIST_CACHE: dict[tuple, list[re.Pattern]] = {}


def _allowlist_patterns(patterns: list[str]) -> list[re.Pattern]:
    key = tuple(patterns)
    cached = _PATH_ALLOWLIST_CACHE.get(key)
    if cached is not None:
        return cached
    compiled: list[re.Pattern] = []
    for pat in patterns:
        try:
            compiled.append(re.compile(pat))
        except re.error as exc:  # a bad pattern must not open the gateway
            logger.error("Invalid path_allowlist regex %r: %s (entry ignored)", pat, exc)
    _PATH_ALLOWLIST_CACHE[key] = compiled
    return compiled


def _path_allowed(path: str, patterns: list[str]) -> bool:
    return any(rx.search(path) for rx in _allowlist_patterns(patterns))


def _harden_response(resp: Response, path: str = "") -> Response:
    """Attach defensive security headers without clobbering existing ones.

    The Swagger UI document and the admin console need scripts/styles, so those
    paths get the scoped DOCS_CSP / ADMIN_CSP instead of the blanket
    `default-src 'none'` default.
    """
    for name, value in SECURITY_HEADERS.items():
        resp.headers.setdefault(name, value)
    clean = path.rstrip("/")
    if clean in DOCS_PATHS:
        resp.headers["Content-Security-Policy"] = DOCS_CSP
    elif path.startswith("/admin"):
        resp.headers["Content-Security-Policy"] = ADMIN_CSP
    return resp


def _deny(status_code: int, detail: str) -> JSONResponse:
    return _harden_response(JSONResponse({"detail": detail}, status_code=status_code))


# --------------------------------------------------------------------------- #
# In-memory token-bucket rate limiter (single process)
# --------------------------------------------------------------------------- #
_RATE_BUCKETS: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)


def _rate_limit_key(rl, request: Request, sec) -> str:
    if rl.by == "key":
        k = extract_api_key(request, allow_query=sec.allow_query_api_key)
        if k:
            return "key:" + k
    return "ip:" + (client_ip(request) or "unknown")


def _rate_limited(rl, request: Request, sec) -> bool:
    """Return True when the request should be throttled (HTTP 429)."""
    key = _rate_limit_key(rl, request, sec)
    now = time.time()
    capacity = max(int(rl.burst), 1)
    rate = max(float(rl.per_minute), 1) / 60.0  # tokens per second
    tokens, ts = _RATE_BUCKETS.get(key, (float(capacity), now))
    tokens = min(float(capacity), tokens + (now - ts) * rate)
    if tokens < 1.0:
        _RATE_BUCKETS[key] = (tokens, now)
        return True
    _RATE_BUCKETS[key] = (tokens - 1.0, now)
    # Occasionally prune stale buckets to bound memory.
    if len(_RATE_BUCKETS) > 5000:
        old = now - 120.0
        for k in [k for k, (_, t) in _RATE_BUCKETS.items() if t < old]:
            _RATE_BUCKETS.pop(k, None)
    return False


@app.middleware("http")
async def gateway_security_middleware(request: Request, call_next):
    """Per-request hardening of the public surface, evaluated in this order:

    1. HTTP method allowlist (security.allowed_http_methods)  -> 405
    2. path regex allowlist   (security.path_allowlist)       -> 403
    3. hide /docs /redoc /openapi.json unless allowed         -> 404
    4. client IP allowlist     (security.allowed_client_ips)  -> 403
    5. rate limit              (security.rate_limit)           -> 429
    6. CORS only for explicitly listed origins                -> 204 / header
    """
    manager = getattr(request.app.state, "manager", None)
    cfg = manager.config if manager is not None else None
    sec = cfg.security if cfg is not None else None
    path = request.url.path

    # The admin console authenticates with its own password and drives a REST API that
    # needs PUT (config save) and other verbs. It is therefore exempt from the public
    # perimeter rules below (the method allowlist and the path allowlist): otherwise a
    # gateway locked down to GET/POST - the default - would make the console's own Save
    # return 405 and never write config.yaml. The docs are gated separately on
    # auth.allow_access_ip_whitelist.
    management_api = path.startswith("/v1/management")
    is_console = path.startswith("/admin") or management_api

    # 1. HTTP method allowlist -------------------------------------------------
    allowed_methods = set(getattr(sec, "allowed_http_methods", None) or ["GET", "POST"])
    method = request.method.upper()
    # CORS preflight needs OPTIONS; permit it only when CORS is actually configured.
    if method == "OPTIONS" and sec is not None and sec.cors_origins:
        allowed_methods = allowed_methods | {"OPTIONS"}
    if method not in allowed_methods and not is_console:
        return _deny(status.HTTP_405_METHOD_NOT_ALLOWED, "Method not allowed.")

    if sec is not None:
        # 2. path regex allowlist (perimeter control) --------------------------
        if (
            not is_console
            and sec.path_allowlist
            and not _path_allowed(path, sec.path_allowlist)
        ):
            logger.warning("Path not in allowlist: %s %s", method, path)
            return _deny(status.HTTP_403_FORBIDDEN, "Path not allowed.")

        # 3. API docs go to whitelisted addresses only -------------------------
        if path.rstrip("/") in DOCS_PATHS and not docs_allowed(request, cfg):
            return _deny(status.HTTP_404_NOT_FOUND, "Not found.")

        # /redoc was intentionally removed (superseded by /docs); return a clean
        # 404 instead of leaking into the catch-all (which would demand an API key).
        if path.rstrip("/") == "/redoc":
            return _deny(status.HTTP_404_NOT_FOUND, "Not found.")

        # 4. client IP allowlist -----------------------------------------------
        if not ip_allowed(client_ip(request), sec.allowed_client_ips):
            return _deny(status.HTTP_403_FORBIDDEN, "Client address is not permitted to access this gateway.")

        # 5. rate limit -------------------------------------------------------
        if sec.rate_limit.enabled and _rate_limited(sec.rate_limit, request, sec):
            logger.warning("Rate limit exceeded: %s %s", method, path)
            return _deny(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests.")

        # 6. CORS (only for explicitly allowed origins) -----------------------
        origin = request.headers.get("origin")
        if origin and sec.cors_origins:
            allowed = "*" in sec.cors_origins or origin in sec.cors_origins
            if method == "OPTIONS" and allowed:
                return _harden_response(
                    JSONResponse(
                        {},
                        status_code=204,
                        headers={
                            "Access-Control-Allow-Origin": origin,
                            "Access-Control-Allow-Methods": "*",
                            "Access-Control-Allow-Headers": "*",
                        },
                    )
                )
            if allowed:
                resp = await call_next(request)
                resp.headers["Access-Control-Allow-Origin"] = origin
                return _harden_response(resp, path)

    resp = await call_next(request)
    return _harden_response(resp, path)


# --------------------------------------------------------------------------- #
# Special endpoints
# --------------------------------------------------------------------------- #
async def health_response(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


def _discovery_headers(upstream) -> dict[str, str] | None:
    """Headers for control-plane calls (health / model list); providers may require auth here too."""
    custom = getattr(upstream, "headers", None)
    if isinstance(custom, dict) and custom:
        return {str(k): str(v) for k, v in custom.items()}
    return None


async def probe_response(request: Request) -> JSONResponse:
    """Probe all upstreams and check model validity. Requires API key."""
    manager = request.app.state.manager
    client: httpx.AsyncClient = request.app.state.client
    cfg = manager.config

    results = []
    for up in cfg.upstreams:
        upstream_info = {
            "name": up.name,
            "type": up.type,
            "base_url": up.base_url,
            "reachable": False,
            "models": [],
        }
        try:
            resp = await client.get(
                f"{up.base_url_stripped()}/{up.health_path()}",
                headers=_discovery_headers(up),
                timeout=5.0,
            )
            upstream_info["reachable"] = resp.status_code < 500
            if resp.status_code < 500:
                try:
                    upstream_info["version"] = resp.json().get("version")
                except Exception:
                    pass
            else:
                upstream_info["error"] = f"HTTP {resp.status_code}"
        except Exception as exc:
            upstream_info["error"] = str(exc)

        # Check declared models
        for model_name in up.models:
            model_info = {"name": model_name, "declared": True, "available": False}
            if not model_name.endswith("*"):
                # Exact match: check if model exists in upstream
                if upstream_info["reachable"]:
                    try:
                        resp = await client.get(
                            f"{up.base_url_stripped()}/{up.tags_path()}",
                            headers=_discovery_headers(up),
                            timeout=8.0,
                        )
                        resp.raise_for_status()
                        available_models = up.parse_model_names(resp.json())
                        model_info["available"] = model_name in available_models
                    except Exception:
                        model_info["available"] = False
            else:
                # Wildcard: mark as available if upstream is reachable
                model_info["available"] = upstream_info["reachable"]
                model_info["wildcard"] = True
            upstream_info["models"].append(model_info)

        results.append(upstream_info)

    return JSONResponse({"upstreams": results})


# Upstream model list cache: {upstream_name: (timestamp, [model names])}
_UPSTREAM_MODELS_CACHE: dict[str, tuple] = {}
UPSTREAM_MODELS_TTL = 60.0


async def fetch_upstream_models(client: httpx.AsyncClient, upstream) -> list[str]:
    """Fetch the real model list from an upstream (for wildcard routing); cached for 60 seconds, reusing the last value on failure.

    Works for both Ollama (/api/tags) and OpenAI-compatible backends such as LM Studio (/v1/models).
    """
    now = time.time()
    cached = _UPSTREAM_MODELS_CACHE.get(upstream.name)
    if cached and now - cached[0] < UPSTREAM_MODELS_TTL:
        return cached[1]

    try:
        resp = await client.get(
            f"{upstream.base_url_stripped()}/{upstream.tags_path()}",
            headers=_discovery_headers(upstream),
            timeout=8.0,
        )
        resp.raise_for_status()
        names = upstream.parse_model_names(resp.json())
        _UPSTREAM_MODELS_CACHE[upstream.name] = (now, names)
        return names
    except Exception as exc:
        logger.warning("Failed to fetch the model list of upstream %s: %s", upstream.name, exc)
        return cached[1] if cached else []


async def aggregate_models(request: Request) -> list[dict[str, Any]]:
    """Aggregate the model list across all configured services.

    The result is the union of:
      1. models explicitly declared on each upstream (highest priority)
      2. models discovered by live-querying every upstream (declared wildcards are
         expanded, and the real upstream model list is merged in)
      3. global aliases

    The live fetch is cached (60s) inside `fetch_upstream_models` and degrades to an
    empty list per upstream on failure, so one unreachable backend never blanks the
    whole catalog.
    """
    cfg = request.app.state.manager.config
    client: httpx.AsyncClient = request.app.state.client

    items: list[dict[str, Any]] = []
    seen: set = set()

    def add(name: str, upstream_name: str | None, kind: str, **extra) -> None:
        if name in seen:
            return
        seen.add(name)
        entry = {
            "id": name,
            "object": "model",
            "created": 0,
            "owned_by": "ollama",
            "upstream": upstream_name,
            "type": kind,
        }
        entry.update(extra)
        items.append(entry)

    # Live model lists from every upstream, fetched concurrently (cached / [] on failure).
    live = dict(
        zip(
            [up.name for up in cfg.upstreams],
            await asyncio.gather(
                *[fetch_upstream_models(client, up) for up in cfg.upstreams]
            ),
            strict=True,
        )
    )

    # 1. Models declared explicitly by each upstream (highest priority)
    for up in cfg.upstreams:
        for m in up.models:
            if not m.endswith("*"):
                add(m, up.name, "upstream", wildcard=False)

    # 2. Wildcard expansion + live merge (both draw from the fetched lists).
    for up in cfg.upstreams:
        names = live.get(up.name, [])
        prefixes = [m[:-1] for m in up.models if m.endswith("*")]
        for name in names:
            wildcard = any(name.startswith(p) for p in prefixes)
            add(name, up.name, "upstream", wildcard=wildcard, live=True)

    # 3. Aliases
    for alias_name, alias in cfg.aliases.items():
        upstream = cfg.get_upstream(alias.upstream) or cfg.find_upstream_for_model(alias.model)
        add(
            alias_name,
            upstream.name if upstream else None,
            "alias",
            target=alias.model,
        )

    return items


async def models_response(request: Request) -> JSONResponse:
    """Unified /v1/models: aggregates the models and aliases of all configured upstreams."""
    data = await aggregate_models(request)
    return JSONResponse({"object": "list", "data": data})


async def tags_response(request: Request) -> JSONResponse:
    """Return the aggregated model list in Ollama /api/tags format."""
    now = datetime.now(UTC).isoformat()
    models: list[dict[str, Any]] = []
    for item in await aggregate_models(request):
        models.append(
            {
                "name": item["id"],
                "model": item["id"],
                "modified_at": now,
                "size": 0,
                "digest": "sha256:gateway",
                "details": {
                    "parent_model": item.get("target", ""),
                    "format": "gguf",
                    "family": "gateway",
                    "families": ["gateway"],
                    "parameter_size": "",
                    "quantization_level": "",
                },
                "gateway": {
                    "upstream": item.get("upstream"),
                    "type": item.get("type"),
                    "target": item.get("target"),
                },
            }
        )
    return JSONResponse({"models": models})


async def ps_response(request: Request) -> JSONResponse:
    """Aggregate `/api/ps` (running models) across all Ollama upstreams.

    `/api/ps` is Ollama-specific, so LM Studio (and other OpenAI-compatible) upstreams
    are skipped. Each entry returned by an upstream is tagged with its `upstream` name
    so callers can tell which instance a model is loaded on. Failures are reported under
    an `errors` key and never abort the whole response.
    """
    cfg = request.app.state.manager.config
    client: httpx.AsyncClient = request.app.state.client

    models: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for up in cfg.upstreams:
        if up.type != "ollama":
            continue  # /api/ps is an Ollama-only endpoint
        try:
            resp = await client.get(f"{up.base_url_stripped()}/api/ps", timeout=5.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("Failed to fetch /api/ps from upstream %s: %s", up.name, exc)
            errors.append({"upstream": up.name, "error": str(exc)})
            continue

        for m in data.get("models", []):
            if isinstance(m, dict):
                entry = dict(m)
                entry["upstream"] = up.name
                models.append(entry)

    payload: dict[str, Any] = {"models": models}
    if errors:
        payload["errors"] = errors
    return JSONResponse(payload)


async def systemone_response(request: Request) -> Response:
    """Proxy `POST /v1/systemone` to the configured System One backend."""
    service: SystemOneService = request.app.state.systemone
    return await service.handle(request)


async def websearch_response(request: Request) -> Response:
    """Proxy `POST /v1/tools/web-search` to the configured web search provider."""
    service: WebSearchService = request.app.state.websearch
    return await service.handle(request)


async def rerank_response(request: Request) -> JSONResponse:
    if request.method.upper() != "POST":
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="Rerank endpoint only accepts POST.",
        )
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Request body must be valid JSON."
        ) from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request body must be a JSON object.")

    service: RerankService = request.app.state.rerank
    result = await service.handle(payload)
    return JSONResponse(result)


# --------------------------------------------------------------------------- #
# Documented routes (explicit + schema-visible; registered before the catch-all
# so they take precedence and appear in /docs, which is served to the addresses
# matched by auth.allow_access_ip_whitelist)
# --------------------------------------------------------------------------- #
@app.get(
    "/health",
    include_in_schema=True,
    summary="Health check",
    tags=["gateway"],
)
async def route_health(request: Request, _auth=Depends(require_api_key)):
    return await health_response(request)


@app.get(
    "/",
    include_in_schema=True,
    summary="System information",
    tags=["gateway"],
)
async def route_root(request: Request, _auth=Depends(require_api_key)):
    """Return the system name and gateway version."""
    return JSONResponse({"name": app.title, "version": __version__})


@app.get("/probe", include_in_schema=True, summary="Probe upstreams and model validity", tags=["gateway"])
async def route_probe(request: Request, _auth=Depends(require_api_key)):
    return await probe_response(request)


@app.get("/v1/models", include_in_schema=True, summary="List aggregated models (OpenAI format)", tags=["models"])
@app.get("/models", include_in_schema=True, summary="List aggregated models (alias)", tags=["models"])
async def route_models(request: Request, _auth=Depends(require_api_key)):
    return await models_response(request)


@app.post("/v1/rerank", include_in_schema=True, summary="Rerank documents", tags=["rerank"])
@app.post("/rerank", include_in_schema=True, summary="Rerank documents (alias)", tags=["rerank"])
@app.post("/api/rerank", include_in_schema=True, summary="Rerank documents (alias)", tags=["rerank"])
async def route_rerank(request: Request, _auth=Depends(require_api_key)):
    return await rerank_response(request)


# Anthropic Messages API bridge. The gateway translates the Anthropic request into the OpenAI
# chat format each backend understands (Ollama /api/chat or /v1/chat/completions) and converts
# the answer back. `POST /v1/message` is a singular alias of the standard `/v1/messages`.
@app.post(
    "/v1/messages",
    include_in_schema=True,
    summary="Anthropic Messages API (compatible)",
    tags=["messages"],
    openapi_extra={
        "description": (
            "Anthropic-compatible Messages endpoint. The gateway converts the request to the "
            "OpenAI chat format for the resolved upstream and converts the reply back into the "
            "Anthropic Messages envelope (id / type: message / role / content / stop_reason / "
            "usage). stream=true emits the Anthropic SSE event sequence "
            "(message_start -> content_block_delta -> message_stop)."
        ),
        "requestBody": {
            "required": True,
            "description": "Anthropic Messages request. 'messages' and 'max_tokens' are required.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["messages", "max_tokens"],
                        "properties": {
                            "model": {"type": "string", "default": "auto", "description": "Target model, or 'auto' for the default upstream's first model."},
                            "max_tokens": {"type": "integer", "exclusiveMinimum": 0, "description": "Max tokens to generate."},
                            "system": {
                                "description": "System prompt (string or array of text blocks).",
                                "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "object"}}],
                            },
                            "messages": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "required": ["role", "content"],
                                    "properties": {
                                        "role": {"type": "string", "enum": ["user", "assistant"]},
                                        "content": {
                                            "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "object"}}]
                                        },
                                    },
                                },
                            },
                            "stream": {"type": "boolean", "default": False},
                            "temperature": {"type": "number"},
                            "top_p": {"type": "number"},
                            "top_k": {"type": "integer"},
                            "stop_sequences": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                }
            },
        },
    },
)
@app.post("/v1/message", include_in_schema=True, summary="Anthropic Messages API (alias)", tags=["messages"])
async def route_messages(request: Request, _auth=Depends(require_api_key)):
    return await messages_response(request)


async def messages_response(request: Request):
    return await request.app.state.messages.handle(request)


@app.get("/api/tags", include_in_schema=True, summary="List aggregated models (Ollama format)", tags=["models"])
async def route_tags(request: Request, _auth=Depends(require_api_key)):
    return await tags_response(request)


@app.get("/api/ps", include_in_schema=True, summary="Aggregated running-model status across upstreams", tags=["models"])
async def route_ps(request: Request, _auth=Depends(require_api_key)):
    return await ps_response(request)


# Ollama-native text completion. Everything about it is the proxy's normal path
# (model routing, alias + parameter injection, num_ctx clamping, NDJSON streaming,
# request logging); this explicit route exists so the endpoint is described in
# /openapi.json and so non-POST methods get a gateway 405 instead of being proxied.
@app.post(
    "/api/generate",
    include_in_schema=True,
    summary="Ollama-native text generation (routed by model, passed through)",
    tags=["generation"],
    openapi_extra={
        "description": (
            "Forwarded to the upstream that owns `model`, exactly like the catch-all "
            "proxy. Unknown fields are preserved; `stream` may be omitted because "
            "Ollama streams `/api/generate` by default."
        ),
        "requestBody": {
            "required": True,
            "description": (
                "`model` is resolved through the gateway's aliases and routing before "
                "forwarding, so the forwarded body can carry a different model name plus "
                "alias-injected parameters. `raw: true` skips the model's chat template, "
                "which is what a reranker/scoring prompt needs."
            ),
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["model", "prompt"],
                        "properties": {
                            "model": {"type": "string", "description": "Model name or gateway alias."},
                            "prompt": {"type": "string", "description": "Prompt to continue."},
                            "system": {"type": "string", "description": "System prompt (template mode)."},
                            "template": {"type": "string", "description": "Override the model's prompt template."},
                            "context": {"type": "array", "items": {"type": "integer"}, "description": "Ollama context tokens from a previous call."},
                            "raw": {"type": "boolean", "description": "Skip the chat template and continue `prompt` verbatim."},
                            "images": {"type": "array", "items": {"type": "string"}, "description": "Base64 images for vision models."},
                            "format": {
                                "description": "\"json\" or a JSON schema for structured output.",
                                "oneOf": [{"type": "string", "enum": ["json"]}, {"type": "object"}],
                            },
                            "stream": {"type": "boolean", "description": "Default true (NDJSON); false returns one JSON object."},
                            "keep_alive": {"description": "How long the model stays loaded, e.g. \"3m\" or 0."},
                            "logprobs": {"type": "boolean", "description": "Return per-token log probabilities."},
                            "top_logprobs": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Alternatives per position (requires logprobs)."},
                            "options": {
                                "type": "object",
                                "description": "Generation options; `num_ctx` may be clamped by the gateway's num_ctx_overrides.",
                                "properties": {
                                    "temperature": {"type": "number"},
                                    "top_p": {"type": "number"},
                                    "num_predict": {"type": "integer"},
                                    "num_ctx": {"type": "integer"},
                                    "seed": {"type": "integer"},
                                    "stop": {"type": "array", "items": {"type": "string"}},
                                },
                                "additionalProperties": True,
                            },
                        },
                        "additionalProperties": True,
                    },
                    "example": {
                        "model": "qwen3:8b",
                        "prompt": "写一首关于编程的诗：",
                        "raw": True,
                        "stream": False,
                        "keep_alive": "3m",
                        "options": {"temperature": 0, "num_predict": 256},
                    },
                }
            },
        },
        "responses": {
            "200": {
                "description": "Upstream response passed through unchanged (NDJSON when streaming, one JSON object otherwise).",
                "content": {
                    "application/json": {
                        "example": {
                            "model": "qwen3:8b",
                            "created_at": "2026-01-01T00:00:00Z",
                            "response": "代码如诗行，逻辑似水流……",
                            "done": True,
                            "done_reason": "stop",
                            "total_duration": 2345678901,
                            "load_duration": 1200000000,
                            "prompt_eval_count": 12,
                            "prompt_eval_duration": 300000000,
                            "eval_count": 50,
                            "eval_duration": 700000000,
                        }
                    },
                    "application/x-ndjson": {
                        "example": '{"model":"qwen3:8b","response":"代码","done":false}\n'
                        '{"model":"qwen3:8b","response":"如诗行","done":true,"done_reason":"stop","eval_count":8}\n'
                    },
                },
            },
            "404": {"description": "No upstream serves the requested model and routing is strict."},
            "405": {"description": "`/api/generate` only accepts POST."},
        },
    },
)
async def route_generate(request: Request, _auth=Depends(require_api_key)):
    proxy: ProxyService = request.app.state.proxy
    return await proxy.forward(request, "api/generate")


@app.post(
    "/v1/systemone",
    include_in_schema=True,
    summary="System One decision API (proxied to the configured System One backend)",
    tags=["systemone"],
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": (
                "Body is forwarded to the System One backend as-is. `state` is a free-form object; "
                "`questions` maps a question id to a tagged question whose `type` selects "
                "the variant: `noul` (yes/no probability), `choice` (single label) or "
                "`score` (pick one of >= 2 criteria). Optional top-level `model` selects "
                "the routing model. laya-server takes a routing enum (auto / english / multilingual / "
                "typed-decisions); an Ollama backend needs a real model name and 404s on `auto`. "
            ),
            "content": {
                "application/json": {
                    "example": {
                        "state": {"message": "I was charged twice and need a refund today."},
                        "questions": {
                            "refund": {
                                "type": "noul",
                                "instructions": "Does the customer ask for a refund?",
                            },
                            "intent": {
                                "type": "choice",
                                "instructions": "What is the customer's primary intent?",
                                "criteria": ["refund", "cancel", "other"],
                            },
                            "urgency": {
                                "type": "score",
                                "instructions": "How urgent is this request?",
                                "criteria": ["urgent", "normal"],
                            },
                        },
                        "model": "auto",
                    }
                }
            },
        },
        "responses": {
            "200": {
                "description": "Decision result returned by the System One backend (pass-through).",
                "content": {
                    "application/json": {
                        "example": {
                            "model": "laya-rl-agent",
                            "answers": {
                                "refund": {
                                    "type": "noul",
                                    "noul": 0.9937,
                                    "confidence": 0.9937,
                                    "action": {"act_probability": 1.0},
                                }
                            },
                            "usage": {"input_tokens": 49, "output_tokens": 0},
                            "routing": {
                                "model": "multilingual",
                                "reason": "explicit model='multilingual'",
                            },
                        }
                    }
                },
            }
        },
    },
)
async def route_systemone(request: Request, _auth=Depends(require_api_key)):
    return await systemone_response(request)


@app.post(
    "/v1/tools/web-search",
    include_in_schema=True,
    summary="Web search (passages for a keyword query, proxied to the configured provider)",
    tags=["websearch"],
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": (
                "Body is forwarded to the provider unchanged (unknown fields are preserved). "
                "Only `query` is required. `model` is optional: when omitted the provider picks "
                "it, so llm-hub does not rewrite or resolve it against its own aliases. "
                "`synthesize=true` additionally drives an LLM answer synthesis."
            ),
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["query"],
                        "properties": {
                            "query": {"type": "string", "minLength": 1, "description": "Search keywords."},
                            "model": {"type": "string", "description": "Provider-side model id (passed through verbatim)."},
                            "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Pages to retrieve."},
                            "max_chars_per_page": {"type": "integer", "minimum": 1, "description": "Per-page character budget before truncation."},
                            "locale": {"type": "string", "description": "Search locale hint, e.g. zh-CN."},
                            "recency": {
                                "type": "string",
                                "enum": ["day", "week", "month", "year"],
                                "description": "Restrict results to the given time window.",
                            },
                            "synthesize": {"type": "boolean", "description": "Also produce an `answer` by model synthesis."},
                            "include_sources": {"type": "boolean", "description": "Include the flat `sources` URL list."},
                        },
                        "additionalProperties": True,
                    },
                    "example": {"query": "Ollama keep_alive 参数作用", "top_k": 5, "recency": "year"},
                }
            },
        },
        "responses": {
            "200": {
                "description": "Search result returned by the provider (pass-through, status and body unchanged).",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["query", "model", "engine", "truncated", "text", "sources"],
                            "properties": {
                                "query": {"type": "string"},
                                "model": {"type": "string", "description": "Model actually used by the provider."},
                                "engine": {"type": "string", "description": "Retrieval backend that answered the query."},
                                "answer": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "Present only when synthesize=true."},
                                "truncated": {"type": "boolean", "description": "True when a page was cut at max_chars_per_page."},
                                "results": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "required": ["title", "url", "snippet", "paragraphs"],
                                        "properties": {
                                            "title": {"type": "string"},
                                            "url": {"type": "string"},
                                            "snippet": {"type": "string"},
                                            "paragraphs": {"type": "array", "items": {"type": "string"}},
                                        },
                                    },
                                },
                                "text": {"type": "string", "description": "All retrieved passages concatenated, ready to stuff into a prompt."},
                                "sources": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                        "example": {
                            "query": "Ollama keep_alive 参数作用",
                            "model": "ollama-local/qwen3.5:9b",
                            "engine": "opencode-websearch",
                            "answer": None,
                            "truncated": True,
                            "results": [
                                {
                                    "title": "FAQ - Ollama",
                                    "url": "https://github.com/ollama/ollama/blob/main/docs/faq.md",
                                    "snippet": "keep_alive controls how long a model stays loaded in memory.",
                                    "paragraphs": ["By default a model stays loaded for 5 minutes."],
                                }
                            ],
                            "text": "By default a model stays loaded for 5 minutes.",
                            "sources": ["https://github.com/ollama/ollama/blob/main/docs/faq.md"],
                        },
                    }
                },
            },
            "404": {"description": "websearch.enabled is false."},
            "503": {"description": "websearch is enabled but the configured provider upstream is missing."},
        },
    },
)
async def route_websearch(request: Request, _auth=Depends(require_api_key)):
    return await websearch_response(request)


# --------------------------------------------------------------------------- #
# API docs (explicit, key-free, and registered BEFORE the catch-all so the
# catch-all never intercepts these paths). Visibility is gated by
# auth.allow_access_ip_whitelist in the middleware above: an address that does not
# match one of those regexes gets a 404, a matching one is served the docs.
# --------------------------------------------------------------------------- #
# Local vendored Swagger UI assets (no public CDN dependency -> works offline).
SWAGGER_STATIC_DIR = Path(__file__).parent / "static" / "swagger"


@app.get("/static/swagger/{file_path:path}", include_in_schema=False, tags=["docs"])
async def serve_swagger_static(file_path: str):
    """Serve the vendored Swagger UI assets locally (offline, no CDN)."""
    candidate = (SWAGGER_STATIC_DIR / file_path).resolve()
    if candidate.is_file() and str(candidate).startswith(str(SWAGGER_STATIC_DIR.resolve())):
        return FileResponse(candidate)
    return JSONResponse({"detail": "Not found"}, status_code=404)


@app.get("/openapi.json", include_in_schema=False, tags=["docs"])
async def route_openapi(request: Request):
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False, tags=["docs"])
async def route_docs(request: Request):
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=f"{app.title} API",
        swagger_js_url="/static/swagger/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger/swagger-ui.css",
        # Local favicon: the FastAPI default points at an external CDN, which
        # CSP (img-src 'self') and offline deployments would both reject.
        swagger_favicon_url="/static/swagger/favicon.svg",
        swagger_ui_parameters={
            # Keep the key entered via Authorize across page reloads, so every
            # subsequent "Try it out" request carries it without re-typing.
            "persistAuthorization": True,
        },
    )


# --------------------------------------------------------------------------- #
# Admin console authentication (single password from ADMIN_PASSWORD env var)
# --------------------------------------------------------------------------- #
# The canonical implementation lives in management.py alongside /v1/management/auth/*;
# these legacy paths are kept for old bookmarks and behave identically.
@app.post("/admin/login", include_in_schema=False, tags=["admin"])
async def route_admin_login(request: Request, body: AdminLogin):
    """Validate the admin password and mint a signed session cookie."""
    return await admin_login(request, body)


@app.post("/admin/logout", include_in_schema=False, tags=["admin"])
async def route_admin_logout():
    return admin_logout()


@app.get("/admin/ping", include_in_schema=False, tags=["admin"])
async def route_admin_ping(_=Depends(require_admin)):
    """Liveness check for the admin session; 200 only with a valid admin auth."""
    return JSONResponse({"ok": True, "admin": True})


# --------------------------------------------------------------------------- #
# Web console: the Vue SPA built into app/web, served under /admin
# --------------------------------------------------------------------------- #
# Static assets carry no secrets, so they stay anonymous - the SPA itself is gated
# by the login screen, and every /v1/management call it makes needs a session
# cookie or a gateway API key. `npm run build` in web/ writes app/web, which is next
# to the modules locally and lands in /app/web in the image (same flat layout as
# app/static), so one path resolves in both.
CONSOLE_DIST = Path(__file__).resolve().parent / "web"
CONSOLE_MISSING_DETAIL = (
    "Web console is not built: run `npm install && npm run build` in web/, "
    "or use an image that ships app/web."
)


def _console_headers(asset_path: str) -> dict[str, str]:
    # Vite content-hashes everything under assets/, so those files may be cached
    # forever; index.html must always be revalidated or a rebuild never lands.
    if asset_path.startswith("assets/"):
        return {"Cache-Control": "public, max-age=31536000, immutable"}
    return {"Cache-Control": "no-cache"}


@app.get("/admin", include_in_schema=False, tags=["admin"])
async def route_console_root():
    """Canonical console entrypoint: /admin/ so relative routing has a base."""
    return RedirectResponse(url="/admin/")


@app.get("/admin/{asset_path:path}", include_in_schema=False, tags=["admin"])
async def route_console(asset_path: str):
    if not (CONSOLE_DIST / "index.html").is_file():
        return JSONResponse({"detail": CONSOLE_MISSING_DETAIL}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    candidate = (CONSOLE_DIST / asset_path).resolve()
    # resolve() + relative check: ../ segments can never escape the built directory
    if candidate.is_file() and candidate.is_relative_to(CONSOLE_DIST):
        return FileResponse(candidate, headers=_console_headers(asset_path))
    # Unknown path: let the SPA router answer (deep links such as /admin/requests).
    return FileResponse(CONSOLE_DIST / "index.html", headers={"Cache-Control": "no-cache"})


# Management API for the web console: read-only dashboard, upstream probes,
# config projections and request-log queries. Registered before the catch-all,
# otherwise every /v1/management call would be proxied to an upstream.
app.include_router(management_router)


# --------------------------------------------------------------------------- #
# Unified entrypoint (catch-all proxy for everything not handled above)
# --------------------------------------------------------------------------- #
# Paths documented above that only accept POST. The catch-all below accepts every
# method, so without this guard a GET/PUT/DELETE to e.g. /v1/rerank would be
# proxied to an upstream instead of being rejected with 405.
POST_ONLY_PATHS = frozenset(
    {"/v1/rerank", "/rerank", "/api/rerank", "/v1/systemone", "/v1/tools/web-search", "/api/generate"}
)


@app.api_route("/", methods=HTTP_METHODS, include_in_schema=False)
@app.api_route("/{full_path:path}", methods=HTTP_METHODS, include_in_schema=False)
async def gateway(request: Request, full_path: str = "", _auth=Depends(require_api_key)):
    path = "/" + (full_path or "").strip("/")
    if path in POST_ONLY_PATHS and request.method.upper() != "POST":
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail=f"{path} only accepts POST.",
            headers={"Allow": "POST"},
        )
    proxy: ProxyService = request.app.state.proxy
    return await proxy.forward(request, (full_path or "").strip("/"))


# --------------------------------------------------------------------------- #
# Local entrypoint
# --------------------------------------------------------------------------- #
def main() -> None:
    manager = ConfigManager()
    cfg = manager.config
    setup_logging(cfg.server.log_level)
    uvicorn.run(
        "main:app",
        host=cfg.server.host,
        port=cfg.server.port,
        log_level=cfg.server.log_level,
        reload=False,
    )


if __name__ == "__main__":
    main()
