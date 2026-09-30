"""
LLM Hub - the unified entrypoint FastAPI application.

Intercepts and handles:
  GET  /                —— system information (gateway name + version)
  GET  /v1/models     —— aggregates the model list of all upstreams
  POST /v1/rerank     —— reranking endpoint (not supported by Ollama natively)
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
from auth import (
    DOCS_PATHS,
    client_ip,
    extract_api_key,
    find_weak_keys,
    ip_allowed,
    require_api_key,
)
from config import CONFIG_PATH, ConfigManager
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from proxy import ProxyService
from rerank import RerankService
from systemone import SystemOneService

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
    # (Ollama / LM Studio / laya-server — all internal addresses). Inheriting ambient
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
    app.state.proxy = ProxyService(manager, client)
    app.state.rerank = RerankService(manager, client)
    app.state.systemone = SystemOneService(manager, client)
    app.state.started_at = time.time()

    # Security self-check: never serve an authenticated gateway without any key.
    # Keys are meant to arrive from the environment so config.json holds no secrets.
    if cfg.auth.enabled and not cfg.auth.api_keys:
        raise RuntimeError(
            "Refusing to start: auth.enabled is true but no API key is configured. "
            "Supply keys through the GATEWAY_API_KEYS environment variable (comma separated) "
            "or GATEWAY_API_KEYS_FILE (path to a secret file), add them to auth.api_keys in the "
            "config file, or turn auth off with auth.enabled=false."
        )

    # Security self-check: refuse to start with weak/placeholder keys on a public gateway.
    weak = find_weak_keys(cfg.auth.api_keys)
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
        await client.aclose()
        logger.info("LLM Hub stopped")


app = FastAPI(
    title="LLM Hub",
    description="Unified entrypoint for Ollama / LM Studio instances: model routing / API key auth / Rerank API / alias and parameter injection",
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

    The Swagger UI document needs scripts/styles, so docs paths get the scoped
    DOCS_CSP instead of the blanket `default-src 'none'` default.
    """
    for name, value in SECURITY_HEADERS.items():
        resp.headers.setdefault(name, value)
    if path.rstrip("/") in DOCS_PATHS:
        resp.headers["Content-Security-Policy"] = DOCS_CSP
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
    sec = manager.config.security if manager is not None else None
    path = request.url.path

    # 1. HTTP method allowlist -------------------------------------------------
    allowed_methods = set(getattr(sec, "allowed_http_methods", None) or ["GET", "POST"])
    method = request.method.upper()
    # CORS preflight needs OPTIONS; permit it only when CORS is actually configured.
    if method == "OPTIONS" and sec is not None and sec.cors_origins:
        allowed_methods = allowed_methods | {"OPTIONS"}
    if method not in allowed_methods:
        return _deny(status.HTTP_405_METHOD_NOT_ALLOWED, "Method not allowed.")

    if sec is not None:
        # 2. path regex allowlist (perimeter control) --------------------------
        if sec.path_allowlist and not _path_allowed(path, sec.path_allowlist):
            logger.warning("Path not in allowlist: %s %s", method, path)
            return _deny(status.HTTP_403_FORBIDDEN, "Path not allowed.")

        # 3. hide API docs on a public gateway ---------------------------------
        if path.rstrip("/") in DOCS_PATHS and not sec.allow_docs:
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
            resp = await client.get(f"{up.base_url_stripped()}/{up.health_path()}", timeout=5.0)
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
                        resp = await client.get(f"{up.base_url_stripped()}/{up.tags_path()}", timeout=8.0)
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
        resp = await client.get(f"{upstream.base_url_stripped()}/{upstream.tags_path()}", timeout=8.0)
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
    """Proxy `POST /v1/systemone` to the configured laya-server backend."""
    service: SystemOneService = request.app.state.systemone
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
# so they take precedence and appear in /docs when security.allow_docs is true)
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


@app.get("/api/tags", include_in_schema=True, summary="List aggregated models (Ollama format)", tags=["models"])
async def route_tags(request: Request, _auth=Depends(require_api_key)):
    return await tags_response(request)


@app.get("/api/ps", include_in_schema=True, summary="Aggregated running-model status across upstreams", tags=["models"])
async def route_ps(request: Request, _auth=Depends(require_api_key)):
    return await ps_response(request)


@app.post(
    "/v1/systemone",
    include_in_schema=True,
    summary="System One decision API (proxied to laya-server)",
    tags=["systemone"],
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": (
                "Body is forwarded to laya-server as-is. `state` is a free-form object; "
                "`questions` maps a question id to a tagged question whose `type` selects "
                "the variant: `noul` (yes/no probability), `choice` (single label) or "
                "`score` (pick one of >= 2 criteria). Optional top-level `model` selects "
                "the routing model (`auto` lets laya-server decide)."
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
                "description": "Decision result returned by laya-server (pass-through).",
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


# --------------------------------------------------------------------------- #
# API docs (explicit, key-free, and registered BEFORE the catch-all so the
# catch-all never intercepts these paths). Visibility is still gated by
# security.allow_docs in the middleware above (False -> 404, True -> served).
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
# Unified entrypoint (catch-all proxy for everything not handled above)
# --------------------------------------------------------------------------- #
# Paths documented above that only accept POST. The catch-all below accepts every
# method, so without this guard a GET/PUT/DELETE to e.g. /v1/rerank would be
# proxied to an upstream instead of being rejected with 405.
POST_ONLY_PATHS = frozenset({"/v1/rerank", "/rerank", "/api/rerank", "/v1/systemone"})


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
