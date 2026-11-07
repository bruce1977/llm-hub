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
    require_admin_session,
)
from api_test import MAX_RESPONSE_BYTES, KINDS, execute, get_kind
from auth import client_ip
from bench import BenchError, BenchOptions, run_bench
from bench_store import BenchStore
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi import status as http_status
from fastapi.responses import JSONResponse
from key_store import LABEL_MAX_CHARS, ApiKeyStore, get_store
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from request_log import _DISTRIBUTION_KEYS, MAX_PAGE_SIZE, normalize_ts, now_ts

import yaml

from config import (
    Config,
    RerankConfig,
    SystemOneConfig,
    UpstreamConfig,
    WebSearchConfig,
    _dump_yaml,
)

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
    """``(upstream name, real model id) -> renamed model``; the first declaration wins.

    Only `upstreams[].model_aliases` belongs here: it is a pure display rename, so the
    declared model is shown under its short name. An entry written under root `aliases:`
    is a model of its own (it can carry its own fixed parameters) and the target stays
    reachable under its real id, so it must not rename anything - it gets its own row.
    """
    mapping: dict[tuple[str, str], str] = {}
    for alias_name, alias in cfg.aliases.items():
        if not cfg.is_renamed_model(alias_name):
            continue
        owner = cfg.get_upstream(alias.upstream) or cfg.find_upstream_for_model(alias.model)
        if owner is not None:
            mapping.setdefault((owner.name, alias.model), alias_name)
    return mapping


def _derived_row(cfg, runtime: dict[tuple[str, str], dict[str, Any]], reachable: dict[str, bool], name: str, alias) -> dict[str, Any] | None:
    """One root `aliases:` entry, listed as the separate model it is.

    The alias is what the caller sends, so it is both `model` and `display`, and the real
    id stays in `target`. Device and context length are read from the target because that
    is the instance Ollama actually holds in memory; memory is deliberately `null` - the
    same instance is already counted under its own name, so this row must not read as a
    second allocation.
    """
    owner = (
        cfg.get_upstream(alias.upstream)
        or cfg.find_upstream_for_model(alias.model)
        or cfg.default_upstream_obj()
    )
    if owner is None:
        return None
    live = runtime.get((owner.name, alias.model))
    hosted = owner.type == "opencode" and reachable.get(owner.name, False)
    return {
        "model": name,
        "display": name,
        "alias": None,
        "derived": True,
        "target": alias.model,
        "params": alias.params,
        "description": alias.description,
        "upstream": owner.name,
        "type": owner.type,
        "wildcard": False,
        "running": live is not None or hosted,
        "device": live["device"] if live else ("cloud" if hosted else None),
        "device_ratio": live["device_ratio"] if live else None,
        "num_ctx": (live["num_ctx"] if live else None) or cfg.num_ctx_overrides.get(alias.model),
        "size_gib": None,
        "vram_gib": None,
        "expires_at": None,
    }


def _derived_rows(
    cfg,
    runtime: dict[tuple[str, str], dict[str, Any]],
    reachable: dict[str, bool],
    *,
    available_only: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, alias in cfg.derived_aliases().items():
        row = _derived_row(cfg, runtime, reachable, name, alias)
        if row is None or (available_only and not row["running"]):
            continue
        rows.append(row)
    return rows


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
        "derived": False,
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
        "derived": False,
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
                    "derived": False,
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
async def overview(
    request: Request,
    today_start: str | None = None,
    today_end: str | None = None,
    _=Depends(require_admin),
):
    cfg = request.app.state.manager.config
    client = request.app.state.client
    infos = await _probe_all(cfg, client)
    # The browser owns the definition of "today": it sends its local midnight converted to UTC.
    # Without it the KPI falls back to a UTC day, which is only correct for callers east of
    # nothing - at UTC+8 it would hide every request made between 00:00 and 08:00 local.
    try:
        today_begin = normalize_ts(today_start)
        today_finish = normalize_ts(today_end)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if today_begin and today_finish and today_begin > today_finish:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="today_start must not be later than today_end.",
        )
    today = await _log(request).today_summary(today_begin, today_finish)

    available = [model for info in infos for model in info["running_models"]]
    aliases = _alias_by_target(cfg)
    discovered = await _wildcard_discovery(cfg, client)
    reachable = {i["name"]: i["reachable"] for i in infos}
    available.extend(_hosted_models(cfg, aliases, discovered, reachable))
    # A model declared under root `aliases:` is listed next to its target because callers can
    # ask for it, but it never joins the KPI count: it runs inside the instance already
    # counted under its own id, so counting it again would inflate both the models and the VRAM.
    runtime = {(i["name"], m["model"]): m for i in infos for m in i["running_models"]}
    running = [*available, *_derived_rows(cfg, runtime, reachable, available_only=True)]
    vram_total = sum(i["vram_total_gib"] for i in infos if i["vram_total_gib"] is not None)
    has_total = any(i["vram_total_gib"] is not None for i in infos)
    return JSONResponse(
        {
            "kpi": {
                "upstreams_online": sum(1 for i in infos if i["reachable"]),
                "upstreams_total": len(infos),
                "running_models": len(available),
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
    upstream's model list, which is the same call the gateway's routing makes. A
    `model_aliases` rename replaces the name it renames; a model declared under root
    `aliases:` is added next to its target, because both names are callable.
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
    runtime = {(upstream.name, model["model"]): model for model in info["running_models"]}
    derived = [
        row
        for row in _derived_rows(cfg, runtime, {upstream.name: True})
        if row["upstream"] == upstream.name
    ]
    return JSONResponse(
        {
            **_public_upstream(info),
            "models": [
                *(aliases.get((upstream.name, model), model) for model in names),
                *(row["model"] for row in derived),
            ],
            # A hosted provider has no /api/ps, so its available models are the declared ones.
            "running_models": [
                *info["running_models"],
                *_hosted_models(cfg, aliases, discovered, {upstream.name: True}),
                *(row for row in derived if row["running"]),
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
    the pattern itself is not a model. A ``model_aliases`` rename replaces the declared name
    (``display``) while ``model`` keeps the real upstream id; an entry under root
    ``aliases:`` is a model of its own and gets its own row right after its target.
    """
    cfg = request.app.state.manager.config
    client = request.app.state.client
    infos = await _probe_all(cfg, client)
    runtime = {(i["name"], m["model"]): m for i in infos for m in i["running_models"]}
    reachable = {i["name"]: i["reachable"] for i in infos}
    aliases = _alias_by_target(cfg)
    discovered = await _wildcard_discovery(cfg, client)

    # A derived model reads best next to the instance it runs on, so each one is inserted
    # after its target; one whose target nothing declares lands at the end instead.
    pending = {row["model"]: row for row in _derived_rows(cfg, runtime, reachable)}
    models: list[dict[str, Any]] = []
    for upstream in cfg.upstreams:
        for name, via_wildcard in _declared_models(upstream, discovered.get(upstream.name, [])):
            models.append(_llm_row(cfg, aliases, runtime, reachable, upstream, name, via_wildcard))
            for model_name, row in list(pending.items()):
                if row["upstream"] == upstream.name and row["target"] == name:
                    models.append(row)
                    pending.pop(model_name)
    models.extend(pending.values())

    alias_rows = []
    for alias_name, alias in cfg.aliases.items():
        owner = cfg.get_upstream(alias.upstream) or cfg.find_upstream_for_model(alias.model)
        alias_rows.append(
            {
                "alias": alias_name,
                "kind": "rename" if cfg.is_renamed_model(alias_name) else "derived",
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
                        # An object, not a list: Ollama rejects an array with 400, and
                        # laya-server accepts this shape too.
                        "criteria": {
                            "refund": "asks for a refund",
                            "cancel": "wants to cancel",
                            "other": "anything else",
                        },
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
    method: str | None = None,
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
        method=method or None,
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
    tz_offset: int = 0,
    _=Depends(require_admin),
):
    if bucket not in {"hour", "day"}:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST, detail="bucket must be 'hour' or 'day'."
        )
    # Minutes to add to UTC to reach the caller's local clock (UTC+8 -> 480). Only day buckets
    # use it, so a bar is one local calendar day instead of one UTC day.
    if not -840 <= tz_offset <= 840:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST, detail="tz_offset must be within -840..840."
        )
    begin, finish = _resolve_range(start, end)
    log = _log(request)
    await log.flush()
    return JSONResponse(
        await log.timeseries(start=begin, end=finish, bucket=bucket, tz_offset_minutes=tz_offset)
    )


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


# --------------------------------------------------------------------------- #
# Interactive API testers (the console's "API usage" tabs)
# --------------------------------------------------------------------------- #
def _family_list(cfg, family: str) -> tuple[list[str], str]:
    """The model names a family may offer, plus where they came from.

    `api_models.<family>` always wins; otherwise each family falls back to the block that
    already names its models (`rerank.models`, `systemone.model`), and with nothing declared
    the caller gets the whole pool (`upstreams[].models`) so a tester is never empty.
    """
    explicit = list(getattr(cfg.api_models, family, []))
    if explicit:
        return explicit, f"api_models.{family}"
    if family == "rerank" and cfg.rerank.models:
        return list(cfg.rerank.models), "rerank.models"
    if family == "systemone" and cfg.systemone.model not in {"", "local"}:
        return [cfg.systemone.model], "systemone.model"
    return [], "upstreams[].models"


def _callable_pool(cfg, aliases, discovered, runtime, reachable) -> dict[str, dict[str, Any]]:
    """Every name a caller can actually send, keyed by that name.

    Declared models (wildcards already expanded) plus one entry per root `aliases:` model,
    which is a name of its own. A prefix pattern nothing matched is not a sendable name, so
    it never joins the pool.
    """
    pool: dict[str, dict[str, Any]] = {}
    for upstream in cfg.upstreams:
        for name, via_wildcard in _declared_models(upstream, discovered.get(upstream.name, [])):
            if name.endswith("*"):
                continue
            row = _llm_row(cfg, aliases, runtime, reachable, upstream, name, via_wildcard)
            row["api_key"] = _masked_key(upstream)
            row["unknown"] = False
            pool.setdefault(name, row)
    for row in _derived_rows(cfg, runtime, reachable):
        owner = cfg.get_upstream(row["upstream"])
        row["api_key"] = _masked_key(owner) if owner else None
        row["unknown"] = False
        pool.setdefault(row["model"], row)
    return pool


def _pool_row_for_name(cfg, name: str, pool: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Row for a name written in an `api_models` list.

    It may be the real id (a pool entry), an alias of one (the alias is kept as the name to
    send, so the gateway still applies its injected parameters) or a name nothing serves -
    which stays visible, because a dead entry is a misconfiguration to show, not hide.
    """
    found = pool.get(name)
    if found is not None:
        return found
    resolved = cfg.resolve(name)
    target = pool.get(resolved.target)
    if target is not None:
        row = dict(target)
        row.update({"model": name, "display": name, "alias": name, "unknown": False})
        if not resolved.is_alias:
            row["derived"] = False
        row["target"] = resolved.target
        return row
    return {
        "model": name,
        "display": name,
        "alias": name if name in cfg.aliases else None,
        "derived": False,
        "upstream": resolved.upstream.name if resolved.upstream else None,
        "type": resolved.upstream.type if resolved.upstream else None,
        "wildcard": False,
        "running": False,
        "device": None,
        "device_ratio": None,
        "num_ctx": None,
        "size_gib": None,
        "vram_gib": None,
        "api_key": _masked_key(resolved.upstream) if resolved.upstream else None,
        "unknown": True,
    }


def _bare(name: str) -> str:
    """Ollama reads `tev1` and `tev1:latest` as one model, and an `api_models` entry can be
    written either way, so claims and pool rows are compared without the default tag."""
    return name[: -len(":latest")] if name.endswith(":latest") else name


def _claimed_names(cfg) -> set[str]:
    """Names some other family was given on purpose, normalised for comparison.

    Only explicit declarations count - the `api_models` lists and `rerank.models`. A fallback
    such as `systemone.model` is deliberately not a claim: a deployment may point System One
    at the same model it chats with, and that model must stay selectable for chat.

    Web search is not a claim either. Its endpoint is a chat pass-through - the model answers
    the synthesis prompt - so a hosted provider's routing model (`opencode/auto`) is a chat
    model first and must stay selectable for generate / chat.
    """
    declared = (
        set(cfg.api_models.embed)
        | set(cfg.api_models.rerank)
        | set(cfg.api_models.systemone)
        | set(cfg.rerank.models)
    )
    claimed: set[str] = set()
    for name in declared:
        if not name:
            continue
        claimed.add(_bare(name))
        claimed.add(_bare(cfg.resolve(name).target))
    return claimed


def _family_rows(
    cfg, family: str, pool: dict[str, dict[str, Any]], *, native: bool = False
) -> tuple[list[dict[str, Any]], str]:
    names, source = _family_list(cfg, family)
    if names:
        return [_pool_row_for_name(cfg, name, pool) for name in names], source
    rows = list(pool.values())
    if native:
        # `/api/generate` and `/api/embed` are Ollama's native dialect; an `lmstudio` or
        # `opencode` upstream answers only the `/v1` side, so offering its models would be a
        # guaranteed upstream 404. An explicit `api_models` list is still served as written.
        rows = [row for row in rows if row.get("type") == "ollama"]
    if family == "generate":
        # A name another family was explicitly given answers that kind of request, not a chat
        # one, so the generate and chat selectors must not offer it. Both spellings count: a
        # list may name the alias or the tagless id while the pool row keeps the declared name.
        claimed = _claimed_names(cfg)
        rows = [
            row
            for row in rows
            if _bare(row["model"]) not in claimed and _bare(row.get("target") or "") not in claimed
        ]
    return rows, source


class ApiTestRequest(BaseModel):
    kind: str
    body: dict[str, Any] = Field(default_factory=dict)


@router.get("/api-test/models", summary="Usable models, defaults and masked keys per API family", tags=["management"])
async def api_test_models(request: Request, _=Depends(require_admin)):
    """What each tester needs before it can send anything: which models answer that kind of
    request, which one to preselect, and whether the call is signed with an extra key.

    Credentials never leave the server: an upstream auth header or a provider env key is
    reported as the fixed mask, next to the name of the variable it came from.
    """
    cfg = request.app.state.manager.config
    client = request.app.state.client
    infos = await _probe_all(cfg, client)
    runtime = {(i["name"], m["model"]): m for i in infos for m in i["running_models"]}
    reachable = {i["name"]: i["reachable"] for i in infos}
    pool = _callable_pool(cfg, _alias_by_target(cfg), await _wildcard_discovery(cfg, client), runtime, reachable)
    ws_owner = cfg.websearch_upstream_obj()
    so_key_set = bool(os.getenv(cfg.systemone.api_key_env or "", "").strip())
    ws_key_set = bool(os.getenv(cfg.websearch.api_key_env or "", "").strip())

    kinds: dict[str, Any] = {}
    for name, kind in KINDS.items():
        rows, source = _family_rows(cfg, kind.family, pool, native=kind.native)
        if kind.executor == "websearch" and ws_owner is not None:
            provider_rows = [row for row in rows if row["upstream"] == ws_owner.name]
            if provider_rows:
                rows, source = provider_rows, "upstreams[].models (search provider)"
        endpoint = cfg.rerank.endpoint if kind.executor == "rerank" else kind.endpoint
        default = None
        if kind.executor == "websearch":
            # `model` is optional here and the provider resolves it, so the selector starts
            # empty rather than preselecting a name the gateway would not rewrite.
            default = None
        elif kind.executor == "systemone" and cfg.systemone.model not in {"", "local"}:
            default = next((r["model"] for r in rows if r["model"] == cfg.systemone.model), cfg.systemone.model)
        elif rows:
            default = rows[0]["model"]
        enabled = {
            "rerank": cfg.rerank.enabled,
            "systemone": cfg.systemone.enabled,
            "websearch": cfg.websearch.enabled,
        }.get(kind.executor, True)
        kinds[name] = {
            "kind": name,
            "method": "POST",
            "endpoint": endpoint,
            "family": kind.family,
            "model_required": kind.model_required,
            "free_model": kind.free_model,
            "enabled": enabled,
            "source": source,
            "models": rows,
            "default_model": default,
            # Only the two env-keyed capabilities carry a key of their own; a proxied call
            # signs with the selected model's upstream header, reported row by row.
            "api_key": MASK
            if (kind.executor == "systemone" and so_key_set) or (kind.executor == "websearch" and ws_key_set)
            else None,
            "api_key_env": cfg.systemone.api_key_env if kind.executor == "systemone" else (
                cfg.websearch.api_key_env if kind.executor == "websearch" else None
            ),
        }
    return JSONResponse({"kinds": kinds, "max_response_bytes": MAX_RESPONSE_BYTES})


@router.post("/api-test", summary="Send one live request through the gateway's own endpoint", tags=["management"])
async def api_test_run(payload: ApiTestRequest, request: Request, _=Depends(require_admin)):
    """Execute a tester request server-side, because the admin cookie is not a caller key.

    The body goes to the same service the public route uses, so alias injection, `num_ctx`
    clamping, path blocking and the request log all apply exactly as they would to an
    external client. See `app/api_test.py`.
    """
    kind = get_kind(payload.kind)
    cfg = request.app.state.manager.config
    body = dict(payload.body)
    if kind.model_required:
        model = body.get("model")
        if not isinstance(model, str) or not model.strip():
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"'{kind.name}' needs a model; pick one in the selector.",
            )
        body["model"] = model.strip()
    if kind.executor == "rerank":
        kind = dataclasses.replace(kind, endpoint=cfg.rerank.endpoint)
    result = await execute(request.app, kind, body, (client_ip(request) or "console", 0))
    return JSONResponse(result)


# --------------------------------------------------------------------------- #
# Config read/write (Settings console)
# --------------------------------------------------------------------------- #
def _format_validation_error(exc: ValidationError) -> str:
    """Flatten a pydantic ValidationError into a short, human-readable message."""
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", []))
        msg = err.get("msg", "invalid")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)


# --------------------------------------------------------------------------- #
# API keys (the console mints and revokes the credentials callers present)
# --------------------------------------------------------------------------- #
# These three routes are the only place a credential is created or destroyed, so they take the
# console session cookie and never a gateway API key: a key is what every caller already holds,
# and accepting one here would let any client issue itself another. What the list returns is a
# mask, a label, a source and a creation time -- the hash stays in the database and the
# plaintext exists only in the response of the call that created it.


class ApiKeyCreate(BaseModel):
    label: str = Field(default="", max_length=LABEL_MAX_CHARS)


def _key_store() -> ApiKeyStore:
    store = get_store()
    if store is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key store is not ready.",
        )
    return store


@router.get("/api-keys", summary="Caller API keys, masked", tags=["management"])
async def api_keys_list(_=Depends(require_admin_session)) -> dict[str, Any]:
    items = await _key_store().list_keys()
    return {"items": items, "total": len(items)}


@router.post("/api-keys", status_code=http_status.HTTP_201_CREATED, summary="Create a caller API key", tags=["management"])
async def api_keys_create(body: ApiKeyCreate, _=Depends(require_admin_session)) -> dict[str, Any]:
    label = body.label.strip()
    if not label:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="A label is required, so the key is recognisable later.",
        )
    item, secret = await _key_store().create(label)
    return {"item": item, "secret": secret}


@router.delete("/api-keys/{key_id}", summary="Revoke a caller API key", tags=["management"])
async def api_keys_delete(key_id: int, _=Depends(require_admin_session)) -> dict[str, Any]:
    if not await _key_store().delete(key_id):
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="No such API key.")
    return {"deleted": key_id}


def _clean_config_dict(cfg: Config) -> dict[str, Any]:
    """Serialisable view of the config for the console to edit: generated model_aliases are
    stripped so the round trip never grows config.yaml with entries that
    ``_expand_upstream_aliases`` recomputes on every load anyway."""
    data = cfg.model_dump(mode="json")
    renamed = cfg._renamed_alias_names
    if renamed:
        data["aliases"] = {k: v for k, v in data.get("aliases", {}).items() if k not in renamed}
    return data


_SEGMENT_MODELS: dict[str, type[BaseModel]] = {
    "rerank": RerankConfig,
    "systemone": SystemOneConfig,
    "websearch": WebSearchConfig,
}


@router.get("/config", summary="Current config.yaml as a structured object", tags=["management"])
async def get_config(request: Request, _=Depends(require_admin)) -> JSONResponse:
    """The live config the Settings console loads and edits. Generated aliases are removed
    so saving never adds the rename entries the loader recomputes for itself."""
    cfg = request.app.state.manager.config
    return JSONResponse(_clean_config_dict(cfg))


class ConfigSaveBody(BaseModel):
    config: dict[str, Any]


@router.put("/config", summary="Validate and overwrite config.yaml", tags=["management"])
async def put_config(request: Request, body: ConfigSaveBody, _=Depends(require_admin)) -> JSONResponse:
    """Validate each edited segment, then overwrite the whole config.yaml atomically.

    Only the four UI-editable segments (``upstreams``, ``rerank``, ``systemone``,
    ``websearch``) are taken from the request; every other top-level key is preserved
    verbatim from the file on disk, so advanced sections the console does not expose are
    never lost. Each segment is checked on its own first so the error names the exact block
    that failed; a clean pass then re-validates the merged whole before writing.
    """
    manager = request.app.state.manager
    incoming = body.config
    segments = ["upstreams", "rerank", "systemone", "websearch"]
    errors: dict[str, str] = {}

    # Re-parse the on-disk file so untouched sections keep their exact current shape.
    try:
        raw = yaml.safe_load(manager.path.read_text(encoding="utf-8")) if manager.path.exists() else {}
    except Exception as exc:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read config.yaml: {exc}",
        )
    if not isinstance(raw, dict):
        raw = {}

    # Validate each editable segment individually and replace it in `raw`.
    for seg in segments:
        value = incoming.get(seg)
        try:
            if seg == "upstreams":
                validated = TypeAdapter(list[UpstreamConfig]).validate_python(
                    value if value is not None else []
                )
                raw[seg] = [u.model_dump(mode="json", exclude_defaults=True) for u in validated]
            else:
                model_cls = _SEGMENT_MODELS[seg]
                validated = model_cls.model_validate(value if value is not None else {})
                raw[seg] = validated.model_dump(mode="json", exclude_defaults=True)
        except ValidationError as exc:
            errors[seg] = _format_validation_error(exc)
        except Exception as exc:  # e.g. a wrong shape fed to the adapter
            errors[seg] = str(exc)

    if errors:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"segments": errors},
        )

    # Full-object sanity check on the merged file before touching disk.
    try:
        Config.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"segments": {"config": _format_validation_error(exc)}},
        )

    # Atomic overwrite: write a temp file beside the target, then replace.
    target = manager.path
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(_dump_yaml(raw), encoding="utf-8")
        os.replace(tmp, target)
    except Exception as exc:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to write config.yaml: {exc}",
        )
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    manager.reload()
    return JSONResponse({"ok": True, "reloaded": True})

