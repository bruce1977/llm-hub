#!/usr/bin/env python3
"""
Tests for the web-console management API (``/v1/management``) and the request log.

Runs entirely against a local mock Ollama upstream: no live models, no external
services. Covers admin auth (cookie only; password-as-header rejected; 503 when unset),
upstream probing and
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
                        {"name": "qwen3-reranker:4b"},
                        {"name": "qwen3:*"},
                    ]
                }
            )
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
            "models": ["qwen3:8b", "qwen3-reranker:4b", "qwen3:*"],
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
        "q35": {
            "model": "qwen3:8b",
            "upstream": "mock-ollama",
            "params": {"think": False},
            "description": "short name for qwen3:8b",
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
        "allow_docs": False,
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
        check("ping with a gateway API key -> 401", r.status_code == 401, f"got {r.status_code}")
        r = client.post("/admin/login", json={"password": "admin-secret-pw"})
        check("legacy /admin/login reuses the same flow -> 200", r.status_code == 200, f"got {r.status_code}")

        # -------------------------------------------------------- overview ---
        print("[2] overview / upstream probes")
        r = client.get("/v1/management/overview")
        check("GET /overview -> 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        data = r.json()
        kpi = data.get("kpi", {})
        check("one upstream online of two", kpi.get("upstreams_online") == 1 and kpi.get("upstreams_total") == 2, str(kpi))
        check("three running models", kpi.get("running_models") == 3, str(kpi))
        check("vram used = 14.0 GiB", kpi.get("vram_used_gib") == 14.0, str(kpi))
        check("vram total = 32.0 GiB", kpi.get("vram_total_gib") == 32.0, str(kpi))
        devices = {m["model"]: m["device"] for m in data.get("running_models", [])}
        check("device classification gpu/partial/cpu", devices == {
            "qwen3:8b": "gpu",
            "qwen3-reranker:4b": "partial",
            "tiny:cpu": "cpu",
        }, str(devices))
        up = {i["name"]: i for i in data.get("upstreams", [])}
        check("mock upstream online", up["mock-ollama"]["status"] == "online", str(up.get("mock-ollama")))
        check("dead upstream offline with error", up["dead-ollama"]["status"] == "offline" and up["dead-ollama"]["error"])

        print("[3] upstream listing / detail")
        r = client.get("/v1/management/upstreams")
        check("GET /upstreams -> 200", r.status_code == 200, f"got {r.status_code}")
        check("two upstreams listed", len(r.json().get("upstreams", [])) == 2)
        r = client.get("/v1/management/upstreams/mock-ollama")
        check("upstream detail -> 200", r.status_code == 200, f"got {r.status_code}")
        detail = r.json()
        check("installed models from /api/tags", detail.get("models") == ["qwen3:8b", "qwen3-reranker:4b", "qwen3:*"], str(detail.get("models")))
        check("detail reports 3 loaded models", detail.get("loaded") == 3, str(detail.get("loaded")))
        check("detail has no models_error", detail.get("models_error") is None, str(detail.get("models_error")))
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
        check("wildcard declaration projected", models.get("qwen3:*", {}).get("wildcard") is True, str(models.get("qwen3:*")))
        check("alias resolved onto the model", models["qwen3:8b"]["alias"] == "q35", str(models.get("qwen3:8b")))
        check("num_ctx override projected", models["qwen3:8b"]["num_ctx"] == 8192, str(models.get("qwen3:8b")))
        check("running flag from /api/ps", models["qwen3:8b"]["running"] is True, str(models.get("qwen3:8b")))
        check("aliases list includes q35", any(a["alias"] == "q35" and a["target"] == "qwen3:8b" for a in llm.get("aliases", [])))
        check("num_ctx_overrides echoed", llm.get("num_ctx_overrides", {}).get("qwen3:8b") == 8192)

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
        check("failed call logged with upstream", any(
            r["status"] == 502 and r["upstream"] == "dead-ollama" for r in rows
        ))
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
    print("[10] tools/websearch projection")

    def patch_config(**blocks) -> None:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
        raw.update(blocks)
        with open(_CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, ensure_ascii=False)
        client.app.state.manager.reload()

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
    print("[11] schema migration on an existing database")
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
        old_rows = conn.execute("SELECT COUNT(*) AS c FROM requests").fetchone()["c"]
    check("old columns kept", old_rows == 1, str(old_rows))
    for column in ("ts_end", "requested_model", "query"):
        check(f"column added on upgrade: {column}", column in columns, str(sorted(columns)))

    print(f"\n{'=' * 60}\nPASSED: {PASSED}  FAILED: {FAILED}\n{'=' * 60}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(run())
    finally:
        MOCK_SERVER.shutdown()
