"""
LLM Hub - the unified entrypoint FastAPI application.

Intercepts and handles:
  GET  /v1/models     —— aggregates the model list of all upstreams
  POST /v1/rerank     —— reranking endpoint (not supported by Ollama natively)
  GET  /api/tags      —— aggregates the model list in Ollama format
  GET  /health        —— gateway and upstream health status

All other requests (/api/*, /v1/*) are routed to the matching Ollama instance by model name.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import uvicorn
from auth import (
    DOCS_PATHS,
    client_ip,
    find_weak_keys,
    ip_allowed,
    require_api_key,
)
from config import CONFIG_PATH, ConfigManager
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from proxy import ProxyService
from rerank import RerankService

logger = logging.getLogger("llm_hub")

#: Gateway version (was previously exposed via the app package __init__)
__version__ = "1.1.0"

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
    client = httpx.AsyncClient(
        limits=limits,
        timeout=httpx.Timeout(cfg.server.request_timeout, connect=10.0),
        follow_redirects=True,
    )

    app.state.manager = manager
    app.state.client = client
    app.state.proxy = ProxyService(manager, client)
    app.state.rerank = RerankService(manager, client)
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
)


@app.middleware("http")
async def gateway_security_middleware(request: Request, call_next):
    """Per-request hardening of the public surface.

    - hides /docs, /redoc, /openapi.json unless security.allow_docs is true
    - enforces an optional client IP allowlist (security.allowed_client_ips)
    - applies CORS only for origins explicitly listed in security.cors_origins
    """
    manager = getattr(request.app.state, "manager", None)
    if manager is not None:
        sec = manager.config.security
        path = request.url.path.rstrip("/") or "/"

        # hide API docs on a public gateway
        if path in DOCS_PATHS and not sec.allow_docs:
            return JSONResponse({"detail": "Not found"}, status_code=404)

        # optional client IP allowlist
        if not ip_allowed(client_ip(request), sec.allowed_client_ips):
            return JSONResponse(
                {"detail": "Client address is not permitted to access this gateway."},
                status_code=403,
            )

        # CORS (only for explicitly allowed origins)
        origin = request.headers.get("origin")
        if origin and sec.cors_origins:
            allowed = "*" in sec.cors_origins or origin in sec.cors_origins
            if request.method == "OPTIONS" and allowed:
                return JSONResponse(
                    {},
                    status_code=204,
                    headers={
                        "Access-Control-Allow-Origin": origin,
                        "Access-Control-Allow-Methods": "*",
                        "Access-Control-Allow-Headers": "*",
                    },
                )
            if allowed:
                resp = await call_next(request)
                resp.headers["Access-Control-Allow-Origin"] = origin
                return resp

    return await call_next(request)


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
    """Aggregate the model list: explicitly declared models + models discovered via wildcards + aliases.

    Wildcards (e.g. qwen3:*) are never exposed as model names; they are expanded by querying the upstream.
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

    # 1. Models declared explicitly by each upstream (highest priority)
    for up in cfg.upstreams:
        for m in up.models:
            if not m.endswith("*"):
                add(m, up.name, "upstream", wildcard=False)

    # 2. Wildcard declarations: query the upstream and expand
    for up in cfg.upstreams:
        prefixes = [m[:-1] for m in up.models if m.endswith("*")]
        if not prefixes:
            continue
        for name in await fetch_upstream_models(client, up):
            if any(name.startswith(p) for p in prefixes):
                add(name, up.name, "upstream", wildcard=True)

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
# Unified entrypoint
# --------------------------------------------------------------------------- #
@app.api_route("/", methods=HTTP_METHODS, include_in_schema=False)
@app.api_route("/{full_path:path}", methods=HTTP_METHODS, include_in_schema=False)
async def gateway(request: Request, full_path: str = "", _auth=Depends(require_api_key)):
    path = (full_path or "").strip("/")

    if path in ("", "health", "healthz"):
        return await health_response(request)

    if path in ("probe",):
        return await probe_response(request)

    if path in ("v1/models", "models"):
        return await models_response(request)

    if path in ("v1/rerank", "rerank", "api/rerank"):
        return await rerank_response(request)

    if path == "api/tags":
        return await tags_response(request)

    proxy: ProxyService = request.app.state.proxy
    return await proxy.forward(request, path)


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
