"""
In-gateway LLM inference benchmark (Python port of ``bench_llm.mjs``).

Reimplements the dual-protocol core of the Node script so the management console can
measure a model's decode / prefill / TTFT without shelling out to Node:

  * ``ollama``    -> POST ``{base}/api/chat``          (newline-delimited JSON stream)
  * ``lmstudio``  -> POST ``{base}/v1/chat/completions`` (OpenAI SSE)
  * ``opencode``  -> same OpenAI-compatible contract as ``lmstudio``

Metrics mirror the script's口径:
  * TTFT   = client wall clock from request start to first content token
  * decode = generated tokens / generation time, preferring the server's own
             ``eval_duration`` (Ollama) or ``predicted_ms`` (llama.cpp), falling back
             to client wall clock (total - TTFT)
  * prefill= prompt tokens / prefill time (server ``prompt_eval_duration`` / ``prompt_ms``,
             else TTFT)

``thinking`` models (Qwen3 / DeepSeek-R1 / GPT-OSS ...) auto-suppress the thinking chain
(``--think auto`` semantics) so reasoning tokens do not pollute ``completion_tokens``.
A light preflight resolves the model id against the upstream catalog (exact / normalized /
substring) to avoid bare 400s.

The run is round-based: ``warmup`` rounds are discarded, the rest feed the steady
statistics (mean / std-dev / min / max / CV%). ``run_bench`` is the async entry point; an
optional ``on_progress`` callback reports each completed round so a caller can drive a UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("llm_hub")

GiB = 1024**3

# A model family that emits a reasoning chain by default; when --think is auto we turn it
# off so completion_tokens stays clean. Same list as the Node script.
THINKING_MODEL_PATTERN = re.compile(
    r"qwen3|qwq|deepseek-r1|gpt-oss|magistral|glm-4\.[56]|seed-oss|\bthinking\b|\breasoning\b",
    re.IGNORECASE,
)

DEFAULT_PROMPT = "用三句话介绍量化（quantization）在大模型推理中的作用。"


class BenchError(Exception):
    """Raised for user-facing benchmark failures (model not found, HTTP error, timeout)."""


# --------------------------------------------------------------------------- #
# Options + small stats
# --------------------------------------------------------------------------- #
class BenchOptions:
    def __init__(
        self,
        rounds: int = 4,
        warmup: int = 1,
        max_tokens: int = 500,
        temperature: float = 0.0,
        think: str = "auto",
        timeout_seconds: int = 600,
        prompt: str = DEFAULT_PROMPT,
    ) -> None:
        if rounds < 1:
            raise BenchError("rounds 必须 >= 1")
        if warmup < 0 or warmup >= rounds:
            warmup = max(0, rounds - 1)
        self.rounds = rounds
        self.warmup = warmup
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.think = think
        self.timeout_seconds = timeout_seconds
        self.prompt = prompt


def compute_stats(values: list[float]) -> dict[str, float]:
    values = [v for v in values if v is not None and v > 0]
    count = len(values)
    if count == 0:
        return {"count": 0, "mean": 0.0, "std_dev": 0.0, "min": 0.0, "max": 0.0, "cv_percent": 0.0}
    mean = sum(values) / count
    std_dev = math.sqrt(sum((v - mean) ** 2 for v in values) / (count - 1)) if count > 1 else 0.0
    cv = (std_dev / mean) * 100 if mean > 0 else 0.0
    return {
        "count": count,
        "mean": mean,
        "std_dev": std_dev,
        "min": min(values),
        "max": max(values),
        "cv_percent": cv,
    }


# --------------------------------------------------------------------------- #
# Preflight + catalog
# --------------------------------------------------------------------------- #
async def _fetch_catalog(client: httpx.AsyncClient, base: str, server_type: str) -> list[str]:
    if server_type == "ollama":
        url = f"{base}/api/tags"
    else:
        url = f"{base}/v1/models"
    resp = await client.get(url, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    if server_type == "ollama":
        if not isinstance(payload.get("models"), list):
            raise BenchError(f"{base} 不是 Ollama（响应缺少 models 数组）。OpenAI 兼容后端请用 lm 协议。")
        return [m["name"] for m in payload["models"] if isinstance(m, dict) and m.get("name")]
    if not isinstance(payload.get("data"), list):
        raise BenchError(f"{base}/v1/models 返回异常。该端口可能不是 OpenAI 兼容服务。")
    return [str(m["id"]) for m in payload["data"] if isinstance(m, dict) and m.get("id")]


def _normalize(model_id: str) -> str:
    return re.sub(r"[^a-z0-9]", "", model_id.lower())


async def _resolve_model_id(
    client: httpx.AsyncClient, base: str, server_type: str, requested: str
) -> str:
    """Align the requested id to a real catalog id (exact / normalized / substring)."""
    try:
        catalog = await _fetch_catalog(client, base, server_type)
    except httpx.HTTPError as exc:  # list endpoint guarded by auth or wrong path
        logger.warning("bench preflight skipped (%s): %s", base, exc)
        return requested

    if not catalog:
        raise BenchError(f"模型目录为空（{base} 上没有任何已加载的模型），无法基准测试。")
    if requested in catalog:
        return requested
    norm = _normalize(requested)
    exact = next((c for c in catalog if _normalize(c) == norm), None)
    if exact:
        return exact
    candidates = [c for c in catalog if norm and _normalize(c).find(norm) >= 0]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        raise BenchError(
            f'模型名 "{requested}" 在 {base} 上无精确匹配，且子串命中多个候选，请明确指定: '
            + ", ".join(candidates[:10])
        )
    raise BenchError(
        f'模型名 "{requested}" 在 {base} 上找不到。可用: ' + ", ".join(catalog[:20])
    )


# --------------------------------------------------------------------------- #
# Single round
# --------------------------------------------------------------------------- #
def _resolve_think(model_name: str, think_mode: str) -> tuple[bool, bool]:
    detected = bool(THINKING_MODEL_PATTERN.search(model_name))
    suppressed = think_mode == "off" or (think_mode == "auto" and detected)
    forced = think_mode == "on"
    return suppressed, forced


async def _run_single_round(
    client: httpx.AsyncClient,
    base: str,
    server_type: str,
    model: str,
    opts: BenchOptions,
    think_suppressed: bool,
    think_forced: bool,
) -> dict[str, Any]:
    use_ollama = server_type == "ollama"
    url = f"{base}/api/chat" if use_ollama else f"{base}/v1/chat/completions"
    messages = [{"role": "user", "content": opts.prompt}]

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": opts.temperature,
        "stream": True,
    }
    if use_ollama:
        body["options"] = {"temperature": opts.temperature}
        if opts.max_tokens > 0:
            body["options"]["num_predict"] = opts.max_tokens
        if think_suppressed:
            body["think"] = False
        if think_forced:
            body["think"] = True
    else:
        if opts.max_tokens > 0:
            body["max_tokens"] = opts.max_tokens
        if opts.max_tokens > 0:
            body["stream_options"] = {"include_usage": True}
        if think_suppressed:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if think_forced:
            body["chat_template_kwargs"] = {"enable_thinking": True}

    start = time.perf_counter()
    try:
        resp = await client.post(url, json=body, timeout=opts.timeout_seconds)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:400] if exc.response else str(exc)
        raise BenchError(f"HTTP {exc.response.status_code}: {detail}") from exc
    except httpx.HTTPError as exc:
        raise BenchError(f"请求失败: {exc}") from exc

    first_token_s: Optional[float] = None
    content_text = ""
    reasoning_text = ""
    usage: Optional[dict[str, Any]] = None
    server_timings: Optional[dict[str, Any]] = None
    ollama_final: Optional[dict[str, Any]] = None
    finish_reason: Optional[str] = None

    # One loop handles both newline-JSON (Ollama) and SSE (OpenAI). The response is
    # already fully buffered by ``client.post``; iter_lines / aiter_lines work on it.
    # SSE frames are prefixed with "data:"; Ollama frames are bare JSON lines. After
    # stripping the optional prefix we parse every JSON frame the same way.
    async for raw_line in resp.aiter_lines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
            if not line or line == "[DONE]":
                continue
        if not line.startswith("{"):
            continue
        try:
            chunk = json.loads(line)
        except Exception:
            continue

        reasoning_text += _extract_reasoning(use_ollama, chunk)

        if use_ollama:
            delta = chunk.get("message", {}).get("content")
            if first_token_s is None and delta:
                first_token_s = (time.perf_counter() - start)
            if delta:
                content_text += delta
            if chunk.get("done"):
                ollama_final = chunk
                finish_reason = chunk.get("done_reason") or finish_reason
        else:
            delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
            if first_token_s is None and delta:
                first_token_s = (time.perf_counter() - start)
            if delta:
                content_text += delta
            if chunk.get("usage"):
                usage = chunk["usage"]
            if chunk.get("timings"):
                server_timings = chunk["timings"]
            fr = chunk.get("choices", [{}])[0].get("finish_reason")
            if fr:
                finish_reason = fr

    total_s = time.perf_counter() - start
    return _derive_metrics(
        use_ollama=use_ollama,
        total_s=total_s,
        first_token_s=first_token_s,
        content_text=content_text,
        reasoning_text=reasoning_text,
        usage=usage,
        server_timings=server_timings,
        ollama_final=ollama_final,
        finish_reason=finish_reason,
    )


def _extract_reasoning(use_ollama: bool, chunk: dict[str, Any]) -> str:
    if use_ollama:
        return chunk.get("message", {}).get("thinking") or ""
    delta = chunk.get("choices", [{}])[0].get("delta", {})
    return delta.get("reasoning_content") or delta.get("reasoning") or ""


def _derive_metrics(
    *,
    use_ollama: bool,
    total_s: float,
    first_token_s: Optional[float],
    content_text: str,
    reasoning_text: str,
    usage: Optional[dict[str, Any]],
    server_timings: Optional[dict[str, Any]],
    ollama_final: Optional[dict[str, Any]],
    finish_reason: Optional[str],
) -> dict[str, Any]:
    prompt_tokens = 0
    gen_tokens = 0
    server_gen_ms: Optional[float] = None
    server_prefill_ms: Optional[float] = None
    server_load_ms: Optional[float] = None

    if use_ollama and ollama_final:
        prompt_tokens = ollama_final.get("prompt_eval_count") or 0
        gen_tokens = ollama_final.get("eval_count") or 0
        if ollama_final.get("eval_duration"):
            server_gen_ms = ollama_final["eval_duration"] / 1e6
        if ollama_final.get("prompt_eval_duration"):
            server_prefill_ms = ollama_final["prompt_eval_duration"] / 1e6
        if ollama_final.get("load_duration"):
            server_load_ms = ollama_final["load_duration"] / 1e6
    elif not use_ollama:
        prompt_tokens = (usage or {}).get("prompt_tokens") or (server_timings or {}).get("prompt_n") or 0
        gen_tokens = (usage or {}).get("completion_tokens") or (server_timings or {}).get("predicted_n") or 0
        if (server_timings or {}).get("predicted_ms"):
            server_gen_ms = server_timings["predicted_ms"]
        if (server_timings or {}).get("prompt_ms"):
            server_prefill_ms = server_timings["prompt_ms"]

    decode_tok_s = 0.0
    prefill_tok_s = 0.0
    timing_source = "client"
    if gen_tokens > 0 and server_gen_ms:
        decode_tok_s = gen_tokens / (server_gen_ms / 1000)
        timing_source = "server"
    elif gen_tokens > 0 and first_token_s is not None and total_s > first_token_s:
        decode_tok_s = gen_tokens / (total_s - first_token_s)
    elif gen_tokens > 0 and total_s > 0:
        decode_tok_s = gen_tokens / total_s
        timing_source = "client*"

    if prompt_tokens > 0 and server_prefill_ms:
        prefill_tok_s = prompt_tokens / (server_prefill_ms / 1000)
    elif prompt_tokens > 0 and first_token_s:
        prefill_tok_s = prompt_tokens / first_token_s

    embedded = re.search(r"<think>[\s\S]*?(?:<\/think>|$)", content_text, re.IGNORECASE)
    reasoning_chars = len(reasoning_text) + (len(embedded.group(0)) if embedded else 0)
    thinking_leaked = bool(reasoning_chars) and timing_source == "client*"  # heuristic

    return {
        "total_elapsed_s": round(total_s, 3),
        "ttft_s": round(first_token_s, 3) if first_token_s is not None else None,
        "prompt_tokens": prompt_tokens,
        "gen_tokens": gen_tokens,
        "server_gen_ms": round(server_gen_ms, 1) if server_gen_ms is not None else None,
        "server_prefill_ms": round(server_prefill_ms, 1) if server_prefill_ms is not None else None,
        "server_load_ms": round(server_load_ms, 1) if server_load_ms is not None else None,
        "decode_tok_s": round(decode_tok_s, 2),
        "prefill_tok_s": round(prefill_tok_s, 1),
        "timing_source": timing_source,
        "finish_reason": finish_reason,
        "reasoning_chars": reasoning_chars,
        "content_len": len(content_text),
        "thinking_leaked": thinking_leaked,
    }


# --------------------------------------------------------------------------- #
# Full run
# --------------------------------------------------------------------------- #
async def run_bench(
    *,
    base_url: str,
    server_type: str,
    model: str,
    options: Optional[BenchOptions] = None,
    headers: Optional[dict[str, str]] = None,
    on_progress: Optional[Callable[[int, int, bool], Awaitable[None]]] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    """Run the benchmark for a single model and return a structured result.

    ``server_type`` is "ollama" or "lm" (OpenAI-compatible: lmstudio / opencode).
    ``on_progress(completed, total, is_warmup)`` is awaited after each round.
    ``client`` is an optional caller-provided httpx client (used for testing); when omitted
    a dedicated client with per-round timeouts is created and closed here.
    """
    opts = options or BenchOptions()
    base = base_url.rstrip("/")
    server = "ollama" if server_type == "ollama" else "lm"
    parsed = urlparse(base)
    host = parsed.hostname or "localhost"
    port = parsed.port or (11434 if server == "ollama" else 11433)

    think_suppressed, think_forced = _resolve_think(model, opts.think)

    own_client = client is None
    if own_client:
        # trust_env=False: the gateway benchmarks explicitly-configured internal upstreams, so
        # do not inherit the host's ambient HTTP(S)_PROXY. On hosts that set HTTP_PROXY to a
        # local proxy (e.g. 127.0.0.1:60630) a default client would route localhost / LAN
        # upstream requests through that proxy and fail with 502.
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(opts.timeout_seconds + 5, connect=10.0),
            headers=headers or {},
            limits=httpx.Limits(max_connections=4),
            trust_env=False,
        )
    try:
        # Preflight: align the model id to a real catalog entry.
        aligned = await _resolve_model_id(client, base, server, model)
        if aligned != model:
            logger.info("bench model aligned: %s -> %s", model, aligned)
            model = aligned

        rounds: list[dict[str, Any]] = []
        for index in range(1, opts.rounds + 1):
            is_warmup = index <= opts.warmup
            result = await _run_single_round(
                client, base, server, model, opts, think_suppressed, think_forced
            )
            result["round"] = index
            result["warmup"] = is_warmup
            rounds.append(result)
            if on_progress is not None:
                progress = on_progress(index, opts.rounds, is_warmup)
                if asyncio.iscoroutine(progress):
                    await progress

        steady = [r for r in rounds if not r["warmup"]]
        decode_stats = compute_stats([r["decode_tok_s"] for r in steady])
        prefill_stats = compute_stats([r["prefill_tok_s"] for r in steady])
        ttft_stats = compute_stats([(r["ttft_s"] * 1000) for r in steady if r["ttft_s"] is not None])
    finally:
        if own_client:
            await client.aclose()

    sources = {r["timing_source"] for r in steady if r.get("timing_source")}
    aggregate_source = "server" if "server" in sources else (sorted(sources)[0] if sources else "client")

    return {
        "config": {
            "server": server,
            "base_url": base,
            "host": host,
            "port": port,
            "model": model,
            "rounds": opts.rounds,
            "warmup": opts.warmup,
            "max_tokens": opts.max_tokens,
            "temperature": opts.temperature,
            "think": opts.think,
            "think_suppressed": think_suppressed,
            "prompt": opts.prompt,
        },
        "rounds": rounds,
        "timing_source": aggregate_source,
        "steady": {
            "decode": decode_stats,
            "prefill": prefill_stats,
            "ttft": {**ttft_stats, "mean_ms": ttft_stats["mean"]},
        },
    }
