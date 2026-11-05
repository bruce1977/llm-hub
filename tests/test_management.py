#!/usr/bin/env python3
"""
Tests for the web-console management API (``/v1/management``) and the request log.

Runs entirely against a local mock Ollama upstream: no live models, no external
services. Covers admin auth (session cookie or gateway API key; the admin password
sent as a header is rejected; 503 when
ADMIN_PASSWORD is unset), the static console served at ``/admin`` (SPA fallback,
immutable asset caching, traversal guard, 503 before a build), upstream probing and
device classification, read-only config projections (credentials masked), request-log
capture (stream + non-stream + upstream failure), stats, filters, pagination and the
path-allowlist exemption.

Run:
    python tests/test_management.py
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# --- configuration must exist before importing the app ---------------------------------
GIB = 1024**3


def _closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


MOCK_STORE: list[dict] = []


def _json(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode()


class MockHandler(BaseHTTPRequestHandler):
    """Minimal Ollama surface: health, ps, tags, chat (stream + non-stream), OpenAI chat."""

    def log_message(self, *args):  # silence the access log
        pass

    def _read(self) -> dict:
        length = int(self.headers.get("content-length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return {}

    def _send(self, payload, status: int = 200, content_type: str = "application/json"):
        body = payload if isinstance(payload, bytes) else _json(payload)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        path = self.path.split("?")[0]
        MOCK_STORE.append({"method": "HEAD", "path": path, "auth": self.headers.get("authorization")})
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        MOCK_STORE.append({"method": "GET", "path": path, "auth": self.headers.get("authorization")})
        if path.endswith("/api/version"):
            self._send({"version": "0.40.0"})
        elif path.endswith("/api/ps"):
            self._send(
                {
                    "models": [
                        {"name": "qwen3:8b", "size": 10 * GIB, "size_vram": 10 * GIB, "context_length": 8192},
                        {"name": "qwen3:4b", "size": 4 * GIB, "size_vram": 0, "context_length": 4096},
                        {"name": "qwen3-reranker:4b", "size": 8 * GIB, "size_vram": 4 * GIB, "context_length": 4096},
                        {"name": "tiny:cpu", "size": 2 * GIB, "size_vram": 0, "context_length": 2048},
                    ]
                }
            )
        elif path.endswith("/api/tags"):
            self._send(
                {
                    "models": [
                        {"name": "qwen3:8b"},
                        {"name": "qwen3:4b"},
                        {"name": "qwen3-reranker:4b"},
                        {"name": "qwen3.5:9b"},
                    ]
                }
            )
        elif path.endswith("/v1/models"):
            # OpenAI-compatible discovery, used by the opencode / lmstudio upstream types.
            self._send(
                {
                    "object": "list",
                    "data": [
                        {"id": "auto"},
                        {"id": "opencode/big-pickle"},
                        {"id": "opencode/gpt-5-mini"},
                        {"id": "other/not-matched"},
                    ],
                }
            )
        elif path.endswith("/health"):
            # The OpenCode gateway liveness endpoint, probed by upstream_detail / overview.
            self._send({"status": "ok"})
        else:
            self._send({"error": "not found"}, status=404)

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._read()
        MOCK_STORE.append({"method": "POST", "path": path, "body": body, "auth": self.headers.get("authorization")})
        if path.endswith("/api/chat"):
            if body.get("stream"):
                chunks = [
                    {"message": {"role": "assistant", "content": "po"}, "done": False},
                    {
                        "message": {"role": "assistant", "content": "ng"},
                        "done": True,
                        "prompt_eval_count": 5,
                        "eval_count": 7,
                    },
                ]
                payload = "".join(json.dumps(c) + "\n" for c in chunks).encode()
                self._send(payload, content_type="application/x-ndjson")
            else:
                self._send(
                    {
                        "model": body.get("model"),
                        "message": {"role": "assistant", "content": "pong"},
                        "done": True,
                        "prompt_eval_count": 5,
                        "eval_count": 7,
                        "_echo": body,
                    }
                )
        elif path.endswith("/v1/chat/completions"):
            if body.get("stream"):
                payload = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
                self._send(payload, content_type="text/event-stream")
            else:
                self._send(
                    {
                        "id": "chatcmpl-1",
                        "model": body.get("model"),
                        "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                        "usage": {"prompt_tokens": 4, "completion_tokens": 8, "total_tokens": 12},
                    }
                )
        elif path.endswith("/api/generate"):
            # `raw` marks the reranker's scoring prompt: answer it with the yes token the
            # scoring parser looks for, so the rerank test path stays deterministic.
            # `__huge__` is the oversized-response probe the tester truncation check uses.
            if body.get("prompt") == "__huge__":
                answer = "x" * 300000
            else:
                answer = "yes" if body.get("raw") else "hello"
            if body.get("stream"):
                chunks = [
                    {"model": body.get("model"), "response": answer[:1], "done": False},
                    {
                        "model": body.get("model"),
                        "response": answer[1:],
                        "done": True,
                        "prompt_eval_count": 4,
                        "eval_count": 3,
                    },
                ]
                payload = "".join(json.dumps(c) + "\n" for c in chunks).encode()
                self._send(payload, content_type="application/x-ndjson")
            else:
                self._send(
                    {
                        "model": body.get("model"),
                        "created_at": "2026-01-01T00:00:00Z",
                        "response": answer,
                        "done": True,
                        "prompt_eval_count": 4,
                        "eval_count": 3,
                        "_echo": body,
                    }
                )
        elif path.endswith("/api/embed"):
            self._send({"model": body.get("model"), "embeddings": [[0.1, 0.2]], "embed_dimension": 2})
        elif path.endswith("/v1/embeddings"):
            self._send(
                {
                    "object": "list",
                    "model": body.get("model"),
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                    "usage": {"prompt_tokens": 3, "total_tokens": 3},
                }
            )
        else:
            self._send({"error": "not found"}, status=404)


MOCK_SERVER = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
MOCK_PORT = MOCK_SERVER.server_address[1]
threading.Thread(target=MOCK_SERVER.serve_forever, daemon=True).start()

DEAD_PORT = _closed_port()

_TMP_DIR = tempfile.mkdtemp(prefix="llm-hub-management-")
_CONFIG_PATH = os.path.join(_TMP_DIR, "config.json")
_CONFIG = {
    "server": {"host": "127.0.0.1", "port": 8000},
    "auth": {"enabled": True, "api_keys": [], "allow_anonymous_health": True},
    "routing": {"strict": False},
    "upstreams": [
        {
            "name": "mock-ollama",
            "type": "ollama",
            "base_url": f"http://127.0.0.1:{MOCK_PORT}",
            "models": ["qwen3:8b", "qwen3-reranker:4b", "qwen3:*", "gone:1b"],
            # A pure display rename: `q8` replaces "qwen3:8b" wherever that model is shown,
            # and the real id stays callable. Contrast with root `aliases` below, which is a
            # model of its own.
            "model_aliases": {"q8": "qwen3:8b"},
            "headers": {"Authorization": "Bearer upstream-secret"},
            "description": "mock instance",
            "vram_total_gb": 24.0,
        },
        {
            "name": "dead-ollama",
            "type": "ollama",
            "base_url": f"http://127.0.0.1:{DEAD_PORT}",
            "models": ["dead:*"],
            "vram_total_gb": 8.0,
        },
    ],
    "default_upstream": "mock-ollama",
    "aliases": {
        # Declared under root `aliases` with its own parameters, so the console lists it as a
        # second model instead of renaming `qwen3:8b`.
        "q35": {
            "model": "qwen3:8b",
            "upstream": "mock-ollama",
            "params": {"think": False},
            "description": "qwen3:8b with thinking disabled",
        }
    },
    "num_ctx_overrides": {"qwen3:8b": 8192},
    "rerank": {"enabled": True, "models": ["qwen3-reranker:4b"]},
    "systemone": {
        "enabled": True,
        "backend": "http://laya.example",
        "model": "qwen3:8b",
        "api_key_env": "SYSTEMONE_SERVER_API_KEY",
        "path": "v1/systemone",
    },
    "websearch": {"enabled": False},
    "security": {
        "admin_endpoints": "deny",
        "allowed_http_methods": ["GET", "POST"],
        "path_allowlist": [],
        "rate_limit": {"enabled": False},
    },
}
with open(_CONFIG_PATH, "w", encoding="utf-8") as _fh:
    json.dump(_CONFIG, _fh, ensure_ascii=False)

os.environ["CONFIG_PATH"] = _CONFIG_PATH
os.environ["GATEWAY_API_KEYS"] = "sk-management-test-key"
os.environ["ADMIN_PASSWORD"] = "admin-secret-pw"
os.environ["SYSTEMONE_SERVER_API_KEY"] = "systemone-secret-env-key"
os.environ["REQUEST_LOG_DB"] = os.path.join(_TMP_DIR, "llmhub-test.db")
os.environ["REQUEST_LOG_RETENTION_DAYS"] = "0"

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

API_KEY = "sk-management-test-key"
GW = {"Authorization": f"Bearer {API_KEY}"}
MASK = "••••••"

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


def run() -> int:
    def patch_config(**blocks) -> None:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
        raw.update(blocks)
        with open(_CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, ensure_ascii=False)
        client.app.state.manager.reload()

    with TestClient(main.app) as client:
        # ------------------------------------------------------------ auth ---
        print("[1] admin auth")
        r = client.get("/v1/management/auth/ping")
        check("ping without credentials -> 401", r.status_code == 401, f"got {r.status_code}")
        r = client.post("/v1/management/auth/login", json={"password": "wrong"})
        check("login with a wrong password -> 401", r.status_code == 401, f"got {r.status_code}")
        r = client.post("/v1/management/auth/login", json={"password": "admin-secret-pw"})
        check("login with the admin password -> 200", r.status_code == 200, f"got {r.status_code}")
        check("login sets the session cookie", "llmhub_admin" in r.cookies or "llmhub_admin" in client.cookies)
        r = client.get("/v1/management/auth/ping")
        check("ping with the session cookie -> 200", r.status_code == 200, f"got {r.status_code}")
        r = client.post("/v1/management/auth/logout")
        check("logout -> 200", r.status_code == 200, f"got {r.status_code}")
        r = client.get("/v1/management/auth/ping")
        check("ping after logout -> 401", r.status_code == 401, f"got {r.status_code}")
        r = client.get("/v1/management/auth/ping", headers={"Authorization": "Bearer admin-secret-pw"})
        check("ping with the password as Bearer -> 401", r.status_code == 401, f"got {r.status_code}")
        r = client.get("/v1/management/auth/ping", headers=GW)
        check("ping with a gateway API key -> 200", r.status_code == 200, f"got {r.status_code}")
        r = client.get("/v1/management/overview", headers={"X-API-Key": API_KEY})
        check("overview with X-API-Key -> 200", r.status_code == 200, f"got {r.status_code}")
        r = client.get("/v1/management/overview", headers={"Authorization": "Bearer sk-not-a-real-key"})
        check("overview with an unknown key -> 401", r.status_code == 401, f"got {r.status_code}")
        r = client.get("/v1/management/overview", params={"api_key": API_KEY})
        check("a key in the query string is not accepted -> 401", r.status_code == 401, f"got {r.status_code}")
        r = client.post("/v1/management/auth/login", json={"username": "root", "password": "admin-secret-pw"})
        check("login with the wrong username -> 401", r.status_code == 401, f"got {r.status_code}")
        r = client.post("/v1/management/auth/login", json={"username": " Admin ", "password": "admin-secret-pw"})
        check("login trims and lowercases the username -> 200", r.status_code == 200, f"got {r.status_code}")
        r = client.post("/admin/login", json={"password": "admin-secret-pw"})
        check("legacy /admin/login reuses the same flow -> 200", r.status_code == 200, f"got {r.status_code}")

        # -------------------------------------------------------- overview ---
        print("[2] overview / upstream probes")
        r = client.get("/v1/management/overview")
        check("GET /overview -> 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        data = r.json()
        kpi = data.get("kpi", {})
        check("one upstream online of two", kpi.get("upstreams_online") == 1 and kpi.get("upstreams_total") == 2, str(kpi))
        # The derived `q35` row is listed next to its target but never counted: it runs inside
        # the instance already counted as qwen3:8b, so neither the KPI nor the VRAM double-counts.
        check("four running models, derived row not counted", kpi.get("running_models") == 4, str(kpi))
        check("vram used = 14.0 GiB", kpi.get("vram_used_gib") == 14.0, str(kpi))
        check("vram total = 32.0 GiB", kpi.get("vram_total_gib") == 32.0, str(kpi))
        devices = {m["model"]: m["device"] for m in data.get("running_models", [])}
        check("device classification gpu/partial/cpu", devices == {
            "qwen3:8b": "gpu",
            "q35": "gpu",
            "qwen3:4b": "cpu",
            "qwen3-reranker:4b": "partial",
            "tiny:cpu": "cpu",
        }, str(devices))
        check(
            "derived model inherits its target's device",
            devices.get("q35") == devices.get("qwen3:8b"),
            str(devices),
        )
        up = {i["name"]: i for i in data.get("upstreams", [])}
        check("mock upstream online", up["mock-ollama"]["status"] == "online", str(up.get("mock-ollama")))
        check("dead upstream offline with error", up["dead-ollama"]["status"] == "offline" and up["dead-ollama"]["error"])
        by_running = {m["model"]: m for m in data.get("running_models", [])}
        check(
            "running model is displayed under its model_aliases rename",
            by_running["qwen3:8b"]["display"] == "q8" and by_running["qwen3:8b"]["alias"] == "q8",
            str(by_running.get("qwen3:8b")),
        )
        check(
            "resident model without an alias keeps its id",
            by_running["tiny:cpu"]["display"] == "tiny:cpu" and by_running["tiny:cpu"]["alias"] is None,
            str(by_running.get("tiny:cpu")),
        )
        check(
            "derived model is its own running row",
            by_running["q35"]["display"] == "q35"
            and by_running["q35"]["alias"] is None
            and by_running["q35"]["derived"] is True
            and by_running["q35"]["target"] == "qwen3:8b",
            str(by_running.get("q35")),
        )
        check(
            "derived row shows no memory of its own",
            by_running["q35"]["size_gib"] is None and by_running["q35"]["vram_gib"] is None,
            str(by_running.get("q35")),
        )
        check(
            "a root alias never renames its target",
            by_running["qwen3:8b"]["display"] != "q35" and by_running["qwen3:8b"]["derived"] is False,
            str(by_running.get("qwen3:8b")),
        )

        print("[3] upstream listing / detail")
        r = client.get("/v1/management/upstreams")
        check("GET /upstreams -> 200", r.status_code == 200, f"got {r.status_code}")
        check("two upstreams listed", len(r.json().get("upstreams", [])) == 2)
        r = client.get("/v1/management/upstreams/mock-ollama")
        check("upstream detail -> 200", r.status_code == 200, f"got {r.status_code}")
        detail = r.json()
        check(
            "models come from config, wildcard expanded, derived model appended",
            detail.get("models") == ["q8", "qwen3-reranker:4b", "gone:1b", "qwen3:4b", "q35"],
            str(detail.get("models")),
        )
        check(
            "detail lists no model the config does not cover",
            "qwen3.5:9b" not in (detail.get("models") or []) and "other/not-matched" not in (detail.get("models") or []),
            str(detail.get("models")),
        )
        check("detail no longer names a discovery endpoint", detail.get("models_endpoint") is None, str(detail))
        check("detail reports 4 loaded models", detail.get("loaded") == 4, str(detail.get("loaded")))
        check(
            "detail carries the per-instance runtime",
            [m["model"] for m in detail.get("running_models", [])]
            == ["qwen3:8b", "qwen3:4b", "qwen3-reranker:4b", "tiny:cpu", "q35"],
            str(detail.get("running_models")),
        )
        check(
            "detail runtime renames but does not rename away",
            detail["running_models"][0]["display"] == "q8"
            and detail["running_models"][-1]["display"] == "q35"
            and detail["running_models"][-1]["derived"] is True,
            str(detail.get("running_models")),
        )
        r = client.get("/v1/management/upstreams/dead-ollama")
        check("unreachable upstream -> 503", r.status_code == 503, f"got {r.status_code}")
        check("unreachable marker present", r.json().get("unreachable") is True, r.text[:200])
        r = client.get("/v1/management/upstreams/nope")
        check("unknown upstream -> 404", r.status_code == 404, f"got {r.status_code}")

        # ---------------------------------------------------- model config ---
        print("[4] model configuration projections")
        r = client.get("/v1/management/models/llm")
        check("GET /models/llm -> 200", r.status_code == 200, f"got {r.status_code}")
        llm = r.json()
        models = {m["model"]: m for m in llm.get("models", []) if m["upstream"] == "mock-ollama"}
        check("qwen3:8b projected", "qwen3:8b" in models, str(list(models)))
        check(
            "installed-but-undeclared model is not listed",
            all(m["model"] != "qwen3.5:9b" for m in llm.get("models", [])),
            str([m["model"] for m in llm.get("models", [])]),
        )
        check(
            "declared model the instance lacks is still listed",
            models.get("gone:1b", {}).get("running") is False
            and models.get("gone:1b", {}).get("device") is None,
            str(models.get("gone:1b")),
        )
        check(
            "wildcard row is replaced by the models it matches",
            "qwen3:*" not in models
            and models.get("qwen3:4b", {}).get("wildcard") is True
            and models.get("qwen3:8b", {}).get("wildcard") is False,
            str(list(models)),
        )
        check(
            "each expanded model carries its own runtime",
            models.get("qwen3:4b", {}).get("running") is True
            and models["qwen3:4b"]["device"] == "cpu"
            and models["qwen3:4b"]["num_ctx"] == 4096,
            str(models.get("qwen3:4b")),
        )
        check(
            "model_aliases rename replaces the displayed id",
            models.get("qwen3:8b", {}).get("display") == "q8"
            and models["qwen3:8b"]["alias"] == "q8"
            and models["qwen3:8b"]["derived"] is False,
            str(models.get("qwen3:8b")),
        )
        check(
            "root alias is its own model row",
            models.get("q35", {}).get("display") == "q35"
            and models["q35"]["alias"] is None
            and models["q35"]["derived"] is True
            and models["q35"]["target"] == "qwen3:8b"
            and models["q35"]["params"] == {"think": False},
            str(models.get("q35")),
        )
        check(
            "derived row inherits the target runtime but claims no memory",
            models.get("q35", {}).get("running") is True
            and models["q35"]["device"] == "gpu"
            and models["q35"]["num_ctx"] == 8192
            and models["q35"]["size_gib"] is None
            and models["q35"]["vram_gib"] is None,
            str(models.get("q35")),
        )
        check(
            "derived row follows its target upstream, listed after it",
            [m["model"] for m in llm.get("models", []) if m["upstream"] == "mock-ollama"][0:2]
            == ["qwen3:8b", "q35"],
            str([m["model"] for m in llm.get("models", []) if m["upstream"] == "mock-ollama"]),
        )
        check(
            "unaliased model displays its declared name",
            models.get("gone:1b", {}).get("display") == "gone:1b"
            and models["gone:1b"]["alias"] is None,
            str(models.get("gone:1b")),
        )
        check("runtime device from /api/ps", models.get("qwen3:8b", {}).get("device") == "gpu", str(models.get("qwen3:8b")))
        check(
            "runtime memory from /api/ps",
            (models["qwen3:8b"]["size_gib"], models["qwen3:8b"]["vram_gib"]) == (10.0, 10.0),
            str(models.get("qwen3:8b")),
        )
        check(
            "loaded-only model is not listed",
            "tiny:cpu" not in models,
            str(list(models)),
        )
        check(
            "idle model has no runtime state",
            models.get("gone:1b", {}).get("device") is None
            and models["gone:1b"]["size_gib"] is None
            and models["gone:1b"]["running"] is False,
            str(models.get("gone:1b")),
        )
        check("runtime num_ctx wins", models.get("qwen3-reranker:4b", {}).get("num_ctx") == 4096, str(models.get("qwen3-reranker:4b")))
        check("num_ctx override for an idle model", models.get("qwen3:8b", {}).get("num_ctx") == 8192, str(models.get("qwen3:8b")))
        check("running flag from /api/ps", models["qwen3:8b"]["running"] is True, str(models.get("qwen3:8b")))
        check(
            "dead upstream keeps its declared rows",
            [m["model"] for m in llm.get("models", []) if m["upstream"] == "dead-ollama"] == ["dead:*"]
            and all(m["running"] is False for m in llm.get("models", []) if m["upstream"] == "dead-ollama"),
            str([m for m in llm.get("models", []) if m["upstream"] == "dead-ollama"]),
        )
        check("aliases list includes q35", any(a["alias"] == "q35" and a["target"] == "qwen3:8b" for a in llm.get("aliases", [])))
        by_alias = {a["alias"]: a for a in llm.get("aliases", [])}
        check(
            "alias projection marks the two kinds",
            by_alias["q35"]["kind"] == "derived"
            and by_alias["q35"]["params"] == {"think": False}
            and by_alias["q8"]["kind"] == "rename"
            and by_alias["q8"]["params"] == {},
            str(by_alias),
        )
        check("num_ctx_overrides echoed", llm.get("num_ctx_overrides", {}).get("qwen3:8b") == 8192)

        # ------------------------------- opencode: hosted API, no runtime telemetry ---
        base_upstreams = list(_CONFIG["upstreams"])

        def swap_upstreams(items: list[dict]) -> list[dict]:
            with open(_CONFIG_PATH, encoding="utf-8") as fh:
                raw = json.load(fh)
            raw["upstreams"] = items
            with open(_CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, ensure_ascii=False)
            client.app.state.manager.reload()
            rows = client.get("/v1/management/models/llm").json()["models"]
            return [m for m in rows if m["upstream"] == "mock-opencode"]

        opencode_up = {
            "name": "mock-opencode",
            "type": "opencode",
            "base_url": f"http://127.0.0.1:{MOCK_PORT}",
            "models": ["auto", "opencode/*"],
        }
        try:
            rows = swap_upstreams(base_upstreams + [opencode_up])
            check(
                "opencode wildcard expands to the provider models",
                [m["model"] for m in rows] == ["auto", "opencode/big-pickle", "opencode/gpt-5-mini"],
                str(rows),
            )
            check("expanded rows are marked as wildcard-covered", [m["wildcard"] for m in rows] == [False, True, True], str(rows))
            check("opencode probe online makes every model available", all(m["running"] for m in rows), str(rows))
            check("opencode rows report the cloud device", all(m["device"] == "cloud" for m in rows), str(rows))
            check("opencode memory is unknown", all(m["size_gib"] is None and m["vram_gib"] is None for m in rows), str(rows))

            # A prefix that matches nothing on the provider stays visible as the pattern itself,
            # but must never be reported as an online model.
            with open(_CONFIG_PATH, encoding="utf-8") as fh:
                raw = json.load(fh)
            raw["upstreams"] = base_upstreams + [
                opencode_up,
                {**opencode_up, "name": "mock-opencode-empty", "models": ["opencode/zzz:*"]},
            ]
            with open(_CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, ensure_ascii=False)
            client.app.state.manager.reload()
            empty = [
                m
                for m in client.get("/v1/management/models/llm").json()["models"]
                if m["upstream"] == "mock-opencode-empty"
            ]
            check(
                "an unresolvable wildcard keeps its pattern row",
                [m["model"] for m in empty] == ["opencode/zzz:*"],
                str(empty),
            )
            check(
                "an unresolvable wildcard is not reported running",
                all(m["running"] is False for m in empty),
                str(empty),
            )

            overview = client.get("/v1/management/overview").json()
            check(
                "an unresolvable wildcard adds no dashboard row",
                not [m for m in overview["running_models"] if m["upstream"] == "mock-opencode-empty"],
                str(overview["running_models"]),
            )

            hosted = [m for m in overview["running_models"] if m["upstream"] == "mock-opencode"]
            check(
                "dashboard lists the hosted provider models",
                [m["model"] for m in hosted] == ["auto", "opencode/big-pickle", "opencode/gpt-5-mini"],
                str(hosted),
            )
            check(
                "hosted dashboard rows are cloud with no memory",
                all(m["device"] == "cloud" and m["size_gib"] is None and m["vram_gib"] is None for m in hosted),
                str(hosted),
            )
            check(
                "hosted models count in the running KPI",
                overview["kpi"]["running_models"] == 4 + len(hosted),
                str(overview["kpi"]),
            )

            rows = swap_upstreams(base_upstreams + [{**opencode_up, "base_url": f"http://127.0.0.1:{DEAD_PORT}"}])
            check("opencode offline when the probe fails", all(m["running"] is False for m in rows), str(rows))
            overview = client.get("/v1/management/overview").json()
            check(
                "an offline hosted provider adds no dashboard rows",
                not [m for m in overview["running_models"] if m["upstream"] == "mock-opencode"],
                str(overview["running_models"]),
            )
        finally:
            swap_upstreams(base_upstreams)
        check("upstreams restored after the opencode probe", len(client.get("/v1/management/upstreams").json()["upstreams"]) == 2)

        r = client.get("/v1/management/models/rerank")
        check("GET /models/rerank -> 200", r.status_code == 200, f"got {r.status_code}")
        rr = r.json()
        entry = rr["models"][0]
        check("rerank upstream resolved", entry.get("upstream") == "mock-ollama", str(entry))
        check("rerank api_key masked", entry.get("api_key") == MASK, str(entry.get("api_key")))
        check("rerank scoring mode exposed", rr.get("mode") == "logprobs", str(rr.get("mode")))
        check("rerank upstream protocol exposed", rr.get("upstream_api") == "auto", str(rr.get("upstream_api")))
        check("rerank failure policy exposed", rr.get("on_error") == "score_zero", str(rr.get("on_error")))
        check("rerank request example present", "request_example" in rr)

        r = client.get("/v1/management/models/systemone")
        check("GET /models/systemone -> 200", r.status_code == 200, f"got {r.status_code}")
        so = r.json()
        check("systemone linked upstream", so.get("upstream") == "mock-ollama", str(so.get("upstream")))
        check("systemone api_key masked", so.get("api_key") == MASK, str(so.get("api_key")))
        check("systemone stream example present", "stream_example" in so)

        # ------------------------------------------------ request logging ---
        print("[5] request log capture")
        r = client.post(
            "/api/chat",
            headers=GW,
            json={
                "model": "qwen3:8b",
                "messages": [{"role": "user", "content": "ping"}],
                "stream": False,
                "api_key": "secret-should-be-masked",
            },
        )
        check("non-stream /api/chat -> 200", r.status_code == 200, f"got {r.status_code}")

        # Inspect this entry right away: it is the newest row for the model, so the
        # alias call below cannot displace it.
        rq = client.get("/v1/management/requests", params={"model": "qwen3:8b"})
        entry_id = rq.json()["rows"][0]["id"]
        detail_r = client.get(f"/v1/management/requests/{entry_id}")
        check("GET /requests/{id} -> 200", detail_r.status_code == 200, f"got {detail_r.status_code}")
        entry = detail_r.json()
        check("token usage extracted", entry.get("tokens") == 12, str(entry.get("tokens")))
        check(
            "Ollama usage split into input / output",
            (entry.get("tokens_in"), entry.get("tokens_out")) == (5, 7),
            str((entry.get("tokens_in"), entry.get("tokens_out"))),
        )
        check("client ip recorded", bool(entry.get("client_ip")), str(entry.get("client_ip")))
        check("api_key masked in stored body", "secret-should-be-masked" not in (entry.get("request_body") or ""), entry.get("request_body"))
        check("mask marker present in stored body", "***" in (entry.get("request_body") or ""), entry.get("request_body"))
        check("response body stored", "pong" in (entry.get("response_body") or ""), str(entry.get("response_body"))[:120])
        check("stream flag is a bool in detail", isinstance(entry.get("stream"), bool), str(entry.get("stream")))
        check(
            "arrival and finish timestamps",
            bool(entry.get("ts")) and bool(entry.get("ts_end")) and entry["ts_end"] >= entry["ts"],
            f"{entry.get('ts')} -> {entry.get('ts_end')}",
        )
        check(
            "requested model kept as sent",
            entry.get("requested_model") == "qwen3:8b",
            str(entry.get("requested_model")),
        )

        r = client.post(
            "/api/chat",
            headers=GW,
            json={"model": "q35", "messages": [{"role": "user", "content": "alias"}], "stream": False},
        )
        check("alias /api/chat -> 200", r.status_code == 200, f"got {r.status_code}")
        echo = r.json().get("_echo", {})
        check("alias forwarded the real model", echo.get("model") == "qwen3:8b", str(echo.get("model")))
        check("alias injected params", echo.get("think") is False, str(echo.get("think")))

        alias_row = client.get("/v1/management/requests", params={"path": "api/chat", "model": "qwen3:8b"}).json()["rows"][0]
        alias_entry = client.get(f"/v1/management/requests/{alias_row['id']}").json()
        check(
            "alias call logs both the requested and the resolved model",
            alias_entry.get("requested_model") == "q35" and alias_entry.get("model") == "qwen3:8b",
            str((alias_entry.get("requested_model"), alias_entry.get("model"))),
        )

        client.get("/api/version", headers=GW, params={"format": "json"})
        version_row = client.get("/v1/management/requests", params={"path": "api/version"}).json()["rows"][0]
        version_entry = client.get(f"/v1/management/requests/{version_row['id']}").json()
        check(
            "URL query string recorded",
            version_entry.get("query") == "format=json",
            str(version_entry.get("query")),
        )
        check(
            "a response without usage records no token split",
            version_entry.get("tokens_in") is None and version_entry.get("tokens_out") is None,
            str((version_entry.get("tokens_in"), version_entry.get("tokens_out"))),
        )

        r = client.post(
            "/v1/chat/completions",
            headers=GW,
            json={"model": "qwen3:8b", "stream": False, "messages": [{"role": "user", "content": "openai usage"}]},
        )
        check("non-stream /v1/chat/completions -> 200", r.status_code == 200, f"got {r.status_code}")
        openai_row = client.get("/v1/management/requests", params={"path": "chat/completions"}).json()["rows"][0]
        openai_entry = client.get(f"/v1/management/requests/{openai_row['id']}").json()
        check(
            "OpenAI usage split into input / output",
            (openai_entry.get("tokens_in"), openai_entry.get("tokens_out"), openai_entry.get("tokens")) == (4, 8, 12),
            str((openai_entry.get("tokens_in"), openai_entry.get("tokens_out"), openai_entry.get("tokens"))),
        )

        r = client.post(
            "/v1/chat/completions",
            headers=GW,
            json={"model": "qwen3:8b", "stream": True, "messages": [{"role": "user", "content": "stream"}]},
        )
        check("streaming /v1/chat/completions -> 200", r.status_code == 200, f"got {r.status_code}")
        check("streaming body passed through", "data:" in r.text)

        r = client.post(
            "/v1/chat/completions",
            headers=GW,
            json={"model": "dead:7b", "stream": False, "messages": [{"role": "user", "content": "boom"}]},
        )
        check("upstream failure -> 502", r.status_code == 502, f"got {r.status_code}")

        # Liveness probes are forwarded but never recorded; HEAD has to be allowed for this one.
        try:
            patch_config(security={**_CONFIG["security"], "allowed_http_methods": ["GET", "POST", "HEAD"]})
            r = client.head("/api/chat", headers=GW)
            check("an allowed HEAD is still forwarded", r.status_code == 200, f"got {r.status_code}")
        finally:
            patch_config(security=_CONFIG["security"])

        # The log writer is asynchronous; /requests flushes before querying.
        r = client.get("/v1/management/requests")
        check("GET /requests -> 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        payload = r.json()
        rows = payload.get("rows", [])
        stats = payload.get("stats", {})
        check("rows recorded", len(rows) >= 4, f"got {len(rows)}")
        check("stats count matches", stats.get("count") == payload.get("pagination", {}).get("total"), str(stats))
        check("error rate reflects the 502", stats.get("error_rate", 0) > 0, str(stats))
        check("p95 present", stats.get("p95_ms", 0) > 0, str(stats))

        by_path = {}
        for row in rows:
            by_path.setdefault(row["path"], []).append(row)
        check("non-stream row stored by name", any(
            r["model"] == "qwen3:8b" and r["stream"] == 0 for r in by_path.get("/api/chat", [])
        ), str(by_path.get("/api/chat")))
        check("aliased call logged under the real model", all(
            r["model"] == "qwen3:8b" for r in by_path.get("/api/chat", [])
        ), str(by_path.get("/api/chat")))
        check("streaming row flagged stream=1", any(
            r["stream"] == 1 for r in by_path.get("/v1/chat/completions", [])
        ), str(by_path.get("/v1/chat/completions")))
        check("token split is on the list rows too", any(
            x["tokens_in"] == 5 and x["tokens_out"] == 7
            for x in client.get("/v1/management/requests", params={"path": "api/chat"}).json()["rows"]
        ))
        check("failed call logged with upstream", any(
            r["status"] == 502 and r["upstream"] == "dead-ollama" for r in rows
        ))
        check("a HEAD probe never lands in the log", not any(r["method"] == "HEAD" for r in rows),
              str({r["method"] for r in rows}))
        check("unknown request id -> 404", client.get("/v1/management/requests/999999").status_code == 404)

        # ------------------------------------------------------- filtering ---
        print("[6] filters, pagination, stats")
        r = client.get("/v1/management/requests", params={"upstream": "dead-ollama"})
        check("filter by upstream", all(x["upstream"] == "dead-ollama" for x in r.json()["rows"]))
        r = client.get("/v1/management/requests", params={"status": "5xx"})
        check("filter by 5xx", r.json()["rows"] and all(x["status"] >= 500 for x in r.json()["rows"]))
        r = client.get("/v1/management/requests", params={"path": "chat/completions"})
        check("filter by path keyword", all("chat/completions" in x["path"] for x in r.json()["rows"]))
        r = client.get("/v1/management/requests", params={"model": "dead:7b"})
        check("filter by model", all(x["model"] == "dead:7b" for x in r.json()["rows"]))
        r = client.get("/v1/management/requests", params={"page_size": 1, "page": 2})
        page = r.json()
        check("pagination page size respected", len(page["rows"]) == 1, str(len(page.get("rows", []))))
        check("pagination reports pages", page["pagination"]["pages"] >= 2, str(page["pagination"]))
        check("invalid status filter -> 400", client.get("/v1/management/requests", params={"status": "9xx"}).status_code == 400)
        check("invalid page_size -> 400", client.get("/v1/management/requests", params={"page_size": 0}).status_code == 400)
        check("invalid page -> 400", client.get("/v1/management/requests", params={"page": 0}).status_code == 400)
        check("invalid timestamp -> 400", client.get("/v1/management/requests", params={"start": "not-a-date"}).status_code == 400)
        check("start after end -> 400", client.get(
            "/v1/management/requests", params={"start": "2026-01-02T00:00:00", "end": "2026-01-01T00:00:00"}
        ).status_code == 400)

        r = client.get("/v1/management/stats/timeseries")
        check("timeseries -> 200", r.status_code == 200, f"got {r.status_code}")
        ts = r.json()
        check("timeseries hourly buckets", ts.get("bucket") == "hour" and ts.get("points"), str(ts)[:200])
        check("timeseries counts our requests", sum(p["count"] for p in ts["points"]) >= 4, str(ts["points"]))
        check("timeseries p95 populated", any(p["p95"] > 0 for p in ts["points"]), str(ts["points"]))
        r = client.get("/v1/management/stats/timeseries", params={"bucket": "day"})
        check("timeseries daily bucket -> 200", r.status_code == 200 and r.json().get("bucket") == "day")
        check("invalid bucket -> 400", client.get("/v1/management/stats/timeseries", params={"bucket": "week"}).status_code == 400)

        r = client.get("/v1/management/stats/failures")
        check("failures -> 200", r.status_code == 200, f"got {r.status_code}")
        fail = r.json()
        check("failure by_code has 502", fail.get("by_code", {}).get("502", 0) >= 1, str(fail.get("by_code")))
        check("failure by_upstream has dead-ollama", fail.get("by_upstream", {}).get("dead-ollama", 0) >= 1, str(fail.get("by_upstream")))
        check("failure samples include the model", any(s.get("model") == "dead:7b" for s in fail.get("samples", [])), str(fail.get("samples"))[:200])

        # A blocked admin call gives the distribution a row that never reached an upstream.
        blocked = client.post("/api/pull", headers=GW, json={"model": "qwen3:8b"})
        check("blocked admin path -> 403", blocked.status_code == 403, f"got {blocked.status_code}")

        r = client.get("/v1/management/stats/distribution")
        check("distribution -> 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        dist = r.json()
        counts = {item["key"]: item["count"] for item in dist["items"]}
        check("distribution groups by upstream by default", dist.get("group_by") == "upstream", str(dist.get("group_by")))
        check("distribution counts the live upstream", counts.get("mock-ollama", 0) >= 2, str(counts))
        check("distribution counts the failing upstream", counts.get("dead-ollama", 0) >= 1, str(counts))
        check("distribution buckets calls that never routed", counts.get("-", 0) >= 1, str(counts))
        total = client.get("/v1/management/requests").json()["pagination"]["total"]
        check("distribution items sum to the log", sum(counts.values()) == total, f"{sum(counts.values())} vs {total}")
        check("distribution is ordered by count", list(counts) == [k for k, _ in sorted(counts.items(), key=lambda kv: -kv[1])], str(list(counts)))

        status_items = client.get("/v1/management/stats/distribution", params={"group_by": "status"}).json()["items"]
        status_counts = {item["key"]: item["count"] for item in status_items}
        check("status buckets into ok / error", set(status_counts) == {"ok", "error"}, str(status_counts))
        check("status errors include 403 and 502", status_counts.get("error", 0) >= 2, str(status_counts))

        model_counts = {
            item["key"]: item["count"]
            for item in client.get("/v1/management/stats/distribution", params={"group_by": "model"}).json()["items"]
        }
        check("model grouping uses the resolved name", model_counts.get("qwen3:8b", 0) >= 2, str(model_counts))

        check("invalid group_by -> 400", client.get("/v1/management/stats/distribution", params={"group_by": "client_ip"}).status_code == 400)
        empty = client.get(
            "/v1/management/stats/distribution",
            params={"start": "2020-01-01T00:00:00", "end": "2020-01-02T00:00:00"},
        ).json()
        check("distribution honours the time range", empty["items"] == [], str(empty))

        r = client.get("/v1/management/overview")
        check("overview counts today's requests", r.json()["kpi"]["requests_today"] >= 4, str(r.json()["kpi"]))
        check(
            "overview sums today's tokens",
            r.json()["kpi"]["tokens_today"] >= 24,
            str(r.json()["kpi"]),
        )

        # ------------------------------------------- allowlist exemption ---
        print("[7] perimeter exemptions")
        sec = client.app.state.manager.config.security
        sec.path_allowlist = ["^/no-such-path$"]
        r = client.get("/v1/management/auth/ping")
        check("management API exempt from path_allowlist", r.status_code == 200, f"got {r.status_code}")
        r = client.get("/v1/models", headers=GW)
        check("gateway path still enforced by path_allowlist", r.status_code == 403, f"got {r.status_code}")
        sec.path_allowlist = []

        # ------------------------------------------- admin disabled (503) ---
        print("[8] admin not configured")
        saved = os.environ.pop("ADMIN_PASSWORD", None)
        try:
            check("ping without ADMIN_PASSWORD -> 503", client.get("/v1/management/auth/ping").status_code == 503)
            check("login without ADMIN_PASSWORD -> 503", client.post(
                "/v1/management/auth/login", json={"password": "anything"}
            ).status_code == 503)
        finally:
            if saved is not None:
                os.environ["ADMIN_PASSWORD"] = saved

        # ------------------------------------------ request_log.enabled ---
        print("[9] request_log.enabled switch")

        def set_logging(enabled: bool) -> None:
            with open(_CONFIG_PATH, encoding="utf-8") as fh:
                raw = json.load(fh)
            raw["request_log"] = {"enabled": enabled}
            with open(_CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, ensure_ascii=False)
            client.app.state.manager.reload()

        def total_rows() -> int:
            return client.get("/v1/management/requests").json()["pagination"]["total"]

        def chat(content: str) -> int:
            return client.post(
                "/api/chat",
                headers=GW,
                json={"model": "qwen3:8b", "messages": [{"role": "user", "content": content}], "stream": False},
            ).status_code

        check("default config records", client.app.state.manager.config.request_log.enabled is True)
        before = total_rows()
        check("overview reports the switch", "request_log" in client.get("/v1/management/overview").json())
        check("requests payload reports the switch", "request_log" in client.get("/v1/management/requests").json())

        set_logging(False)
        check("switch off is read from config", client.app.state.manager.config.request_log.enabled is False)
        check("overview shows logging off", client.get("/v1/management/overview").json()["request_log"]["enabled"] is False)
        check("call still succeeds with logging off", chat("not-logged") == 200)
        after = total_rows()
        check("no row written while disabled", after == before, f"{before} -> {after}")
        check("requests payload shows logging off", client.get("/v1/management/requests").json()["request_log"]["enabled"] is False)

        set_logging(True)
        check("call succeeds after re-enabling", chat("logged-again") == 200)
        check("rows resume after re-enabling", total_rows() == after + 1, f"{after} -> {total_rows()}")
        newest = client.get("/v1/management/requests").json()["rows"][0]
        check("re-enabled row is the new call", newest["path"] == "/api/chat" and newest["status"] == 200, str(newest))

    # ------------------------------------------- tools/websearch projection ---
        # ------------------------------------------- model speed test (bench) ---
        # Runs inside the TestClient context on purpose: the bench job is a worker thread
        # writing through app.state.bench_store, which only exists while the lifespan runs.
        print("[10b] model speed test (bench)")

        r = client.post("/v1/management/bench", json={"upstream": "nope", "model": "qwen3:8b"})
        check("bench: unknown upstream -> 404", r.status_code == 404, f"got {r.status_code}")

        r = client.post("/v1/management/bench", json={"upstream": "mock-ollama", "model": "   "})
        check("bench: blank model -> 400", r.status_code == 400, f"got {r.status_code}")

        # Route-order regression guard: "history" is a literal segment and must not be captured
        # by /bench/{job_id} (which would answer 404 "任务不存在或已过期").
        r = client.get("/v1/management/bench/history", params={"upstream": "mock-ollama", "model": "never-tested"})
        check(
            "bench history: unknown pair -> 200 with no records (not a job lookup)",
            r.status_code == 200 and r.json().get("records") == [],
            f"got {r.status_code}: {r.text[:160]}",
        )

        r = client.get("/v1/management/bench/deadbeef")
        check("bench: unknown job id -> 404", r.status_code == 404, f"got {r.status_code}")

        def _run_bench_job(model: str = "qwen3:8b") -> dict:
            started = client.post(
                "/v1/management/bench",
                json={"upstream": "mock-ollama", "model": model, "rounds": 2, "max_tokens": 8},
            )
            if started.status_code != 200:
                return {"status": f"start-failed-{started.status_code}", "detail": started.text[:160]}
            job_id = started.json()["job_id"]
            state: dict = {}
            for _ in range(200):  # ~10s ceiling; the mock upstream answers instantly
                state = client.get(f"/v1/management/bench/{job_id}").json()
                if state.get("status") in ("done", "failed"):
                    break
                time.sleep(0.05)
            return state

        state = _run_bench_job()
        check("bench job reaches done", state.get("status") == "done", str(state)[:200])
        steady = (state.get("result") or {}).get("steady") or {}
        check(
            "bench result carries decode / prefill / ttft",
            all(k in steady for k in ("decode", "prefill", "ttft"))
            and steady.get("decode", {}).get("mean") is not None,
            str(steady)[:200],
        )
        check(
            "bench result keeps per-round detail",
            len((state.get("result") or {}).get("rounds") or []) == 2,  # rounds includes the warm-up
            str((state.get("result") or {}).get("rounds"))[:200],
        )

        history = client.get(
            "/v1/management/bench/history", params={"upstream": "mock-ollama", "model": "qwen3:8b"}
        ).json().get("records", [])
        check(
            "bench result is persisted and readable",
            len(history) == 1 and history[0]["decode_tok_s"] is not None,
            str(history)[:200],
        )
        check(
            "persisted record keeps the timing source",
            history and history[0]["timing_source"] in ("server", "client", "client*"),
            str(history[:1])[:200],
        )

        # Retention: four more runs must leave exactly the newest three rows for this pair.
        for _ in range(4):
            _run_bench_job()
        history = client.get(
            "/v1/management/bench/history", params={"upstream": "mock-ollama", "model": "qwen3:8b"}
        ).json().get("records", [])
        check(
            "bench history keeps only the latest 3 per (upstream, model)",
            len(history) == 3,
            f"got {len(history)}",
        )
        check(
            "bench history is newest-first",
            [h["created_at"] for h in history]
            == sorted((h["created_at"] for h in history), reverse=True),
            str([h["created_at"] for h in history]),
        )

        r = client.get("/v1/management/bench/history", params={"upstream": "mock-ollama", "model": "qwen3:8b"})
        check("bench history echoes the requested key", r.json().get("model") == "qwen3:8b", r.text[:160])

        # ----------------------------------------------- interactive API testers ---
        print("[10] api-test context and live execution")
        # A separate client: the shared one still holds the session cookie from the login tests.
        anon = TestClient(main.app)
        check("context without credentials -> 401", anon.get("/v1/management/api-test/models").status_code == 401)
        check("execution without credentials -> 401", anon.post("/v1/management/api-test", json={"kind": "generate", "body": {}}).status_code == 401)
        r = anon.get("/v1/management/api-test/models", headers={"Authorization": "Bearer admin-secret-pw"})
        check("the admin password is not a request credential here either", r.status_code == 401, f"got {r.status_code}")
        r = anon.get("/v1/management/api-test/models", headers=GW)
        check("context with a gateway API key -> 200", r.status_code == 200, f"got {r.status_code}: {r.text[:160]}")
        ctx = r.json()
        kinds = ctx["kinds"]
        check(
            "one context block per documented endpoint",
            set(kinds) == {"generate", "chat", "embed_ollama", "embed_openai", "rerank", "systemone", "websearch"},
            str(sorted(kinds)),
        )
        check("context publishes the response cap", ctx["max_response_bytes"] == 128 * 1024, str(ctx.get("max_response_bytes")))
        check("generate serves the documented path", kinds["generate"]["endpoint"] == "/api/generate")
        check("chat shares the generate family", kinds["chat"]["family"] == "generate" and kinds["chat"]["endpoint"] == "/v1/chat/completions")
        gen_rows = {m["model"]: m for m in kinds["generate"]["models"]}
        check(
            "the chat selector comes from the declared pool",
            {"qwen3:8b", "qwen3:4b", "q35", "gone:1b"} <= set(gen_rows),
            str(sorted(gen_rows)),
        )
        check("the reranker is never offered for chat", "qwen3-reranker:4b" not in gen_rows, str(sorted(gen_rows)))
        check("an unresolvable wildcard pattern is not a selectable model", not any(m.endswith("*") for m in gen_rows))
        check("a model_aliases rename is what the selector shows", gen_rows["qwen3:8b"]["display"] == "q8", str(gen_rows["qwen3:8b"]))
        check("an unaliased model shows its configured name", gen_rows["qwen3:4b"]["display"] == "qwen3:4b")
        check("a derived alias is selectable as itself", gen_rows["q35"]["derived"] is True and gen_rows["q35"]["target"] == "qwen3:8b")
        check("every row reports whether its upstream signs", gen_rows["qwen3:8b"]["api_key"] == MASK, str(gen_rows["qwen3:8b"]))
        check("the first choice is preselected", kinds["generate"]["default_model"] == "qwen3:8b", str(kinds["generate"]["default_model"]))
        check("a proxied kind is always enabled", kinds["generate"]["enabled"] is True)
        check("generation requires a model, a free name is not invented", kinds["generate"]["model_required"] is True and kinds["generate"]["free_model"] is False)
        rr = kinds["rerank"]
        check("rerank falls back to rerank.models", rr["source"] == "rerank.models" and [m["model"] for m in rr["models"]] == ["qwen3-reranker:4b"], str(rr))
        check("rerank takes its endpoint from config", rr["endpoint"] == "/v1/rerank", str(rr.get("endpoint")))
        so = kinds["systemone"]
        check("systemone preselects systemone.model", so["default_model"] == "qwen3:8b" and so["source"] == "systemone.model", str(so))
        check("systemone masks the env key and names the variable", so["api_key"] == MASK and so["api_key_env"] == "SYSTEMONE_SERVER_API_KEY", str(so))
        check("systemone accepts a laya routing enum", so["free_model"] is True and so["model_required"] is False)
        check("systemone follows the enabled switch", so["enabled"] is True)
        ws = kinds["websearch"]
        check("web search leaves the model to the provider", ws["model_required"] is False and ws["default_model"] is None, str(ws))
        check("a disabled capability says so instead of hiding the tester", ws["enabled"] is False)
        check("an unconfigured provider key is shown as absent", ws["api_key"] is None, str(ws))
        check(
            "the context never carries a credential value",
            "upstream-secret" not in r.text and "systemone-secret" not in r.text,
        )

        try:
            patch_config(api_models={"generate": ["q8", "qwen3:4b", "not:declared"]})
            narrowed = client.get("/v1/management/api-test/models").json()["kinds"]
            check("api_models.generate replaces the derived pool", [m["model"] for m in narrowed["generate"]["models"]] == ["q8", "qwen3:4b", "not:declared"], str(narrowed["generate"]["models"]))
            check("the narrowing reports its source", narrowed["generate"]["source"] == "api_models.generate")
            check("an alias in the list stays the sendable name", narrowed["generate"]["models"][0]["target"] == "qwen3:8b", str(narrowed["generate"]["models"][0]))
            check("a name nothing serves stays visible and flagged", narrowed["generate"]["models"][2]["unknown"] is True, str(narrowed["generate"]["models"][2]))
            check("the same list feeds every kind of that family", narrowed["chat"]["models"] == narrowed["generate"]["models"])

            patch_config(api_models={"embed": ["q8"]})
            claimed = client.get("/v1/management/api-test/models").json()["kinds"]
            check("embed lists the name it was given", [m["model"] for m in claimed["embed_ollama"]["models"]] == ["q8"], str(claimed["embed_ollama"]["models"]))
            check("claiming an alias hides its real model from the chat selector",
                  "qwen3:8b" not in {m["model"] for m in claimed["generate"]["models"]}
                  and "q35" not in {m["model"] for m in claimed["generate"]["models"]},
                  str(sorted(m["model"] for m in claimed["generate"]["models"])))
            check("both embed endpoints share the embed family", claimed["embed_openai"]["models"] == claimed["embed_ollama"]["models"])

            patch_config(api_models={"systemone": ["qwen3:4b"], "websearch": ["qwen3:8b"]})
            others = client.get("/v1/management/api-test/models").json()["kinds"]
            chat_names = {m["model"] for m in others["generate"]["models"]}
            check("a name systemone claims is not offered for chat", "qwen3:4b" not in chat_names, str(sorted(chat_names)))
            # Web search is a chat pass-through: the provider's model answers the synthesis
            # prompt, so claiming it must not remove it (or its alias) from the chat selector.
            check("a web search claim does not hide a chat model",
                  {"qwen3:8b", "q35"} <= chat_names, str(sorted(chat_names)))
            check("web search still lists the names it was given",
                  {m["model"] for m in others["websearch"]["models"]} == {"qwen3:8b"},
                  str(others["websearch"]["models"]))

            # Ollama reads `tagged` and `tagged:latest` as one model, so a tagless entry in
            # api_models still claims the declared name.
            upstreams = [{**upstream, "models": [*upstream["models"]]} for upstream in _CONFIG["upstreams"]]
            upstreams[0]["models"].append("tagged:latest")
            patch_config(upstreams=upstreams, api_models={"systemone": ["tagged"]})
            shorthand = client.get("/v1/management/api-test/models").json()["kinds"]
            check("a tagless claim matches the declared :latest model",
                  "tagged:latest" not in {m["model"] for m in shorthand["generate"]["models"]},
                  str(sorted(m["model"] for m in shorthand["generate"]["models"])))

            # `/api/generate` and `/api/embed` are Ollama's native dialect: an LM Studio or
            # opencode upstream answers only the /v1 side, so a tester for the native path
            # must not offer its models. The OpenAI-compatible testers keep them.
            openai_up = {
                "name": "mock-openai",
                "type": "lmstudio",
                "base_url": f"http://127.0.0.1:{MOCK_PORT}",
                "models": ["lm-model"],
            }
            patch_config(upstreams=[*_CONFIG["upstreams"], openai_up])
            dialect = client.get("/v1/management/api-test/models").json()["kinds"]
            check("the native generate tester offers only Ollama upstreams",
                  "lm-model" not in {m["model"] for m in dialect["generate"]["models"]},
                  str(sorted(m["model"] for m in dialect["generate"]["models"])))
            check("the OpenAI chat tester still offers a non-Ollama model",
                  "lm-model" in {m["model"] for m in dialect["chat"]["models"]},
                  str(sorted(m["model"] for m in dialect["chat"]["models"])))
            check("native embeddings follow the same dialect rule",
                  "lm-model" not in {m["model"] for m in dialect["embed_ollama"]["models"]}
                  and "lm-model" in {m["model"] for m in dialect["embed_openai"]["models"]},
                  str([m["model"] for m in dialect["embed_ollama"]["models"]]))
            patch_config(api_models={"generate": ["lm-model", "q8"]}, upstreams=[*_CONFIG["upstreams"], openai_up])
            written = client.get("/v1/management/api-test/models").json()["kinds"]
            check("an explicit api_models.generate list is still served as written",
                  [m["model"] for m in written["generate"]["models"]] == ["lm-model", "q8"],
                  str([m["model"] for m in written["generate"]["models"]]))
            patch_config(upstreams=_CONFIG["upstreams"])
        finally:
            patch_config(api_models={"generate": [], "embed": [], "rerank": [], "systemone": [], "websearch": []})

        r = client.post("/v1/management/api-test", json={"kind": "api/pull", "body": {}})
        check("a kind outside the allowlist -> 400", r.status_code == 400, f"got {r.status_code}")
        check("the refusal names the accepted kinds", "generate" in r.json().get("detail", ""), r.text[:160])
        r = client.post("/v1/management/api-test", json={"kind": "generate", "body": {"prompt": "ping", "stream": False}})
        check("a generation test with no model -> 400", r.status_code == 400, f"got {r.status_code}: {r.text[:120]}")

        r = client.post("/v1/management/api-test", json={"kind": "generate", "body": {"model": "qwen3:4b", "prompt": "ping", "stream": False}})
        res = r.json()
        check("non-streaming generation answers 200", r.status_code == 200 and res.get("ok") is True and res.get("status") == 200, str(res)[:200])
        check("the envelope names the endpoint and transport", res.get("endpoint") == "/api/generate" and res.get("stream") is False, str(res)[:160])
        check("the response is parsed as JSON", (res.get("body") or {}).get("_echo", {}).get("prompt") == "ping", str(res)[:200])
        check("the raw text is kept beside it", '"_echo"' in res.get("text", ""), res.get("text", "")[:120])
        check("routing preview resolves a plain name", res.get("model") == "qwen3:4b" and res.get("upstream") == "mock-ollama" and res.get("injected") is None, str(res)[:200])
        check("duration is measured, first byte is not (non-stream)", isinstance(res.get("duration_ms"), int) and res.get("ttft_ms") is None, str(res)[:160])
        check("the upstream content type is reported", res.get("content_type") == "application/json", str(res.get("content_type")))
        check("nothing was truncated", res.get("truncated") is False)

        r = client.post("/v1/management/api-test", json={"kind": "generate", "body": {"model": "q35", "prompt": "ping", "stream": False}})
        res = r.json()
        check("a derived alias reports the model it runs on", res.get("requested_model") == "q35" and res.get("model") == "qwen3:8b", str(res)[:200])
        check("the alias's fixed parameters are injected", res.get("injected") == {"think": False}, str(res)[:200])
        forwarded = (res.get("body") or {}).get("_echo") or {}
        check("the upstream really received the rewritten body", forwarded.get("model") == "qwen3:8b" and forwarded.get("think") is False, str(forwarded)[:200])
        check("num_ctx clamping applies to a test call too", (forwarded.get("options") or {}).get("num_ctx") == 8192, str(forwarded)[:200])

        r = client.post("/v1/management/api-test", json={"kind": "generate", "body": {"model": "qwen3:4b", "prompt": "go"}})
        res = r.json()
        check("omitting stream follows the gateway default (NDJSON)", res.get("stream") is True, str(res)[:160])
        r = client.post(
            "/v1/management/api-test",
            json={"kind": "generate", "body": {"model": "qwen3:4b", "prompt": "go", "stream": True}},
        )
        res = r.json()
        check("the streamed chunks are assembled into the text", '"response"' in res.get("text", "") and res.get("body") is None, str(res)[:200])
        check("first-byte timing lands for a stream", isinstance(res.get("ttft_ms"), int), str(res.get("ttft_ms")))
        check("the NDJSON content type is reported", res.get("content_type") == "application/x-ndjson", str(res.get("content_type")))

        r = client.post(
            "/v1/management/api-test",
            json={"kind": "chat", "body": {"model": "qwen3:4b", "messages": [{"role": "user", "content": "hi"}]}},
        )
        res = r.json()
        check("an OpenAI chat test is answered", res.get("ok") is True and (res.get("body") or {}).get("choices"), str(res)[:200])
        check("chat is reported as streaming by default, like the proxy", res.get("stream") is True, str(res)[:160])
        r = client.post(
            "/v1/management/api-test",
            json={"kind": "chat", "body": {"model": "qwen3:4b", "messages": [{"role": "user", "content": "hi"}], "stream": True}},
        )
        res = r.json()
        check("SSE chunks are captured for chat", res.get("stream") is True and "data:" in res.get("text", ""), str(res)[:200])

        r = client.post("/v1/management/api-test", json={"kind": "embed_ollama", "body": {"model": "qwen3:4b", "input": "hi"}})
        res = r.json()
        check("native embed test", res.get("ok") is True and (res.get("body") or {}).get("embeddings"), str(res)[:200])
        r = client.post("/v1/management/api-test", json={"kind": "embed_openai", "body": {"model": "qwen3:4b", "input": "hi"}})
        res = r.json()
        check("OpenAI embeddings test", res.get("ok") is True and (res.get("body") or {}).get("data"), str(res)[:200])

        r = client.post(
            "/v1/management/api-test",
            json={"kind": "rerank", "body": {"model": "qwen3-reranker:4b", "query": "keep_alive", "documents": ["doc one"], "top_n": 1}},
        )
        res = r.json()
        check("rerank test runs through the gateway's own scorer", res.get("ok") is True and (res.get("body") or {}).get("results"), str(res)[:200])
        check("the rerank envelope reports the configured endpoint", res.get("endpoint") == "/v1/rerank", str(res.get("endpoint")))

        r = client.post(
            "/v1/management/api-test",
            json={"kind": "systemone", "body": {"state": {"message": "hi"}, "questions": {"a": {"type": "noul", "instructions": "x"}}}},
        )
        res = r.json()
        check("an unreachable backend comes back as the gateway's 502, not a crash", res.get("status") == 502 and res.get("ok") is False, str(res)[:200])
        check("the refusal text explains which backend failed", "laya.example" in res.get("text", ""), res.get("text", "")[:160])
        r = client.post("/v1/management/api-test", json={"kind": "websearch", "body": {"query": "hi"}})
        res = r.json()
        check("a disabled capability answers 404 inside the envelope", res.get("status") == 404 and res.get("error") == "HTTP 404", str(res)[:200])

        r = client.post("/v1/management/api-test", json={"kind": "generate", "body": {"model": "qwen3:4b", "prompt": "__huge__", "stream": False}})
        res = r.json()
        check("an oversized response is cut at the cap", res.get("truncated") is True and len(res.get("text", "")) == 128 * 1024, str(len(res.get("text", ""))))

        logged = client.get("/v1/management/requests", params={"path": "api/generate"}).json().get("rows", [])
        check(
            "a tester call is written to the request log like any caller",
            any(row.get("upstream") == "mock-ollama" and row.get("model") in {"qwen3:4b", "qwen3:8b"} for row in logged),
            str(logged[:2]),
        )
        check("the test call itself never needed a caller API key", any(row.get("status") == 200 for row in logged), str(logged[:2]))

    print("[11] tools/websearch projection")


    ws = client.get("/v1/management/tools/websearch").json()
    check("GET /tools/websearch -> 200", client.get("/v1/management/tools/websearch").status_code == 200)
    check("websearch reports the fixed public endpoint", ws["endpoint"] == "/v1/tools/web-search", str(ws.get("endpoint")))
    check("websearch disabled state projected", ws["enabled"] is False, str(ws.get("enabled")))
    check("websearch without a provider", ws["provider_configured"] is False and ws["base_url"] is None, str(ws))
    check("websearch hides an unset provider key", ws["api_key"] is None, str(ws.get("api_key")))

    os.environ["WEBSEARCH_TEST_KEY"] = "provider-secret"
    try:
        patch_config(
            websearch={
                "enabled": True,
                "upstream": "mock-ollama",
                "path": "v1/web-search",
                "api_key_env": "WEBSEARCH_TEST_KEY",
                "timeout": 42.0,
            }
        )
        ws = client.get("/v1/management/tools/websearch").json()
        check(
            "websearch derives base_url from the upstream",
            ws["base_url"] == f"http://127.0.0.1:{MOCK_PORT}",
            str(ws.get("base_url")),
        )
        check("websearch names the provider upstream", ws["upstream"] == "mock-ollama", str(ws.get("upstream")))
        check("websearch echoes the upstream type", ws["upstream_type"] == "ollama", str(ws.get("upstream_type")))
        check("websearch keeps the env var name", ws["api_key_env"] == "WEBSEARCH_TEST_KEY", str(ws.get("api_key_env")))
        check("websearch masks the provider key value", ws["api_key"] == MASK, str(ws.get("api_key")))
        check("websearch projects the timeout", ws["timeout"] == 42.0, str(ws.get("timeout")))
        check("websearch flags a configured provider", ws["provider_configured"] is True, str(ws.get("provider_configured")))
        check("websearch enabled flag follows config", ws["enabled"] is True, str(ws.get("enabled")))
        check(
            "websearch never leaks the secret",
            "provider-secret" not in client.get("/v1/management/tools/websearch").text,
        )
    finally:
        del os.environ["WEBSEARCH_TEST_KEY"]
        patch_config(websearch={"enabled": False})

    # -------------------------------------------- legacy database upgrade ---
    print("[12] schema migration on an existing database")
    legacy_db = os.path.join(_TMP_DIR, "legacy.db")
    with sqlite3.connect(legacy_db) as conn:
        conn.execute(
            "CREATE TABLE requests ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL, "
            "model TEXT, upstream TEXT, status INTEGER NOT NULL, duration_ms INTEGER NOT NULL, ttfb_ms INTEGER, "
            "stream INTEGER NOT NULL DEFAULT 0, tokens INTEGER, req_size INTEGER, res_size INTEGER, client_ip TEXT, "
            "error TEXT, request_body TEXT, response_body TEXT, request_body_truncated INTEGER NOT NULL DEFAULT 0, "
            "response_body_truncated INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "INSERT INTO requests (ts, method, path, status, duration_ms) VALUES (?, 'GET', '/api/tags', 200, 5)",
            ("2026-01-01T00:00:00",),
        )

    from request_log import RequestLog

    RequestLog(db_path=legacy_db)._init_db()
    with sqlite3.connect(legacy_db) as conn:
        conn.row_factory = sqlite3.Row
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(requests)")}
        declared = {row["name"]: row["type"] for row in conn.execute("PRAGMA table_info(requests)")}
        old_rows = conn.execute("SELECT COUNT(*) AS c FROM requests").fetchone()["c"]
    check("old columns kept", old_rows == 1, str(old_rows))
    for column in ("ts_end", "requested_model", "query", "tokens_in", "tokens_out"):
        check(f"column added on upgrade: {column}", column in columns, str(sorted(columns)))
    check(
        "token columns are added as INTEGER",
        (declared.get("tokens_in"), declared.get("tokens_out")) == ("INTEGER", "INTEGER"),
        str((declared.get("tokens_in"), declared.get("tokens_out"))),
    )

    # ------------------------------------------------ console static surface ---
    print("[13] web console static serving")
    dist = main.CONSOLE_DIST
    if (dist / "index.html").is_file():
        r = client.get("/admin", follow_redirects=False)
        check("/admin redirects to the trailing-slash entrypoint -> 307",
              r.status_code == 307 and r.headers.get("location") == "/admin/",
              f"got {r.status_code} {r.headers.get('location')}")
        r = client.get("/admin/")
        check("console shell is served without credentials -> 200",
              r.status_code == 200 and "text/html" in r.headers.get("content-type", ""),
              f"got {r.status_code} {r.headers.get('content-type')}")
        check("shell mounts the SPA root", '<div id="app">' in r.text, r.text[:120])
        check("shell declares the tab icon under the /admin/ base",
              '/admin/favicon.svg' in r.text, r.text[:200])
        r = client.get("/admin/favicon.svg")
        check("tab icon is served as svg",
              r.status_code == 200 and "image/svg+xml" in r.headers.get("content-type", ""),
              f"got {r.status_code} {r.headers.get('content-type')}")
        check("tab icon is real markup, not the SPA fallback",
              r.text.lstrip().startswith("<svg"), r.text[:80])
        check("console keeps the scoped admin CSP",
              "frame-ancestors 'self'" in r.headers.get("content-security-policy", ""),
              r.headers.get("content-security-policy"))
        check("index.html is never cached blindly",
              "no-cache" in r.headers.get("cache-control", ""), r.headers.get("cache-control"))
        r = client.get("/admin/requests")
        check("deep link falls back to the SPA shell -> 200",
              r.status_code == 200 and '<div id="app">' in r.text, f"got {r.status_code}")
        hashed = sorted((dist / "assets").glob("*.js"))
        r = client.get(f"/admin/assets/{hashed[0].name}")
        check("hashed asset served -> 200", r.status_code == 200, f"got {r.status_code}")
        check("hashed asset is cached immutably",
              "immutable" in r.headers.get("cache-control", ""), r.headers.get("cache-control"))
        r = client.get("/admin/%2e%2e%2f%2e%2e%2fapp%2fmain.py")
        check("a ../ path cannot escape the built directory",
              r.status_code == 200 and '<div id="app">' in r.text, f"got {r.status_code}: {r.text[:120]}")
    else:
        check("console shell needs a build first", False, f"no {dist / 'index.html'}: run npm run build in web/")

    empty_dist = Path(_TMP_DIR) / "no-dist"
    empty_dist.mkdir(parents=True, exist_ok=True)
    original_dist = main.CONSOLE_DIST
    main.CONSOLE_DIST = empty_dist
    try:
        r = client.get("/admin/")
        check("unbuilt console -> 503", r.status_code == 503, f"got {r.status_code}")
        check("503 explains how to build", "npm run build" in r.json().get("detail", ""), r.text[:160])
    finally:
        main.CONSOLE_DIST = original_dist

    print(f"\n{'=' * 60}\nPASSED: {PASSED}  FAILED: {FAILED}\n{'=' * 60}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(run())
    finally:
        MOCK_SERVER.shutdown()
