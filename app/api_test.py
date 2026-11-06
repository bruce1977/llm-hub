"""
Execution engine behind the web console's per-endpoint API testers.

The console cannot call a business endpoint itself: `/api/generate`, `/v1/rerank` and the
rest authenticate the *caller* with a gateway API key, and the admin session cookie is
deliberately not one (see `admin_auth.require_admin`). So a test runs on the server, where
the request is rebuilt as a real ASGI `Request` and handed to the same service the gateway
uses for that endpoint. That reuse is the point: alias resolution, forced parameter
injection, `num_ctx` clamping, blocked-path rules and request-log capture all behave exactly
as they would for an external caller, so what the tester shows is what the API really does.

Nothing here reaches a URL the config did not name - `KINDS` is a closed allowlist of
endpoint families, and each of them resolves its own upstream through the normal routing
code, so the route cannot be turned into a proxy for an arbitrary address.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException, Request
from starlette.responses import Response, StreamingResponse

from proxy import STREAM_BY_DEFAULT

logger = logging.getLogger("llm_hub")

#: How much of a response body is carried back to the console. A tester is for looking at
#: an answer, not for downloading one, so a runaway generation is cut short here.
MAX_RESPONSE_BYTES = 128 * 1024

Executor = Literal["proxy", "rerank", "systemone", "websearch", "messages"]


@dataclass(frozen=True)
class Kind:
    """One documented endpoint family the console can test."""

    name: str
    #: Public path the tester presents, and the path the gateway itself serves.
    endpoint: str
    #: Which app service the call goes through.
    executor: Executor
    #: `Config.api_models` field that narrows the model selector for this family.
    family: str
    #: Whether the endpoint needs a model at all (web search lets the provider choose).
    model_required: bool
    #: Whether a name outside the selector is legal (a laya-server routing enum, an
    #: opencode-port model id), which makes the console select accept free input.
    free_model: bool = False
    #: Whether the endpoint speaks Ollama's native dialect (`/api/...`). An `lmstudio` or
    #: `opencode` upstream answers only the `/v1` side, so its models must not be offered.
    native: bool = False


KINDS: dict[str, Kind] = {
    kind.name: kind
    for kind in (
        Kind("generate", "/api/generate", "proxy", "generate", True, native=True),
        Kind("chat", "/v1/chat/completions", "proxy", "generate", True),
        Kind("embed_ollama", "/api/embed", "proxy", "embed", True, native=True),
        Kind("embed_openai", "/v1/embeddings", "proxy", "embed", True),
        Kind("rerank", "/v1/rerank", "rerank", "rerank", True),
        Kind("systemone", "/v1/systemone", "systemone", "systemone", False, free_model=True),
        Kind("websearch", "/v1/tools/web-search", "websearch", "websearch", False, free_model=True),
        Kind("messages", "/v1/messages", "messages", "generate", True),
    )
}


def get_kind(name: Any) -> Kind:
    """Look a kind up, 400 on anything outside the allowlist."""
    kind = KINDS.get(str(name or ""))
    if kind is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown API test kind '{name}'. Expected one of: {', '.join(sorted(KINDS))}.",
        )
    return kind


def _will_stream(endpoint: str, body: dict[str, Any]) -> bool:
    """The transport the proxy will pick, so the console labels the pane correctly."""
    if isinstance(body.get("stream"), bool):
        return body["stream"]
    return endpoint.strip("/") in STREAM_BY_DEFAULT


def _make_request(app: Any, path: str, payload: bytes, client: tuple[str, int]) -> Request:
    """Rebuild the caller's POST as a real `Request` so the shared services accept it.

    Only what those services read is filled in: method, path, body and client address. The
    headers carry nothing but the content type, which is the same thing the forwarders do
    after stripping caller credentials.
    """
    clean = "/" + path.strip("/")
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": clean,
        "raw_path": clean.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), (b"host", b"llm-hub-console")],
        "client": client,
        "server": ("llm-hub-console", 80),
        "app": app,
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(scope, receive)


async def _drain(response: Response) -> tuple[int, str | None, bytes, int | None, bool]:
    """Reduce a served response to (status, content type, body, ms to first byte, truncated)."""
    if not isinstance(response, StreamingResponse):
        raw = bytes(response.body)
        return (
            response.status_code,
            response.media_type,
            raw[:MAX_RESPONSE_BYTES],
            None,
            len(raw) > MAX_RESPONSE_BYTES,
        )

    started = time.perf_counter()
    collected = bytearray()
    total = 0
    first_chunk_at: float | None = None
    iterator = response.body_iterator
    try:
        async for chunk in iterator:
            if isinstance(chunk, str):
                chunk = chunk.encode()
            if chunk and first_chunk_at is None:
                first_chunk_at = time.perf_counter()
            total += len(chunk)
            if len(collected) < MAX_RESPONSE_BYTES:
                collected.extend(chunk[: MAX_RESPONSE_BYTES - len(collected)])
    finally:
        # The proxy's generator writes the request-log row in its own finally, so closing
        # the iterator here is what makes a test call show up in the Requests view.
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            await aclose()
    ttfb_ms = round((first_chunk_at - started) * 1000) if first_chunk_at is not None else None
    return response.status_code, response.media_type, bytes(collected), ttfb_ms, total > len(collected)


def _decode(raw: bytes) -> tuple[Any, str]:
    """Return (parsed body when JSON, text) so the console can show either."""
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text), text
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, text


async def execute(app: Any, kind: Kind, body: dict[str, Any], client: tuple[str, int]) -> dict[str, Any]:
    """Run one test request through the endpoint's own gateway service."""
    cfg = app.state.manager.config
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")

    # What the gateway would route to, reported so the console can show alias resolution
    # instead of leaving the caller to guess which name actually ran.
    requested = body.get("model")
    preview: dict[str, Any] = {"requested_model": requested, "model": None, "upstream": None, "injected": None}
    if isinstance(requested, str) and requested and requested != "auto" and kind.executor in {"proxy", "rerank", "messages"}:
        resolved = cfg.resolve(requested)
        preview["model"] = resolved.target
        preview["upstream"] = resolved.upstream.name if resolved.upstream else None
        preview["injected"] = resolved.extra_params or None

    envelope: dict[str, Any] = {
        "kind": kind.name,
        "method": "POST",
        "endpoint": kind.endpoint,
        "stream": _will_stream(kind.endpoint, body),
        "truncated": False,
        "error": None,
        **preview,
    }

    def _failed(status_code: int, detail: Any, elapsed_ms: int) -> dict[str, Any]:
        parsed, text = _decode(
            detail if isinstance(detail, bytes)
            else json.dumps(detail, ensure_ascii=False).encode("utf-8")
            if isinstance(detail, (dict, list))
            else str(detail).encode("utf-8")
        )
        return {
            **envelope,
            "status": status_code,
            "ok": False,
            "duration_ms": elapsed_ms,
            "ttft_ms": None,
            "content_type": "application/json",
            "body": parsed,
            "text": text,
            "error": f"HTTP {status_code}" if status_code else None,
        }

    started = time.perf_counter()
    try:
        if kind.executor == "rerank":
            result = await app.state.rerank.handle(body)
            raw = json.dumps(result, ensure_ascii=False).encode("utf-8")[:MAX_RESPONSE_BYTES]
            status_code, content_type, ttfb_ms, truncated = 200, "application/json", None, False
        else:
            request = _make_request(app, kind.endpoint, payload, client)
            if kind.executor == "proxy":
                response = await app.state.proxy.forward(request, kind.endpoint.strip("/"))
            elif kind.executor == "messages":
                response = await app.state.messages.handle(request)
            else:
                service = app.state.systemone if kind.executor == "systemone" else app.state.websearch
                response = await service.handle(request)
            status_code, content_type, raw, ttfb_ms, truncated = await _drain(response)
    except HTTPException as exc:
        # A gateway refusal is an answer: show its status and detail like any caller would.
        return _failed(exc.status_code, exc.detail, round((time.perf_counter() - started) * 1000))
    except Exception as exc:  # a service that raised rather than answered
        logger.exception("API test '%s' failed", kind.name)
        return {
            **envelope,
            "status": 0,
            "ok": False,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "ttft_ms": None,
            "content_type": None,
            "body": None,
            "text": "",
            "error": f"{type(exc).__name__}: {exc}",
        }

    parsed, text = _decode(raw)
    return {
        **envelope,
        "status": status_code,
        "ok": status_code < 400,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "ttft_ms": ttfb_ms,
        "content_type": content_type,
        "body": parsed,
        "text": text,
        "truncated": truncated,
        "error": None if status_code < 400 else f"HTTP {status_code}",
    }
