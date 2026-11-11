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
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from admin_auth import (
    ADMIN_COOKIE_NAME,
    create_admin_token,
    get_admin_password,
    refresh_due,
    require_admin,
    set_admin_cookie,
)
from auth import (
    DOCS_PATHS,
    docs_allowed,
    extract_api_key,
    is_public,
    is_weak_key,
    peer_ip,
    require_api_key,
)
from bench_store import BenchStore
from bridge import bridge_denial, public_auth_denial
from config import CONFIG_PATH, ConfigManager
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from http_clients import build_clients
from key_store import ApiKeyStore, set_store
from logging_config import DATE_FORMAT, LOG_FORMAT, apply_to_existing_handlers
from management import AdminLogin
from management import login as admin_login
from management import logout as admin_logout
from management import router as management_router
from messages import MessagesService
from proxy import ProxyService
from request_log import RequestLog, basic_entry, now_ts
from rerank import RerankService
from systemone import SystemOneService, backend_key
from websearch import WebSearchService

logger = logging.getLogger("llm_hub")

#: Gateway version (was previously exposed via the app package __init__)
__version__ = "1.2.0"

HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def setup_logging(level: str) -> None:
    numeric = getattr(logging, str(level).upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
    )
    logging.getLogger("llm_hub").setLevel(numeric)
    # basicConfig is a no-op once root already owns a handler (uvicorn's dictConfig ran first),
    # so pin the level explicitly - otherwise `--log-config logging.json` would leave root at INFO.
    logging.getLogger().setLevel(numeric)
    # A plain `uvicorn main:app` has already installed uvicorn's stock handlers (no timestamps);
    # serve.py instead handed uvicorn logging.json, making this a no-op.
    apply_to_existing_handlers(str(level))


# --------------------------------------------------------------------------- #
# Lifespan
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    manager = ConfigManager()
    cfg = manager.config
    setup_logging(cfg.server.log_level)

    # Two outbound pools. `client` stays the general-purpose one used by probes,
    # System One, web search and the management API. `inference_client` is the
    # queued concurrency gate that protects local model backends (see
    # http_clients.py): a burst waits for a free slot instead of being refused.
    control_client, inference_client = build_clients(cfg)

    app.state.manager = manager
    app.state.client = control_client
    app.state.inference_client = inference_client
    app.state.request_log = RequestLog()
    await app.state.request_log.start()
    app.state.proxy = ProxyService(manager, inference_client, app.state.request_log)
    app.state.rerank = RerankService(manager, inference_client)
    app.state.systemone = SystemOneService(manager, control_client)
    app.state.websearch = WebSearchService(manager, control_client)
    app.state.messages = MessagesService(manager, inference_client)
    app.state.bench_store = BenchStore()
    await app.state.bench_store.start()
    app.state.api_keys = ApiKeyStore()
    await app.state.api_keys.start()
    set_store(app.state.api_keys)
    app.state.started_at = time.time()

    # Zone state comes from config.yaml only. serve.py binds exactly these sockets in
    # this same process, so the listening socket remains an unforgeable trust anchor.
    public_ports = sorted(cfg.public_ports())
    listen_ports = cfg.listen_ports()
    internal_ports = [p for p in listen_ports if p not in public_ports]
    public = cfg.security.public
    app.state.public_ports = cfg.public_ports()
    app.state.listen_ports = listen_ports
    app.state.listen_endpoints = f"{cfg.server.host}:{','.join(str(p) for p in listen_ports)}"

    # Zone self-check. The listening socket is the only trust anchor; the public port
    # comes from security.public.port, never from an environment variable.
    if public.port is not None:
        rule_count = sum(len(patterns) for patterns in public.path_allowlist.values())
        logger.info(
            "Public zone listener %s: %d configured method+path rule(s) (%s). Only these "
            "paths exist here; every surviving request needs a valid caller key or console "
            "session. The management API and /admin are always 404.",
            public.port,
            rule_count,
            "; ".join(public.visible_paths()) or "none",
        )
        if not public.path_allowlist:
            logger.warning("security.public.path_allowlist is empty: the public listener serves no route")
        # `admin_endpoints` is only half of the answer: the mode decides whether an admin
        # call is refused, `path_allowlist` decides whether the path exists at all. Report
        # the intersection, because "I set allow and nothing changed" is almost always a
        # missing POST rule, and a 404 does not say so.
        reachable_admin = public.reachable_admin_paths()
        if public.admin_endpoints == "deny":
            logger.info(
                "Public admin endpoints: all blocked (security.public.admin_endpoints=deny); "
                "Ollama model management, /admin and /v1/management/* stay internal-only"
            )
        elif public.admin_endpoints == "allow":
            logger.warning(
                "security.public.admin_endpoints=allow OPENS THE MANAGEMENT PLANE on the public "
                "listener %s: /admin and /v1/management/* are reachable there and are routed to "
                "their real handlers (path_allowlist is not consulted for them). They are never "
                "anonymous - only the console shell and POST /v1/management/auth/login skip the "
                "credential check, everything else needs the session cookie, and a gateway API "
                "key is not an admin credential. Ollama admin calls are forwarded untouched: %s. "
                "Keep ADMIN_PASSWORD at 32+ random characters and leave security.rate_limit on.",
                public.port,
                ", ".join(reachable_admin)
                or "no Ollama admin path is listed, so only the console and the management API are reachable",
            )
        elif reachable_admin:
            logger.info(
                "Public admin endpoints (admin_endpoints=%s): %s",
                public.admin_endpoints,
                ", ".join(reachable_admin),
            )
        else:
            logger.warning(
                "security.public.admin_endpoints=%s has no effect: no security.public.path_allowlist "
                "rule admits an Ollama admin endpoint, so POST /api/pull and friends still answer 404 "
                "(the allowlist is the surface, the mode only lifts the block). Add e.g. "
                "POST: ['^/api/pull$'] for readonly, or the push/create/delete/copy paths plus "
                "'^/api/blobs/.*$' for allow",
                public.admin_endpoints,
            )
        if internal_ports:
            logger.info(
                "Internal zone listener(s) %s: normal APIs are %s; /docs is %s, /admin is "
                "reachable, /health stays anonymous for the container HEALTHCHECK, and the "
                "management API still requires the console cookie. Publish this port on the LAN only.",
                internal_ports,
                "key-free" if cfg.security.internal.allow_anonymous else "key-required",
                "reachable" if cfg.security.internal.expose_docs else "404",
            )
        else:
            logger.warning(
                "Public-only instance: %s is the sole socket, so there is NO anonymous HTTP "
                "path. Point the container HEALTHCHECK at a TCP port check instead.",
                public.port,
            )
    else:
        logger.info(
            "Internal-only instance: %s is the trusted LAN zone (%s). To expose it to the "
            "internet set security.public.port to the SAME port (public-only) or to a second "
            "port (internal + public). Two host-port mappings to one container socket do NOT "
            "create two zones.",
            listen_ports,
            "key-free APIs" if cfg.security.internal.allow_anonymous else "key-required APIs",
        )
        if cfg.server.host in {"0.0.0.0", "::", "*"} and cfg.security.internal.allow_anonymous:
            logger.error(
                "INTERNAL ZONE IS FULLY OPEN: server.host=%s binds every interface and "
                "security.internal.allow_anonymous=true. Restrict the bind address to the "
                "LAN, set allow_anonymous=false, or declare security.public.port.",
                cfg.server.host,
            )

    # Outbound signing for the System One proxy: the caller's gateway key authenticates
    # *into* llm-hub, while the backend key below is what llm-hub sends *out*. Naming the
    # variable at startup is the only place an operator can see it without reading code.
    if cfg.systemone.enabled:
        so = cfg.systemone
        authorization, unsigned_reason = backend_key(so.api_key_env)
        if so.api_key_env:
            logger.log(
                logging.WARNING if unsigned_reason else logging.INFO,
                "System One: POST /v1/systemone -> %s%s | model=%s | backend key from $%s (%s)",
                so.backend.rstrip("/"),
                "/" + so.path.strip("/"),
                so.model,
                so.api_key_env,
                unsigned_reason or "set",
            )
        else:
            # Empty `api_key_env` is a valid unsigned mode, but it is also the usual way
            # to lose the backend key by accident. Say so ONCE at startup, with a name.
            logger.warning(
                "System One: POST /v1/systemone -> %s%s | model=%s | UNSIGNED: "
                "systemone.api_key_env is empty (set it to SYSTEMONE_SERVER_API_KEY if the "
                "backend requires a key; an Ollama 0.40+ backend ignores Authorization).",
                so.backend.rstrip("/"),
                "/" + so.path.strip("/"),
                so.model,
            )

    # ADMIN_PASSWORD doubles as the HMAC key that signs console session cookies:
    # guessing it lets an attacker mint a token with any expiry. Warn (without
    # ever logging the value) instead of refusing, so a local console still works.
    if admin_password := get_admin_password():
        if weak_reason := is_weak_key(admin_password):
            # A value copied verbatim from .env.example is a *published* password.
            # Refuse rather than warn: a warning scrolls past, the console stays open
            # to the internet, and online brute-force is no longer even necessary.
            if "placeholder" in weak_reason or "example" in weak_reason:
                raise RuntimeError(
                    "Refusing to start: ADMIN_PASSWORD is a documented example/placeholder value. "
                    "Generate a random >=32 character password before exposing the console."
                )
            logger.warning(
                "Weak ADMIN_PASSWORD (%s): it signs console session cookies, use >=16 random chars",
                weak_reason,
            )
    if public.port is not None and not cfg.security.rate_limit.enabled:
        logger.warning(
            "security.rate_limit is disabled while a public listener is open; enable it "
            "(or rate-limit at the reverse proxy). Console sign-in has its own failure "
            "lockout, but other endpoints are unthrottled."
        )

    # Caller keys are rows in llmhub.db and are minted in the console. Nothing seeds them any
    # more: no environment variable, no key file, no config node. An empty table is therefore a
    # normal first-run state - every request that needs a key simply answers 401 - and it is a
    # dead end only when the console cannot mint one either, i.e. without ADMIN_PASSWORD.
    if app.state.api_keys.count() == 0:
        if not admin_password:
            raise RuntimeError(
                "Refusing to start: the api_keys table at "
                f"{app.state.api_keys.db_path} is empty and ADMIN_PASSWORD is not set, so no "
                "credential can ever exist - callers cannot authenticate and the console cannot "
                "mint a key. Set ADMIN_PASSWORD, start the gateway, then create the first key at "
                "/admin -> API keys."
            )
        logger.warning(
            "The api_keys table at %s is empty: every request that needs a caller key answers "
            "401 until the first key exists. Sign in at /admin with ADMIN_PASSWORD and mint one "
            "at API keys -> Create. Keys come from the console only.",
            app.state.api_keys.db_path,
        )

    # serve.py binds exactly cfg.listen_ports(); config.yaml is the single source.
    logger.info(
        "LLM Hub started | config=%s | upstreams=%d | aliases=%d | listening=%s | public=%s | "
        "inference concurrency=%d (queue timeout=%.0fs)",
        CONFIG_PATH,
        len(cfg.upstreams),
        len(cfg.aliases),
        app.state.listen_endpoints,
        ",".join(str(p) for p in public_ports) or "none",
        cfg.server.max_concurrency,
        cfg.server.upstream_queue_timeout,
    )
    try:
        yield
    finally:
        await app.state.request_log.stop()
        if getattr(app.state, "bench_store", None) is not None:
            await app.state.bench_store.stop()
        await control_client.aclose()
        await inference_client.aclose()
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

# Filled from config.yaml by lifespan() and used by serve.py / the startup log.
# An empty value at import time keeps module import side-effect free; auth.zone()
# reads the live Config object from app.state.manager, not this cached set.
app.state.public_ports = frozenset()
app.state.listen_ports: list[int] = []


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


def _deny(status_code: int, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
    response = JSONResponse({"detail": detail}, status_code=status_code)
    for name, value in (headers or {}).items():
        response.headers[name] = value
    return _harden_response(response)


# --------------------------------------------------------------------------- #
# In-memory token-bucket rate limiter (single process)
# --------------------------------------------------------------------------- #
_RATE_BUCKETS: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)


def _rate_limit_key(rl, request: Request) -> str:
    if rl.by == "key":
        k = extract_api_key(request)
        if k:
            return "key:" + k
    return "ip:" + (peer_ip(request) or "unknown")


def _rate_limited(rl, request: Request) -> bool:
    """Return True when the request should be throttled (HTTP 429)."""
    key = _rate_limit_key(rl, request)
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


def _renew_admin_session(request: Request, response: Response) -> None:
    """Sliding renewal: an actively used console session never hard-expires.

    Once a valid session is past half its TTL, re-issue the cookie with a fresh
    expiry. Runs on successful responses only, so a rejected request cannot
    keep a stolen session alive.
    """
    password = get_admin_password()
    if not password:
        return
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if refresh_due(token, password):
        set_admin_cookie(response, request, create_admin_token(password))


@app.middleware("http")
async def gateway_security_middleware(request: Request, call_next):
    """Per-request hardening, evaluated in this order:

    0. public listener -> config-driven method+path allowlist (404 for anything
       not explicitly listed; management API and /admin are always 404)
    1. body size cap                                  -> 413
    2. /docs, /openapi.json, Swagger assets            -> 404 outside internal
    3. rate limit (security.rate_limit)               -> 429
    4. CORS only for explicitly listed origins        -> 204 / header
    5. public listener -> caller key or console session -> 401 / 403

    Steps 0 and 5 are the two halves of the public-zone policy and live in
    ``bridge.py``: an unlisted path never exists on that socket, and a listed one
    is never anonymous. Neither waives the other.

    There is no global HTTP-method gate and no global path allowlist any more.
    On the public listener BOTH are expressed by ``security.public.path_allowlist``;
    on the internal listener the trusted LAN may use any verb the routes define.
    """
    manager = getattr(request.app.state, "manager", None)
    cfg = manager.config if manager is not None else None
    sec = cfg.security if cfg is not None else None
    path = request.url.path
    method = request.method.upper()
    cors_origin = ""

    # ---- request-log envelope for calls that never reach ProxyService.forward ----
    # The rich row (bodies / tokens / upstream / stream) is built inside the proxy.
    # Two kinds of traffic bypass it entirely: (a) this middleware refuses them
    # before call_next, and (b) the in-process handlers (systemone, web-search,
    # rerank, messages, models, tags, ps, health, probe, root) answer on their own.
    # Both must still show up in the console's Requests view, so we write a
    # metadata-only row here. A claim flag on the shared ASGI scope (set by the
    # proxy once it has taken ownership) keeps the two writers from double-logging.
    started = time.perf_counter()
    arrived = now_ts()
    log_store = getattr(request.app.state, "request_log", None)
    # HEAD/OPTIONS are liveness/pre-flight probes and stay out of the log, matching
    # the proxy's own rule. Management/console/docs paths are control-plane noise:
    # the console polls them continuously and they are not gateway API calls.
    noisy_path = (
        path.rstrip("/") in DOCS_PATHS
        or path.rstrip("/") in ("/redoc", "/admin")
        or path.startswith(("/static/swagger/", "/admin/", "/v1/management"))
    )
    log_enabled = (
        log_store is not None
        and cfg is not None
        and cfg.request_log.enabled
        and method not in ("HEAD", "OPTIONS")
        and not noisy_path
    )

    def record_basic(status_code: int, detail: str | None = None, *, resp=None) -> None:
        """Queue a metadata-only row when no proxy row will be written for this call."""
        if not log_enabled:
            return
        try:
            req_size = int(value) if (value := request.headers.get("content-length")) and value.isdigit() else None
            res_size = None
            if resp is not None and (value := resp.headers.get("content-length")) and value.isdigit():
                res_size = int(value)
            log_store.record(
                basic_entry(
                    method=method,
                    path=path,
                    status=status_code,
                    ts=arrived,
                    started=started,
                    client_ip=peer_ip(request),
                    query=request.url.query,
                    req_size=req_size,
                    res_size=res_size,
                    error=detail or (f"HTTP {status_code}" if status_code >= 400 else None),
                )
            )
        except Exception as exc:  # logging must never change the response path
            logger.debug("Request log: skipped metadata row for %s %s: %s", method, path, exc)

    def deny(status_code: int, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
        record_basic(status_code, detail)
        return _deny(status_code, detail, headers)

    # 0. Public zone: the config file is the COMPLETE list of reachable routes.
    #    This runs first so an unlisted path never leaks a different status
    #    through another rule (and is 404 even when the caller has a valid key).
    if is_public(request) and cfg is not None:
        denial = bridge_denial(method, path, cfg)
        if denial is not None:
            return deny(*denial)

    if sec is not None:
        # 1. Reject oversized bodies by declaration BEFORE the route reads a byte.
        #    Services without Content-Length are covered by auth.read_limited_body's
        #    streaming counter, so both transports have a hard cap.
        if sec.max_body_bytes and sec.max_body_bytes > 0:
            declared = request.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > sec.max_body_bytes:
                return deny(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Request body too large.")

        # 2. API docs and their vendored Swagger assets are internal-only.
        if (
            path.rstrip("/") in DOCS_PATHS or path.startswith("/static/swagger/")
        ) and not docs_allowed(request, cfg):
            return deny(status.HTTP_404_NOT_FOUND, "Not found.")

        # /redoc was intentionally removed (superseded by /docs); return a clean
        # 404 instead of leaking into the catch-all (which would demand an API key).
        if path.rstrip("/") == "/redoc":
            return deny(status.HTTP_404_NOT_FOUND, "Not found.")

        # 3. rate limit -------------------------------------------------------
        if sec.rate_limit.enabled and _rate_limited(sec.rate_limit, request):
            logger.warning("Rate limit exceeded: %s %r", method, path)
            limited = deny(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests.")
            limited.headers["Retry-After"] = "1"
            return limited

        # 4. CORS (only for explicitly allowed origins) -----------------------
        origin = request.headers.get("origin")
        if origin and sec.cors_origins:
            allowed = "*" in sec.cors_origins or origin in sec.cors_origins
            if method == "OPTIONS" and allowed:
                # A pre-flight answer must be a bodyless 204: Response omits
                # content-length for 204/304, so a JSONResponse would still send
                # its "{}" bytes and uvicorn drops the connection with
                # "Response content longer than Content-Length".
                return _harden_response(
                    Response(
                        status_code=204,
                        headers={
                            "Access-Control-Allow-Origin": origin,
                            "Access-Control-Allow-Methods": "*",
                            "Access-Control-Allow-Headers": "*",
                        },
                    )
                )
            if allowed:
                cors_origin = origin

    # 6. Public zone: the credential belongs to the zone policy, not to whichever
    #    dependencies a route happens to declare. Runs after the rate limiter (a
    #    throttled caller still sees 429) and after the CORS pre-flight answer (a
    #    pre-flight carries no credentials by design).
    if cfg is not None and is_public(request):
        denial = await public_auth_denial(request, cfg)
        if denial is not None:
            status_code, detail, auth_headers = denial
            headers = dict(auth_headers)
            if cors_origin:
                headers["Access-Control-Allow-Origin"] = cors_origin
            return deny(status_code, detail, headers)

    resp = await call_next(request)
    _renew_admin_session(request, resp)
    if cors_origin:
        resp.headers["Access-Control-Allow-Origin"] = cors_origin
    # The proxy claims ownership of a request in forward(); anything else that
    # reached a route (standalone handlers, FastAPI 404s) has no row yet.
    if log_enabled and not getattr(request.state, "request_log_claimed", False):
        record_basic(resp.status_code, resp=resp)
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
# so they take precedence and appear in /docs, which is served only on the
# internal zone when security.internal.expose_docs is true)
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
    """Return the system name; the exact version is opt-in for anonymous probes."""
    manager = getattr(request.app.state, "manager", None)
    cfg = getattr(manager, "config", None)
    expose = cfg.security.internal.expose_version if cfg is not None else True
    payload = {"name": app.title}
    if expose:
        payload["version"] = __version__
    return JSONResponse(payload)


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


# The remaining pass-through families, documented the same way: the handler is a
# one-liner into ProxyService.forward(), so routing, alias + parameter injection,
# num_ctx clamping, streaming and request-log capture are unchanged. Registering
# them explicitly puts them in /openapi.json and makes POST_ONLY_PATHS answer 405
# for another verb instead of forwarding it.
@app.post(
    "/api/chat",
    include_in_schema=True,
    summary="Ollama-native chat (routed by model, passed through)",
    tags=["chat"],
    openapi_extra={
        "description": (
            "Forwarded to the upstream that owns `model`, exactly like the catch-all "
            "proxy. Unknown fields are preserved; `stream` may be omitted because "
            "Ollama streams `/api/chat` by default."
        ),
        "requestBody": {
            "required": True,
            "description": "Ollama chat request; `model` goes through alias resolution first.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["model", "messages"],
                        "properties": {
                            "model": {"type": "string", "description": "Model name or gateway alias."},
                            "messages": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "required": ["role", "content"],
                                    "properties": {
                                        "role": {"type": "string", "enum": ["system", "user", "assistant", "tool"]},
                                        "content": {"type": "string"},
                                        "images": {"type": "array", "items": {"type": "string"}},
                                        "tool_calls": {"type": "array", "items": {"type": "object"}},
                                    },
                                },
                            },
                            "tools": {"type": "array", "items": {"type": "object"}},
                            "format": {
                                "description": "\"json\" or a JSON schema for structured output.",
                                "oneOf": [{"type": "string", "enum": ["json"]}, {"type": "object"}],
                            },
                            "stream": {"type": "boolean", "description": "Default true (NDJSON); false returns one JSON object."},
                            "think": {"description": "Thinking mode: true / false, or a budget such as 8000."},
                            "keep_alive": {"description": "How long the model stays loaded, e.g. \"3m\" or 0."},
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
                        "messages": [{"role": "user", "content": "hello"}],
                        "stream": False,
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
                            "message": {"role": "assistant", "content": "Hello! What can I help you with?"},
                            "done": True,
                            "prompt_eval_count": 5,
                            "eval_count": 42,
                        }
                    },
                    "application/x-ndjson": {
                        "example": '{"model":"qwen3:8b","message":{"role":"assistant","content":"Hello"},"done":false}\n'
                        '{"model":"qwen3:8b","done":true,"done_reason":"stop","eval_count":42}\n'
                    },
                },
            },
            "404": {"description": "No upstream serves the requested model and routing is strict."},
            "405": {"description": "`/api/chat` only accepts POST."},
        },
    },
)
async def route_chat(request: Request, _auth=Depends(require_api_key)):
    proxy: ProxyService = request.app.state.proxy
    return await proxy.forward(request, "api/chat")


@app.post(
    "/v1/chat/completions",
    include_in_schema=True,
    summary="OpenAI-compatible chat completions (routed by model, passed through)",
    tags=["chat"],
    openapi_extra={
        "description": (
            "Forwarded to the upstream that owns `model`. Claude SDK / Claude Code clients "
            "should use `/v1/messages` instead: same routing, different envelope."
        ),
        "requestBody": {
            "required": True,
            "description": "OpenAI chat request; `model` goes through alias resolution first.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["model", "messages"],
                        "properties": {
                            "model": {"type": "string", "description": "Model name or gateway alias."},
                            "messages": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "required": ["role", "content"],
                                    "properties": {
                                        "role": {"type": "string", "enum": ["system", "developer", "user", "assistant", "tool"]},
                                        "content": {"description": "String or a list of content parts."},
                                    },
                                },
                            },
                            "temperature": {"type": "number", "minimum": 0, "maximum": 2},
                            "top_p": {"type": "number"},
                            "max_tokens": {"type": "integer", "description": "Generation budget (OpenAI-compatible backends do not read num_ctx)."},
                            "n": {"type": "integer"},
                            "stop": {"description": "Stop sequence or list of them."},
                            "stream": {"type": "boolean", "description": "Default false here; SSE chunks when true."},
                            "reasoning_effort": {
                                "type": "string",
                                "enum": ["none", "low", "medium", "high"],
                                "description": "OpenAI-compatible reasoning effort; on Ollama `none` disables thinking.",
                            },
                            "tools": {"type": "array", "items": {"type": "object"}},
                            "response_format": {"type": "object"},
                            "seed": {"type": "integer"},
                        },
                        "additionalProperties": True,
                    },
                    "example": {
                        "model": "qwen3:8b",
                        "messages": [
                            {"role": "system", "content": "You are a helpful assistant"},
                            {"role": "user", "content": "What is machine learning?"},
                        ],
                        "temperature": 0.7,
                        "max_tokens": 500,
                        "stream": False,
                    },
                }
            },
        },
        "responses": {
            "200": {
                "description": "Upstream response passed through unchanged (SSE when streaming, one JSON object otherwise).",
                "content": {
                    "application/json": {
                        "example": {
                            "id": "chatcmpl-abc123",
                            "object": "chat.completion",
                            "created": 1694000000,
                            "model": "qwen3:8b",
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {"role": "assistant", "content": "Machine learning is a branch of AI..."},
                                    "finish_reason": "stop",
                                }
                            ],
                            "usage": {"prompt_tokens": 25, "completion_tokens": 100, "total_tokens": 125},
                        }
                    },
                    "text/event-stream": {
                        "example": 'data: {"choices":[{"delta":{"content":"Machine"},"index":0}]}\n\n'
                        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                        "data: [DONE]\n\n"
                    },
                },
            },
            "404": {"description": "No upstream serves the requested model and routing is strict."},
            "405": {"description": "`/v1/chat/completions` only accepts POST."},
        },
    },
)
async def route_chat_completions(request: Request, _auth=Depends(require_api_key)):
    proxy: ProxyService = request.app.state.proxy
    return await proxy.forward(request, "v1/chat/completions")


@app.post(
    "/v1/completions",
    include_in_schema=True,
    summary="OpenAI-compatible text completions (routed by model, passed through)",
    tags=["generation"],
    openapi_extra={
        "description": "Forwarded to the upstream that owns `model`, exactly like the catch-all proxy.",
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["model"],
                        "properties": {
                            "model": {"type": "string", "description": "Model name or gateway alias."},
                            "prompt": {"description": "String or list of strings."},
                            "max_tokens": {"type": "integer"},
                            "temperature": {"type": "number"},
                            "top_p": {"type": "number"},
                            "logprobs": {"type": "integer"},
                            "echo": {"type": "boolean"},
                            "suffix": {"type": "string"},
                            "stop": {"description": "Stop sequence or list of them."},
                            "stream": {"type": "boolean", "description": "Default false here; SSE chunks when true."},
                        },
                        "additionalProperties": True,
                    },
                    "example": {"model": "qwen3:8b", "prompt": "写一首关于编程的诗：", "max_tokens": 256, "stream": False},
                }
            },
        },
        "responses": {
            "200": {
                "description": "Upstream response passed through unchanged (SSE when streaming, one JSON object otherwise).",
                "content": {
                    "application/json": {
                        "example": {
                            "id": "cmpl-abc123",
                            "object": "text_completion",
                            "model": "qwen3:8b",
                            "choices": [{"index": 0, "text": "代码如诗行……", "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": 12, "completion_tokens": 50, "total_tokens": 62},
                        }
                    },
                    "text/event-stream": {
                        "example": 'data: {"choices":[{"text":"代码","index":0}]}\n\ndata: [DONE]\n\n'
                    },
                },
            },
            "404": {"description": "No upstream serves the requested model and routing is strict."},
            "405": {"description": "`/v1/completions` only accepts POST."},
        },
    },
)
async def route_completions(request: Request, _auth=Depends(require_api_key)):
    proxy: ProxyService = request.app.state.proxy
    return await proxy.forward(request, "v1/completions")


@app.post(
    "/api/embed",
    include_in_schema=True,
    summary="Ollama-native embeddings (routed by model, passed through)",
    tags=["embeddings"],
    openapi_extra={
        "description": (
            "Forwarded to the upstream that owns `model`. This is the embedding dialect "
            "Ollama answers natively; `/v1/embeddings` is the OpenAI-compatible side of the "
            "same capability, which is also what LM Studio and OpenCode expose."
        ),
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["model", "input"],
                        "properties": {
                            "model": {"type": "string", "description": "Embedding model name or alias."},
                            "input": {"description": "A string, or a list of strings.", "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                            "images": {"type": "array", "items": {"type": "string"}, "description": "Base64 images for multimodal embedders."},
                            "truncate": {"type": "boolean", "description": "Cut an over-long input instead of failing (Ollama default: true)."},
                            "keep_alive": {"description": "How long the model stays loaded, e.g. \"3m\" or 0."},
                        },
                        "additionalProperties": True,
                    },
                    "example": {"model": "bge-m3:latest", "input": ["hello world", "intro to machine learning"]},
                }
            },
        },
        "responses": {
            "200": {
                "description": "Upstream response passed through unchanged.",
                "content": {
                    "application/json": {
                        "example": {
                            "model": "bge-m3:latest",
                            "embeddings": [[0.123, 0.456, 0.789], [0.987, 0.654, 0.321]],
                        }
                    }
                },
            },
            "404": {
                "description": "No upstream serves the requested model (or the target only answers the OpenAI-compatible side)."
            },
            "405": {"description": "`/api/embed` only accepts POST."},
        },
    },
)
async def route_embed(request: Request, _auth=Depends(require_api_key)):
    proxy: ProxyService = request.app.state.proxy
    return await proxy.forward(request, "api/embed")


@app.post(
    "/v1/embeddings",
    include_in_schema=True,
    summary="OpenAI-compatible embeddings (routed by model, passed through)",
    tags=["embeddings"],
    openapi_extra={
        "description": "Forwarded to the upstream that owns `model`, exactly like the catch-all proxy.",
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["model", "input"],
                        "properties": {
                            "model": {"type": "string", "description": "Embedding model name or alias."},
                            "input": {
                                "description": "A string, a list of strings, or a list of token ids.",
                                "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                            },
                            "encoding_format": {"type": "string", "enum": ["float", "base64"]},
                            "dimensions": {"type": "integer"},
                            "user": {"type": "string"},
                        },
                        "additionalProperties": True,
                    },
                    "example": {"model": "bge-m3:latest", "input": ["hello world", "intro to machine learning"]},
                }
            },
        },
        "responses": {
            "200": {
                "description": "Upstream response passed through unchanged.",
                "content": {
                    "application/json": {
                        "example": {
                            "object": "list",
                            "model": "bge-m3:latest",
                            "data": [
                                {"object": "embedding", "index": 0, "embedding": [0.123, 0.456, 0.789]},
                                {"object": "embedding", "index": 1, "embedding": [0.987, 0.654, 0.321]},
                            ],
                            "usage": {"prompt_tokens": 8, "total_tokens": 8},
                        }
                    }
                },
            },
            "404": {"description": "No upstream serves the requested model and routing is strict."},
            "405": {"description": "`/v1/embeddings` only accepts POST."},
        },
    },
)
async def route_openai_embeddings(request: Request, _auth=Depends(require_api_key)):
    proxy: ProxyService = request.app.state.proxy
    return await proxy.forward(request, "v1/embeddings")


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
# catch-all never intercepts these paths). Visibility is gated by the listener
# zone in the middleware above: only the internal zone may serve them, and only
# when security.internal.expose_docs is true. The public zone always returns 404.
# --------------------------------------------------------------------------- #
# Local vendored Swagger UI assets (no public CDN dependency -> works offline).
SWAGGER_STATIC_DIR = Path(__file__).parent / "static" / "swagger"


@app.get("/static/swagger/{file_path:path}", include_in_schema=False, tags=["docs"])
async def serve_swagger_static(file_path: str):
    """Serve the vendored Swagger UI assets locally (offline, no CDN)."""
    root = SWAGGER_STATIC_DIR.resolve()
    try:
        candidate = (root / file_path).resolve()
    except (ValueError, OSError):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    # `str().startswith()` also accepted sibling directories such as `swaggerX/`.
    if candidate.is_file() and candidate.is_relative_to(root):
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
    try:
        candidate = (CONSOLE_DIST / asset_path).resolve()
        # resolve() + relative check: ../ segments can never escape the built directory
        allowed = candidate.is_file() and candidate.is_relative_to(CONSOLE_DIST)
    except (ValueError, OSError):
        # A %00 in the path makes Path.resolve() raise; answer a clean 404 instead of
        # letting the exception escape as a per-request 500.
        allowed = False
    if allowed:
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
# Paths documented above that only accept POST. Starlette prefers the catch-all's
# full match over the documented route's partial (wrong-method) match, so without
# this guard a GET/PUT/DELETE to e.g. /v1/rerank or /api/chat would be proxied to
# an upstream instead of being rejected with 405.
POST_ONLY_PATHS = frozenset(
    {
        "/v1/rerank",
        "/rerank",
        "/api/rerank",
        "/v1/systemone",
        "/v1/tools/web-search",
        "/api/generate",
        "/api/chat",
        "/v1/chat/completions",
        "/v1/completions",
        "/api/embed",
        "/v1/embeddings",
        "/v1/messages",
        "/v1/message",
    }
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
    """`python main.py` delegates to the hardened runner (no proxy-header rewrite).

    Pass the already-created `app` object: importing `main` a second time would
    build a second application instance with a separate ApiKeyStore.
    """
    from serve import run as serve_run

    cfg = ConfigManager().config
    setup_logging(cfg.server.log_level)
    serve_run(
        app,
        host=cfg.server.host,
        port=cfg.server.port,
        public_port=cfg.security.public.port,
        log_level=cfg.server.log_level,
    )


if __name__ == "__main__":
    main()
