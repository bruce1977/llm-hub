#!/usr/bin/env python3
"""
Execute test cases from TESTCASES.md against a running LLM Hub instance.

Usage:
    # Start the server first, then run:
    python tests/test_api.py

    # Or with custom base URL:
    python tests/test_api.py --base-url http://localhost:8888

    # Or with custom API key:
    python tests/test_api.py --api-key sk-your-key
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import httpx

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name} {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run API test cases")
    parser.add_argument("--base-url", default="http://localhost:8000", help="LLM Hub base URL")
    parser.add_argument("--api-key", default="sk-test-key", help="API key for authentication")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.api_key}"}

    print(f"\n=== LLM Hub API Test Cases ===")
    print(f"Target: {base_url}\n")

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        # --------------------------------------------------- Health check ---
        print("[1] Health check")
        r = client.get("/health")
        check("GET /health returns 200", r.status_code == 200, f"got {r.status_code}")
        data = r.json()
        check("health returns {ok: true}", data.get("ok") is True, str(data))

        # --------------------------------------------------- Probe ---
        print("[2] Probe (requires auth)")
        r = client.get("/probe")
        check("GET /probe without auth returns 401", r.status_code == 401, f"got {r.status_code}")

        r = client.get("/probe", headers=headers)
        check("GET /probe with auth returns 200", r.status_code == 200, f"got {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            upstreams = data.get("upstreams", [])
            check("probe returns upstreams list", len(upstreams) > 0, f"upstreams={len(upstreams)}")
            for up in upstreams:
                check(
                    f"upstream '{up['name']}' has required fields",
                    all(k in up for k in ["name", "type", "base_url", "reachable", "models"]),
                    str(up.keys()),
                )

        # --------------------------------------------------- Auth ---
        print("[3] API authentication")
        r = client.get("/v1/models")
        check("Missing key returns 401", r.status_code == 401, f"got {r.status_code}")

        r = client.get("/v1/models", headers={"Authorization": "Bearer wrong-key"})
        check("Invalid key returns 403", r.status_code == 403, f"got {r.status_code}")

        r = client.get("/v1/models", headers=headers)
        check("Valid key returns 200", r.status_code == 200, f"got {r.status_code}")

        # --------------------------------------------------- Models ---
        print("[4] Model list")
        r = client.get("/v1/models", headers=headers)
        if r.status_code == 200:
            data = r.json()
            models = data.get("data", [])
            check("models list is not empty", len(models) > 0, f"count={len(models)}")
            check("models have id field", all("id" in m for m in models))
        else:
            check("models list returns 200", False, f"got {r.status_code}")

        # --------------------------------------------------- Chat (Ollama) ---
        print("[5] Chat (Ollama format)")
        r = client.post(
            "/api/chat",
            headers=headers,
            json={
                "model": "qwen3:8b",
                "messages": [{"role": "user", "content": "Say OK"}],
                "stream": False,
            },
        )
        check("POST /api/chat returns 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")

        # --------------------------------------------------- Chat (OpenAI) ---
        print("[6] Chat (OpenAI format)")
        r = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "qwen3:8b",
                "messages": [{"role": "user", "content": "Say OK"}],
                "stream": False,
            },
        )
        check("POST /v1/chat/completions returns 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")

        # --------------------------------------------------- Rerank ---
        print("[7] Rerank")
        r = client.post(
            "/v1/rerank",
            headers=headers,
            json={
                "model": "qwen3-reranker:4b",
                "query": "test query",
                "documents": ["doc1", "doc2", "doc3"],
            },
        )
        check("POST /v1/rerank returns 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        if r.status_code == 200:
            data = r.json()
            results = data.get("results", [])
            check("rerank returns results", len(results) > 0, f"count={len(results)}")

        # --------------------------------------------------- Admin blocked ---
        print("[8] Admin endpoints blocked")
        r = client.post("/api/delete", headers=headers, json={"name": "test"})
        check("POST /api/delete is blocked (403)", r.status_code == 403, f"got {r.status_code}")

        r = client.post("/api/pull", headers=headers, json={"name": "test"})
        check("POST /api/pull is blocked (403)", r.status_code == 403, f"got {r.status_code}")

        # --------------------------------------------------- Docs hidden ---
        print("[9] API docs hidden by default")
        r = client.get("/docs")
        check("/docs returns 404 (hidden)", r.status_code == 404, f"got {r.status_code}")

    print(f"\n=== Result: {PASSED} passed, {FAILED} failed ===\n")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
