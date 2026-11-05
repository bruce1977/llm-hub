"""
Reverse proxy module: routes requests to the matching Ollama upstream by model name.

Responsibilities:
  - extract the `model` field from the request body
  - resolve aliases (swap in the real model name + force-inject parameters such as think:false)
  - locate the target upstream and forward (with NDJSON streaming passthrough)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from auth import client_ip
from fastapi import HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from request_log import extract_token_usage, now_ts, sanitize_body

logger = logging.getLogger("llm_hub")

#: Response bytes retained for the request log (bodies and sizes beyond this are cut).
LOG_BODY_BYTES = 8192

# hop-by-hop / response headers that must not be forwarded
EXCLUDED_RESPONSE_HEADERS = {
    "transfer-encoding",
    "content-encoding",
    "content-length",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "upgrade",
}

# These endpoints stream by default when `stream` is not set (matches Ollama behavior)
STREAM_BY_DEFAULT = {
    "api/chat",
    "api/generate",
    "v1/chat/completions",
    "v1/completions",
}

# Endpoints without a `model` field; forwarded straight to the default upstream
MODELLESS_ENDPOINTS = {
    "api/tags",
    "api/ps",
    "api/version",
    "",
}


class ProxyService:
    def __init__(self, manager, client: httpx.AsyncClient, request_log=None):
        self.manager = manager
        self.client = client
        self.request_log = request_log

    # ------------------------------------------------------------------ logging ---
    def _logging_on(self, cfg) -> bool:
        """Per-request switch: ``config.yaml`` -> ``request_log.enabled`` (hot reload)."""
        return self.request_log is not None and cfg.request_log.enabled

    def _record(self, meta: dict[str, Any], *, status_code: int, duration_ms: int, **extra: Any) -> None:
        """Queue one request-log row; never raises and never blocks the forwarding path."""
        if not meta.get("logging"):
            return
        entry = {
            "ts": meta["ts"],
            "ts_end": now_ts(),
            "method": meta["method"],
            "path": meta["path"],
            "query": meta.get("query"),
            "requested_model": meta.get("requested_model"),
            "model": meta.get("model"),
            "upstream": meta.get("upstream"),
            "status": status_code,
            "duration_ms": duration_ms,
            "ttfb_ms": extra.get("ttfb_ms"),
            "stream": meta.get("stream", 0),
            "tokens_in": extra.get("tokens_in"),
            "tokens_out": extra.get("tokens_out"),
            "tokens": extra.get("tokens"),
            "req_size": meta.get("req_size"),
            "res_size": extra.get("res_size"),
            "client_ip": meta.get("client_ip"),
            "error": extra.get("error"),
            "request_body": meta.get("request_body"),
            "response_body": extra.get("response_body"),
            "request_body_truncated": meta.get("request_body_truncated", 0),
            "response_body_truncated": extra.get("response_body_truncated", 0),
        }
        self.request_log.record(entry)

    def _record_error(self, meta: dict[str, Any], status_code: int, error: str) -> None:
        duration_ms = round((time.perf_counter() - meta["started"]) * 1000)
        logger.debug("Request log: %s /%s -> %s (%d ms)", meta["method"], meta["path"].lstrip("/"), status_code, duration_ms)
        self._record(meta, status_code=status_code, duration_ms=duration_ms, error=error)

    # ------------------------------------------------------------------ helpers ---
    @staticmethod
    def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
        return {k: v for k, v in headers.items() if k.lower() not in EXCLUDED_RESPONSE_HEADERS}

    @staticmethod
    def _should_stream(path: str, body: dict[str, Any] | None) -> bool:
        if isinstance(body, dict) and isinstance(body.get("stream"), bool):
            return body["stream"]
        return path in STREAM_BY_DEFAULT

    @staticmethod
    def _normalize_tool_calls(body: dict[str, Any] | None) -> dict[str, Any] | None:
        """Some OpenAI-compatible clients stream tool calls with `arguments` as a JSON *object*
        (e.g. {"path": "main.py"}) instead of the required JSON *string*
        (e.g. "{\"path\":\"main.py\"}"). Ollama's gin parser strictly rejects the object form
        with HTTP 400 ('cannot unmarshal object into ... arguments of type string').

        We re-serialize objects/arrays to a string at the proxy boundary so upstream Ollama
        always receives a valid payload, regardless of how the client emitted it."""
        if not isinstance(body, dict):
            return body
        messages = body.get("messages")
        if not isinstance(messages, list):
            return body
        changed = False
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            tool_calls = msg.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function")
                if not isinstance(fn, dict):
                    continue
                args = fn.get("arguments")
                if isinstance(args, (dict, list)):
                    fn["arguments"] = json.dumps(args, ensure_ascii=False)
                    changed = True
                elif args is None:
                    fn["arguments"] = ""
                    changed = True
        if changed:
            logger.debug("Normalized tool_calls.arguments from object/array to JSON string")
        return body

    def _build_headers(self, request: Request, upstream) -> dict[str, str]:
        """Build forwarding headers: strip hop-by-hop and client credentials, attach upstream-specific headers."""
        headers: dict[str, str] = {}
        skip = {
            "host",
            "content-length",
            "connection",
            "keep-alive",
            "transfer-encoding",
            "upgrade",
            "authorization",
            "x-api-key",
            "accept-encoding",
        }
        for key, value in request.headers.items():
            if key.lower() in skip:
                continue
            headers[key] = value

        # Upstream-specific headers (optional)
        custom = getattr(upstream, "headers", None)
        if isinstance(custom, dict):
            headers.update({str(k): str(v) for k, v in custom.items()})
        return headers

    # ------------------------------------------------------------------ main flow ---
    async def forward(self, request: Request, path: str) -> Response:
        cfg = self.manager.config
        method = request.method.upper()
        started = time.perf_counter()

        # read and parse the request body
        raw_body = await request.body()
        body: dict[str, Any] | None = None
        if raw_body:
            try:
                parsed = json.loads(raw_body)
                if isinstance(parsed, dict):
                    body = parsed
            except (json.JSONDecodeError, UnicodeDecodeError):
                body = None

        path_clean = path.strip("/")
        # A HEAD carries no body and no answer, and monitors send it continuously, so it is
        # forwarded but never recorded - the log is for calls, not for liveness probes.
        logging_on = self._logging_on(cfg) and method != "HEAD"
        req_text, req_trunc = sanitize_body(raw_body) if logging_on else (None, False)
        requested = (body or {}).get("model")
        meta: dict[str, Any] = {
            "ts": now_ts(),
            "method": method,
            "path": "/" + path_clean,
            "query": request.url.query or None,
            "model": None,
            "requested_model": requested if isinstance(requested, str) else None,
            "upstream": None,
            "client_ip": client_ip(request),
            "req_size": len(raw_body),
            "request_body": req_text,
            "request_body_truncated": 1 if req_trunc else 0,
            "stream": 0,
            "started": started,
            "logging": logging_on,
        }

        # security hardening: block admin/model-management endpoints on public gateways
        if cfg.security.is_path_blocked(path_clean):
            logger.warning("Blocked admin/management endpoint: %s /%s", method, path_clean)
            detail = f"Endpoint '/{path_clean}' is disabled on this public gateway."
            self._record_error(meta, status.HTTP_403_FORBIDDEN, detail)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

        # security hardening: reject oversized request bodies (DoS protection)
        if cfg.security.max_body_bytes and raw_body and len(raw_body) > cfg.security.max_body_bytes:
            self._record_error(meta, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Request body too large.")
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Request body too large.",
            )

        # locate the target upstream
        resolved = None
        if body is not None and isinstance(body.get("model"), str):
            resolved = cfg.resolve(body["model"])
        elif path_clean in MODELLESS_ENDPOINTS or method == "GET":
            resolved = cfg.resolve("")  # fall back to the default upstream
        else:
            resolved = cfg.resolve("")

        upstream = resolved.upstream if resolved else None
        if upstream is None:
            model = (body or {}).get("model", "")
            meta["model"] = model or None
            detail = (
                f"No upstream configured for model '{model}'. "
                "Add it to an upstream's 'models' list, or set 'default_upstream'."
            )
            self._record_error(meta, status.HTTP_404_NOT_FOUND, detail)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
        meta["upstream"] = upstream.name
        meta["model"] = (resolved.target if resolved else None) or (body or {}).get("model") or None

        # alias handling: swap in the real model name + force-inject parameters
        forward_body: Any = body
        if body is not None and resolved is not None:
            body = dict(body)
            if resolved.is_alias:
                body["model"] = resolved.target
                logger.debug(
                    "Alias mapping: %s -> %s (injected params %s)",
                    resolved.requested,
                    resolved.target,
                    resolved.extra_params or "none",
                )
            if resolved.extra_params:
                body.update(resolved.extra_params)
            forward_body = body
            forward_body = self._normalize_tool_calls(forward_body)

        # num_ctx 固化：按真实模型名强制覆盖 Ollama 的 options.num_ctx，忽略客户端请求值。
        # 只对 Ollama 上游生效（OpenAI / LM Studio 用 max_tokens，不认 num_ctx）。
        # 无论客户端是否显式传 num_ctx，最终都以这里的约定值为准，避免同一模型因不同 ctx
        # 被 Ollama 反复新建实例、显存叠加。
        if (
            forward_body is not None
            and isinstance(forward_body, dict)
            and upstream is not None
            and getattr(upstream, "type", None) == "ollama"
            and resolved is not None
            and resolved.target in cfg.num_ctx_overrides
        ):
            override = cfg.num_ctx_overrides[resolved.target]
            options = forward_body.get("options")
            if not isinstance(options, dict):
                options = {}
                forward_body["options"] = options
            if options.get("num_ctx") != override:
                options["num_ctx"] = override
                logger.info(
                    "num_ctx clamped: model=%s -> options.num_ctx=%d (client value ignored)",
                    resolved.target,
                    override,
                )

        # Body serialization: pass through raw bytes when JSON parsing fails
        content: bytes | None = None
        json_body: dict[str, Any] | None = None
        if forward_body is not None:
            json_body = forward_body
        elif raw_body:
            content = raw_body

        # preserve query params (drop api_key)
        params = {k: v for k, v in request.query_params.items() if k.lower() != "api_key"}

        url = f"{upstream.base_url_stripped()}/{path_clean}" if path_clean else upstream.base_url_stripped()
        timeout = upstream.timeout or cfg.server.request_timeout

        logger.info(
            "Forwarding %s /%s -> %s | model=%s | stream=%s",
            method,
            path_clean,
            upstream.name,
            resolved.target if resolved else "-",
            self._should_stream(path_clean, json_body),
        )

        headers = self._build_headers(request, upstream)
        streaming = self._should_stream(path_clean, json_body)
        meta["stream"] = 1 if streaming else 0

        try:
            if streaming:
                return await self._forward_stream(
                    method=method,
                    url=url,
                    headers=headers,
                    json_body=json_body,
                    content=content,
                    params=params,
                    timeout=timeout,
                    meta=meta,
                )
            return await self._forward_normal(
                method=method,
                url=url,
                headers=headers,
                json_body=json_body,
                content=content,
                params=params,
                timeout=timeout,
                meta=meta,
            )
        except httpx.ConnectError as exc:
            logger.error("Cannot connect to upstream %s (%s): %s", upstream.name, url, exc)
            detail = f"Upstream '{upstream.name}' is unreachable: {exc}"
            self._record_error(meta, status.HTTP_502_BAD_GATEWAY, detail)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc
        except httpx.ReadTimeout as exc:
            logger.error("Upstream %s read timed out: %s", upstream.name, exc)
            detail = f"Upstream '{upstream.name}' timed out after {timeout}s."
            self._record_error(meta, status.HTTP_504_GATEWAY_TIMEOUT, detail)
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=detail) from exc
        except httpx.HTTPError as exc:
            logger.error("Upstream %s request failed: %s", upstream.name, exc)
            detail = f"Upstream '{upstream.name}' error: {exc}"
            self._record_error(meta, status.HTTP_502_BAD_GATEWAY, detail)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc

    # ------------------------------------------------------------ Forwarding implementations ---
    async def _forward_stream(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        json_body: dict[str, Any] | None,
        content: bytes | None,
        params: dict[str, str],
        timeout: float,
        meta: dict[str, Any],
    ) -> Response:
        request = self.client.build_request(
            method=method,
            url=url,
            headers=headers,
            json=json_body if json_body is not None else None,
            content=content,
            params=params or None,
            timeout=timeout,
        )
        resp = await self.client.send(request, stream=True)

        async def body_iterator():
            captured = bytearray()
            total = 0
            first_chunk_at: float | None = None
            try:
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        if first_chunk_at is None:
                            first_chunk_at = time.perf_counter()
                        total += len(chunk)
                        if meta["logging"] and len(captured) < LOG_BODY_BYTES:
                            captured.extend(chunk[: LOG_BODY_BYTES - len(captured)])
                        yield chunk
            finally:
                await resp.aclose()
                if meta["logging"]:
                    duration_ms = round((time.perf_counter() - meta["started"]) * 1000)
                    ttfb_ms = (
                        round((first_chunk_at - meta["started"]) * 1000) if first_chunk_at is not None else None
                    )
                    resp_text, _ = sanitize_body(bytes(captured))
                    self._record(
                        meta,
                        status_code=resp.status_code,
                        duration_ms=duration_ms,
                        ttfb_ms=ttfb_ms,
                        res_size=total,
                        response_body=resp_text,
                        response_body_truncated=1 if total > len(captured) else 0,
                        error=None if resp.status_code < 400 else f"HTTP {resp.status_code}",
                    )

        media_type = resp.headers.get("content-type")
        return StreamingResponse(
            body_iterator(),
            status_code=resp.status_code,
            headers=self._filter_response_headers(resp.headers),
            media_type=media_type,
        )

    async def _forward_normal(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        json_body: dict[str, Any] | None,
        content: bytes | None,
        params: dict[str, str],
        timeout: float,
        meta: dict[str, Any],
    ) -> Response:
        resp = await self.client.request(
            method=method,
            url=url,
            headers=headers,
            json=json_body if json_body is not None else None,
            content=content,
            params=params or None,
            timeout=timeout,
        )
        if meta["logging"]:
            duration_ms = round((time.perf_counter() - meta["started"]) * 1000)
            resp_text, resp_trunc = sanitize_body(resp.content)
            tokens_in, tokens_out, tokens_total = extract_token_usage(resp.content)
            self._record(
                meta,
                status_code=resp.status_code,
                duration_ms=duration_ms,
                ttfb_ms=duration_ms,
                res_size=len(resp.content),
                tokens=tokens_total,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                response_body=resp_text,
                response_body_truncated=1 if resp_trunc else 0,
                error=None if resp.status_code < 400 else f"HTTP {resp.status_code}",
            )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=self._filter_response_headers(resp.headers),
            media_type=resp.headers.get("content-type"),
        )
