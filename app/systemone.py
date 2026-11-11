"""
System One proxy service.

Integrates the System One structured decision API (`POST /v1/systemone`) into llm-hub.
Both a laya-server deployment and Ollama 0.40+ (which exposes the same path natively)
can serve it.

Authentication split:
  - The *caller* authenticates against llm-hub with a key from the SQLite api_keys
    table (minted at /admin -> API keys), or a console session.
    On the internal zone this API may be key-free when allow_anonymous is true;
    the public zone always requires a credential.
  - llm-hub then forwards the request to the configured `backend`, signing it with the
    key read from the environment variable named by `api_key_env` (default
    SYSTEMONE_SERVER_API_KEY). That key never has to live in config.yaml, and a backend
    that needs no credential just ignores the header.

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
from auth import read_limited_body
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
    "cookie",
    "referer",
    "origin",
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-forwarded-host",
    "x-real-ip",
    "x-client-ip",
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
    "set-cookie",
}


def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _EXCLUDED_RESPONSE_HEADERS}


def backend_key(api_key_env: str) -> tuple[str, str]:
    """Return ``(Authorization value, unsigned reason)`` for the System One backend.

    An empty ``api_key_env`` is the documented *unsigned by design* mode (e.g. an Ollama
    0.40+ backend ignores ``Authorization``). It must not be reported as "environment
    variable  is not set" - there is no variable name to look up. The caller logs the
    reason only when it is non-empty.
    """
    if not api_key_env:
        return "", ""
    value = os.getenv(api_key_env, "").strip()
    if value:
        return f"Bearer {value}", ""
    return "", f"Environment variable {api_key_env} is not set"


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
        raw_body = await read_limited_body(request, cfg.security.max_body_bytes)
        try:
            body: Any = json.loads(raw_body) if raw_body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        if not isinstance(body, dict):
            body = {}

        # Model injection: only when a concrete model is configured (not the "local"
        # sentinel) and the caller did not already set one. laya-server reads it as a
        # routing hint and defaults to "auto"; an Ollama backend wants a real model name
        # and answers 404 for "auto", so point `systemone.model` at a model it serves.
        if so.model and so.model != "local" and not body.get("model"):
            body["model"] = so.model

        # Build upstream request headers.
        headers = self._build_headers(request)

        # Sign the upstream request with the backend key from the environment. An empty
        # `api_key_env` is a valid unsigned mode, not a missing variable with a blank name.
        authorization, unsigned_reason = backend_key(so.api_key_env)
        if authorization:
            headers["Authorization"] = authorization
        elif unsigned_reason:
            logger.warning(
                "%s; forwarding /%s to the System One backend unsigned (only a problem "
                "if that backend requires a key).",
                unsigned_reason,
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
            logger.error("Cannot connect to System One backend (%s): %s", url, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"System One backend '{so.backend}' is unreachable: {exc}",
            ) from exc
        except httpx.ReadTimeout as exc:
            logger.error("System One backend read timed out: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"System One backend timed out after {timeout}s.",
            ) from exc
        except httpx.HTTPError as exc:
            logger.error("System One backend request failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"System One backend error: {exc}",
            ) from exc

        # Pass the response through as-is (status, body, content-type).
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=_filter_response_headers(resp.headers),
            media_type=resp.headers.get("content-type"),
        )
