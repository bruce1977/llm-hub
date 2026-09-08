"""
Reranker API implementation.
Exposes the standard /v1/rerank endpoint (Cohere / Infinity style).

Supports two scoring modes:
1. logprobs: For generative rerankers (e.g. Qwen3-Reranker).
   Builds a yes/no prompt, reads the logprobs of the answering token and turns the
   yes/no distribution into a relevance probability.
2. embedding: For embedding models (e.g. bge-m3).
   Embeds the query together with the documents and uses cosine similarity as score.
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
FALLBACK_UNKNOWN = 0.5

#: How many generated token positions are inspected when looking for the yes/no answer.
MAX_TOKEN_SCAN = 4


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


def _token_matches(token: str, target: str) -> bool:
    """Case/whitespace/punctuation tolerant comparison (e.g. ' Yes' vs 'yes')."""
    if not target:
        return False
    normalized = token.strip().strip("\"'`.,!?:;").lower()
    expected = target.strip().lower()
    return normalized == expected


def yes_no_probability(
    positions: list[dict[str, Any]],
    yes_token: str,
    no_token: str,
    max_scan: int = MAX_TOKEN_SCAN,
) -> float | None:
    """Binary softmax over the yes/no candidates of the answering token position.

    Returns ``None`` when no position carries a yes/no candidate (e.g. the model
    started with ``<think>``), so the caller can fall back to text parsing.
    """
    for pos in positions[:max_scan]:
        candidates = _position_candidates(pos)
        if not candidates:
            continue

        p_yes = 0.0
        p_no = 0.0
        for token, logprob in candidates:
            if _token_matches(token, yes_token):
                p_yes += math.exp(logprob)
            elif _token_matches(token, no_token):
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


def text_fallback_score(text: str, yes_token: str, no_token: str) -> float:
    """Last-resort scoring when the upstream returns no logprobs at all."""
    lowered = (text or "").strip().lower()
    if not lowered:
        return FALLBACK_UNKNOWN

    head = lowered[:32]
    yes = yes_token.strip().lower()
    no = no_token.strip().lower()

    if lowered.startswith(yes):
        return FALLBACK_YES
    if lowered.startswith(no):
        return FALLBACK_NO
    if yes in head and no not in head:
        return 0.75
    if no in head and yes not in head:
        return 0.25
    return FALLBACK_UNKNOWN


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


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


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

        instruction = req.instruction or rcfg.instruction

        if mode == "embedding":
            scores, failures, last_error = await self._score_by_embedding(
                resolved, req.query, documents, req.options
            )
        elif mode == "logprobs":
            scores, failures, last_error = await self._score_by_logprobs(
                resolved, req.query, documents, instruction, req.options
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported rerank mode '{mode}' for model '{model}'. Choose 'logprobs' or 'embedding'.",
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
            },
        }

    # ------------------------------------------------- logprobs scoring mode ---
    async def _score_by_logprobs(
        self,
        resolved,
        query: str,
        documents: list[str],
        instruction: str,
        extra_options: dict[str, Any] | None,
    ) -> tuple[list[float], int, str]:
        """Returns (scores, failed_count, last_error)."""
        cfg = self.manager.config
        rcfg = cfg.rerank
        upstream = resolved.upstream
        model_name = resolved.target or resolved.requested
        timeout = upstream.timeout or cfg.server.request_timeout
        is_ollama = upstream.type == "ollama"
        base = upstream.base_url_stripped()

        # "auto" -> Ollama gets a raw /api/generate call (the default template already is a
        # full chat prompt, and raw mode keeps the engine from injecting <think> tokens);
        # OpenAI-compatible backends only have a chat endpoint.
        style = (rcfg.upstream_api or "auto").strip().lower()
        use_generate = is_ollama and style != "chat"

        if use_generate:
            url = f"{base}/api/generate"
        elif is_ollama:
            url = f"{base}/api/chat"
        else:
            url = f"{base}/v1/chat/completions"

        top_logprobs = max(1, int(rcfg.top_logprobs or 1))
        semaphore = asyncio.Semaphore(max(1, min(rcfg.max_concurrency, len(documents) or 1)))
        failures = 0
        last_error = ""

        async def score_one(doc: str) -> float | None:
            nonlocal failures, last_error
            content = rcfg.template.format(instruction=instruction, query=query, document=doc)

            options: dict[str, Any] = {
                "temperature": rcfg.temperature,
                "num_predict": max(1, rcfg.max_tokens),
                "stop": ["\n"],
            }
            if extra_options:
                options.update(extra_options)

            if use_generate:
                payload: dict[str, Any] = {
                    "model": model_name,
                    "prompt": content,
                    "stream": False,
                    "raw": bool(rcfg.raw_prompt),
                    # Ollama only returns logprobs when they are requested top-level.
                    "logprobs": True,
                    "top_logprobs": top_logprobs,
                    "options": options,
                }
            elif is_ollama:
                payload = {
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
            else:  # OpenAI-compatible (LM Studio, vLLM, ...)
                payload = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": content}],
                    "stream": False,
                    "logprobs": True,
                    "top_logprobs": min(top_logprobs, 20),  # OpenAI caps at 20
                    "max_tokens": max(1, rcfg.max_tokens),
                    "temperature": rcfg.temperature,
                }
                if extra_options:
                    payload.update(extra_options)

            try:
                async with semaphore:
                    resp = await self.client.post(url, json=payload, timeout=timeout)
                    resp.raise_for_status()
                    data = resp.json()
            except Exception as exc:  # noqa: BLE001 - one bad document must not kill the batch
                failures += 1
                last_error = str(exc) or exc.__class__.__name__
                logger.error("Rerank call to %s failed: %s", url, last_error)
                return None

            if not isinstance(data, dict):
                failures += 1
                last_error = f"Unexpected upstream response type: {type(data).__name__}"
                return None

            score = yes_no_probability(extract_positions(data), rcfg.yes_token, rcfg.no_token)
            if score is not None:
                return min(1.0, max(0.0, score))

            # Fallback: no usable logprobs -> parse the generated text
            text = response_text(data)
            parsed = text_fallback_score(text, rcfg.yes_token, rcfg.no_token)
            logger.warning(
                "No yes/no logprobs in upstream response (model=%s); fell back to text %r -> %.2f",
                model_name,
                text[:40],
                parsed,
            )
            return parsed

        raw_scores = await asyncio.gather(*[score_one(doc) for doc in documents])
        scores = [0.0 if s is None else float(s) for s in raw_scores]
        return scores, failures, last_error

    # ----------------------------------------------- embedding scoring mode ---
    async def _score_by_embedding(
        self,
        resolved,
        query: str,
        documents: list[str],
        extra_options: dict[str, Any] | None,
    ) -> tuple[list[float], int, str]:
        """Returns (scores, failed_count, last_error)."""
        cfg = self.manager.config
        rcfg = cfg.rerank
        upstream = resolved.upstream
        model_name = resolved.target or resolved.requested
        timeout = upstream.timeout or cfg.server.request_timeout
        base = upstream.base_url_stripped()

        prefix = rcfg.embedding_query_prefix or ""
        query_text = f"{prefix}{query}" if prefix else query
        texts = [query_text, *documents]

        vectors = await self._embed(upstream, model_name, texts, timeout, extra_options)

        if len(vectors) != len(texts) or not vectors:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Embedding upstream '{upstream.name}' returned {len(vectors)} vectors for {len(texts)} inputs.",
            )

        query_vec = vectors[0]
        if not query_vec:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Embedding upstream '{upstream.name}' returned an empty query vector.",
            )

        scores = [cosine_similarity(query_vec, vec) for vec in vectors[1:]]
        if rcfg.embedding_clamp:
            # Cosine similarity lives in [-1, 1]; most rerank clients expect [0, 1].
            scores = [min(1.0, max(0.0, s)) for s in scores]
        return scores, 0, ""

    async def _embed(
        self,
        upstream,
        model_name: str,
        texts: list[str],
        timeout: float,
        extra_options: dict[str, Any] | None,
    ) -> list[list[float]]:
        """Embed a batch of texts; Ollama uses /api/embed, OpenAI-compatible /v1/embeddings."""
        cfg = self.manager.config
        rcfg = cfg.rerank
        base = upstream.base_url_stripped()
        headers = dict(upstream.headers or {})

        if upstream.type == "ollama":
            url = f"{base}/api/embed"
            payload: dict[str, Any] = {"model": model_name, "input": texts}
            if extra_options:
                payload.update(extra_options)
            resp = await self.client.post(url, json=payload, timeout=timeout, headers=headers)

            if resp.status_code == 404:
                # Older Ollama only has the single-text endpoint.
                logger.debug("/api/embed not available on %s, falling back to /api/embeddings", upstream.name)
                return await self._embed_ollama_legacy(upstream, model_name, texts, timeout, headers)

            resp.raise_for_status()
            data = resp.json()
            vectors = data.get("embeddings")
            if isinstance(vectors, list) and vectors:
                return [v for v in vectors if isinstance(v, list)]
            single = data.get("embedding")
            return [single] if isinstance(single, list) else []

        url = f"{base}/v1/embeddings"
        payload = {"model": model_name, "input": texts}
        if extra_options:
            payload.update(extra_options)
        resp = await self.client.post(url, json=payload, timeout=timeout, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data")
        if isinstance(items, list):
            return [it.get("embedding") for it in items if isinstance(it, dict) and isinstance(it.get("embedding"), list)]
        return []

    async def _embed_ollama_legacy(
        self,
        upstream,
        model_name: str,
        texts: list[str],
        timeout: float,
        headers: dict[str, str],
    ) -> list[list[float]]:
        url = f"{upstream.base_url_stripped()}/api/embeddings"
        semaphore = asyncio.Semaphore(max(1, min(self.manager.config.rerank.max_concurrency, len(texts) or 1)))

        async def one(text: str) -> list[float]:
            async with semaphore:
                resp = await self.client.post(
                    url, json={"model": model_name, "prompt": text}, timeout=timeout, headers=headers
                )
                resp.raise_for_status()
                vec = resp.json().get("embedding")
                return vec if isinstance(vec, list) else []

        return list(await asyncio.gather(*[one(t) for t in texts]))
