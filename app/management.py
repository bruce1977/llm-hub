"""
Read-only management API for the web console (``/v1/management``).

Every route except login/logout depends on :func:`admin_auth.require_admin`, so it
accepts only the signed admin session cookie minted by ``POST /auth/login``; the admin
password is a login-page credential, never a request credential. Nothing here writes
configuration: the console is a projection of ``config.json`` plus live probes of the
upstreams.

Live probes reuse the same ``httpx.AsyncClient`` as forwarding, with a short timeout
so one dead upstream never stalls the whole view.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_auth import (
    ADMIN_COOKIE_NAME,
    ADMIN_SESSION_TTL,
    create_admin_token,
    get_admin_password,
    require_admin,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi import status as http_status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from request_log import _DISTRIBUTION_KEYS, MAX_PAGE_SIZE, normalize_ts, now_ts

logger = logging.getLogger("llm_hub")

router = APIRouter(prefix="/v1/management", tags=["management"])

GiB = 1024**3
MASK = "••••••"
PROBE_TIMEOUT = 5.0
_STATUS_FILTERS = {"2xx", "3xx", "4xx", "5xx"}


# --------------------------------------------------------------------------- #
# Auth (login/logout are public; everything else requires an admin session)
# --------------------------------------------------------------------------- #
class AdminLogin(BaseModel):
    password: str


async def login(request: Request, body: AdminLogin) -> Response:
    """Validate the admin password and mint a signed session cookie."""
    key = get_admin_password()
    if not key:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin is not configured: set the ADMIN_PASSWORD environment variable.",
        )
    # Constant-time compare; a small delay blunts online brute-force.
    if not hmac.compare_digest(body.password, key):
        await asyncio.sleep(0.1)
        raise HTTPException(status_code=http_status.HTTP_401_UNAUTHORIZED, detail="Invalid admin password.")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        ADMIN_COOKIE_NAME,
        create_admin_token(key),
        httponly=True,
        samesite="lax",
        max_age=ADMIN_SESSION_TTL,
        secure=request.url.scheme == "https",
    )
    return resp


def logout() -> Response:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(ADMIN_COOKIE_NAME)
    return resp


@router.post("/auth/login", summary="Admin login (sets a session cookie)", tags=["management"])
async def route_login(request: Request, body: AdminLogin):
    return await login(request, body)


@router.post("/auth/logout", summary="Admin logout (clears the session cookie)", tags=["management"])
async def route_logout():
    return logout()


@router.get("/auth/ping", summary="Admin session heartbeat", tags=["management"])
async def route_ping(_=Depends(require_admin)):
    return JSONResponse({"ok": True, "admin": True})


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _log(request: Request):
    log = getattr(request.app.state, "request_log", None)
    if log is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Request log is not available.",
        )
    return log


def _upstream_headers(upstream) -> dict[str, str] | None:
    custom = getattr(upstream, "headers", None)
    if isinstance(custom, dict) and custom:
        return {str(k): str(v) for k, v in custom.items()}
    return None


def _masked_key(upstream) -> str | None:
    """Never echo a credential: any configured auth header becomes a fixed mask."""
    headers = getattr(upstream, "headers", None)
    if isinstance(headers, dict):
        for name, value in headers.items():
            if str(name).lower() in {"authorization", "x-api-key"} and value:
                return MASK
    return None


def _device_entry(model: dict[str, Any], upstream_name: str, cfg) -> dict[str, Any]:
    size = model.get("size") or 0
    vram = model.get("size_vram") or 0
    ratio = (vram / size) if size else 0.0
    if ratio >= 0.999:
        device = "gpu"
    elif ratio > 0:
        device = "partial"
    else:
        device = "cpu"
    name = model.get("name") or model.get("model")
    num_ctx = model.get("context_length") or model.get("num_ctx") or cfg.num_ctx_overrides.get(name)
    return {
        "model": name,
        "upstream": upstream_name,
        "device": device,
        "device_ratio": round(ratio, 3),
        "num_ctx": num_ctx,
        "size_gib": round(size / GiB, 2),
        "vram_gib": round(vram / GiB, 2),
        "expires_at": model.get("expires_at"),
    }


async def _probe_upstream(upstream, client, cfg) -> dict[str, Any]:
    """Health + (for Ollama) /api/ps probe for one upstream."""
    info: dict[str, Any] = {
        "name": upstream.name,
        "type": upstream.type,
        "base_url": upstream.base_url,
        "description": getattr(upstream, "description", ""),
        "status": "offline",
        "reachable": False,
        "probe_ms": None,
        "loaded": None,
        "vram_used_gib": 0.0,
        "vram_total_gib": getattr(upstream, "vram_total_gb", None),
        "running_models": [],
        "ps_supported": upstream.type == "ollama",
        "error": None,
    }
    headers = _upstream_headers(upstream)
    base = upstream.base_url_stripped()
    started = time.perf_counter()
    try:
        resp = await client.get(f"{base}/{upstream.health_path()}", headers=headers, timeout=PROBE_TIMEOUT)
        info["probe_ms"] = round((time.perf_counter() - started) * 1000)
        info["reachable"] = resp.status_code < 500
        info["status"] = "online" if resp.status_code < 500 else "offline"
        if resp.status_code >= 500:
            info["error"] = f"HTTP {resp.status_code}"
    except Exception as exc:
        info["probe_ms"] = round((time.perf_counter() - started) * 1000)
        info["error"] = str(exc)
        return info

    if upstream.type != "ollama":
        return info

    try:
        resp = await client.get(f"{base}/api/ps", headers=headers, timeout=PROBE_TIMEOUT)
        resp.raise_for_status()
        models = [m for m in resp.json().get("models", []) if isinstance(m, dict)]
    except Exception as exc:
        info["error"] = f"/api/ps: {exc}"
        return info

    info["running_models"] = [_device_entry(m, upstream.name, cfg) for m in models]
    info["loaded"] = len(info["running_models"])
    info["vram_used_gib"] = round(sum((m.get("size_vram") or 0) for m in models) / GiB, 1)
    return info


async def _probe_all(cfg, client) -> list[dict[str, Any]]:
    if not cfg.upstreams:
        return []
    return list(await asyncio.gather(*[_probe_upstream(up, client, cfg) for up in cfg.upstreams]))


def _request_log_state(cfg, log) -> dict[str, Any]:
    """Recording switch plus where the rows live, so the console can show its state."""
    return {
        "enabled": bool(cfg.request_log.enabled),
        "db": str(log.db_path),
        "retention_days": log.retention_days,
        "dropped": log.dropped,
    }


def _public_upstream(info: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in info.items() if k != "running_models"}


def _resolve_range(start: str | None, end: str | None, *, default_hours: int = 24) -> tuple[str, str]:
    try:
        begin = normalize_ts(start) or (
            datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=default_hours)
        ).isoformat(timespec="seconds")
        finish = normalize_ts(end) or now_ts()
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if begin > finish:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST, detail="start must not be later than end."
        )
    return begin, finish


# --------------------------------------------------------------------------- #
# Overview & upstreams
# --------------------------------------------------------------------------- #
@router.get("/overview", summary="Dashboard KPIs, upstream summary and running models", tags=["management"])
async def overview(request: Request, _=Depends(require_admin)):
    cfg = request.app.state.manager.config
    client = request.app.state.client
    infos = await _probe_all(cfg, client)
    today = await _log(request).today_summary()

    running = [model for info in infos for model in info["running_models"]]
    vram_total = sum(i["vram_total_gib"] for i in infos if i["vram_total_gib"] is not None)
    has_total = any(i["vram_total_gib"] is not None for i in infos)
    return JSONResponse(
        {
            "kpi": {
                "upstreams_online": sum(1 for i in infos if i["reachable"]),
                "upstreams_total": len(infos),
                "running_models": len(running),
                "vram_used_gib": round(sum(i["vram_used_gib"] for i in infos), 1),
                "vram_total_gib": round(vram_total, 1) if has_total else None,
                "requests_today": today["requests_today"],
                "p95_ms": today["p95_ms"],
                "error_rate": today["error_rate"],
            },
            "request_log": _request_log_state(cfg, _log(request)),
            "upstreams": [_public_upstream(i) for i in infos],
            "running_models": running,
            "generated_at": now_ts(),
        }
    )


@router.get("/upstreams", summary="Upstream instance list with health and VRAM", tags=["management"])
async def list_upstreams(request: Request, _=Depends(require_admin)):
    cfg = request.app.state.manager.config
    infos = await _probe_all(cfg, request.app.state.client)
    return JSONResponse({"upstreams": [_public_upstream(i) for i in infos]})


@router.get("/upstreams/{name}", summary="One upstream: health, running and installed models", tags=["management"])
async def upstream_detail(name: str, request: Request, _=Depends(require_admin)):
    cfg = request.app.state.manager.config
    client = request.app.state.client
    upstream = cfg.get_upstream(name)
    if upstream is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=f"Unknown upstream '{name}'.")

    info = await _probe_upstream(upstream, client, cfg)
    if not info["reachable"]:
        payload = {**_public_upstream(info), "unreachable": True}
        return JSONResponse(payload, status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE)

    models: list[str] = []
    models_error = None
    try:
        resp = await client.get(
            f"{upstream.base_url_stripped()}/{upstream.tags_path()}",
            headers=_upstream_headers(upstream),
            timeout=PROBE_TIMEOUT,
        )
        resp.raise_for_status()
        models = upstream.parse_model_names(resp.json())
    except Exception as exc:
        models_error = str(exc)

    payload = {**_public_upstream(info), "models": models, "unreachable": False}
    if models_error:
        payload["models_error"] = models_error
    return JSONResponse(payload)


# --------------------------------------------------------------------------- #
# Model configuration projections (read-only)
# --------------------------------------------------------------------------- #
@router.get("/models/llm", summary="LLM model / alias projection", tags=["management"])
async def models_llm(request: Request, _=Depends(require_admin)):
    cfg = request.app.state.manager.config
    infos = await _probe_all(cfg, request.app.state.client)
    running = {(i["name"], m["model"]) for i in infos for m in i["running_models"]}

    def alias_for(model: str, upstream_name: str) -> str | None:
        for alias_name, alias in cfg.aliases.items():
            if alias.model != model:
                continue
            owner = cfg.get_upstream(alias.upstream) or cfg.find_upstream_for_model(alias.model)
            if owner is not None and owner.name != upstream_name:
                continue
            return alias_name
        return None

    models = [
        {
            "model": model,
            "upstream": upstream.name,
            "type": upstream.type,
            "alias": alias_for(model, upstream.name),
            "num_ctx": cfg.num_ctx_overrides.get(model),
            "wildcard": model.endswith("*"),
            "running": (upstream.name, model) in running,
        }
        for upstream in cfg.upstreams
        for model in upstream.models
    ]
    aliases = []
    for alias_name, alias in cfg.aliases.items():
        owner = cfg.get_upstream(alias.upstream) or cfg.find_upstream_for_model(alias.model)
        aliases.append(
            {
                "alias": alias_name,
                "target": alias.model,
                "upstream": owner.name if owner else None,
                "params": alias.params,
                "description": alias.description,
            }
        )
    return JSONResponse({"models": models, "aliases": aliases, "num_ctx_overrides": cfg.num_ctx_overrides})


@router.get("/models/rerank", summary="Rerank configuration projection", tags=["management"])
async def models_rerank(request: Request, _=Depends(require_admin)):
    cfg = request.app.state.manager.config
    rerank = cfg.rerank
    models = []
    for model in rerank.models:
        owner = cfg.find_upstream_for_model(model)
        models.append(
            {
                "model": model,
                "upstream": owner.name if owner else None,
                "type": owner.type if owner else None,
                "base_url": owner.base_url if owner else None,
                "api_key": _masked_key(owner) if owner else None,
            }
        )
    return JSONResponse(
        {
            "enabled": rerank.enabled,
            "endpoint": rerank.endpoint,
            "mode": rerank.mode,
            "upstream_api": rerank.upstream_api,
            "on_error": rerank.on_error,
            "models": models,
            "scoring": {
                "temperature": rerank.temperature,
                "top_logprobs": rerank.top_logprobs,
                "max_tokens": rerank.max_tokens,
                "token_scan_depth": rerank.token_scan_depth,
                "keep_alive": rerank.keep_alive,
                "options": rerank.options,
            },
            "limits": {
                "max_concurrency": rerank.max_concurrency,
                "max_documents": rerank.max_documents,
                "max_document_chars": rerank.max_document_chars,
            },
            "request_example": {
                "model": rerank.models[0] if rerank.models else "qwen3-reranker:4b",
                "query": "Ollama keep_alive 参数作用",
                "documents": ["keep_alive 控制模型在显存中驻留的时长。", "今天天气不错。"],
                "top_n": 1,
            },
            "response_example": {
                "model": rerank.models[0] if rerank.models else "qwen3-reranker:4b",
                "results": [{"index": 0, "relevance_score": 0.993, "document": {"text": "..."}}],
            },
        }
    )


@router.get("/models/systemone", summary="System One configuration projection", tags=["management"])
async def models_systemone(request: Request, _=Depends(require_admin)):
    cfg = request.app.state.manager.config
    so = cfg.systemone
    owner = None if so.model in {"", "local", "auto"} else cfg.find_upstream_for_model(so.model)
    key_set = bool(os.getenv(so.api_key_env or "", "").strip())
    return JSONResponse(
        {
            "enabled": so.enabled,
            "endpoint": "/" + so.path.strip("/"),
            "path": so.path,
            "backend": so.backend,
            "base_url": so.backend,
            "upstream": owner.name if owner else None,
            "linked_model": so.model,
            "api_key_env": so.api_key_env,
            "api_key": MASK if key_set else None,
            "timeout": so.timeout,
            "request_example": {
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
                },
                "model": so.model,
            },
            "stream_example": (
                'data: {"answers": {"refund": {"type": "noul", "noul": 0.99}}, "model": "'
                + so.model
                + '"}\n\n'
            ),
        }
    )


# --------------------------------------------------------------------------- #
# Tools configuration
# --------------------------------------------------------------------------- #
@router.get("/tools/websearch", summary="Web search configuration projection", tags=["management"])
async def tools_websearch(request: Request, _=Depends(require_admin)):
    cfg = request.app.state.manager.config
    ws = cfg.websearch
    owner = cfg.websearch_upstream_obj()
    key_set = bool(os.getenv(ws.api_key_env or "", "").strip())
    return JSONResponse(
        {
            "enabled": ws.enabled,
            # The public path is fixed; only the provider path is configurable.
            "endpoint": "/v1/tools/web-search",
            "upstream": ws.upstream or (owner.name if owner else ""),
            "upstream_type": owner.type if owner else None,
            "path": ws.path,
            "base_url": owner.base_url if owner else None,
            "api_key_env": ws.api_key_env,
            "api_key": MASK if key_set else None,
            "timeout": ws.timeout,
            "provider_configured": owner is not None,
        }
    )


# --------------------------------------------------------------------------- #
# Request log queries
# --------------------------------------------------------------------------- #
@router.get("/requests", summary="Request log with stats, filters and pagination", tags=["management"])
async def list_requests(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    upstream: str | None = None,
    model: str | None = None,
    status: str | None = None,
    path: str | None = None,
    page: int = 1,
    page_size: int = 15,
    _=Depends(require_admin),
):
    if page < 1:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="page must be >= 1.")
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"page_size must be between 1 and {MAX_PAGE_SIZE}.",
        )
    if status and status not in _STATUS_FILTERS:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"status must be one of {sorted(_STATUS_FILTERS)}.",
        )
    try:
        begin = normalize_ts(start)
        finish = normalize_ts(end)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if begin and finish and begin > finish:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST, detail="start must not be later than end."
        )

    log = _log(request)
    await log.flush()
    payload = await log.query_requests(
        start=begin,
        end=finish,
        upstream=upstream or None,
        model=model or None,
        status=status or None,
        path=path or None,
        page=page,
        page_size=page_size,
    )
    payload["request_log"] = _request_log_state(request.app.state.manager.config, log)
    return JSONResponse(payload)


@router.get("/requests/{record_id}", summary="One request log entry with bodies", tags=["management"])
async def request_detail(record_id: int, request: Request, _=Depends(require_admin)):
    log = _log(request)
    await log.flush()
    entry = await log.get_request(record_id)
    if entry is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=f"Unknown request '{record_id}'.")
    entry["stream"] = bool(entry["stream"])
    entry["request_body_truncated"] = bool(entry["request_body_truncated"])
    entry["response_body_truncated"] = bool(entry["response_body_truncated"])
    return JSONResponse(entry)


@router.get("/stats/timeseries", summary="Request count / errors / P95 per time bucket", tags=["management"])
async def stats_timeseries(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    bucket: str = "hour",
    _=Depends(require_admin),
):
    if bucket not in {"hour", "day"}:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST, detail="bucket must be 'hour' or 'day'."
        )
    begin, finish = _resolve_range(start, end)
    log = _log(request)
    await log.flush()
    return JSONResponse(await log.timeseries(start=begin, end=finish, bucket=bucket))


@router.get("/stats/distribution", summary="Grouped request counts for the distribution pies", tags=["management"])
async def stats_distribution(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    group_by: str = "upstream",
    _=Depends(require_admin),
):
    if group_by not in _DISTRIBUTION_KEYS:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"group_by must be one of {sorted(_DISTRIBUTION_KEYS)}.",
        )
    begin, finish = _resolve_range(start, end)
    log = _log(request)
    await log.flush()
    return JSONResponse(await log.distribution(start=begin, end=finish, group_by=group_by))


@router.get("/stats/failures", summary="Failure breakdown and recent samples", tags=["management"])
async def stats_failures(request: Request, start: str | None = None, end: str | None = None, _=Depends(require_admin)):
    begin, finish = _resolve_range(start, end)
    log = _log(request)
    await log.flush()
    return JSONResponse(await log.failures(start=begin, end=finish))
