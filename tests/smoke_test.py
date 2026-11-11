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
from pathlib import Path
from typing import Any

# --- the config path must be set before importing app -------------------------------------
TMP_DIR = tempfile.mkdtemp(prefix="llm-hub-test-")
CONFIG_FILE = os.path.join(TMP_DIR, "config.json")
os.environ["CONFIG_PATH"] = CONFIG_FILE
# Caller keys are minted in the console and stored in llmhub.db; there is no environment
# variable for them. Each suite inserts its own rows once the app has started.

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


def seed_api_keys(*secrets: str) -> None:
    """Write caller-key rows straight into llmhub.db.

    Caller keys are minted at /admin and hashed into llmhub.db; no environment variable,
    key file or config node can seed them. Tests therefore create the rows themselves, and
    must do it before the app starts: an empty table with no ADMIN_PASSWORD refuses to boot.
    """
    import sqlite3

    from key_store import SCHEMA, hash_key, mask_key, now_ts

    db = Path(os.getenv("LLMHUB_DB") or Path(CONFIG_FILE).parent / "llmhub.db")
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA)
        for secret in secrets:
            conn.execute(
                "INSERT OR IGNORE INTO api_keys (hash, masked, label, source, created_at) "
                "VALUES (?, ?, '', 'console', ?)",
                (hash_key(secret), mask_key(secret), now_ts()),
            )
        conn.commit()


# --------------------------------------------------------------------------- #
# Mock Ollama upstream
# --------------------------------------------------------------------------- #
def make_handler(store: list[dict[str, Any]], generate_mode: str = "ok"):
    """Mock Ollama upstream.

    generate_mode:
      "ok"           - /api/generate returns proper yes/no logprobs
      "no_logprobs"  - emulates an Ollama that silently ignores the top-level
                       ``logprobs`` field (-> flat 0.9/0.1 text scores)
      "garbage"      - emulates a model that is not in an answering state and emits
                       multilingual junk ('ਐ', '경') instead of yes/no
    """

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

            # --- generate: plain completion (nothing asks for logprobs) ---
            # Emulates Ollama: NDJSON when `stream` is true or omitted, one JSON object
            # when it is explicitly false. Echoes the caller's fields so the test can
            # assert the gateway forwarded the body unchanged.
            if self.path.endswith("/api/generate") and not body.get("logprobs"):
                echo = {
                    "echo_raw": body.get("raw"),
                    "echo_format": body.get("format"),
                    "echo_keep_alive": body.get("keep_alive"),
                    "echo_options": body.get("options"),
                }
                final = {
                    "model": model,
                    "response": "",
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 5,
                    "eval_count": 2,
                    **echo,
                }
                if body.get("stream") is False:
                    final["response"] = "Hello"
                    self._send(final)
                else:
                    self._send(
                        [
                            {"model": model, "response": "Hel", "done": False},
                            {"model": model, "response": "lo", "done": False},
                            final,
                        ],
                        streaming=True,
                    )

            # --- rerank: logprobs mode ---
            elif self.path.endswith("/api/generate"):
                prompt = body.get("prompt", "")
                # return different yes/no probabilities per document so ordering can be verified
                if "highly relevant" in prompt:
                    yes_lp, no_lp = -0.1, -3.0
                elif "partially relevant" in prompt:
                    yes_lp, no_lp = -1.0, -1.0
                else:
                    yes_lp, no_lp = -3.0, -0.2
                answer = {
                    "model": model,
                    "response": "yes" if yes_lp > no_lp else "no",
                    "done": True,
                }
                if generate_mode == "garbage":
                    # Real-world symptom: the model is not in an answering state and
                    # "continues the document" with multilingual junk.
                    answer["response"] = "ਐ"
                    answer["logprobs"] = [
                        {
                            "token": "ਐ",
                            "logprob": -0.05,
                            "bytes": [],
                            "top_logprobs": [
                                {"token": "ਐ", "logprob": -0.05, "bytes": []},
                                {"token": " 경", "logprob": -2.1, "bytes": []},
                            ],
                        }
                    ]
                elif generate_mode == "ok":
                    answer["logprobs"] = [
                        {
                            "token": "yes",
                            "logprob": yes_lp,
                            "bytes": [],
                            "top_logprobs": [
                                {"token": "yes", "logprob": yes_lp, "bytes": []},
                                {"token": " no", "logprob": no_lp, "bytes": []},
                            ],
                        }
                    ]
                self._send(answer)

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
    """Start four mock upstreams.

    * mock-a / mock-b simulate a normal multi-instance deployment
    * mock-c simulates an Ollama that returns no logprobs on /api/generate
    * mock-d simulates a reranker that emits junk tokens instead of yes/no
    """
    store_a: list[dict[str, Any]] = []
    store_b: list[dict[str, Any]] = []
    store_c: list[dict[str, Any]] = []
    store_d: list[dict[str, Any]] = []

    server_a = HTTPServer(("127.0.0.1", 0), make_handler(store_a))
    server_b = HTTPServer(("127.0.0.1", 0), make_handler(store_b))
    server_c = HTTPServer(("127.0.0.1", 0), make_handler(store_c, generate_mode="no_logprobs"))
    server_d = HTTPServer(("127.0.0.1", 0), make_handler(store_d, generate_mode="garbage"))
    for server in (server_a, server_b, server_c, server_d):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return (
        (server_a, store_a, f"http://127.0.0.1:{server_a.server_address[1]}"),
        (server_b, store_b, f"http://127.0.0.1:{server_b.server_address[1]}"),
        (server_c, store_c, f"http://127.0.0.1:{server_c.server_address[1]}"),
        (server_d, store_d, f"http://127.0.0.1:{server_d.server_address[1]}"),
    )


# --------------------------------------------------------------------------- #
# Main test flow
# --------------------------------------------------------------------------- #
def main() -> int:
    (srv_a, store_a, url_a), (srv_b, store_b, url_b), (srv_c, store_c, url_c), (srv_d, store_d, url_d) = start_mock()

    config = {
        "server": {"host": "127.0.0.1", "port": 8000, "log_level": "warning"},
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
            {
                # "Old Ollama": /api/generate silently drops the logprobs field.
                "name": "mock-c",
                "base_url": url_c,
                "models": ["reranker-old:4b"],
                "timeout": 30,
            },
            {
                # Reranker that answers with junk instead of yes/no.
                "name": "mock-d",
                "base_url": url_d,
                "models": ["reranker-broken:4b"],
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
            "models": ["qwen3-reranker:4b"],
            "normalize": True,
            "max_concurrency": 4,
            "return_documents": True,
        },
        "security": {
            "internal": {"allow_anonymous": False, "expose_version": True, "expose_docs": True},
            "public": {"port": None, "path_allowlist": {}},
        },
    }

    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(config, fh)

    from fastapi.testclient import TestClient
    from main import app

    print("\n=== LLM Hub smoke tests ===\n")

    # Keys are minted in the console only, so the fixture writes the rows itself - before the
    # lifespan runs, because an empty table with no ADMIN_PASSWORD refuses to start.
    seed_api_keys("sk-test-1", "sk-test-2")

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
        r = client.get("/")
        check("GET / returns 200 without a key", r.status_code == 200, f"got {r.status_code}: {r.text[:120]}")
        if r.status_code == 200:
            system = r.json()
            check(
                "/ returns the system name",
                bool(system.get("name")),
                json.dumps(system, ensure_ascii=False),
            )
            check(
                "/ returns the gateway version",
                bool(system.get("version")),
                json.dumps(system, ensure_ascii=False),
            )
        r = client.get("/healthz")
        check("GET /healthz no longer maps to the health route (401)", r.status_code == 401, f"got {r.status_code}")

        # -------------------------------------------------------- Authentication ---
        print("[2] API key authentication")
        r = client.get("/v1/models")
        check("Missing key returns 401", r.status_code == 401, f"got {r.status_code}")
        r = client.get("/v1/models", headers={"Authorization": "Bearer wrong-key"})
        check("Invalid key returns 403", r.status_code == 403, f"got {r.status_code}")
        r = client.get("/v1/models", headers={"X-API-Key": "sk-test-2"})
        check("The second key and the X-API-Key header work", r.status_code == 200, f"got {r.status_code}")

        # --------------------------- zone split: internal vs public listener ---
        print("[2b] zone split (internal / public listener)")
        # TestClient's scope["server"] port is 80. Declaring 80 as security.public.port
        # routes EVERY request on this client into the untrusted zone without binding a
        # second real socket (the same trick verify_security.py uses). zone() reads the
        # kernel-assigned listener from config, never a header or a client IP.
        manager = getattr(client.app.state, "manager", None)
        sec = manager.config.security
        saved_port = sec.public.port
        saved_allowlist = dict(sec.public.path_allowlist)
        saved_anon = sec.internal.allow_anonymous
        saved_pw = os.environ.get("ADMIN_PASSWORD")
        try:
            sec.public.port = 80
            sec.public.path_allowlist = {
                "GET": [r"^/v1/models$"],
                "POST": [r"^/api/generate$"],
            }
            sec.internal.allow_anonymous = True  # must NOT soften the public zone

            # Only configured method+path pairs exist on the public listener.
            check("public: listed GET with a key -> 200",
                  client.get("/v1/models", headers=good).status_code == 200)
            check("public: listed POST with a key is forwarded (not 401/404)",
                  client.post("/api/generate", headers=good,
                              json={"model": "qwen3.5:4b", "prompt": "hi", "stream": False}).status_code == 200)
            check("public: listed path without a key -> 401",
                  client.get("/v1/models").status_code == 401)
            check("public: wrong key -> 403",
                  client.get("/v1/models", headers={"Authorization": "Bearer wrong-key"}).status_code == 403)
            check("public: unlisted path -> 404 even with a key",
                  client.get("/api/tags", headers=good).status_code == 404)
            check("public: listed path with the wrong verb -> 404",
                  client.post("/v1/models", headers=good, json={}).status_code == 404)
            check("public: an unlisted POST endpoint (/v1/messages) -> 404",
                  client.post("/v1/messages", headers=good, json={}).status_code == 404)

            # Red lines config cannot open: management, console, probes, docs.
            for hidden in ("/", "/health", "/healthz", "/probe", "/docs", "/openapi.json",
                           "/api/ps", "/api/version", "/v1/management/config", "/admin"):
                check(f"public hides {hidden} -> 404",
                      client.get(hidden, headers=good).status_code == 404)

            # A forged XFF does not change the zone or bypass the key check.
            check("public: a forged internal XFF cannot bypass the key check",
                  client.get("/v1/models", headers={"X-Forwarded-For": "192.168.10.5"}).status_code == 401)

            # A console session is a credential in its own right, even on public.
            from admin_auth import ADMIN_COOKIE_NAME, create_admin_token

            os.environ["ADMIN_PASSWORD"] = "zone-test-admin-password-123456"
            token = create_admin_token(os.environ["ADMIN_PASSWORD"])
            check("public: a valid console session unlocks a listed path",
                  client.get("/v1/models", cookies={ADMIN_COOKIE_NAME: token}).status_code == 200)
            check("public: a forged console session does not",
                  client.get("/v1/models", cookies={ADMIN_COOKIE_NAME: "1.deadbeef"}).status_code == 401)

            # Back to a single internal socket: allow_anonymous is the only toggle.
            sec.public.port = None
            sec.internal.allow_anonymous = True
            check("internal: allow_anonymous=true is key-free",
                  client.get("/v1/models").status_code == 200)
            check("internal: /health is anonymous either way",
                  client.get("/health").status_code == 200)
            sec.internal.allow_anonymous = False
            check("internal: allow_anonymous=false needs a key",
                  client.get("/v1/models").status_code == 401)
            check("internal: /health stays anonymous for the container probe",
                  client.get("/health").status_code == 200)
            check("internal: /healthz needs a key",
                  client.get("/healthz").status_code == 401)
            check("internal: a forged XFF does not unlock the API",
                  client.get("/v1/models", headers={"X-Forwarded-For": "10.1.2.3"}).status_code == 401)
        finally:
            sec.public.port = saved_port
            sec.public.path_allowlist = saved_allowlist
            sec.internal.allow_anonymous = saved_anon
            if saved_pw is None:
                os.environ.pop("ADMIN_PASSWORD", None)
            else:
                os.environ["ADMIN_PASSWORD"] = saved_pw

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
        meta = result.get("meta", {})
        check("meta reports that real logprobs were used", meta.get("logprobs_ok") is True, json.dumps(meta))
        check("meta reports the endpoint style", meta.get("endpoint") == "generate", str(meta.get("endpoint")))
        check(
            "no document had to use the text fallback",
            meta.get("text_fallbacks") == 0,
            str(meta.get("text_fallbacks")),
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
        check(
            "unparsable text scores 0.0 instead of a misleading 0.5",
            text_fallback_score("ਐ", "yes", "no") == 0.0,
            str(text_fallback_score("ਐ", "yes", "no")),
        )
        zh_resp = {
            "logprobs": [
                {
                    "token": " 是",
                    "logprob": -0.1,
                    "top_logprobs": [
                        {"token": " 是", "logprob": -0.1},
                        {"token": " 否", "logprob": -4.0},
                    ],
                }
            ]
        }
        score = yes_no_probability(extract_positions(zh_resp), "yes", "no", ["是"], ["否"])
        check("configured yes/no aliases are honoured", score is not None and score > 0.9, str(score))

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

        # ------------------------------------- Rerank: logprobs fallback chain ---
        print("[7e] Rerank - upstream without logprobs on /api/generate")
        store_c.clear()
        r = client.post(
            "/v1/rerank",
            headers=good,
            json={
                "model": "reranker-old:4b",
                "query": "what is the capital of France",
                "documents": [
                    "highly relevant document about Paris",
                    "totally unrelated content",
                ],
            },
        )
        check(
            "rerank survives an upstream that drops logprobs",
            r.status_code == 200,
            f"got {r.status_code}: {r.text[:200]}",
        )
        meta = r.json().get("meta", {}) if r.status_code == 200 else {}
        paths = [c["path"] for c in store_c]
        check(
            "falls back to /api/chat when /api/generate returns no logprobs",
            any(p.endswith("/api/chat") for p in paths),
            str(paths),
        )
        check("meta reports the endpoint that finally worked", meta.get("endpoint") == "chat", json.dumps(meta))
        check("scores come from real logprobs after the fallback", meta.get("logprobs_ok") is True, json.dumps(meta))
        check(
            "ordering is preserved after the fallback",
            [x["index"] for x in r.json().get("results", [])] == [0, 1],
            str([(x["index"], x["relevance_score"]) for x in r.json().get("results", [])]),
        )

        # ------------------------------- Rerank: junk tokens instead of yes/no ---
        print("[7f] Rerank - model answers with garbage tokens")
        store_d.clear()
        r = client.post(
            "/v1/rerank",
            headers=good,
            json={
                "model": "reranker-broken:4b",
                "query": "what is the capital of France",
                "documents": [
                    "highly relevant document about Paris",
                    "totally unrelated content",
                ],
            },
        )
        check("rerank returns 200 for a rambling model", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        meta = r.json().get("meta", {}) if r.status_code == 200 else {}
        check(
            "junk logprobs trigger the next endpoint style",
            any(c["path"].endswith("/api/chat") for c in store_d),
            str([c["path"] for c in store_d]),
        )
        check("the healthy endpoint is reported", meta.get("endpoint") == "chat", json.dumps(meta))
        check("scores still come from real logprobs", meta.get("logprobs_ok") is True, json.dumps(meta))
        check(
            "no document was scored by the text fallback",
            meta.get("text_fallbacks") == 0,
            json.dumps(meta),
        )

        config["rerank"]["normalize"] = True
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(config, fh)
        if manager:
            manager.reload()

        # ------------------------------------------------ Rerank: embedding ---
        print("[8] Rerank - embedding mode has been removed")
        config["rerank"]["model_modes"] = {"qwen3-reranker:4b": "embedding"}
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(config, fh)
        if manager:
            manager.reload()
        r = client.post(
            "/v1/rerank",
            headers=good,
            json={
                "model": "qwen3-reranker:4b",
                "query": "test query",
                "documents": ["highly relevant", "unrelated"],
            },
        )
        check("embedding mode is rejected with 400", r.status_code == 400, f"got {r.status_code}: {r.text[:200]}")
        check(
            "the error explains why embedding mode was removed",
            "removed" in str(r.json().get("detail", "")).lower(),
            str(r.json().get("detail"))[:160],
        )
        config["rerank"]["model_modes"] = {}
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(config, fh)
        if manager:
            manager.reload()

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
        print("[10] Security - admin endpoints are blocked on the public zone only")
        # The internal listener is a trusted LAN socket: model management is forwarded,
        # so the gateway must not answer with its admin-block message here.
        r = client.post("/api/delete", headers=good, json={"name": "some-model"})
        check(
            "internal /api/delete is forwarded, not admin-blocked",
            not (r.status_code == 403 and "public gateway" in r.text),
            f"got {r.status_code}: {r.text[:120]}",
        )

        # On the public listener the same call is refused - but only once it has passed
        # the allowlist, so allow /api/* on purpose for this probe.
        live = client.app.state.manager.config
        saved_public_port = live.security.public.port
        saved_allowlist = {verb: list(pats) for verb, pats in live.security.public.path_allowlist.items()}
        try:
            live.security.public.port = 80
            live.security.public.path_allowlist = {"POST": [r"^/api/.*$"]}
            for path in ("/api/delete", "/api/pull", "/api/create", "/api/blobs/sha256:abc"):
                rr = client.post(path, headers=good, json={"name": "some-model"})
                check(f"public {path} is admin-blocked (403)", rr.status_code == 403, f"got {rr.status_code}")
            rr = client.post("/api/chat", headers=good, json={"model": "qwen3.5:4b", "messages": []})
            check("public /api/chat passes the admin filter", rr.status_code != 403, f"got {rr.status_code}")
        finally:
            live.security.public.port = saved_public_port
            live.security.public.path_allowlist = saved_allowlist

        # ------------------------- Security: the catch-all is not an open proxy ---
        print("[10b] Security - only declared API paths reach an upstream")
        from proxy import FORWARDABLE_PATHS, is_forwardable

        check("Ollama inference paths are forwardable",
              {"api/generate", "api/chat", "api/embed", "api/embeddings", "api/show", "api/version"}
              <= FORWARDABLE_PATHS, str(sorted(FORWARDABLE_PATHS)))
        check("OpenAI-compatible paths are forwardable",
              {"v1/chat/completions", "v1/completions", "v1/embeddings"} <= FORWARDABLE_PATHS)
        check("model management stays in the set so admin_endpoints decides it",
              {"api/pull", "api/push", "api/create", "api/delete", "api/copy"} <= FORWARDABLE_PATHS)
        check("blob upload matches by prefix", is_forwardable("api/blobs/sha256:abc"))
        check("a bare api/blobs is not a route", not is_forwardable("api/blobs"))
        # /api/tags and /api/ps are answered by the gateway itself (aggregated), and
        # /v1/rerank is its own service - none of them is ever put on an upstream's wire.
        for local in ("api/tags", "api/ps", "v1/models", "v1/rerank", "admin/login", "anything"):
            check(f"{local} is never forwarded", not is_forwardable(local))

        r = client.post("/api/some-private-route", headers=good, json={"model": "qwen3.5:4b"})
        check("an undeclared path is 404, not proxied (internal)",
              r.status_code == 404 and "not part of this gateway" in r.text,
              f"got {r.status_code}: {r.text[:160]}")
        r = client.get("/api/some-private-route", headers=good)
        check("an undeclared GET is 404 too (internal)", r.status_code == 404, f"got {r.status_code}")
        r = client.post("/api/generate", headers=good,
                        json={"model": "qwen3.5:4b", "prompt": "hi", "stream": False})
        check("a declared path still forwards", r.status_code == 200, f"got {r.status_code}: {r.text[:120]}")

        # `routing.strict: true` refuses an unknown *model*; it must not veto the Ollama
        # admin calls, whose body carries `name` and which therefore have no model to
        # route by. Otherwise `admin_endpoints: allow` + an allowlist entry would still
        # dead-end in "No upstream configured for model ''".
        _live = client.app.state.manager.config
        _saved_strict = _live.routing.strict
        try:
            _live.routing.strict = True
            r = client.post("/api/pull", headers=good, json={"name": "some-model"})
            check("strict routing still forwards a model-less admin call",
                  not (r.status_code == 404 and "No upstream configured" in r.text),
                  f"got {r.status_code}: {r.text[:120]}")
            r = client.post("/api/chat", headers=good,
                            json={"model": "no-such-model:1", "messages": [], "stream": False})
            check("strict routing still refuses an unknown model",
                  r.status_code == 404 and "No upstream configured" in r.text,
                  f"got {r.status_code}: {r.text[:120]}")
        finally:
            _live.routing.strict = _saved_strict

        # `admin_endpoints: allow` must forward *everything*, blob uploads included. The
        # blob path used to stay blocked because the prefix test sat outside the mode list.
        live = client.app.state.manager.config
        saved_public_port = live.security.public.port
        saved_allowlist = {verb: list(pats) for verb, pats in live.security.public.path_allowlist.items()}
        saved_admin_mode = live.security.public.admin_endpoints
        try:
            live.security.public.port = 80
            live.security.public.path_allowlist = {"POST": [r"^/api/.*$"]}
            for mode, should_block in (("deny", True), ("readonly", True), ("allow", False)):
                live.security.public.admin_endpoints = mode
                rr = client.post("/api/blobs/sha256:deadbeef", headers=good, json={"name": "m"})
                blocked = rr.status_code == 403 and "public gateway" in rr.text
                check(f"public blob upload blocked under admin_endpoints={mode}",
                      blocked is should_block, f"got {rr.status_code}: {rr.text[:120]}")
            # A path the gateway does not serve stays 404 even when the allowlist lists it:
            # the allowlist opens a route, it does not invent one.
            live.security.public.admin_endpoints = "allow"
            rr = client.post("/api/some-private-route", headers=good, json={"model": "qwen3.5:4b"})
            check("a listed but undeclared path is still 404 on the public zone",
                  rr.status_code == 404, f"got {rr.status_code}: {rr.text[:120]}")
        finally:
            live.security.public.port = saved_public_port
            live.security.public.path_allowlist = saved_allowlist
            live.security.public.admin_endpoints = saved_admin_mode

        # ------------------- Schema-visible routes: POST only, on every method ---
        print("[10c] Every documented pass-through route answers 405 for non-POST")
        for documented in ("/api/generate", "/api/chat", "/v1/chat/completions", "/v1/completions",
                           "/api/embed", "/v1/embeddings", "/v1/messages", "/v1/message"):
            rr = client.get(documented, headers=good)
            check(f"GET {documented} -> 405", rr.status_code == 405, f"got {rr.status_code}")
            check(f"{documented} 405 advertises Allow: POST",
                  rr.headers.get("allow") == "POST", str(dict(rr.headers)))
            rr = client.put(documented, headers=good, json={})
            check(f"PUT {documented} -> 405", rr.status_code == 405, f"got {rr.status_code}")

        # ----------------------------------------------- Security: unit-level checks ---
        print("[11] Security - unit checks")
        from auth import find_weak_keys
        from config import SecurityConfig

        STRONG = "xK9mP2qL8vR4nB7wT1yC5uH3jF6sD0eA"  # 32 random-looking chars, high entropy
        wk = find_weak_keys(["sk-change-me-001", "short", STRONG])
        check("find_weak_keys flags placeholder/short keys", len(wk) == 2, str(wk))
        check("find_weak_keys accepts a strong key", len(find_weak_keys([STRONG])) == 0, "unexpected")

        deny = SecurityConfig(public={"admin_endpoints": "deny"})
        check("deny blocks api/delete on the public zone", deny.is_path_blocked("api/delete", public=True))
        check("deny blocks api/pull on the public zone", deny.is_path_blocked("api/pull", public=True))
        check("deny blocks api/blobs/* on the public zone",
              deny.is_path_blocked("api/blobs/sha256:abc", public=True))
        check("deny allows api/chat", not deny.is_path_blocked("api/chat", public=True))
        check("the internal zone is never admin-filtered",
              not deny.is_path_blocked("api/delete", public=False))
        readonly = SecurityConfig(public={"admin_endpoints": "readonly"})
        check("readonly allows api/pull on the public zone",
              not readonly.is_path_blocked("api/pull", public=True))
        check("readonly blocks api/delete on the public zone",
              readonly.is_path_blocked("api/delete", public=True))
        allow = SecurityConfig(public={"admin_endpoints": "allow"})
        check("allow forwards everything on the public zone",
              not allow.is_path_blocked("api/delete", public=True))
        check("allow forwards blob uploads too",
              not allow.is_path_blocked("api/blobs/sha256:abc", public=True))
        check("blocked_paths still applies in both zones",
              SecurityConfig(blocked_paths=["api/show"]).is_path_blocked("api/show", public=False))
        check("a legacy root admin_endpoints migrates into public",
              SecurityConfig(**{"admin_endpoints": "readonly"}).public.admin_endpoints == "readonly")

        # --------------------------------------------------- Security: CORS / docs ---
        print("[12] Security - CORS and docs defaults")
        r = client.get("/v1/models", headers=good)
        check(
            "Default config sends no Access-Control-Allow-Origin header",
            "access-control-allow-origin" not in r.headers,
            str(dict(r.headers)),
        )
        check("internal docs visible when expose_docs=true",
              client.get("/docs").status_code == 200, f"got {client.get('/docs').status_code}")
        _sec = manager.config.security.internal if (manager := getattr(client.app.state, "manager", None)) else None
        _saved_docs = _sec.expose_docs
        try:
            _sec.expose_docs = False
            check("internal docs hidden when expose_docs=false",
                  client.get("/docs").status_code == 404, f"got {client.get('/docs').status_code}")
            check("openapi.json hidden too when expose_docs=false",
                  client.get("/openapi.json").status_code == 404, f"got {client.get('/openapi.json').status_code}")
        finally:
            _sec.expose_docs = _saved_docs

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
        from config import SecurityConfig
        from config import build_default_config

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

        # ------------------------------- API keys live only in llmhub.db ---
        print("[16] API keys come from the console, never from the environment")
        import config as config_module

        check(
            "no environment seed exists in config.py",
            not hasattr(config_module, "ENV_API_KEYS")
            and not hasattr(config_module, "collect_env_api_keys")
            and not hasattr(config_module, "set_api_keys")
            and not hasattr(config_module, "get_api_keys")
            and not hasattr(config_module, "default_keys_file"),
            "GATEWAY_API_KEYS and the key-file seed must be gone",
        )
        check(
            "Config has no api_keys attribute any more",
            not hasattr(GatewayConfig(), "api_keys"),
            "config.yaml and the environment are not key sources",
        )

        # A stale environment variable must not resurrect a key.
        os.environ["GATEWAY_API_KEYS"] = "envKey-cccc3333"
        try:
            migrated = GatewayConfig(
                upstreams=[{"name": "u", "base_url": "http://127.0.0.1:1", "models": ["m"]}],
                auth={"enabled": True, "api_keys": ["yamlKey-dddd4444"]},
            )
            check(
                "the removed auth node is migrated, never kept on the config",
                not hasattr(migrated, "auth"),
            )
            check(
                "auth.enabled=true maps to security.internal.allow_anonymous=false",
                migrated.security.internal.allow_anonymous is False,
            )
            check(
                "a stale GATEWAY_API_KEYS creates no key",
                not hasattr(migrated, "api_keys"),
                "the environment must not be a key source",
            )
        finally:
            os.environ.pop("GATEWAY_API_KEYS", None)

        # The store itself is the single source of truth: a row added there verifies,
        # and nothing else can add one.
        store = client.app.state.api_keys
        check(
            "the fixture keys are in the store",
            store.verify("sk-test-1") and store.verify("sk-test-2"),
            f"count={store.count()}",
        )
        check(
            "an unknown key never verifies",
            not store.verify("sk-env-only-key-000000"),
        )

        defaults = build_default_config()
        check(
            "default config template has no legacy auth node",
            "auth" not in defaults,
            str(sorted(defaults)),
        )
        check(
            "default security has internal + public zone nodes",
            {"internal", "public"} <= set(defaults["security"]),
            str(sorted(defaults["security"])),
        )
        check(
            "default public path_allowlist is a method->regex dict",
            isinstance(defaults["security"]["public"]["path_allowlist"], dict)
            and all(isinstance(v, list) for v in defaults["security"]["public"]["path_allowlist"].values()),
            str(defaults["security"]["public"]["path_allowlist"]),
        )
        check(
            "default public listener is closed (port null)",
            defaults["security"]["public"]["port"] is None,
        )
        check(
            "legacy security fields were removed from the config model",
            not ({"allowed_http_methods", "path_allowlist", "expose_version", "expose_health_details"}
                 & set(SecurityConfig.model_fields)),
            str(sorted(SecurityConfig.model_fields)),
        )
        check(
            "AuthConfig model was removed with the auth node",
            not hasattr(__import__("config"), "AuthConfig"),
        )

        # --------------------------- legacy auth/security migration to two zones ---
        print("[16b] legacy auth/security config migration")
        legacy = {
            "server": {"port": 8000},
            "auth": {"enabled": True, "allow_anonymous_health": True,
                     "trusted_networks": ["10.0.0.0/8"],
                     "allow_access_ip_whitelist": [r"^10\.0\.0\.1$"],
                     "api_keys": ["sk-legacy-ignored"]},
            "security": {"expose_version": False, "allowed_http_methods": ["GET", "POST"],
                         "path_allowlist": [r"^/v1/models$"], "expose_health_details": True},
        }
        migrated = GatewayConfig(**legacy)
        check("the auth node is folded away", not hasattr(migrated, "auth"))
        check("auth.enabled=true -> internal.allow_anonymous=false",
              migrated.security.internal.allow_anonymous is False)
        zoned = GatewayConfig(**{**legacy, "security": {**legacy["security"],
                                                       "public": {"port": 8001}}})
        check("auth.enabled=true + public listener -> internal stays anonymous",
              zoned.security.internal.allow_anonymous is True)
        check("legacy expose_version -> internal.expose_version",
              migrated.security.internal.expose_version is False)
        check("legacy global path_allowlist -> public method->regex dict",
              migrated.security.public.path_allowlist
              == {"GET": [r"^/v1/models$"], "POST": [r"^/v1/models$"]},
              str(migrated.security.public.path_allowlist))
        check("migration never opens a public listener", migrated.security.public.port is None)
        check("legacy api_keys never enter the runtime key list",
              not hasattr(migrated, "api_keys"))
        check("listen_ports() is just the internal socket", migrated.listen_ports() == [8000])
        check("public_ports() is empty after migration", migrated.public_ports() == frozenset())

        from pydantic import ValidationError

        try:
            GatewayConfig(**{**legacy, "security": {"public": {"path_allowlist": {"FETCH": [r"^/x$"]}}}})
            check("an unknown HTTP verb in path_allowlist is rejected", False, "expected ValidationError")
        except ValidationError:
            check("an unknown HTTP verb in path_allowlist is rejected", True)
        invalid_regex = GatewayConfig(
            **{**legacy, "security": {"public": {"path_allowlist": {"GET": ["([", r"^/ok$"]}}}}
        )
        check("an invalid regex is dropped, the valid one kept",
              invalid_regex.security.public.path_allowlist == {"GET": [r"^/ok$"]},
              str(invalid_regex.security.public.path_allowlist))

        # ------------------------------- console session cookie is an API credential ---
        print("[16c] console session cookie / session lifecycle")
        from admin_auth import (
            ADMIN_COOKIE_NAME,
            _mint,
            _sign,
            create_admin_token,
            refresh_due,
            session_remaining,
            session_ttl,
            verify_admin_token,
        )

        saved_pw = os.environ.get("ADMIN_PASSWORD")
        # The single TestClient socket is the internal zone. Its allow_anonymous is false
        # in this fixture, so every business call needs a key OR a valid console session.
        os.environ["ADMIN_PASSWORD"] = "test-admin-password-123456"
        pw = os.environ["ADMIN_PASSWORD"]
        token = create_admin_token(pw)
        try:
            check("a valid console session unlocks the API",
                  client.get("/v1/models", cookies={ADMIN_COOKIE_NAME: token}).status_code == 200)
            check("a forged console session does not",
                  client.get("/v1/models", cookies={ADMIN_COOKIE_NAME: "1.deadbeef"}).status_code == 401)
            past = int(time.time()) - 10
            expired = _mint(past, pw)
            check("an expired console session does not",
                  client.get("/v1/models", cookies={ADMIN_COOKIE_NAME: expired}).status_code == 401)
            check("session_remaining reports a live session",
                  (session_remaining(token, pw) or 0) > session_ttl() // 2)
            check("a fresh session is not due for renewal", refresh_due(token, pw) is False)
            check("verify_admin_token rejects a tampered token",
                  verify_admin_token("1.deadbeef", pw) is False)

            # Per-session salt: the cookie exposes `sid` but never the signing key.
            sid = token.split(".")[0]
            check("each session mints a fresh session id",
                  create_admin_token(pw).split(".")[0] != sid)
            swapped = token.split(".")
            swapped[0] = "AAAAAAAAAAAAAAAAAAAAAA"
            check("changing the session id breaks the signature",
                  verify_admin_token(".".join(swapped), pw) is False)
            legacy_expiry = int(time.time()) + 999
            legacy = f"{sid}.{legacy_expiry}.{_sign(f'{sid}.{legacy_expiry}', pw)}"
            check("a cookie signed with ADMIN_PASSWORD alone is no longer valid",
                  verify_admin_token(legacy, pw) is False)
            check("a cookie signed with a different password is rejected",
                  verify_admin_token(_mint(legacy_expiry, "not-the-admin-password"), pw) is False)

            # Sliding renewal: a session past half its TTL gets a fresh cookie.
            due_expiry = int(time.time()) + session_ttl() // 4
            due = _mint(due_expiry, pw)
            check("a session past half its TTL is due for renewal", refresh_due(due, pw) is True)
            renewed = client.get("/v1/models", cookies={ADMIN_COOKIE_NAME: due})
            check("middleware re-issues the session cookie",
                  renewed.cookies.get(ADMIN_COOKIE_NAME) not in (None, due),
                  str(renewed.cookies.get(ADMIN_COOKIE_NAME)))
        finally:
            if saved_pw is None:
                os.environ.pop("ADMIN_PASSWORD", None)
            else:
                os.environ["ADMIN_PASSWORD"] = saved_pw
            os.environ.pop("ADMIN_SESSION_TTL", None)

        # ------------------------- OpenCode gateway upstream type + web search config ---
        print("[17] OpenCode upstream type and web search config")
        oc_cfg = GatewayConfig(
            upstreams=[
                {"name": "ollama", "base_url": "http://127.0.0.1:11434", "models": ["qwen3:8b"]},
                {
                    "name": "opencode",
                    "type": "opencode",
                    "base_url": "http://127.0.0.1:11430",
                    "models": ["opencode/*"],
                },
            ]
        )
        oc_up = oc_cfg.upstreams[1]
        check("opencode: type parsed", oc_up.type == "opencode", oc_up.type)
        check("opencode: discovers models via /v1/models", oc_up.tags_path() == "v1/models", oc_up.tags_path())
        check("opencode: probes via /health", oc_up.health_path() == "health", oc_up.health_path())
        check(
            "opencode: parses data[].id (OpenAI format)",
            oc_up.parse_model_names({"object": "list", "data": [{"id": "opencode/big-pickle"}]})
            == ["opencode/big-pickle"],
        )
        check(
            "wildcard routes the provider's opencode/* models to it",
            oc_cfg.find_upstream_for_model("opencode/mimo-v2.6-flash-free") is oc_up,
        )
        check(
            "opencode models reach the aggregated list",
            any(i["id"] == "opencode/*" for i in oc_cfg.aggregated_models()),
        )
        check("websearch: disabled by default", oc_cfg.websearch.enabled is False)
        check("websearch: provider path defaults to v1/web-search", oc_cfg.websearch.path == "v1/web-search", oc_cfg.websearch.path)
        check("websearch: unsigned by default", oc_cfg.websearch.api_key_env == "", repr(oc_cfg.websearch.api_key_env))
        check("websearch: unnamed selection picks the first opencode upstream", oc_cfg.websearch_upstream_obj() is oc_up)
        oc_cfg.websearch.upstream = "opencode"
        check("websearch: explicit upstream name resolves", oc_cfg.websearch_upstream_obj() is oc_up)
        oc_cfg.websearch.upstream = "missing"
        check("websearch: unknown upstream name never falls back", oc_cfg.websearch_upstream_obj() is None)
        check(
            "default config template ships a websearch block",
            "websearch" in build_default_config(),
            str(sorted(build_default_config())),
        )

        # ------------------------- Ollama-native text generation (/api/generate) ---
        print("[18] Ollama-native text generation (/api/generate)")
        store_a.clear()
        store_b.clear()
        r = client.post(
            "/api/generate",
            headers=good,
            json={
                "model": "qwen3.5:4b",
                "prompt": "Say hello in one word:",
                "raw": True,
                "format": "json",
                "keep_alive": "3m",
                "options": {"temperature": 0, "num_predict": 8},
                "stream": False,
            },
        )
        check("POST /api/generate returns 200", r.status_code == 200, f"got {r.status_code}: {r.text[:160]}")
        payload = r.json() if r.status_code == 200 else {}
        check(
            "non-stream answer is a single JSON object",
            payload.get("done") is True and isinstance(payload.get("response"), str),
            r.text[:160],
        )
        sent = store_a[-1] if store_a else {}
        check("upstream received /api/generate", str(sent.get("path", "")).endswith("/api/generate"), str(sent.get("path")))
        check(
            "model is routed to its own upstream",
            len(store_a) == 1 and sent.get("body", {}).get("model") == "qwen3.5:4b",
            f"a={len(store_a)} b={len(store_b)}",
        )
        check(
            "prompt/raw/format/keep_alive pass through unchanged",
            sent.get("body", {}).get("prompt") == "Say hello in one word:"
            and sent["body"].get("raw") is True
            and sent["body"].get("format") == "json"
            and sent["body"].get("keep_alive") == "3m",
            str(sent.get("body")),
        )
        check(
            "options are forwarded",
            (sent.get("body", {}).get("options") or {}).get("num_predict") == 8,
            str(sent.get("body", {}).get("options")),
        )
        check(
            "upstream fields reach the caller",
            payload.get("echo_raw") is True and payload.get("echo_format") == "json",
            str(payload)[:160],
        )

        # alias resolution + forced parameter injection share the chat pipeline
        store_a.clear()
        store_b.clear()
        r = client.post(
            "/api/generate",
            headers=good,
            json={"model": "big-nothink", "prompt": "hi", "stream": False, "think": True},
        )
        check("alias request still answers 200", r.status_code == 200, f"got {r.status_code}: {r.text[:120]}")
        check(
            "alias pins the upstream named in the alias",
            len(store_b) == 1 and not store_a,
            f"a={len(store_a)} b={len(store_b)}",
        )
        bsent = store_b[-1]["body"] if store_b else {}
        check("alias model is replaced by the real name", bsent.get("model") == "big-model:72b", str(bsent.get("model")))
        check(
            "alias parameters are injected over the client's",
            bsent.get("think") is False and bsent.get("num_ctx") == 8192,
            f"think={bsent.get('think')} num_ctx={bsent.get('num_ctx')}",
        )

        # streaming: explicit and Ollama's default (stream omitted)
        r = client.post(
            "/api/generate", headers=good, json={"model": "qwen3.5:4b", "prompt": "count", "stream": True}
        )
        lines = [ln for ln in r.text.splitlines() if ln.strip()]
        check("stream=true returns NDJSON", r.headers.get("content-type", "").startswith("application/x-ndjson"), str(r.headers.get("content-type")))
        check("stream=true yields 3 chunks", len(lines) == 3, f"lines={len(lines)}")
        check("every streamed line is valid JSON", all(json.loads(ln) for ln in lines), r.text[:120])
        check("last streamed chunk is done", json.loads(lines[-1]).get("done") is True, r.text[-120:])

        r = client.post("/api/generate", headers=good, json={"model": "qwen3.5:4b", "prompt": "count"})
        lines = [ln for ln in r.text.splitlines() if ln.strip()]
        check(
            "streaming is the default when 'stream' is omitted",
            len(lines) == 3 and r.headers.get("content-type", "").startswith("application/x-ndjson"),
            f"lines={len(lines)} ct={r.headers.get('content-type')}",
        )

        # auth and method guards
        r = client.post("/api/generate", json={"model": "qwen3.5:4b", "prompt": "hi", "stream": False})
        check("POST /api/generate without a key is rejected", r.status_code == 401, f"got {r.status_code}")
        r = client.get("/api/generate", headers=good)
        check("GET /api/generate gets 405 instead of being proxied", r.status_code == 405, f"got {r.status_code}: {r.text[:120]}")
        check("the 405 advertises Allow: POST", r.headers.get("allow") == "POST", str(dict(r.headers)))

        # ---------------------------------------------------- Real E2E ---
        if os.getenv("E2E") == "1":
            run_e2e(client, good)

    srv_a.shutdown()
    srv_b.shutdown()
    srv_c.shutdown()
    srv_d.shutdown()

    print(f"\n=== Result: {PASSED} passed, {FAILED} failed ===\n")
    return 1 if FAILED else 0


def run_e2e(client, headers) -> None:
    """End-to-end tests against a real Ollama (requires a local instance on 11434)."""
    print("[10] Real Ollama end-to-end")

    e2e_config = {
        "server": {"host": "127.0.0.1", "port": 8000, "log_level": "warning"},
        "security": {
            "internal": {"allow_anonymous": False, "expose_version": True, "expose_docs": True},
            "public": {"port": None, "path_allowlist": {}},
        },
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
