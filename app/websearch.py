"""
Web search proxy service.

Exposes the OpenCode API Gateway `POST /v1/web-search` (keyword -> retrieved web
passages) as `POST /v1/tools/web-search` on this gateway.

Authentication split:
  - The *caller* authenticates against llm-hub with the gateway's own API key
    (cfg.auth.api_keys, from GATEWAY_API_KEYS / .env).
  - The search provider is a regular entry in `cfg.upstreams`, selected by
    `websearch.upstream` (or the first `type: "opencode"` upstream). Its address,
    timeout and extra headers therefore also serve model routing and aggregation.
    If `websearch.api_key_env` names a set environment variable, its value signs the
    upstream call as `Authorization: Bearer <key>`; otherwise the call is unsigned.

The request body and the upstream response are passed through unchanged, so callers
see the provider's own contract (`query`, `results[]`, `text`, `sources`, ...).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from fastapi import HTTPException, Request, status
from fastapi.responses import Response

logger = logging.getLogger("llm_hub")

# Headers that must never be forwarded upstream (hop-by-hop + client credentials).
_EXCLUDED_REQUEST_HEADERS = {
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

# Response headers that must not be forwarded back to the caller.
_EXCLUDED_RESPONSE_HEADERS = {
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


def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _EXCLUDED_RESPONSE_HEADERS}


class WebSearchService:
    def __init__(self, manager, client: httpx.AsyncClient):
        self.manager = manager
        self.client = client

    @staticmethod
    def _build_headers(request: Request, upstream) -> dict[str, str]:
        """Caller headers minus hop-by-hop and client credentials, plus upstream headers."""
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _EXCLUDED_REQUEST_HEADERS}
        custom = getattr(upstream, "headers", None)
        if isinstance(custom, dict):
            headers.update({str(k): str(v) for k, v in custom.items()})
        return headers

    async def handle(self, request: Request) -> Response:
        cfg = self.manager.config
        ws = cfg.websearch

        if not ws.enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Web search endpoint is not enabled on this gateway.",
            )

        upstream = cfg.websearch_upstream_obj()
        if upstream is None:
            hint = (
                f"no upstream is named '{ws.upstream}'"
                if ws.upstream
                else "no upstream of type 'opencode' is configured"
            )
            logger.error("Web search requested but %s", hint)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Web search has no provider configured: " + hint + ". "
                    "Add an upstream and point `websearch.upstream` at it."
                ),
            )

        raw_body = await request.body()
        try:
            body: Any = json.loads(raw_body) if raw_body else {}
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Web search request body must be valid JSON.",
            ) from exc
        if not isinstance(body, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Web search request body must be a JSON object.",
            )

        headers = self._build_headers(request, upstream)

        # Optional upstream signing: the key lives only in the environment.
        if ws.api_key_env:
            provider_key = os.getenv(ws.api_key_env, "").strip()
            if provider_key:
                headers["Authorization"] = f"Bearer {provider_key}"
            else:
                logger.warning(
                    "Environment variable %s is not set; calling web search provider "
                    "'%s' without an API key.",
                    ws.api_key_env,
                    upstream.name,
                )

        url = f"{upstream.base_url_stripped()}/{ws.path.strip('/')}"
        timeout = float(ws.timeout) or upstream.timeout or cfg.server.request_timeout

        logger.info(
            "Forwarding POST /%s -> %s (upstream '%s') | query=%r",
            ws.path,
            url,
            upstream.name,
            str(body.get("query", ""))[:80],
        )

        try:
            resp = await self.client.request(
                method="POST",
                url=url,
                headers=headers,
                json=body,
                timeout=timeout,
            )
        except httpx.ConnectError as exc:
            logger.error("Cannot connect to web search provider '%s' (%s): %s", upstream.name, url, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Web search provider '{upstream.name}' is unreachable: {exc}",
            ) from exc
        except httpx.ReadTimeout as exc:
            logger.error("Web search provider '%s' read timed out: %s", upstream.name, exc)
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"Web search provider '{upstream.name}' timed out after {timeout}s.",
            ) from exc
        except httpx.HTTPError as exc:
            logger.error("Web search provider '%s' request failed: %s", upstream.name, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Web search provider error: {exc}",
            ) from exc

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=_filter_response_headers(resp.headers),
            media_type=resp.headers.get("content-type"),
        )
