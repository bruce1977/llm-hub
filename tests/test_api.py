#!/usr/bin/env python3
"""
Execute test cases from docs/TESTCASES.md against a running LLM Hub instance.

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
import sys

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

    print("\n=== LLM Hub API Test Cases ===")
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

        # ------------------------------------------------ Admin endpoints ---
        print("[8] Admin endpoints (public listener only)")
        # `security.public.admin_endpoints` guards Ollama model management on the
        # internet-facing listener. Against the internal port these calls are
        # forwarded, so the gateway itself must not answer with the admin-block
        # message; on the public port the allowlist would hide them anyway (404).
        for path in ("/api/delete", "/api/pull"):
            r = client.post(path, headers=headers, json={"name": "test"})
            blocked_by_gateway = r.status_code == 403 and "public gateway" in r.text
            check(f"POST {path} is not admin-blocked on this listener",
                  not blocked_by_gateway, f"got {r.status_code}: {r.text[:120]}")

        # --------------------------------------------------- API docs hidden ---
        print("[9] API docs hidden by default")
        r = client.get("/docs")
        check("/docs returns 404 (hidden)", r.status_code == 404, f"got {r.status_code}")

        # --------------------------------------------------- Web search ---
        print("[10] Web search (POST /v1/tools/web-search)")
        # Retrieval + optional synthesis is slow: allow several minutes per call.
        SEARCH_TIMEOUT = 240.0
        r = client.post("/v1/tools/web-search", json={"query": "Ollama keep_alive default", "top_k": 2}, timeout=SEARCH_TIMEOUT)
        check("web search without auth returns 401", r.status_code == 401, f"got {r.status_code}")

        r = client.get("/v1/tools/web-search", headers=headers)
        check("web search only accepts POST (405)", r.status_code == 405, f"got {r.status_code}")

        r = client.post("/v1/tools/web-search", headers=headers, json={"top_k": 2}, timeout=SEARCH_TIMEOUT)
        check("web search without a query is rejected (400/422)", r.status_code in (400, 422), f"got {r.status_code}")

        r = client.post(
            "/v1/tools/web-search", headers=headers, json={"query": "Ollama keep_alive default", "top_k": 2}, timeout=SEARCH_TIMEOUT
        )
        if r.status_code == 404:
            check("web search is enabled on this gateway", False, "websearch.enabled=false in the test config")
        else:
            check("web search returns 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
            if r.status_code == 200:
                data = r.json()
                check(
                    "web search echoes the provider contract",
                    all(k in data for k in ["query", "model", "engine", "text", "sources"]),
                    str(sorted(data)),
                )
                check("web search retrieved passages", bool(data.get("results")) or bool(data.get("text")), str(data)[:200])
                check("web search query passed through", data.get("query") == "Ollama keep_alive default", str(data)[:120])

        # The search provider upstream must also be visible in the aggregated model list.
        r = client.get("/v1/models", headers=headers)
        if r.status_code == 200:
            ids = [m["id"] for m in r.json().get("data", [])]
            check(
                "opencode provider models are aggregated",
                any(i.startswith("opencode/") for i in ids),
                str(ids)[:200],
            )

    print(f"\n=== Result: {PASSED} passed, {FAILED} failed ===\n")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
