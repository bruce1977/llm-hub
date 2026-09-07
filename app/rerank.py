"""
Reranker API implementation (not provided by Ollama natively).

Exposes the standard /v1/rerank endpoint (Cohere / Infinity style) with two scoring modes:

1. logprobs  - generative reranker models (e.g. Qwen3-Reranker):
   build a yes/no binary prompt, read the logprobs of the first generated token,
   and softmax yes/no into the relevance score.

2. embedding - embedding models (e.g. bge-m3):
   embed the query and each document, then use cosine similarity as the score.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from typing import Any

import httpx
from fastapi import HTTPException, status
from pydantic import BaseModel, Field

logger = logging.getLogger("llm_hub")


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #
class RerankRequest(BaseModel):
    model: str
    query: str
    documents: list[Any] = Field(default_factory=list)
    top_n: int | None = None
    return_documents: bool | None = None
    instruction: str | None = None
    options: dict[str, Any] | None = None

    model_config = {"extra": "allow"}


def _doc_text(doc: Any) -> str:
    """Accepts documents as a list of strings or a list of {text: ...} objects."""
    if isinstance(doc, str):
        return doc
    if isinstance(doc, dict):
        for key in ("text", "content", "document"):
            if isinstance(doc.get(key), str):
                return doc[key]
        return str(doc)
    return str(doc)


# --------------------------------------------------------------------------- #
# logprobs parsing helpers
# --------------------------------------------------------------------------- #
def flatten_logprobs(data: Any) -> dict[str, float]:
    """Flatten the logprobs structure of any Ollama version into {token_lower: logprob}.

    Handles nested structures such as [{"token": "yes", "logprob": -0.2, "top_logprobs": [...]}],
    when a token appears more than once, keep the highest logprob.
    """
    flat: dict[str, float] = {}

    def walk(node: Any) -> None:
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


def softmax_pair(yes_logprob: float, no_logprob: float) -> float:
    """Binary softmax over the yes/no logprobs; returns the probability of yes."""
    # subtract the max value to avoid exp overflow
    max_lp = max(yes_logprob, no_logprob)
    yes_p = math.exp(yes_logprob - max_lp)
    no_p = math.exp(no_logprob - max_lp)
    total = yes_p + no_p
    return yes_p / total if total > 0 else 0.5


def normalize_scores(scores: list[float]) -> list[float]:
    """Min-max normalize to [0, 1]. If all values are equal, returns 0.5 (or the original value when it is 0)."""
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if math.isclose(hi, lo):
        return [0.5 if not math.isclose(lo, 0.0) else 0.0 for _ in scores]
    return [(s - lo) / (hi - lo) for s in scores]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# --------------------------------------------------------------------------- #
# Rerank service
# --------------------------------------------------------------------------- #
class RerankService:
    def __init__(self, manager, client: httpx.AsyncClient):
        self.manager = manager
        self.client = client

    # -------------------------------------------------------------- entrypoint ---
    async def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        cfg = self.manager.config
        rcfg = cfg.rerank

        if not rcfg.enabled:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rerank API is disabled.")

        try:
            req = RerankRequest(**payload)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid rerank request: {exc}",
            ) from exc

        if not req.query.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="'query' must not be empty.")
        if not req.documents:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="'documents' must not be empty.")

        model = req.model
        resolved = cfg.resolve(model)
        if resolved.upstream is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Model '{model}' is not served by any configured upstream.",
            )

        mode = rcfg.mode_for(model).lower()
        documents = [_doc_text(d) for d in req.documents]
        instruction = req.instruction or rcfg.instruction

        if mode == "embedding":
            scores = await self._score_by_embedding(resolved, req.query, documents, req.options)
        elif mode in ("logprobs", "generation", "cross-encoder"):
            scores = await self._score_by_logprobs(resolved, req.query, documents, instruction, req.options)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported rerank mode '{mode}' for model '{model}'. Choose 'logprobs' or 'embedding'.",
            )

        # normalize
        if rcfg.normalize:
            scores = normalize_scores(scores)

        # assemble results and sort by descending score
        indexed: list[tuple[int, float]] = list(enumerate(scores))
        indexed.sort(key=lambda item: item[1], reverse=True)

        top_n = req.top_n
        if top_n is not None and top_n >= 0:
            indexed = indexed[:top_n]

        return_documents = rcfg.return_documents if req.return_documents is None else req.return_documents

        results = []
        for rank, (idx, score) in enumerate(indexed):
            item: dict[str, Any] = {
                "index": idx,
                "relevance_score": round(float(score), 8),
                "rank": rank,
            }
            if return_documents:
                item["document"] = {"text": documents[idx]}
            results.append(item)

        return {
            "id": f"rerank-{uuid.uuid4().hex[:16]}",
            "model": model,
            "object": "list",
            "results": results,
            "usage": {
                "total_tokens": 0,
                "prompt_tokens": 0,
                "rerank_count": len(documents),
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
    ) -> list[float]:
        cfg = self.manager.config
        rcfg = cfg.rerank
        upstream = resolved.upstream
        url = f"{upstream.base_url_stripped()}/api/generate"
        timeout = upstream.timeout or cfg.server.request_timeout

        concurrency = max(1, min(rcfg.max_concurrency, len(documents) or 1))
        semaphore = asyncio.Semaphore(concurrency)

        async def score_one(doc: str) -> float:
            prompt = rcfg.template.format(instruction=instruction, query=query, document=doc)
            options: dict[str, Any] = {
                "temperature": rcfg.temperature,
                "num_predict": max(1, rcfg.max_tokens),
                "stop": ["<|im_end|>", "<|endoftext|>", "\n"],
            }
            if extra_options:
                options.update(extra_options)

            body = {
                "model": resolved.target,
                "prompt": prompt,
                "raw": True,
                "stream": False,
                "logprobs": True,
                "top_logprobs": max(1, rcfg.top_logprobs),
                "options": options,
            }
            # Parameters injected by an alias (e.g. think:false) also apply to generate
            if resolved.extra_params:
                body.update(resolved.extra_params)

            async with semaphore:
                try:
                    resp = await self.client.post(url, json=body, timeout=timeout)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as exc:
                    logger.warning("rerank(logprobs) call failed: %s", exc)
                    return 0.0

            return self._extract_yes_probability(data, rcfg)

        return list(await asyncio.gather(*(score_one(d) for d in documents)))

    def _extract_yes_probability(self, data: dict[str, Any], rcfg) -> float:
        """Extract the probability of "yes" from an /api/generate response."""
        yes_token = (rcfg.yes_token or "yes").strip().lower()
        no_token = (rcfg.no_token or "no").strip().lower()

        logprobs = data.get("logprobs")
        if logprobs:
            flat = flatten_logprobs(logprobs)
            yes_lp = flat.get(yes_token)
            no_lp = flat.get(no_token)
            if yes_lp is not None and no_lp is not None:
                return softmax_pair(yes_lp, no_lp)

            # Some implementations only return the logprob of the generated token
            generated = str(data.get("response", "")).strip().lower()
            if generated.startswith(yes_token) and yes_lp is not None:
                return 1.0
            if generated.startswith(no_token) and no_lp is not None:
                return 0.0

        # Fallback: inspect the generated text directly
        generated = str(data.get("response", "")).strip().lower()
        if generated.startswith(yes_token):
            return 1.0
        if generated.startswith(no_token):
            return 0.0
        logger.debug("Could not extract yes/no from logprobs, response: %s", str(data)[:300])
        return 0.0

    # ------------------------------------------------ embedding scoring mode ---
    async def _score_by_embedding(
        self,
        resolved,
        query: str,
        documents: list[str],
        extra_options: dict[str, Any] | None,
    ) -> list[float]:
        cfg = self.manager.config
        upstream = resolved.upstream
        url = f"{upstream.base_url_stripped()}/api/embed"
        timeout = upstream.timeout or cfg.server.request_timeout

        body: dict[str, Any] = {
            "model": resolved.target,
            "input": [query, *documents],
        }
        if extra_options:
            body["options"] = extra_options
        if resolved.extra_params:
            body.update(resolved.extra_params)

        try:
            resp = await self.client.post(url, json=body, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("rerank(embedding) call failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Upstream embedding call failed: {exc}",
            ) from exc

        embeddings = data.get("embeddings")
        if not embeddings or len(embeddings) < len(documents) + 1:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Upstream returned an unexpected embeddings payload.",
            )

        query_vec = embeddings[0]
        doc_vecs = embeddings[1 : len(documents) + 1]
        return [cosine_similarity(query_vec, vec) for vec in doc_vecs]
