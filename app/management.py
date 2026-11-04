"""
Read-only management API for the web console (``/v1/management``).

Every route except login/logout depends on :func:`admin_auth.require_admin`, so it
accepts only the signed admin session cookie minted by ``POST /auth/login``; the admin
password is a login-page credential, never a request credential. Nothing here writes
configuration: the console is a projection of ``config.yaml`` plus live probes of the
upstreams.

Live probes reuse the same ``httpx.AsyncClient`` as forwarding, with a short timeout
so one dead upstream never stalls the whole view.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hmac
import json
import logging
import os
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_auth import (
    ADMIN_COOKIE_NAME,
    ADMIN_SESSION_TTL,
    ADMIN_USERNAME,
    create_admin_token,
    get_admin_password,
    require_admin,
)
from bench import BenchError, BenchOptions, run_bench
from bench_store import BenchStore
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
    # The console has one account; the username is still checked so a mistyped login
    # page fails with 401 instead of silently accepting anything.
    username: str = ADMIN_USERNAME
    password: str


async def login(request: Request, body: AdminLogin) -> Response:
    """Validate the admin credentials and mint a signed session cookie."""
    key = get_admin_password()
    if not key:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin is not configured: set the ADMIN_PASSWORD environment variable.",
        )
    # Constant-time compare; a small delay blunts online brute-force.
    name_ok = hmac.compare_digest(body.username.strip().lower(), ADMIN_USERNAME)
    if not name_ok or not hmac.compare_digest(body.password, key):
        await asyncio.sleep(0.1)
        raise HTTPException(status_code=http_status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")
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


def _alias_by_target(cfg) -> dict[tuple[str, str], str]:
    """``(upstream name, real model id) -> exposed alias``; the first declaration wins."""
    mapping: dict[tuple[str, str], str] = {}
    for alias_name, alias in cfg.aliases.items():
        owner = cfg.get_upstream(alias.upstream) or cfg.find_upstream_for_model(alias.model)
        if owner is not None:
            mapping.setdefault((owner.name, alias.model), alias_name)
    return mapping


def _match_running(runtime: dict[tuple[str, str], dict[str, Any]], upstream_name: str, pattern: str):
    """Runtime entry for a declared name; a prefix wildcard resolves to its first match."""
    live = runtime.get((upstream_name, pattern))
    if live is not None or not pattern.endswith("*"):
        return live
    prefix = pattern[:-1]
    return next((m for (up, name), m in runtime.items() if up == upstream_name and name.startswith(prefix)), None)


def _declared_models(upstream, discovered: list[str]) -> list[tuple[str, bool]]:
    """``upstreams[].models`` as ``(name, matched_by_wildcard)``, patterns expanded.

    A plain declaration is listed exactly as written - the instance is never asked for it.
    A wildcard has no meaning until something matches it, so it is expanded with the same
    list the gateway's own wildcard routing uses (``fetch_upstream_models``, cached 60s).
    When discovery returns nothing the pattern is kept as its own entry: an unroutable
    declaration is a misconfiguration the console should show, not hide.
    """
    entries: list[tuple[str, bool]] = [
        (name, False) for name in upstream.models if not name.endswith("*")
    ]
    seen = {name for name, _ in entries}
    for pattern in upstream.models:
        if not pattern.endswith("*"):
            continue
        prefix = pattern[:-1]
        hits = [name for name in sorted(discovered) if name.startswith(prefix)]
        new = [name for name in hits if name not in seen]
        entries.extend((name, True) for name in new)
        seen.update(new)
        if not hits:
            entries.append((pattern, True))
    return entries


def _llm_row(
    cfg,
    aliases: dict[tuple[str, str], str],
    runtime: dict[tuple[str, str], dict[str, Any]],
    reachable: dict[str, bool],
    upstream,
    name: str,
    via_wildcard: bool,
) -> dict[str, Any]:
    live = _match_running(runtime, upstream.name, name)
    alias = aliases.get((upstream.name, name))
    hosted = upstream.type == "opencode" and reachable.get(upstream.name, False)
    return {
        "model": name,
        "display": alias or name,
        "alias": alias,
        "upstream": upstream.name,
        "type": upstream.type,
        "wildcard": via_wildcard,
        # An OpenCode gateway is a hosted API with no /api/ps: when its probe answers,
        # every model it declares is available and lives in the provider's cloud
        # (`device: "cloud"`, memory unknown).
        # A pattern that discovery matched nothing stays listed, but never as online.
        "running": not name.endswith("*") and (live is not None or hosted),
        "device": live["device"] if live else ("cloud" if hosted else None),
        "device_ratio": live["device_ratio"] if live else None,
        "num_ctx": (live["num_ctx"] if live else None) or cfg.num_ctx_overrides.get(name),
        "size_gib": live["size_gib"] if live else None,
        "vram_gib": live["vram_gib"] if live else None,
    }


def _device_entry(
    model: dict[str, Any], upstream_name: str, cfg, aliases: dict[tuple[str, str], str]
) -> dict[str, Any]:
    """One ``/api/ps`` row plus the config alias, so the console names models as callers use them."""
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
    alias = aliases.get((upstream_name, name))
    return {
        "model": name,
        "display": alias or name,
        "alias": alias,
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

    aliases = _alias_by_target(cfg)
    info["running_models"] = [_device_entry(m, upstream.name, cfg, aliases) for m in models]
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


async def _wildcard_discovery(cfg, client) -> dict[str, list[str]]:
    """Live model lists, but only for the upstreams that actually declare a wildcard.

    A config of plain names therefore never touches a tags endpoint; the query reuses
    ``fetch_upstream_models``, so the console expands exactly what routing would match.
    """
    # Deferred import: main.py imports this module, so a module-level import would cycle.
    from main import fetch_upstream_models

    targets = [up for up in cfg.upstreams if any(m.endswith("*") for m in up.models)]
    if not targets:
        return {}
    found = await asyncio.gather(*[fetch_upstream_models(client, up) for up in targets])
    return dict(zip((up.name for up in targets), found, strict=True))


# --------------------------------------------------------------------------- #
# Overview & upstreams
# --------------------------------------------------------------------------- #
def _hosted_models(
    cfg,
    aliases: dict[tuple[str, str], str],
    discovered: dict[str, list[str]],
    reachable: dict[str, bool],
) -> list[dict[str, Any]]:
    """Declared models of a hosted provider, listed as available next to the resident ones.

    An OpenCode gateway has no ``/api/ps``: one liveness probe covers the whole service, so
    every model its ``config.yaml`` entry declares is online and reported with
    ``device: "cloud"`` (it runs on the provider's infrastructure), memory unknown. A
    wildcard that discovery could not resolve is skipped - the model tables show it, the
    availability list must not claim a pattern is running.
    """
    rows: list[dict[str, Any]] = []
    for upstream in cfg.upstreams:
        if upstream.type != "opencode" or not reachable.get(upstream.name, False):
            continue
        for name, _ in _declared_models(upstream, discovered.get(upstream.name, [])):
            if name.endswith("*"):  # a pattern nothing matched is not a model that is online
                continue
            alias = aliases.get((upstream.name, name))
            rows.append(
                {
                    "model": name,
                    "display": alias or name,
                    "alias": alias,
                    "upstream": upstream.name,
                    "device": "cloud",
                    "device_ratio": None,
                    "num_ctx": cfg.num_ctx_overrides.get(name),
                    "size_gib": None,
                    "vram_gib": None,
                    "expires_at": None,
                }
            )
    return rows


@router.get("/overview", summary="Dashboard KPIs, upstream summary and running models", tags=["management"])
async def overview(request: Request, _=Depends(require_admin)):
    cfg = request.app.state.manager.config
    client = request.app.state.client
    infos = await _probe_all(cfg, client)
    today = await _log(request).today_summary()

    running = [model for info in infos for model in info["running_models"]]
    aliases = _alias_by_target(cfg)
    discovered = await _wildcard_discovery(cfg, client)
    reachable = {i["name"]: i["reachable"] for i in infos}
    running.extend(_hosted_models(cfg, aliases, discovered, reachable))
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
                "tokens_today": today["tokens_today"],
                "p95_ms": today["p95_ms"],
                "error_rate": today["error_rate"],
            },
            "request_log": _request_log_state(cfg, _log(request)),
            "upstreams": [_public_upstream(i) for i in infos],
            "running_models": running,
            "generated_at": now_ts(),
            "gateway_version": getattr(request.app.state, "gateway_version", None),
        }
    )


@router.get("/upstreams", summary="Upstream instance list with health and VRAM", tags=["management"])
async def list_upstreams(request: Request, _=Depends(require_admin)):
    cfg = request.app.state.manager.config
    infos = await _probe_all(cfg, request.app.state.client)
    return JSONResponse({"upstreams": [_public_upstream(i) for i in infos]})


@router.get("/upstreams/{name}", summary="One upstream: health, configured models and running state", tags=["management"])
async def upstream_detail(name: str, request: Request, _=Depends(require_admin)):
    """Live probe plus the models this upstream declares in ``config.yaml``.

    Plain names are listed as written; only a prefix wildcard is resolved through the
    upstream's model list, which is the same call the gateway's routing makes. An aliased
    model is shown under its alias.
    """
    cfg = request.app.state.manager.config
    client = request.app.state.client
    upstream = cfg.get_upstream(name)
    if upstream is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=f"Unknown upstream '{name}'.")

    info = await _probe_upstream(upstream, client, cfg)
    if not info["reachable"]:
        payload = {**_public_upstream(info), "unreachable": True}
        return JSONResponse(payload, status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE)

    aliases = _alias_by_target(cfg)
    discovered = await _wildcard_discovery(cfg, client)
    names = [name for name, _ in _declared_models(upstream, discovered.get(upstream.name, []))]
    return JSONResponse(
        {
            **_public_upstream(info),
            "models": [aliases.get((upstream.name, model), model) for model in names],
            # A hosted provider has no /api/ps, so its available models are the declared ones.
            "running_models": [
                *info["running_models"],
                *_hosted_models(cfg, aliases, discovered, {upstream.name: True}),
            ],
            "unreachable": False,
        }
    )


# --------------------------------------------------------------------------- #
# Model configuration projections (read-only)
# --------------------------------------------------------------------------- #
@router.get("/models/llm", summary="LLM models declared per upstream, plus aliases", tags=["management"])
async def models_llm(request: Request, _=Depends(require_admin)):
    """One row per model name an upstream declares in ``config.yaml``, with runtime state.

    Only ``upstreams[].models`` is listed - a model installed on the instance but never
    declared is not routable through the gateway, so it does not belong in this table. A
    prefix wildcard (``opencode/*``) is expanded to the names it actually matches, because
    the pattern itself is not a model. A declared name that has an alias is displayed under
    the alias (``display``) while ``model`` keeps the real upstream id.
    """
    cfg = request.app.state.manager.config
    client = request.app.state.client
    infos = await _probe_all(cfg, client)
    runtime = {(i["name"], m["model"]): m for i in infos for m in i["running_models"]}
    reachable = {i["name"]: i["reachable"] for i in infos}
    aliases = _alias_by_target(cfg)
    discovered = await _wildcard_discovery(cfg, client)

    models = [
        _llm_row(cfg, aliases, runtime, reachable, upstream, name, via_wildcard)
        for upstream in cfg.upstreams
        for name, via_wildcard in _declared_models(upstream, discovered.get(upstream.name, []))
    ]

    alias_rows = []
    for alias_name, alias in cfg.aliases.items():
        owner = cfg.get_upstream(alias.upstream) or cfg.find_upstream_for_model(alias.model)
        alias_rows.append(
            {
                "alias": alias_name,
                "target": alias.model,
                "upstream": owner.name if owner else None,
                "params": alias.params,
                "description": alias.description,
            }
        )
    return JSONResponse({"models": models, "aliases": alias_rows, "num_ctx_overrides": cfg.num_ctx_overrides})


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


# --------------------------------------------------------------------------- #
# Benchmark (speed test)
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class BenchJob:
    job_id: str
    upstream: str
    model: str
    status: str = "pending"  # pending -> running -> done | failed
    progress: dict = dataclasses.field(default_factory=lambda: {"completed": 0, "total": 0, "warmup": False})
    result: dict | None = None
    error: str | None = None
    created_at: str = dataclasses.field(default_factory=lambda: now_ts())
    finished_at: str | None = None


# In-memory registry of benchmark jobs. A real deployment is single-process, so this is
# enough; finished jobs are swept once the map grows past MAX_JOBS.
_BENCH_JOBS: dict[str, BenchJob] = {}
MAX_BENCH_JOBS = 200


def _run_bench_job_thread(job: BenchJob, upstream, options: BenchOptions, store: BenchStore) -> None:
    """Drive one benchmark on its own event loop in a worker thread.

    `asyncio.create_task` is not enough: the task lives on the *request's* event loop, so an
    ASGI server that finishes/tears down that loop (or a test client that closes the request
    scope) cancels the benchmark and the job stays "running" forever. A dedicated loop keeps
    the job independent of whoever called POST /bench.
    """
    try:
        asyncio.run(_run_bench_job(job, upstream, options, store))
    except Exception as exc:  # noqa: BLE001 -- never let a worker thread die silently
        job.status = "failed"
        job.error = f"测速线程异常: {exc}"
        job.finished_at = now_ts()
        logger.exception("bench worker crashed for %s/%s", job.upstream, job.model)


def _spawn_bench_job(job: BenchJob, upstream, options: BenchOptions, store: BenchStore) -> None:
    threading.Thread(
        target=_run_bench_job_thread,
        args=(job, upstream, options, store),
        name=f"bench-{job.job_id[:8]}",
        daemon=True,
    ).start()


def _sweep_bench_jobs() -> None:
    if len(_BENCH_JOBS) <= MAX_BENCH_JOBS:
        return
    finished = sorted(
        (j for j in _BENCH_JOBS.values() if j.status in ("done", "failed")),
        key=lambda j: j.created_at,
    )
    for old in finished[: len(_BENCH_JOBS) - MAX_BENCH_JOBS]:
        _BENCH_JOBS.pop(old.job_id, None)


async def _run_bench_job(job: BenchJob, upstream, options: BenchOptions, store: BenchStore) -> None:
    server_type = "ollama" if upstream.type == "ollama" else "lm"
    job.status = "running"

    async def on_progress(completed: int, total: int, is_warmup: bool) -> None:
        job.progress = {"completed": completed, "total": total, "warmup": is_warmup}

    try:
        result = await run_bench(
            base_url=upstream.base_url_stripped(),
            server_type=server_type,
            model=job.model,
            options=options,
            headers=upstream.headers,
            on_progress=on_progress,
        )
        job.result = result
        job.finished_at = now_ts()
        cfg_out = result["config"]
        steady = result["steady"]
        gen_tokens = max((r["gen_tokens"] for r in result["rounds"] if not r["warmup"]), default=None)
        store.record(
            {
                "upstream": job.upstream,
                "model": job.model,
                "created_at": job.finished_at,
                "server": cfg_out.get("server"),
                "host": cfg_out.get("host"),
                "port": cfg_out.get("port"),
                "rounds": cfg_out.get("rounds"),
                "warmup": cfg_out.get("warmup"),
                "max_tokens": cfg_out.get("max_tokens"),
                "decode_tok_s": steady["decode"]["mean"],
                "decode_sd": steady["decode"]["std_dev"],
                "prefill_tok_s": steady["prefill"]["mean"],
                "ttft_ms": steady["ttft"]["mean_ms"],
                "timing_source": result.get("timing_source"),
                "prompt_tokens": max((r["prompt_tokens"] for r in result["rounds"] if not r["warmup"]), default=None),
                "gen_tokens": gen_tokens,
                "payload": json.dumps(result, ensure_ascii=False),
            }
        )
        # Publish "done" only after the row is queued: a client that polls status and then
        # immediately asks for the history must not observe an empty table.
        job.status = "done"
    except BenchError as exc:
        job.status = "failed"
        job.error = str(exc)
        job.finished_at = now_ts()
    except Exception as exc:  # noqa: BLE001 -- surface any failure to the console
        job.status = "failed"
        job.error = f"测速异常: {exc}"
        job.finished_at = now_ts()


class BenchStart(BaseModel):
    upstream: str
    model: str
    rounds: int | None = None
    max_tokens: int | None = None
    think: str | None = None


@router.post("/bench", summary="Start a model benchmark (speed test)", tags=["management"])
async def bench_start(body: BenchStart, request: Request, _=Depends(require_admin)):
    cfg = request.app.state.manager.config
    upstream = next((u for u in cfg.upstreams if u.name == body.upstream), None)
    if upstream is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=f"未知上游: {body.upstream}")
    model = (body.model or "").strip()
    if not model:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="model 不能为空")
    try:
        options = BenchOptions(
            rounds=body.rounds or 4,
            max_tokens=body.max_tokens if body.max_tokens is not None else 500,
            think=body.think or "auto",
        )
    except BenchError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc))

    job_id = uuid.uuid4().hex
    job = BenchJob(job_id=job_id, upstream=upstream.name, model=model)
    _BENCH_JOBS[job_id] = job
    _sweep_bench_jobs()
    store: BenchStore = request.app.state.bench_store
    _spawn_bench_job(job, upstream, options, store)
    return {"job_id": job_id, "status": "pending"}


@router.get("/bench/history", summary="Recent benchmark records for a model", tags=["management"])
async def bench_history(upstream: str, model: str, request: Request, _=Depends(require_admin)):
    # Declared BEFORE "/bench/{job_id}" so the literal "history" segment is not swallowed
    # by the job_id path parameter (which would 404 with "任务不存在或已过期").
    store: BenchStore = request.app.state.bench_store
    await store.flush()  # a job that just finished may still be sitting in the write queue
    records = await store.history(upstream, model)
    return {"upstream": upstream, "model": model, "records": records}


@router.get("/bench/{job_id}", summary="Poll a benchmark job status", tags=["management"])
async def bench_status(job_id: str, _=Depends(require_admin)):
    job = _BENCH_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="任务不存在或已过期")
    return {
        "job_id": job.job_id,
        "upstream": job.upstream,
        "model": job.model,
        "status": job.status,
        "progress": job.progress,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
    }

