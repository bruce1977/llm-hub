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
from typing import Any

import httpx
from fastapi import HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

logger = logging.getLogger("llm_hub")

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
    def __init__(self, manager, client: httpx.AsyncClient):
        self.manager = manager
        self.client = client

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

        # security hardening: block admin/model-management endpoints on public gateways
        if cfg.security.is_path_blocked(path_clean):
            logger.warning("Blocked admin/management endpoint: %s /%s", method, path_clean)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Endpoint '/{path_clean}' is disabled on this public gateway.",
            )

        # security hardening: reject oversized request bodies (DoS protection)
        if cfg.security.max_body_bytes and raw_body and len(raw_body) > cfg.security.max_body_bytes:
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
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No upstream configured for model '{model}'. "
                    "Add it to an upstream's 'models' list, or set 'default_upstream'."
                ),
            )

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

        try:
            if self._should_stream(path_clean, json_body):
                return await self._forward_stream(
                    method=method,
                    url=url,
                    headers=headers,
                    json_body=json_body,
                    content=content,
                    params=params,
                    timeout=timeout,
                )
            return await self._forward_normal(
                method=method,
                url=url,
                headers=headers,
                json_body=json_body,
                content=content,
                params=params,
                timeout=timeout,
            )
        except httpx.ConnectError as exc:
            logger.error("Cannot connect to upstream %s (%s): %s", upstream.name, url, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Upstream '{upstream.name}' is unreachable: {exc}",
            ) from exc
        except httpx.ReadTimeout as exc:
            logger.error("Upstream %s read timed out: %s", upstream.name, exc)
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"Upstream '{upstream.name}' timed out after {timeout}s.",
            ) from exc
        except httpx.HTTPError as exc:
            logger.error("Upstream %s request failed: %s", upstream.name, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Upstream '{upstream.name}' error: {exc}",
            ) from exc

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
            try:
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        yield chunk
            finally:
                await resp.aclose()

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
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=self._filter_response_headers(resp.headers),
            media_type=resp.headers.get("content-type"),
        )
