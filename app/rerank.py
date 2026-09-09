"""
Reranker API implementation.
Exposes the standard /v1/rerank endpoint (Cohere / Infinity style).

Scoring mode: logprobs (generative rerankers such as Qwen3-Reranker).
A yes/no prompt is sent to the model, the logprobs of the answering token are read back
and the yes/no distribution is turned into a relevance probability.

Note: a former "embedding" mode (cosine similarity over bge-m3 vectors) was removed -
re-ranking with the very model that produced the retrieval embeddings adds no signal.

Logprobs robustness
-------------------
Ollama only returns token log probabilities when the caller explicitly asks for them
(``"logprobs": true`` plus ``"top_logprobs": n`` at the TOP level of the request body -
they are *not* Ollama ``options``). Older Ollama releases simply ignore the fields, and
some OpenAI-compatible backends only implement them on the chat endpoint. When the
response carries no logprobs at all, the scorer therefore walks a fallback chain of
endpoint styles (``/api/generate`` -> ``/api/chat`` -> ``/v1/chat/completions``) and only
then degrades to parsing the generated text. Which path was taken is reported back in
``meta`` so a broken deployment is visible instead of silently producing flat scores.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from typing import Any

import httpx
from fastapi import HTTPException, status
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger("llm_hub")

#: Score used when no logprobs are available and the model plainly answered "yes"/"no".
FALLBACK_YES = 0.9
FALLBACK_NO = 0.1
#: Score used when neither logprobs nor a yes/no text answer are available.
#:
#: 0.0 (instead of a neutral 0.5) is deliberate: an unparsable answer means "we could not
#: establish relevance", and treating it as "half relevant" pushes unrelated documents
#: through downstream score thresholds.
FALLBACK_UNKNOWN = 0.0

#: How many generated token positions are inspected when looking for the yes/no answer.
MAX_TOKEN_SCAN = 4

#: Ollama and OpenAI both cap the number of returned alternatives at 20.
MAX_TOP_LOGPROBS = 20

#: Endpoint styles used for generative (logprobs) scoring.
STYLE_GENERATE = "generate"  # Ollama /api/generate (raw prompt, no chat template)
STYLE_CHAT = "chat"  # Ollama /api/chat
STYLE_OPENAI = "openai"  # /v1/chat/completions (LM Studio, vLLM, ...)

#: Endpoints already reported as "no logprobs" (kept small; used to log the hint once).
_no_logprobs_warned: set[str] = set()


def _warn_no_logprobs(key: str, detail: str) -> None:
    """Log the 'upstream returned no logprobs' hint once per endpoint/model."""
    if key in _no_logprobs_warned:
        logger.debug("Upstream %s still returns no logprobs: %s", key, detail)
        return
    _no_logprobs_warned.add(key)
    logger.warning(
        "Rerank: upstream %s did not return any logprobs (%s). "
        "Scores fall back to parsing the generated text, which yields coarse "
        "0.9/0.1 values. Check that (a) the request reaches an Ollama build that "
        "implements 'logprobs' (older releases ignore the field), and (b) the model "
        "is a generative reranker such as qwen3-reranker.",
        key,
        detail,
    )


# --------------------------------------------------------------------------- #
# Request / Response Models
# --------------------------------------------------------------------------- #
def _doc_text(doc: Any) -> str:
    """
    Robustly extracts text from a document object.
    Handles strings, dicts with various keys, and fallbacks.
    """
    if isinstance(doc, str):
        return doc

    if isinstance(doc, dict):
        # Standard keys to look for
        for key in ("text", "content", "document", "doc", "passage", "snippet"):
            value = doc.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        # Fallback: return the first string value found in the dict
        for value in doc.values():
            if isinstance(value, str) and value.strip():
                return value.strip()

        # Last resort: string representation of the dict
        return str(doc)

    return str(doc)


class RerankRequest(BaseModel):
    """Tolerant request model.

    Understands the Cohere/Infinity shape (``model`` / ``query`` / ``documents``) and the
    variants seen in the wild:

    * model:      ``model`` | ``model_name`` | ``model_id``
    * query:      ``query`` | ``input`` (string or single-element list)
    * documents:  ``documents`` | ``texts`` | ``passages``
    """

    model: str = ""
    query: str = ""
    documents: list[Any] = Field(default_factory=list)
    top_n: int | None = None
    return_documents: bool | None = None
    instruction: str | None = None
    options: dict[str, Any] | None = None

    model_config = {"extra": "allow", "populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def _coerce_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        payload = dict(data)

        # --- model -------------------------------------------------------
        if not str(payload.get("model") or "").strip():
            for key in ("model_name", "model_id", "modelId"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    payload["model"] = value.strip()
                    break

        # --- query -------------------------------------------------------
        if not str(payload.get("query") or "").strip():
            raw = payload.get("input")
            if isinstance(raw, str):
                payload["query"] = raw
            elif isinstance(raw, list) and raw:
                payload["query"] = _doc_text(raw[0])
            else:
                for key in ("q", "text"):
                    value = payload.get(key)
                    if isinstance(value, str) and value.strip():
                        payload["query"] = value
                        break

        # --- documents ---------------------------------------------------
        docs = payload.get("documents")
        if isinstance(docs, str):
            payload["documents"] = [docs]
        elif not docs:
            for key in ("texts", "passages", "docs"):
                value = payload.get(key)
                if isinstance(value, list) and value:
                    payload["documents"] = value
                    break

        # --- top_n (some clients send it as a string) --------------------
        top_n = payload.get("top_n")
        if isinstance(top_n, str) and top_n.strip().lstrip("-").isdigit():
            payload["top_n"] = int(top_n.strip())

        return payload


# --------------------------------------------------------------------------- #
# Logprobs Parsing Helpers
# --------------------------------------------------------------------------- #
def flatten_logprobs(data: Any) -> dict[str, float]:
    """
    Flattens the logprobs structure of an upstream into {token_lower: logprob}.
    Kept for backwards compatibility / debugging; scoring uses the ordered
    helpers below so that only the answering token position is considered.
    """
    flat: dict[str, float] = {}

    def walk(node: Any):
        if isinstance(node, dict):
            token = node.get("token")
            logprob = node.get("logprob")
            if isinstance(token, str) and isinstance(logprob, (int, float)):
                key = token.strip().lower()
                if key not in flat or logprob > flat[key]:
                    flat[key] = float(logprob)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return flat


def _as_positions(node: Any) -> list[dict[str, Any]]:
    """Normalise a logprobs payload into an ordered list of token positions.

    Understands Ollama (``logprobs`` is a list, or an object with ``content``)
    and OpenAI-compatible (``logprobs.content``) shapes.
    """
    if isinstance(node, dict):
        content = node.get("content")
        if isinstance(content, list):
            return [p for p in content if isinstance(p, dict)]
        if "token" in node or "top_logprobs" in node:
            return [node]
        return []
    if isinstance(node, list):
        return [p for p in node if isinstance(p, dict)]
    return []


def extract_positions(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the ordered token positions of a chat/generate response."""
    if not isinstance(data, dict):
        return []

    positions = _as_positions(data.get("logprobs"))
    if positions:
        return positions

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            return _as_positions(first.get("logprobs"))
    return []


def has_logprobs(data: dict[str, Any]) -> bool:
    """True when the upstream actually returned token log probabilities."""
    return bool(extract_positions(data))


def top_tokens(data: dict[str, Any], limit: int = 5) -> list[str]:
    """Top-1 token of the first generated positions.

    Purely diagnostic: when a reranker answers with 'ਐ' / '경' / '_binding' instead of
    yes/no, the model is not in an answering state (wrong prompt, truncated context,
    quantisation trouble) - printing what it really emitted makes that obvious.
    """
    tokens: list[str] = []
    for pos in extract_positions(data)[:limit]:
        candidates = _position_candidates(pos)
        if candidates:
            tokens.append(candidates[0][0])
    return tokens


def _position_candidates(pos: dict[str, Any]) -> list[tuple[str, float]]:
    """All (token, logprob) candidates of one position: the token itself + top_logprobs."""
    candidates: list[tuple[str, float]] = []

    token, logprob = pos.get("token"), pos.get("logprob")
    if isinstance(token, str) and isinstance(logprob, (int, float)):
        candidates.append((token, float(logprob)))

    for alt in pos.get("top_logprobs") or []:
        if not isinstance(alt, dict):
            continue
        alt_token, alt_logprob = alt.get("token"), alt.get("logprob")
        if isinstance(alt_token, str) and isinstance(alt_logprob, (int, float)):
            candidates.append((alt_token, float(alt_logprob)))

    return candidates


def _token_matches(token: str, targets: tuple[str, ...]) -> bool:
    """Case/whitespace/punctuation tolerant comparison (e.g. ' Yes' vs 'yes')."""
    if not targets:
        return False
    normalized = token.strip().strip("\"'`.,!?:;()[]").lower()
    if not normalized:
        return False
    return normalized in targets


def _targets(primary: str, aliases: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Build the normalized set of accepted spellings for the yes (or no) answer."""
    values = [primary, *(aliases or [])]
    return tuple(v.strip().lower() for v in values if isinstance(v, str) and v.strip())


def yes_no_probability(
    positions: list[dict[str, Any]],
    yes_token: str,
    no_token: str,
    yes_aliases: list[str] | None = None,
    no_aliases: list[str] | None = None,
    max_scan: int = MAX_TOKEN_SCAN,
) -> float | None:
    """Binary softmax over the yes/no candidates of the answering token position.

    Returns ``None`` when no position carries a yes/no candidate (e.g. the model
    started with ``<think>``), so the caller can fall back to text parsing.
    """
    yes_targets = _targets(yes_token, yes_aliases)
    no_targets = _targets(no_token, no_aliases)

    for pos in positions[:max_scan]:
        candidates = _position_candidates(pos)
        if not candidates:
            continue

        p_yes = 0.0
        p_no = 0.0
        for token, logprob in candidates:
            if _token_matches(token, yes_targets):
                p_yes += math.exp(logprob)
            elif _token_matches(token, no_targets):
                p_no += math.exp(logprob)

        total = p_yes + p_no
        if total > 0:
            return p_yes / total
    return None


def response_text(data: dict[str, Any]) -> str:
    """Assistant text of an Ollama or OpenAI-compatible response."""
    if not isinstance(data, dict):
        return ""

    message = data.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                return msg["content"]

    if isinstance(data.get("response"), str):  # Ollama /api/generate
        return data["response"]
    return ""


def text_fallback_score(
    text: str,
    yes_token: str,
    no_token: str,
    yes_score: float = FALLBACK_YES,
    no_score: float = FALLBACK_NO,
    unknown_score: float = FALLBACK_UNKNOWN,
    yes_aliases: list[str] | None = None,
    no_aliases: list[str] | None = None,
) -> float:
    """Last-resort scoring when the upstream returns no usable logprobs at all."""
    lowered = (text or "").strip().lower()
    if not lowered:
        return unknown_score

    head = lowered[:32]
    yes_targets = _targets(yes_token, yes_aliases)
    no_targets = _targets(no_token, no_aliases)

    for target in yes_targets:
        if lowered.startswith(target):
            return yes_score
    for target in no_targets:
        if lowered.startswith(target):
            return no_score

    has_yes = any(t in head for t in yes_targets)
    has_no = any(t in head for t in no_targets)
    if has_yes and not has_no:
        return (yes_score + unknown_score) / 2
    if has_no and not has_yes:
        return (no_score + unknown_score) / 2
    return unknown_score


def softmax_pair(yes_logprob: float, no_logprob: float) -> float:
    """Binary softmax over yes/no logprobs; returns probability of 'yes'."""
    max_lp = max(yes_logprob, no_logprob)
    yes_p = math.exp(yes_logprob - max_lp)
    no_p = math.exp(no_logprob - max_lp)
    total = yes_p + no_p
    return yes_p / total if total > 0 else 0.5


def normalize_scores(scores: list[float]) -> list[float]:
    """Min-max normalize scores to [0, 1]."""
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if math.isclose(hi, lo):
        # If all scores are identical, return 0.5 (neutral) unless they are 0
        return [0.5 if not math.isclose(lo, 0.0) else 0.0 for _ in scores]
    return [(s - lo) / (hi - lo) for s in scores]


# --------------------------------------------------------------------------- #
# Rerank Service
# --------------------------------------------------------------------------- #
class RerankService:
    def __init__(self, manager, client: httpx.AsyncClient):
        self.manager = manager
        self.client = client

    # -------------------------------------------------------------- entrypoint ---
    async def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        cfg = self.manager.config
        rcfg = cfg.rerank

        if not rcfg.enabled:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rerank API is disabled.")

        if not isinstance(payload, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request body must be a JSON object.")

        try:
            req = RerankRequest(**payload)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid rerank request: {exc}",
            ) from exc

        if not req.query.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'query' (or 'input') must not be empty.",
            )
        if not req.documents:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'documents' must not be empty.",
            )

        model = (req.model or "").strip()
        if not model:
            # Fall back to the first model declared in rerank.models
            model = next((m.strip() for m in rcfg.models if m and m.strip()), "")
        if not model:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A rerank model is required ('model' / 'model_name') or set rerank.models in the config.",
            )

        resolved = cfg.resolve(model)

        if resolved.upstream is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Model '{model}' is not served by any configured upstream.",
            )

        if rcfg.max_documents and len(req.documents) > rcfg.max_documents:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Too many documents: {len(req.documents)} > rerank.max_documents={rcfg.max_documents}.",
            )

        mode = (rcfg.mode_for(model, resolved.target) or "").strip().lower()
        documents: list[str] = []
        for doc in req.documents:
            text = _doc_text(doc)
            if rcfg.max_document_chars and len(text) > rcfg.max_document_chars:
                text = text[: rcfg.max_document_chars]
            documents.append(text)

        instruction = (req.instruction or "").strip() or rcfg.instruction

        if mode in ("logprobs", "logprob", "generative", "yes_no"):
            scores, failures, last_error, stats = await self._score_by_logprobs(
                resolved, req.query, documents, instruction, req.options
            )
        elif mode == "embedding":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Rerank 'embedding' mode has been removed: re-scoring with the same model that "
                    "produced the retrieval embeddings adds no signal. Use a generative reranker "
                    "(e.g. qwen3-reranker:4b) with mode 'logprobs', or drop the rerank step."
                ),
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported rerank mode '{mode}' for model '{model}'. Only 'logprobs' is supported.",
            )

        # Nothing could be scored at all -> surface it instead of returning silent zeros.
        if failures and failures >= len(documents):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Rerank failed for all {len(documents)} document(s): {last_error}",
            )
        if failures and rcfg.on_error == "fail":
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Rerank failed for {failures}/{len(documents)} document(s): {last_error}",
            )

        # Normalize scores if configured (off by default: logprobs already are probabilities
        # and min-max normalization would destroy cross-request comparability).
        if rcfg.normalize:
            scores = normalize_scores(scores)

        # Assemble results and sort (ties keep the original document order).
        indexed: list[tuple[int, float]] = list(enumerate(scores))
        indexed.sort(key=lambda item: (-item[1], item[0]))

        top_n = req.top_n
        if top_n is not None and top_n >= 0:
            indexed = indexed[:top_n]

        return_documents = rcfg.return_documents if req.return_documents is None else req.return_documents

        results = []
        for rank, (idx, score) in enumerate(indexed):
            item: dict[str, Any] = {
                "index": idx,
                "rank": rank,
                "relevance_score": round(float(score), 8),
            }
            if return_documents:
                item["document"] = {"text": documents[idx]}
            results.append(item)

        return {
            "id": f"rerank-{uuid.uuid4().hex[:24]}",
            "object": "list",
            "model": resolved.requested or resolved.target,
            "results": results,
            "usage": {
                "rerank_count": len(documents),
                "returned_count": len(results),
                "failed_count": failures,
            },
            "meta": {
                "mode": mode,
                "upstream": resolved.upstream.name,
                "model": resolved.target,
                "total_documents": len(documents),
                "returned_documents": len(results),
                "failed_documents": failures,
                "normalized": bool(rcfg.normalize),
                "took_ms": round((time.perf_counter() - started) * 1000, 2),
                # --- scoring quality telemetry -------------------------------
                # logprobs_ok=false means the model was NOT scored from real token
                # probabilities, i.e. the ranking is only as good as the 0.9/0.1 text
                # fallback. Watch this field when tuning a deployment.
                "logprobs_ok": bool(stats.get("logprobs_ok")),
                "logprobs_scored": int(stats.get("logprobs_scored", 0)),
                "text_fallbacks": int(stats.get("text_fallbacks", 0)),
                "endpoint": str(stats.get("endpoint") or ""),
            },
        }

    # ------------------------------------------------- logprobs scoring mode ---
    def _endpoint_plan(self, upstream, style: str) -> list[str]:
        """Ordered list of endpoint styles to try for generative scoring.

        ``auto`` starts with the raw ``/api/generate`` call on Ollama (the Qwen3-Reranker
        template already is a complete chat prompt, and raw mode keeps the engine from
        injecting ``<think>`` tokens). When the response carries no logprobs the caller
        moves on to the next style, which is what makes old Ollama builds (that silently
        ignore the ``logprobs`` field) degrade gracefully instead of returning flat scores.
        """
        style = (style or "auto").strip().lower()
        is_ollama = upstream.type == "ollama"

        if style in ("generate", "gen", "raw"):
            return [STYLE_GENERATE] if is_ollama else [STYLE_OPENAI]
        if style in ("chat", "chat_completions"):
            return [STYLE_CHAT] if is_ollama else [STYLE_OPENAI]
        if style in ("openai", "v1"):
            return [STYLE_OPENAI]
        if is_ollama:
            plan = [STYLE_GENERATE]
            if self.manager.config.rerank.logprobs_fallback:
                plan += [STYLE_CHAT, STYLE_OPENAI]
            return plan
        return [STYLE_OPENAI]

    def _build_payload(
        self,
        kind: str,
        upstream,
        model_name: str,
        content: str,
        rcfg,
        top_logprobs: int,
        extra_options: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any]]:
        """Returns (url, request body) for one endpoint style."""
        base = upstream.base_url_stripped()
        options: dict[str, Any] = {
            "temperature": rcfg.temperature,
            "num_predict": max(1, rcfg.max_tokens),
        }
        if rcfg.options:
            options.update(rcfg.options)
        if rcfg.stop:
            options["stop"] = list(rcfg.stop)
        if extra_options:
            options.update(extra_options)

        if kind == STYLE_GENERATE:
            # NOTE: 'logprobs' / 'top_logprobs' are TOP-LEVEL fields for Ollama.
            # Putting them into 'options' makes Ollama drop them silently.
            return f"{base}/api/generate", {
                "model": model_name,
                "prompt": content,
                "stream": False,
                "raw": bool(rcfg.raw_prompt),
                "logprobs": True,
                "top_logprobs": top_logprobs,
                "options": options,
            }

        if kind == STYLE_CHAT:
            payload: dict[str, Any] = {
                "model": model_name,
                "messages": [{"role": "user", "content": content}],
                "stream": False,
                "logprobs": True,
                "top_logprobs": top_logprobs,
                "options": options,
            }
            if rcfg.disable_thinking:
                # Thinking models would otherwise spend the (very small) token budget
                # on <think>... instead of answering yes/no.
                payload["think"] = False
            return f"{base}/api/chat", payload

        # OpenAI-compatible (LM Studio, vLLM, ...)
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": content}],
            "stream": False,
            "logprobs": True,
            "top_logprobs": min(top_logprobs, MAX_TOP_LOGPROBS),  # OpenAI caps at 20
            "max_tokens": max(1, rcfg.max_tokens),
            "temperature": rcfg.temperature,
        }
        if rcfg.stop:
            payload["stop"] = list(rcfg.stop)
        if extra_options:
            payload.update(extra_options)
        return f"{base}/v1/chat/completions", payload

    async def _score_by_logprobs(
        self,
        resolved,
        query: str,
        documents: list[str],
        instruction: str,
        extra_options: dict[str, Any] | None,
    ) -> tuple[list[float], int, str, dict[str, Any]]:
        """Returns (scores, failed_count, last_error, stats)."""
        cfg = self.manager.config
        rcfg = cfg.rerank
        upstream = resolved.upstream
        model_name = resolved.target or resolved.requested
        timeout = upstream.timeout or cfg.server.request_timeout

        plan = self._endpoint_plan(upstream, rcfg.upstream_api)
        top_logprobs = max(1, min(int(rcfg.top_logprobs or 1), MAX_TOP_LOGPROBS))
        max_scan = max(1, int(rcfg.token_scan_depth or MAX_TOKEN_SCAN))
        semaphore = asyncio.Semaphore(max(1, min(rcfg.max_concurrency, len(documents) or 1)))

        failures = 0
        last_error = ""
        # Mutable state shared by the per-document tasks. asyncio runs them on one thread,
        # so plain reads/writes are safe here (no await between read and write).
        state: dict[str, Any] = {
            "kind": plan[0],
            "logprobs_scored": 0,
            "text_fallbacks": 0,
        }

        def ordered_plan() -> list[str]:
            active = state["kind"]
            if active in plan:
                return [active, *(k for k in plan if k != active)]
            return list(plan)

        async def call(kind: str, content: str) -> dict[str, Any] | None:
            nonlocal last_error
            url, payload = self._build_payload(kind, upstream, model_name, content, rcfg, top_logprobs, extra_options)
            try:
                async with semaphore:
                    resp = await self.client.post(url, json=payload, timeout=timeout)
                    resp.raise_for_status()
                    data = resp.json()
            except Exception as exc:  # noqa: BLE001 - one bad document must not kill the batch
                last_error = str(exc) or exc.__class__.__name__
                logger.error("Rerank call to %s (%s) failed: %s", url, kind, last_error)
                return None
            if not isinstance(data, dict):
                last_error = f"Unexpected upstream response type: {type(data).__name__}"
                return None
            return data

        async def score_one(doc: str) -> float | None:
            nonlocal failures
            content = rcfg.template.format(instruction=instruction, query=query, document=doc)
            fallback_data: dict[str, Any] | None = None
            unusable_data: dict[str, Any] | None = None

            for kind in ordered_plan():
                data = await call(kind, content)
                if data is None:
                    continue  # transport error -> try the next endpoint style
                fallback_data = fallback_data or data

                if not has_logprobs(data):
                    # The upstream understood us but gave no probabilities. Remember the
                    # response (it may still contain a usable yes/no text) and try the
                    # next endpoint style before giving up.
                    logger.debug(
                        "Rerank: no logprobs from %s/%s (model=%s), trying next endpoint style",
                        upstream.name,
                        kind,
                        model_name,
                    )
                    continue

                score = yes_no_probability(
                    extract_positions(data),
                    rcfg.yes_token,
                    rcfg.no_token,
                    rcfg.yes_aliases,
                    rcfg.no_aliases,
                    max_scan,
                )
                if score is not None:
                    state["kind"] = kind
                    state["logprobs_scored"] += 1
                    return min(1.0, max(0.0, score))

                # Logprobs ARE there, but the model emitted something else entirely
                # ('ਐ', '경', '_binding', ...). That means the prompt did not put the model
                # into an answering state - a different endpoint style (which lets the
                # backend apply the model's own chat template) often fixes it, so retry
                # before trusting the text.
                logger.warning(
                 "Rerank: no yes/no candidate from %s/%s (model=%s); top tokens=%s. "
                 "This usually means the prompt is malformed for this model (raw prompt "
                 "vs. chat template), the context was truncated (set rerank.options.num_ctx) "
                 "or the model is not a generative reranker. Trying the next endpoint style.",
                    upstream.name,
                    kind,
                    model_name,
                    top_tokens(data),
                )
                unusable_data = unusable_data or data
                continue

            # No endpoint returned usable logprobs -> last resort: parse the text.
            last_data = unusable_data or fallback_data
            if last_data is None:
                failures += 1
                last_error = last_error or "all rerank endpoint styles failed"
                return None

            state["text_fallbacks"] += 1
            text = response_text(last_data)
            parsed = text_fallback_score(
                text,
                rcfg.yes_token,
                rcfg.no_token,
                rcfg.fallback_yes,
                rcfg.fallback_no,
                rcfg.fallback_unknown,
                rcfg.yes_aliases,
                rcfg.no_aliases,
            )
            if unusable_data is None:
                _warn_no_logprobs(
                    f"{upstream.name}:{model_name}",
                    f"styles tried: {', '.join(ordered_plan())}; text={text[:40]!r}",
                )
            logger.warning(
                "No yes/no logprobs in upstream response (model=%s, upstream=%s, kinds=%s); "
                "fell back to text %r -> %.2f",
                model_name,
                upstream.name,
                ", ".join(ordered_plan()),
                text[:40],
                parsed,
            )
            return parsed

        raw_scores = await asyncio.gather(*[score_one(doc) for doc in documents])
        scores = [0.0 if s is None else float(s) for s in raw_scores]

        stats = {
            "logprobs_ok": state["logprobs_scored"] > 0,
            "logprobs_scored": state["logprobs_scored"],
            "text_fallbacks": state["text_fallbacks"],
            "endpoint": state["kind"],
        }
        return scores, failures, last_error, stats

