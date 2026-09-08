"""
Smoke tests.

Two parts:
  1. Mock upstreams: deterministically verify auth, multi-instance routing, alias parameter injection, /v1/models aggregation,
     both Rerank modes (logprobs and embedding), and streaming passthrough.
  2. Real Ollama (enable with E2E=1): verifies end-to-end forwarding and actual Rerank scoring.

Run:
    python tests/smoke_test.py          # mock tests only
    E2E=1 python tests/smoke_test.py    # additionally run the real Ollama end-to-end tests
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

# --- the config path must be set before importing app -------------------------------------
TMP_DIR = tempfile.mkdtemp(prefix="llm-hub-test-")
CONFIG_FILE = os.path.join(TMP_DIR, "config.json")
os.environ["CONFIG_PATH"] = CONFIG_FILE

# The Python modules live directly in APP_DIR; the same layout is mounted at /app in Docker.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
sys.path.insert(0, APP_DIR)

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


# --------------------------------------------------------------------------- #
# Mock Ollama upstream
# --------------------------------------------------------------------------- #
def make_handler(store: list[dict[str, Any]]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence the access log
            pass

        def _read_body(self) -> dict[str, Any]:
            length = int(self.headers.get("content-length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length))
            except Exception:
                return {}

        def _send(self, payload: Any, status: int = 200, streaming: bool = False):
            if streaming:
                body = "".join(json.dumps(chunk) + "\n" for chunk in payload).encode()
            else:
                body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/x-ndjson" if streaming else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # ------------------------------------------------------------ GET ---
        def do_GET(self):
            store.append({"method": "GET", "path": self.path})
            if self.path.endswith("/api/version"):
                self._send({"version": "0.32.13-mock"})
            elif self.path.endswith("/api/tags"):
                self._send({"models": []})
            else:
                self._send({"status": "ok"})

        # ----------------------------------------------------------- POST ---
        def do_POST(self):
            body = self._read_body()
            store.append({"method": "POST", "path": self.path, "body": body})
            model = body.get("model", "")

            # --- rerank: logprobs mode ---
            if self.path.endswith("/api/generate"):
                prompt = body.get("prompt", "")
                # return different yes/no probabilities per document so ordering can be verified
                if "highly relevant" in prompt:
                    yes_lp, no_lp = -0.1, -3.0
                elif "partially relevant" in prompt:
                    yes_lp, no_lp = -1.0, -1.0
                else:
                    yes_lp, no_lp = -3.0, -0.2
                self._send(
                    {
                        "model": model,
                        "response": "yes" if yes_lp > no_lp else "no",
                        "done": True,
                        "logprobs": [
                            {
                                "token": "yes",
                                "logprob": yes_lp,
                                "bytes": [],
                                "top_logprobs": [
                                    {"token": "yes", "logprob": yes_lp, "bytes": []},
                                    {"token": " no", "logprob": no_lp, "bytes": []},
                                ],
                            }
                        ],
                    }
                )

            # --- rerank: embedding mode ---
            elif self.path.endswith("/api/embed"):
                inputs = body.get("input", [])
                vectors = []
                for i, text in enumerate(inputs):
                    if i == 0 or "highly" in text:  # query
                        vectors.append([1.0, 0.0, 0.0])
                    elif "partially" in text:
                        vectors.append([0.7071, 0.7071, 0.0])
                    else:
                        vectors.append([0.0, 1.0, 0.0])
                self._send({"model": model, "embeddings": vectors})

            # --- chat ---
            elif self.path.endswith("/api/chat"):
                # Check if this is a rerank request (has logprobs enabled)
                if body.get("logprobs"):
                    messages = body.get("messages", [])
                    prompt = messages[0].get("content", "") if messages else ""
                    # return different yes/no probabilities per document so ordering can be verified
                    if "highly relevant" in prompt:
                        yes_lp, no_lp = -0.1, -3.0
                    elif "partially relevant" in prompt:
                        yes_lp, no_lp = -1.0, -1.0
                    else:
                        yes_lp, no_lp = -3.0, -0.2
                    self._send(
                        {
                            "model": model,
                            "message": {"role": "assistant", "content": "yes" if yes_lp > no_lp else "no"},
                            "done": True,
                            "logprobs": [
                                {
                                    "token": "yes",
                                    "logprob": yes_lp,
                                    "bytes": [],
                                    "top_logprobs": [
                                        {"token": "yes", "logprob": yes_lp, "bytes": []},
                                        {"token": " no", "logprob": no_lp, "bytes": []},
                                    ],
                                }
                            ],
                        }
                    )
                elif body.get("stream"):
                    self._send(
                        [
                            {"model": model, "message": {"role": "assistant", "content": "Hel"}, "done": False},
                            {"model": model, "message": {"role": "assistant", "content": "lo"}, "done": False},
                            {"model": model, "done": True, "done_reason": "stop"},
                        ],
                        streaming=True,
                    )
                else:
                    self._send(
                        {
                            "model": model,
                            "message": {"role": "assistant", "content": "Hello"},
                            "done": True,
                            "echo_think": body.get("think", "unset"),
                        }
                    )
            else:
                self._send({"model": model, "done": True})

    return Handler


def start_mock() -> tuple:
    """Start two mock upstreams to simulate a multi-instance deployment."""
    store_a: list[dict[str, Any]] = []
    store_b: list[dict[str, Any]] = []

    server_a = HTTPServer(("127.0.0.1", 0), make_handler(store_a))
    server_b = HTTPServer(("127.0.0.1", 0), make_handler(store_b))
    threading.Thread(target=server_a.serve_forever, daemon=True).start()
    threading.Thread(target=server_b.serve_forever, daemon=True).start()
    return (
        (server_a, store_a, f"http://127.0.0.1:{server_a.server_address[1]}"),
        (server_b, store_b, f"http://127.0.0.1:{server_b.server_address[1]}"),
    )


# --------------------------------------------------------------------------- #
# Main test flow
# --------------------------------------------------------------------------- #
def main() -> int:
    (srv_a, store_a, url_a), (srv_b, store_b, url_b) = start_mock()

    config = {
        "server": {"host": "127.0.0.1", "port": 8000, "log_level": "warning"},
        "auth": {
            "enabled": True,
            "api_keys": ["sk-test-1", "sk-test-2"],
            "allow_anonymous_health": True,
        },
        "routing": {"strict": False},
        "upstreams": [
            {
                "name": "mock-a",
                "base_url": url_a,
                "models": ["qwen3.5:4b", "qwen3-reranker:4b"],
            },
            {
                "name": "mock-b",
                "base_url": url_b,
                "models": ["big-model:72b", "bge-m3:latest"],
                "timeout": 30,
            },
        ],
        "default_upstream": "mock-a",
        "aliases": {
            "qwen3.5:4b-nothink": {
                "model": "qwen3.5:4b",
                "params": {"think": False},
            },
            "big-nothink": {
                "model": "big-model:72b",
                "upstream": "mock-b",
                "params": {"think": False, "num_ctx": 8192},
            },
        },
        "rerank": {
            "enabled": True,
            "mode": "logprobs",
            "models": ["qwen3-reranker:4b", "bge-m3:latest"],
            "model_modes": {"bge-m3:latest": "embedding"},
            "normalize": True,
            "max_concurrency": 4,
            "return_documents": True,
        },
        "security": {"fail_on_weak_keys": False},
    }

    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(config, fh)

    from fastapi.testclient import TestClient
    from main import app

    print("\n=== LLM Hub smoke tests ===\n")

    with TestClient(app) as client:
        good = {"Authorization": "Bearer sk-test-1"}

        # ---------------------------------------------------- Health check ---
        print("[1] Health check")
        r = client.get("/health")
        check("GET /health returns 200 without a key", r.status_code == 200, f"got {r.status_code}")
        data = r.json()
        check(
            "health returns {ok: true}",
            data.get("ok") is True,
            json.dumps(data, ensure_ascii=False),
        )

        # -------------------------------------------------------- Authentication ---
        print("[2] API key authentication")
        r = client.get("/v1/models")
        check("Missing key returns 401", r.status_code == 401, f"got {r.status_code}")
        r = client.get("/v1/models", headers={"Authorization": "Bearer wrong-key"})
        check("Invalid key returns 403", r.status_code == 403, f"got {r.status_code}")
        r = client.get("/v1/models", headers={"X-API-Key": "sk-test-2"})
        check("The second key and the X-API-Key header work", r.status_code == 200, f"got {r.status_code}")

        # ----------------------------------------- GATEWAY_AUTH_DISABLED env var ---
        print("[2b] GATEWAY_AUTH_DISABLED")
        os.environ["GATEWAY_AUTH_DISABLED"] = "true"
        try:
            manager = getattr(client.app.state, "manager", None)
            if manager:
                manager.reload()
            r = client.get("/v1/models")
            check("GATEWAY_AUTH_DISABLED=true skips auth", r.status_code == 200, f"got {r.status_code}")
        finally:
            os.environ.pop("GATEWAY_AUTH_DISABLED", None)
            if manager:
                manager.reload()

        # ------------------------------------------------ /v1/models aggregation ---
        print("[3] Unified /v1/models")
        r = client.get("/v1/models", headers=good)
        models = r.json().get("data", [])
        ids = [m["id"] for m in models]
        check(
            "Aggregates the models of all upstreams",
            all(m in ids for m in ["qwen3.5:4b", "big-model:72b", "bge-m3:latest"]),
            str(ids),
        )
        check(
            "Aliases also appear in the model list",
            "qwen3.5:4b-nothink" in ids and "big-nothink" in ids,
            str(ids),
        )
        alias_item = next((m for m in models if m["id"] == "qwen3.5:4b-nothink"), {})
        check(
            "Alias entries are tagged with the target model",
            alias_item.get("type") == "alias" and alias_item.get("target") == "qwen3.5:4b",
            str(alias_item),
        )

        # -------------------------------------------------- multi-instance routing ---
        print("[4] Multi-instance routing")
        store_a.clear()
        store_b.clear()
        r = client.post(
            "/api/chat",
            headers=good,
            json={"model": "qwen3.5:4b", "messages": [{"role": "user", "content": "hi"}], "stream": False},
        )
        check(
            "qwen3.5:4b is forwarded to mock-a",
            r.status_code == 200 and len(store_a) >= 1 and store_b == [],
            f"a={len(store_a)} b={len(store_b)}",
        )
        r = client.post(
            "/api/chat",
            headers=good,
            json={"model": "big-model:72b", "messages": [{"role": "user", "content": "hi"}], "stream": False},
        )
        check(
            "big-model:72b is forwarded to mock-b",
            r.status_code == 200 and len(store_b) >= 1,
            f"a={len(store_a)} b={len(store_b)}",
        )

        # -------------------------------------------- Alias and parameter injection ---
        print("[5] Alias and forced parameter injection")
        store_a.clear()
        r = client.post(
            "/api/chat",
            headers=good,
            json={"model": "qwen3.5:4b-nothink", "messages": [{"role": "user", "content": "hi"}], "stream": False},
        )
        sent = store_a[-1]["body"] if store_a else {}
        check("Alias is replaced by the real model name", sent.get("model") == "qwen3.5:4b", str(sent.get("model")))
        check("think=false is force-injected", sent.get("think") is False, f"think={sent.get('think')}")

        store_b.clear()
        r = client.post(
            "/api/chat",
            headers=good,
            json={
                "model": "big-nothink",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
                "think": True,  # the client explicitly asks for thinking; the alias must override it
                "num_ctx": 2048,
            },
        )
        sent = store_b[-1]["body"] if store_b else {}
        check(
            "Alias parameters override the client's parameters",
            sent.get("think") is False,
            f"think={sent.get('think')}",
        )
        check("Alias injects num_ctx=8192", sent.get("num_ctx") == 8192, f"num_ctx={sent.get('num_ctx')}")
        check(
            "Alias can pin a fixed upstream",
            len(store_b) == 1 and store_a[-1]["body"].get("model") == "qwen3.5:4b",
            "unexpected routing",
        )

        # ---------------------------------------------------- streaming passthrough ---
        print("[6] Streaming passthrough")
        r = client.post(
            "/api/chat",
            headers=good,
            json={"model": "qwen3.5:4b", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        )
        lines = [ln for ln in r.text.splitlines() if ln.strip()]
        check("Streaming response contains multiple NDJSON lines", len(lines) == 3, f"lines={len(lines)}")
        check("Every streamed line is valid JSON", all(json.loads(ln) for ln in lines), r.text[:120])

        # ------------------------------------------------- Rerank: logprobs ---
        print("[7] Rerank - logprobs mode")
        r = client.post(
            "/v1/rerank",
            headers=good,
            json={
                "model": "qwen3-reranker:4b",
                "query": "what is the capital of France",
                "documents": [
                    "highly relevant document about Paris",
                    "partially relevant text",
                    "totally unrelated content",
                ],
                "top_n": 3,
            },
        )
        check("rerank returns 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        result = r.json()
        results = result.get("results", [])
        check("Returns 3 results", len(results) == 3, str(len(results)))
        check(
            "Sorted by descending relevance",
            [x["index"] for x in results] == [0, 1, 2],
            str([x["index"] for x in results]),
        )
        check(
            "Scores are normalized to [0,1] and decreasing",
            all(0 <= x["relevance_score"] <= 1 for x in results)
            and results[0]["relevance_score"] > results[1]["relevance_score"] > results[2]["relevance_score"],
            str([x["relevance_score"] for x in results]),
        )
        check(
            "Includes document text (return_documents=True)",
            "document" in (results[0] if results else {}),
            str(results[0] if results else None),
        )
        # The upstream call must really ask for logprobs - without it Ollama returns none
        # and every score silently degrades to the 0.9/0.1/0.5 text fallback.
        scored_calls = [
            c
            for c in store_a
            if c["path"].endswith(("/api/generate", "/api/chat")) and c["body"].get("logprobs") is True
        ]
        check("rerank requests logprobs from the upstream", len(scored_calls) >= 3, str(len(scored_calls)))
        if scored_calls:
            first_call = scored_calls[-1]["body"]
            check(
                "upstream request carries top_logprobs",
                first_call.get("top_logprobs", 0) >= 1,
                str(first_call.get("top_logprobs")),
            )
            check(
                "upstream request targets the resolved model id",
                first_call.get("model") == "qwen3-reranker:4b",
                str(first_call.get("model")),
            )
        distinct = {round(x["relevance_score"], 6) for x in results}
        check(
            "Scores carry real signal (not the flat fallback)",
            len(distinct) == len(results),
            str([x["relevance_score"] for x in results]),
        )

        # ------------------------------------------- Rerank: logprobs parsing ---
        print("[7b] Rerank - logprobs parsing")
        from rerank import extract_positions, text_fallback_score, yes_no_probability

        ollama_resp = {
            "logprobs": [
                {
                    "token": " yes",
                    "logprob": -0.02,
                    "top_logprobs": [
                        {"token": " yes", "logprob": -0.02},
                        {"token": " no", "logprob": -5.0},
                    ],
                }
            ]
        }
        score = yes_no_probability(extract_positions(ollama_resp), "yes", "no")
        check("ollama logprobs -> high yes probability", score is not None and score > 0.99, str(score))

        openai_resp = {
            "choices": [
                {
                    "logprobs": {
                        "content": [
                            {
                                "token": "no",
                                "logprob": -0.1,
                                "top_logprobs": [
                                    {"token": "yes", "logprob": -2.5},
                                    {"token": "no", "logprob": -0.1},
                                ],
                            }
                        ]
                    }
                }
            ]
        }
        score = yes_no_probability(extract_positions(openai_resp), "yes", "no")
        check("openai logprobs -> low yes probability", score is not None and score < 0.1, str(score))

        thinking_resp = {
            "message": {"content": "<think>\n\n</think>\n\nyes"},
            "logprobs": [
                {"token": "<think>", "logprob": -0.0, "top_logprobs": [{"token": "<think>", "logprob": -0.0}]},
                {
                    "token": "yes",
                    "logprob": -0.5,
                    "top_logprobs": [
                        {"token": "yes", "logprob": -0.5},
                        {"token": "no", "logprob": -2.0},
                    ],
                },
            ],
        }
        score = yes_no_probability(extract_positions(thinking_resp), "yes", "no")
        check("a leading <think> token is skipped", score is not None and score > 0.8, str(score))
        check(
            "text fallback maps yes/no to 0.9/0.1",
            text_fallback_score("yes", "yes", "no") == 0.9 and text_fallback_score("No.", "yes", "no") == 0.1,
            f"{text_fallback_score('yes', 'yes', 'no')} / {text_fallback_score('No.', 'yes', 'no')}",
        )

        # ------------------------------------- Rerank: absolute score semantics ---
        print("[7c] Rerank - normalize=false keeps absolute probabilities")
        config["rerank"]["normalize"] = False
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(config, fh)
        manager = getattr(client.app.state, "manager", None)
        if manager:
            manager.reload()
        r = client.post(
            "/v1/rerank",
            headers=good,
            json={
                "model": "qwen3-reranker:4b",
                "query": "what is the capital of France",
                "documents": ["highly relevant document about Paris"],
            },
        )
        single_scores = [x["relevance_score"] for x in r.json().get("results", [])]
        check(
            "a single document keeps its absolute probability (not 0.5)",
            len(single_scores) == 1 and single_scores[0] > 0.9,
            str(single_scores),
        )

        # ---------------------------------- Rerank: generic provider payload ---
        print("[7d] Rerank - generic provider payload (model_name + input)")
        r = client.post(
            "/v1/rerank",
            headers=good,
            json={
                "documents": [
                    "CPU 飙升通常由死循环代码或高并发请求引起，建议使用 top 命令定位进程。",
                    "服务器内存不足时，系统会频繁使用 Swap 分区。",
                    "如何更换服务器机房空调滤网：首先切断电源。",
                ],
                "input": "服务器 CPU 占用率突然飙升到 100%，导致系统响应极慢，怎么排查？",
                "model_id": "4d5207dd-7784-40b1-8be8-a5927a188c2c",
                "model_name": "qwen3-reranker:4b",
                "model_type": "Rerank",
                "options": {},
                "provider": "generic",
                "source": "remote",
            },
        )
        check("generic provider payload returns 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        body = r.json()
        check("response echoes the requested model", body.get("model") == "qwen3-reranker:4b", str(body.get("model")))
        check(
            "results expose index / relevance_score / document.text",
            all({"index", "relevance_score", "document"} <= set(x) for x in body.get("results", [])),
            json.dumps(body.get("results", [])[:1], ensure_ascii=False),
        )
        check("meta reports the scoring mode", body.get("meta", {}).get("mode") == "logprobs", str(body.get("meta")))

        config["rerank"]["normalize"] = True
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(config, fh)
        if manager:
            manager.reload()

        # ------------------------------------------------ Rerank: embedding ---
        print("[8] Rerank - embedding mode")
        r = client.post(
            "/v1/rerank",
            headers=good,
            json={
                "model": "bge-m3:latest",
                "query": "test query",
                "documents": [
                    "highly relevant",
                    "partially relevant",
                    "unrelated",
                ],
            },
        )
        check("embedding-mode rerank returns 200", r.status_code == 200, r.text[:200])
        results = r.json().get("results", [])
        check(
            "embedding ordering is 0 > 1 > 2",
            [x["index"] for x in results] == [0, 1, 2],
            str([(x["index"], x["relevance_score"]) for x in results]),
        )

        # -------------------------------------------------------- validation ---
        print("[9] Errors and edge cases")
        r = client.post("/v1/rerank", headers=good, json={"model": "qwen3-reranker:4b", "query": "q", "documents": []})
        check("Empty documents returns 400", r.status_code == 400, f"got {r.status_code}")
        r = client.get("/v1/rerank", headers=good)
        check("rerank rejects GET with 405", r.status_code == 405, f"got {r.status_code}")

        # --- field alias compatibility: input/model_name ---
        r = client.post(
            "/v1/rerank",
            headers=good,
            json={
                "model_name": "qwen3-reranker:4b",
                "input": "test query",
                "documents": ["doc1", "doc2"],
            },
        )
        check("model_name+input aliases work", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")

        # ------------------------------------------- Security: admin endpoint block ---
        print("[10] Security - admin endpoint blocking")
        r = client.post("/api/delete", headers=good, json={"name": "some-model"})
        check("Authenticated /api/delete is blocked (403)", r.status_code == 403, f"got {r.status_code}")
        r = client.post("/api/pull", headers=good, json={"name": "some-model"})
        check("Authenticated /api/pull is blocked (403)", r.status_code == 403, f"got {r.status_code}")
        r = client.post("/api/create", headers=good, json={"name": "some-model", "from": "x"})
        check("Authenticated /api/create is blocked (403)", r.status_code == 403, f"got {r.status_code}")

        # ----------------------------------------------- Security: unit-level checks ---
        print("[11] Security - unit checks")
        from auth import find_weak_keys
        from config import SecurityConfig

        STRONG = "xK9mP2qL8vR4nB7wT1yC5uH3jF6sD0eA"  # 32 random-looking chars, high entropy
        wk = find_weak_keys(["sk-change-me-001", "short", STRONG])
        check("find_weak_keys flags placeholder/short keys", len(wk) == 2, str(wk))
        check("find_weak_keys accepts a strong key", len(find_weak_keys([STRONG])) == 0, "unexpected")

        deny = SecurityConfig(admin_endpoints="deny")
        check("deny mode blocks api/delete", deny.is_path_blocked("api/delete"))
        check("deny mode blocks api/pull", deny.is_path_blocked("api/pull"))
        check("deny mode blocks api/blobs/*", deny.is_path_blocked("api/blobs/sha256:abc"))
        check("deny mode allows api/chat", not deny.is_path_blocked("api/chat"))
        readonly = SecurityConfig(admin_endpoints="readonly")
        check("readonly mode allows api/pull", not readonly.is_path_blocked("api/pull"))
        check("readonly mode blocks api/delete", readonly.is_path_blocked("api/delete"))
        allow = SecurityConfig(admin_endpoints="allow")
        check("allow mode forwards everything", not allow.is_path_blocked("api/delete"))

        # --------------------------------------------------- Security: CORS / docs ---
        print("[12] Security - CORS and docs defaults")
        r = client.get("/v1/models", headers=good)
        check(
            "Default config sends no Access-Control-Allow-Origin header",
            "access-control-allow-origin" not in r.headers,
            str(dict(r.headers)),
        )
        r = client.get("/docs")
        check("API docs are hidden by default (404)", r.status_code == 404, f"got {r.status_code}")

        # ------------------------------------------- Proxy: tool_calls.arguments normalization ---
        print("[13] Proxy - tool_calls.arguments normalization")
        obj_payload = {
            "model": "qwen3.5:4b",
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": {"path": "main.py", "line": 10}},
                        }
                    ],
                }
            ],
            "stream": False,
        }
        before_a = len(store_a)
        r = client.post("/v1/chat/completions", headers=good, json=obj_payload)
        check(
            "tool-call request with object arguments returns 200",
            r.status_code == 200,
            f"got {r.status_code}: {r.text[:200]}",
        )
        if len(store_a) > before_a:
            sent_args = (
                store_a[-1]["body"]
                .get("messages", [{}])[0]
                .get("tool_calls", [{}])[0]
                .get("function", {})
                .get("arguments")
            )
            check("object arguments normalized to a JSON string upstream", isinstance(sent_args, str), str(sent_args))
        else:
            check("object arguments reached the upstream", False, "no request recorded in store_a")

        # -------------------------------------------- LM Studio upstream type ---
        print("[14] LM Studio upstream integration")
        from config import Config as GatewayConfig
        from config import build_default_config, merge_env_api_keys

        lm_cfg = GatewayConfig(
            upstreams=[
                {
                    "name": "lmstudio",
                    "type": "lmstudio",
                    "base_url": "http://127.0.0.1:1234",
                    "model_aliases": {"qwen3.5:9b": "qwen/qwen3.5-9b"},
                }
            ]
        )
        lm_up = lm_cfg.upstreams[0]
        check("lmstudio: type parsed", lm_up.type == "lmstudio", lm_up.type)
        check("lmstudio: discovers models via /v1/models", lm_up.tags_path() == "v1/models", lm_up.tags_path())
        check("lmstudio: probes via /v1/models", lm_up.health_path() == "v1/models", lm_up.health_path())
        check(
            "lmstudio: parses data[].id (OpenAI format)",
            lm_up.parse_model_names({"object": "list", "data": [{"id": "qwen/qwen3.5-9b"}]}) == ["qwen/qwen3.5-9b"],
        )
        check(
            "ollama upstream stays the default type",
            GatewayConfig(upstreams=[{"name": "o", "base_url": "http://x"}]).upstreams[0].type == "ollama",
        )
        ol_up = GatewayConfig(upstreams=[{"name": "o", "base_url": "http://x"}]).upstreams[0]
        check("ollama: discovers models via /api/tags", ol_up.tags_path() == "api/tags", ol_up.tags_path())
        check(
            "ollama: parses models[].name",
            ol_up.parse_model_names({"models": [{"name": "qwen3:8b"}]}) == ["qwen3:8b"],
        )

        # ------------------------------------------ Model aliases for long ids ---
        print("[15] Model aliases (short name -> verbose upstream id)")
        lm_resolved = lm_cfg.resolve("qwen3.5:9b")
        check(
            "short name maps to the verbose LM Studio id", lm_resolved.target == "qwen/qwen3.5-9b", lm_resolved.target
        )
        check("short name is flagged as an alias", lm_resolved.is_alias, str(lm_resolved.is_alias))
        check(
            "short name routes to the lmstudio upstream",
            lm_resolved.upstream is not None and lm_resolved.upstream.name == "lmstudio",
        )
        check(
            "short name appears in the aggregated model list",
            any(i["id"] == "qwen3.5:9b" for i in lm_cfg.aggregated_models()),
        )

        # ---------------------------------------- API keys from the environment ---
        print("[16] API keys loaded from the environment")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as kf:
            kf.write("fileKey-aaaa1111,fileKey-bbbb2222\n")
            keyfile = kf.name
        prev_inline = os.environ.get("GATEWAY_API_KEYS")
        prev_file = os.environ.get("GATEWAY_API_KEYS_FILE")
        try:
            os.environ["GATEWAY_API_KEYS"] = "envKey-cccc3333"
            os.environ["GATEWAY_API_KEYS_FILE"] = keyfile
            merged = merge_env_api_keys(["existing-key-0000"])
            check("inline env key is merged", "envKey-cccc3333" in merged, str(merged))
            check(
                "file-based keys are merged", "fileKey-aaaa1111" in merged and "fileKey-bbbb2222" in merged, str(merged)
            )
            check("pre-existing config key is preserved", "existing-key-0000" in merged, str(merged))
        finally:
            if prev_inline is None:
                os.environ.pop("GATEWAY_API_KEYS", None)
            else:
                os.environ["GATEWAY_API_KEYS"] = prev_inline
            if prev_file is None:
                os.environ.pop("GATEWAY_API_KEYS_FILE", None)
            else:
                os.environ["GATEWAY_API_KEYS_FILE"] = prev_file
            try:
                os.unlink(keyfile)
            except OSError:
                pass

        check(
            "default config template ships no API keys",
            build_default_config()["auth"]["api_keys"] == [],
            str(build_default_config()["auth"]["api_keys"]),
        )

        # ---------------------------------------------------- Real E2E ---
        if os.getenv("E2E") == "1":
            run_e2e(client, good)

    srv_a.shutdown()
    srv_b.shutdown()

    print(f"\n=== Result: {PASSED} passed, {FAILED} failed ===\n")
    return 1 if FAILED else 0


def run_e2e(client, headers) -> None:
    """End-to-end tests against a real Ollama (requires a local instance on 11434)."""
    print("[10] Real Ollama end-to-end")

    e2e_config = {
        "server": {"host": "127.0.0.1", "port": 8000, "log_level": "warning"},
        "auth": {"enabled": True, "api_keys": ["sk-test-1"], "allow_anonymous_health": True},
        "upstreams": [
            {
                "name": "real",
                "base_url": "http://127.0.0.1:11434",
                "models": ["*"],
                "timeout": 300,
            }
        ],
        "default_upstream": "real",
        "aliases": {"qwen3.5:4b-nothink": {"model": "qwen3.5:4b", "params": {"think": False}}},
        "rerank": {
            "enabled": True,
            "mode": "logprobs",
            "models": ["qwen3-reranker:4b"],
            "normalize": True,
            "max_concurrency": 4,
        },
        "security": {"fail_on_weak_keys": False},
    }
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(e2e_config, fh)

    manager = getattr(client.app.state, "manager", None)
    if manager:
        manager.reload()
        time.sleep(0.2)

    r = client.get("/health")
    check("E2E /health detects the real upstream", r.status_code == 200, r.text[:200])

    r = client.post(
        "/api/chat",
        headers=headers,
        json={
            "model": "qwen3.5:4b-nothink",
            "messages": [{"role": "user", "content": "Reply with only: OK"}],
            "stream": False,
        },
    )
    ok = r.status_code == 200 and "OK" in r.text
    check("E2E real chat (nothink alias)", ok, f"{r.status_code}: {r.text[:200]}")

    r = client.post(
        "/v1/rerank",
        headers=headers,
        json={
            "model": "qwen3-reranker:4b",
            "query": "How to brew pour-over coffee?",
            "documents": [
                "Pour-over coffee needs the water temperature around 90C, poured in stages",
                "Python is a widely used programming language",
            ],
        },
    )
    check("E2E real rerank returns 200", r.status_code == 200, f"{r.status_code}: {r.text[:300]}")
    if r.status_code == 200:
        results = r.json().get("results", [])
        check(
            "E2E coffee query ranks document 0 first",
            bool(results) and results[0]["index"] == 0,
            str([(x["index"], x["relevance_score"]) for x in results]),
        )


if __name__ == "__main__":
    sys.exit(main())
