"""
System One proxy service.

Integrates the laya-server `POST /v1/systemone` (System One structured decision API,
Jev-compatible) into llm-hub.

Authentication split:
  - The *caller* authenticates against llm-hub with the gateway's own API key
    (cfg.auth.api_keys, sourced from `.env` / GATEWAY_API_KEYS - the `.keys` config).
  - llm-hub then forwards the request to the configured laya-server `backend`, signing
    it with the key read from the environment variable named by `api_key_env`
    (default LAYA_SERVER_API_KEY). That key never has to live in config.json.

Everything else (request body, response body, status code, content-type) is forwarded
as-is. The configured `model` is only injected when it is a concrete value (not the
"local" sentinel) and the caller did not already supply one.
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

# Headers that must never be forwarded to the upstream (hop-by-hop + client credentials).
_EXCLUDED_REQUEST_HEADERS = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "upgrade",
    # Client credentials: the gateway validates these; the upstream uses its own key.
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


class SystemOneService:
    def __init__(self, manager, client: httpx.AsyncClient):
        self.manager = manager
        self.client = client

    @staticmethod
    def _build_headers(request: Request) -> dict[str, str]:
        """Forward caller headers minus hop-by-hop and client credentials."""
        return {k: v for k, v in request.headers.items() if k.lower() not in _EXCLUDED_REQUEST_HEADERS}

    async def handle(self, request: Request) -> Response:
        cfg = self.manager.config
        so = cfg.systemone

        if not so.enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="System One endpoint is not enabled on this gateway.",
            )

        # Read and parse the request body (pass-through semantics: preserve unknown fields).
        raw_body = await request.body()
        try:
            body: Any = json.loads(raw_body) if raw_body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        if not isinstance(body, dict):
            body = {}

        # Model injection: only when a concrete model is configured (not the "local"
        # sentinel) and the caller did not already set one. laya-server defaults to "auto".
        if so.model and so.model != "local" and not body.get("model"):
            body["model"] = so.model

        # Build upstream request headers.
        headers = self._build_headers(request)

        # Sign the upstream request with the laya-server key from the environment.
        laya_key = os.getenv(so.api_key_env, "").strip()
        if laya_key:
            headers["Authorization"] = f"Bearer {laya_key}"
        else:
            logger.warning(
                "Environment variable %s is not set; forwarding /%s without a "
                "laya-server API key (expect HTTP 401 from the backend).",
                so.api_key_env,
                so.path,
            )

        url = f"{so.backend.rstrip('/')}/{so.path.strip('/')}"
        timeout = float(so.timeout) or cfg.server.request_timeout

        logger.info(
            "Forwarding POST /%s -> %s | model=%s",
            so.path,
            url,
            body.get("model", "-"),
        )

        try:
            resp = await self.client.request(
                method="POST",
                url=url,
                headers=headers,
                json=body or None,
                timeout=timeout,
            )
        except httpx.ConnectError as exc:
            logger.error("Cannot connect to laya-server (%s): %s", url, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"laya-server '{so.backend}' is unreachable: {exc}",
            ) from exc
        except httpx.ReadTimeout as exc:
            logger.error("laya-server read timed out: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"laya-server timed out after {timeout}s.",
            ) from exc
        except httpx.HTTPError as exc:
            logger.error("laya-server request failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"laya-server error: {exc}",
            ) from exc

        # Pass the response through as-is (status, body, content-type).
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=_filter_response_headers(resp.headers),
            media_type=resp.headers.get("content-type"),
        )
