"""
Demo: connects to a real Ollama instance and shows
  1. the difference between the base model and the `-nothink` alias
  2. actual scores produced by the Rerank API

Run: python tests/demo.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

TMP_DIR = tempfile.mkdtemp(prefix="llm-hub-demo-")
CONFIG_FILE = os.path.join(TMP_DIR, "config.json")
os.environ["CONFIG_PATH"] = CONFIG_FILE

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

CONFIG = {
    "server": {"host": "127.0.0.1", "port": 8000, "log_level": "warning"},
    "auth": {"enabled": True, "api_keys": ["sk-demo"], "allow_anonymous_health": True},
    "upstreams": [
        {
            "name": "local",
            "base_url": "http://127.0.0.1:11434",
            "models": ["*"],
            "timeout": 300,
        }
    ],
    "default_upstream": "local",
    "aliases": {
        "qwen3.5:4b-nothink": {
            "model": "qwen3.5:4b",
            "params": {"think": False},
        }
    },
    "rerank": {
        "enabled": True,
        "mode": "logprobs",
        "models": ["qwen3-reranker:4b"],
        "normalize": True,
        "max_concurrency": 4,
        "return_documents": False,
    },
}

with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
    json.dump(CONFIG, fh)

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402


def show(title: str) -> None:
    print(f"\n{'=' * 66}\n{title}\n{'=' * 66}")


def main() -> None:
    headers = {"Authorization": "Bearer sk-demo"}
    question = "Which is larger, 9.11 or 9.8? Answer directly."

    with TestClient(app) as client:
        # ------------------------------------------------- 1. base model ---
        show("1. Plain request to qwen3.5:4b (default thinking behaviour)")
        r = client.post(
            "/api/chat",
            headers=headers,
            json={"model": "qwen3.5:4b", "messages": [{"role": "user", "content": question}], "stream": False},
        )
        data = r.json()
        msg = data.get("message", {})
        thinking = (msg.get("thinking") or "").strip()
        print(f"thinking length: {len(thinking)} chars")
        if thinking:
            print(f"thinking preview: {thinking[:120]}...")
        print(f"answer: {msg.get('content', '').strip()[:200]}")

        # --------------------------------------------------- 2. nothink ---
        show("2. Alias request to qwen3.5:4b-nothink (gateway injects think=false)")
        r = client.post(
            "/api/chat",
            headers=headers,
            json={
                "model": "qwen3.5:4b-nothink",
                "messages": [{"role": "user", "content": question}],
                "stream": False,
            },
        )
        data = r.json()
        msg = data.get("message", {})
        thinking2 = (msg.get("thinking") or "").strip()
        print(f"thinking length: {len(thinking2)} chars  <- expected 0")
        print(f"answer: {msg.get('content', '').strip()[:200]}")
        print(f"\nConclusion: thinking went from {len(thinking)} to {len(thinking2)} chars, the alias works")

        # ---------------------------------------------------- 3. rerank ---
        show("3. Rerank API (qwen3-reranker:4b, logprobs scoring)")
        query = "How do I brew pour-over coffee?"
        documents = [
            "Pour-over coffee needs the water at about 90C, poured in three stages over 2.5 minutes",
            "Python is a high-level interpreted programming language created by Guido van Rossum",
            "The grind size of coffee beans directly affects extraction speed and flavour",
        ]
        r = client.post(
            "/v1/rerank",
            headers=headers,
            json={"model": "qwen3-reranker:4b", "query": query, "documents": documents},
        )
        result = r.json()
        print(f"query: {query}\n")
        for item in result.get("results", []):
            idx = item["index"]
            score = item["relevance_score"]
            bar = "#" * int(score * 30)
            print(f"  [{score:.4f}] {bar:<30} doc{idx}: {documents[idx][:44]}...")

        # ---------------------------------------------------- 4. models ---
        show("4. Unified /v1/models (models and aliases from the config)")
        r = client.get("/v1/models", headers=headers)
        print(json.dumps(r.json(), ensure_ascii=False, indent=2)[:900])

    print()


if __name__ == "__main__":
    main()
