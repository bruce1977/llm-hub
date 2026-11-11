"""
Anthropic Messages API bridge.

Exposes ``POST /v1/messages`` (Anthropic-compatible) on the gateway. The request uses the
Anthropic Messages shape (``model`` / ``max_tokens`` / ``system`` / ``messages`` / ``stream``)
and the response follows the Anthropic Messages envelope
(``id`` / ``type: message`` / ``role`` / ``content: [{type: text, text}]`` / ``stop_reason`` /
``usage: {input_tokens, output_tokens}``).

The gateway does **not** speak Anthropic internally: it translates the request into the
OpenAI chat format that every backend understands - Ollama's ``/api/chat`` and the
``/v1/chat/completions`` dialect used by LM Studio / OpenCode - and translates the answer
back. Streaming follows the Anthropic SSE event order
(``message_start`` → ``content_block_start`` → ``content_block_delta*`` → ``content_block_stop``
→ ``message_delta`` → ``message_stop``).

What is translated
-------------------
* ``system`` (string or array of text blocks) → a leading ``system`` message.
* ``messages[*].content`` (string or array of blocks) → string text, with ``image`` blocks
  passed through (base64 for Ollama, ``image_url`` for OpenAI-compatible backends).
* ``max_tokens`` → OpenAI ``max_tokens`` / Ollama ``options.num_predict``.
* ``temperature`` / ``top_p`` / ``top_k`` / ``stop_sequences`` → the backend equivalents;
  ``think`` injected by an alias still applies.
* ``usage`` is reconstructed from the backend's native token counts
  (OpenAI ``prompt_tokens`` / ``completion_tokens``; Ollama ``prompt_eval_count`` / ``eval_count``).

The ``model`` field defaults to ``auto`` (the first model of the default upstream) when empty,
so a caller that just wants "whatever the gateway can serve" does not have to name a model.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncIterator

import httpx
from auth import read_limited_body
from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from http_clients import inference_timeout
from pydantic import BaseModel, Field, ValidationError, model_validator

logger = logging.getLogger("llm_hub")

#: Upstream path used for each backend flavor.
OLLAMA_CHAT = "api/chat"
OPENAI_CHAT = "v1/chat/completions"


# --------------------------------------------------------------------------- #
# Request model (tolerant: any extra Anthropic field is kept but ignored)
# --------------------------------------------------------------------------- #
class _Message(BaseModel):
    model_config = {"extra": "allow"}

    role: str = "user"
    content: Any = ""


class MessageRequest(BaseModel):
    model_config = {"extra": "allow", "populate_by_name": True}

    model: str = "auto"
    max_tokens: int | None = None
    system: Any = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    stop_sequences: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if isinstance(data, dict):
            payload = dict(data)
            # Anthropic allows `system` as a string or an array of text blocks; normalize
            # nothing here, the converter handles both shapes.
            return payload
        return data


# --------------------------------------------------------------------------- #
# Content-block extraction
# --------------------------------------------------------------------------- #
def _system_text(system: Any) -> str:
    """Flatten an Anthropic ``system`` (string or array of text blocks) into one string."""
    if system is None:
        return ""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts: list[str] = []
        for block in system:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(p for p in parts if p)
    return str(system)


def _extract_blocks(content: Any) -> tuple[list[str], list[str]]:
    """Return ``(texts, images_b64)`` from an Anthropic ``content`` (string or block array).

    ``images_b64`` carries the raw base64 payload (no ``data:`` prefix) so it can be handed to
    Ollama directly; OpenAI-compatible backends get a ``data:`` URL built at assembly time.
    """
    texts: list[str] = []
    images: list[str] = []
    if isinstance(content, str):
        texts.append(content)
        return texts, images
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                texts.append(block)
                continue
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text")
                if isinstance(text, str):
                    texts.append(text)
            elif btype == "image":
                source = block.get("source") or {}
                if isinstance(source, dict) and source.get("type") == "base64":
                    data = source.get("data")
                    if isinstance(data, str):
                        images.append(data)
    return texts, images


# --------------------------------------------------------------------------- #
# Stop-reason mapping
# --------------------------------------------------------------------------- #
def _openai_stop(reason: Any) -> str:
    if reason == "length":
        return "max_tokens"
    if reason == "tool_calls":
        return "tool_use"
    if reason == "content_filter":
        return "content_filter"
    return "end_turn"


def _ollama_stop(reason: Any) -> str:
    if reason == "length":
        return "max_tokens"
    return "end_turn"


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class MessagesService:
    def __init__(self, manager, client: httpx.AsyncClient):
        self.manager = manager
        self.client = client

    # ----------------------------------------------------------- entrypoint ---
    async def handle(self, request: Request) -> Response:
        cfg = self.manager.config
        raw = await read_limited_body(request, cfg.security.max_body_bytes)
        try:
            parsed = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request body must be valid JSON.")
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request body must be a JSON object.")

        try:
            req = MessageRequest(**parsed)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid messages request: {exc}",
            ) from exc

        if not req.messages:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'messages' is required and must not be empty.",
            )
        if not req.max_tokens or req.max_tokens <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'max_tokens' is required and must be > 0.",
            )

        model = (req.model or "").strip()
        extra_params: dict[str, Any] = {}
        if not model or model == "auto":
            resolved = cfg.resolve("")
            upstream = resolved.upstream
            if upstream is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No default upstream configured; 'model' is required.",
                )
            if not upstream.models:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Default upstream '{upstream.name}' declares no models; 'model' is required.",
                )
            target = upstream.models[0]
        else:
            resolved = cfg.resolve(model)
            upstream = resolved.upstream
            if upstream is None:
                detail = (
                    f"No upstream configured for model '{model}'. "
                    "Add it to an upstream's 'models' list, or set 'default_upstream'."
                )
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
            target = resolved.target
            extra_params = dict(resolved.extra_params or {})

        is_ollama = getattr(upstream, "type", None) == "ollama"
        payload = self._build_payload(req, target, is_ollama, extra_params, cfg)

        url = f"{upstream.base_url_stripped()}/{'api/chat' if is_ollama else 'v1/chat/completions'}"
        timeout_s = upstream.timeout or cfg.server.request_timeout
        timeout = inference_timeout(timeout_s, cfg)

        logger.info(
            "Messages: %r -> %r | model=%r | stream=%s",
            "auto" if (not model or model == "auto") else model,
            upstream.name,
            target,
            req.stream,
        )

        # `upstreams[].headers` (provider Authorization, custom routing headers) used to
        # be dropped here entirely, so an upstream that requires a key always answered
        # 401. Only configured headers are attached - never the caller's own headers.
        custom = getattr(upstream, "headers", None)
        upstream_headers = (
            {str(k): str(v) for k, v in custom.items()} if isinstance(custom, dict) and custom else None
        )

        try:
            http_req = self.client.build_request(
                "POST", url, json=payload, timeout=timeout, headers=upstream_headers
            )
            resp = await self.client.send(http_req, stream=req.stream)
        except httpx.PoolTimeout as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Inference upstreams are busy; please retry shortly.",
                headers={"Retry-After": "5"},
            ) from exc
        except httpx.ConnectError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Upstream '{upstream.name}' is unreachable: {exc}",
            ) from exc
        except httpx.ReadTimeout as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"Upstream '{upstream.name}' timed out after {timeout_s}s.",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Upstream '{upstream.name}' error: {exc}",
            ) from exc

        if req.stream:
            return StreamingResponse(
                self._stream_anthropic(resp, is_ollama, target),
                media_type="text/event-stream",
                headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
            )

        return self._nonstream_response(resp, is_ollama, target)

    # ----------------------------------------------------- payload assembly ---
    def _build_payload(
        self,
        req: MessageRequest,
        target: str,
        is_ollama: bool,
        extra_params: dict[str, Any],
        cfg: Any,
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        system_text = _system_text(req.system)
        if system_text:
            messages.append({"role": "system", "content": system_text})

        for msg in req.messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role") or "user"
            texts, images = _extract_blocks(msg.get("content"))
            text = "\n".join(texts).strip()
            if images and is_ollama:
                entry: dict[str, Any] = {"role": role, "content": text, "images": list(images)}
            elif images:
                content_blocks = [{"type": "text", "text": text}] if text else []
                for data_b64 in images:
                    content_blocks.append(
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data_b64}"}}
                    )
                entry = {"role": role, "content": content_blocks}
            else:
                entry = {"role": role, "content": text}
            messages.append(entry)

        payload: dict[str, Any] = {"model": target, "messages": messages, "stream": bool(req.stream)}

        if is_ollama:
            payload.setdefault("options", {})["num_predict"] = int(req.max_tokens)
        else:
            payload["max_tokens"] = int(req.max_tokens)

        if req.temperature is not None:
            payload["temperature"] = req.temperature
        if req.top_p is not None:
            payload["top_p"] = req.top_p
        if req.top_k is not None and is_ollama:
            payload.setdefault("options", {})["top_k"] = int(req.top_k)
        if req.stop_sequences:
            if is_ollama:
                payload.setdefault("options", {})["stop"] = list(req.stop_sequences)
            else:
                payload["stop"] = list(req.stop_sequences)

        if extra_params:
            payload.update(extra_params)

        # num_ctx clamping mirrors the proxy: keep Ollama from spinning up a second instance.
        if is_ollama and target in cfg.num_ctx_overrides:
            override = cfg.num_ctx_overrides[target]
            options = payload.setdefault("options", {})
            if options.get("num_ctx") != override:
                options["num_ctx"] = override

        return payload

    # ------------------------------------------------------- non-stream reply ---
    def _nonstream_response(self, resp: httpx.Response, is_ollama: bool, target: str) -> Response:
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                detail = resp.text
            return JSONResponse(detail, status_code=resp.status_code)

        try:
            data = resp.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Upstream returned a non-JSON response.",
            )
        if not isinstance(data, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unexpected upstream response type.",
            )
        return JSONResponse(self._to_anthropic(data, is_ollama, target))

    def _to_anthropic(self, data: dict[str, Any], is_ollama: bool, target: str) -> dict[str, Any]:
        choices = data.get("choices") if not is_ollama else None
        if is_ollama:
            message = data.get("message") or {}
            text = message.get("content") if isinstance(message.get("content"), str) else ""
            usage = {
                "input_tokens": int(data.get("prompt_eval_count") or 0),
                "output_tokens": int(data.get("eval_count") or 0),
            }
            stop_reason = _ollama_stop(data.get("done_reason"))
        else:
            choice = choices[0] if choices else {}
            message = choice.get("message") or {}
            text = message.get("content") if isinstance(message.get("content"), str) else ""
            upstream_usage = data.get("usage") or {}
            usage = {
                "input_tokens": int(upstream_usage.get("prompt_tokens") or 0),
                "output_tokens": int(upstream_usage.get("completion_tokens") or 0),
            }
            stop_reason = _openai_stop(choice.get("finish_reason"))
        return {
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "model": target,
            "content": [{"type": "text", "text": text or ""}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": usage,
        }

    # ---------------------------------------------------------- SSE streaming ---
    @staticmethod
    def _sse(event: str, data: dict[str, Any]) -> str:
        # Compact JSON: smaller SSE payloads and a stable substring shape for clients/tests
        # that scan the raw event stream (e.g. `"text":"po"`).
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"

    @staticmethod
    async def _iter_lines(resp: httpx.Response) -> AsyncIterator[str]:
        """Yield decoded lines from a streaming httpx response (robust to chunk splits)."""
        buffer = bytearray()
        async for chunk in resp.aiter_bytes():
            if not chunk:
                continue
            buffer.extend(chunk)
            while True:
                idx = buffer.find(b"\n")
                if idx < 0:
                    break
                line = buffer[:idx].decode("utf-8", errors="replace").strip()
                del buffer[: idx + 1]
                if line:
                    yield line

    async def _stream_anthropic(
        self, resp: httpx.Response, is_ollama: bool, target: str
    ) -> AsyncIterator[str]:
        started = time.perf_counter()
        usage = {"input_tokens": 0, "output_tokens": 0}
        stop_reason: str | None = None
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"

        def delta(text: str) -> str:
            return self._sse(
                "content_block_delta",
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
            )

        # Surface an upstream error as an Anthropic error event instead of a bare reset.
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                detail = resp.text
            await resp.aclose()
            yield self._sse(
                "error",
                {"type": "error", "error": {"type": "api_error", "message": json.dumps(detail, ensure_ascii=False)}},
            )
            return

        try:
            yield self._sse(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": msg_id,
                        "type": "message",
                        "role": "assistant",
                        "model": target,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            )
            yield self._sse(
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            )

            async for line in self._iter_lines(resp):
                if is_ollama:
                    try:
                        data = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    text = (data.get("message") or {}).get("content")
                    if isinstance(text, str) and text:
                        yield delta(text)
                    if data.get("done"):
                        if data.get("prompt_eval_count") is not None:
                            usage["input_tokens"] = int(data["prompt_eval_count"])
                        if data.get("eval_count") is not None:
                            usage["output_tokens"] = int(data["eval_count"])
                        stop_reason = _ollama_stop(data.get("done_reason"))
                        break
                else:
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    upstream_usage = data.get("usage")
                    if isinstance(upstream_usage, dict):
                        if upstream_usage.get("prompt_tokens") is not None:
                            usage["input_tokens"] = int(upstream_usage["prompt_tokens"])
                        if upstream_usage.get("completion_tokens") is not None:
                            usage["output_tokens"] = int(upstream_usage["completion_tokens"])
                    for choice in data.get("choices") or []:
                        d = choice.get("delta") or {}
                        text = d.get("content")
                        if isinstance(text, str) and text:
                            yield delta(text)
                        if choice.get("finish_reason"):
                            stop_reason = _openai_stop(choice["finish_reason"])
        finally:
            await resp.aclose()

        yield self._sse("content_block_stop", {"type": "content_block_stop", "index": 0})
        yield self._sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason or "end_turn", "stop_sequence": None},
                "usage": usage,
            },
        )
        yield self._sse("message_stop", {"type": "message_stop"})
        logger.info("Messages stream finished in %.0fms", (time.perf_counter() - started) * 1000)
