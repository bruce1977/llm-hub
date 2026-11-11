#!/usr/bin/env python3
"""
Unit/integration tests for the new gateway features, exercised with FastAPI's
TestClient (no live upstreams or System One backend required).

Covers:
  - /v1/models and /api/ps aggregation with no upstreams (graceful empty result)
  - POST /v1/systemone: gateway API-key auth + correct forwarding to the backend,
    including the "local" model sentinel (no injection) vs a concrete model (injected)
  - /docs gate (security.internal.expose_docs): 200 + schema when enabled, 404 when off
  - public listener: config-driven path_allowlist (method->regex) + mandatory key

Run:
    python tests/test_gateway_new.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

# Configure before importing the app so ConfigManager picks up our temp config.
_CFG = {
    "server": {"host": "127.0.0.1", "port": 8000},
    "routing": {"strict": False},
    "upstreams": [],  # no live upstreams -> aggregation must return empty, never crash
    "default_upstream": None,
    "aliases": {},
    "rerank": {"enabled": True, "models": []},
    "systemone": {
        "enabled": True,
        "backend": "http://laya.example",
        "model": "multilingual",
        "api_key_env": "SYSTEMONE_SERVER_API_KEY",
        "path": "v1/systemone",
        "timeout": 60.0,
    },
    "websearch": {
        "enabled": True,
        "upstream": "opencode",
        "path": "v1/web-search",
        "api_key_env": "OPENCODE_GATEWAY_API_KEY",
        "timeout": 30.0,
    },
    "security": {
        "internal": {"allow_anonymous": False, "expose_version": True, "expose_docs": False},
        "public": {"port": None, "path_allowlist": {}, "admin_endpoints": "deny"},
        "rate_limit": {"enabled": False},
    },
}

with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as _tmp:
    _tmp.write(json.dumps(_CFG, ensure_ascii=False))
    _cfg_path = _tmp.name
os.environ["CONFIG_PATH"] = _cfg_path
# One data file per run: `llmhub.db` holds the api_keys table, and this suite writes its own
# key row there (keys are minted in the console, never seeded from the environment).
os.environ["LLMHUB_DB"] = f"{_cfg_path}.db"
os.environ["SYSTEMONE_SERVER_API_KEY"] = "systemone-secret-env-key"
os.environ["OPENCODE_GATEWAY_API_KEY"] = "opencode-secret-env-key"

# Make `app/` importable so the suite also runs as `python tests/test_gateway_new.py`.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

API_KEY = "sk-test-fixture-key"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def seed_api_keys(*secrets: str) -> None:
    """Create caller-key rows in llmhub.db before the app starts (console-only in production)."""
    import sqlite3

    from key_store import SCHEMA, hash_key, mask_key, now_ts

    db = Path(os.environ["LLMHUB_DB"])
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


seed_api_keys(API_KEY)

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


def make_fake_response(body: bytes = b'{"answers":{},"model":"multilingual"}', status: int = 200):
    return SimpleNamespace(
        content=body,
        status_code=status,
        headers=__import__("httpx").Headers({"content-type": "application/json"}),
    )


def run() -> int:
    with TestClient(main.app) as client:
        app = client.app

        # --------------------------------------------- /v1/models (empty) ---
        print("[1] /v1/models aggregation (no upstreams)")
        r = client.get("/v1/models", headers=HEADERS)
        check("GET /v1/models -> 200", r.status_code == 200, f"got {r.status_code}: {r.text[:120]}")
        if r.status_code == 200:
            data = r.json()
            check("models data is a list", isinstance(data.get("data"), list))
            check("models data is empty", len(data.get("data", [])) == 0)

        # --------------------------------------------- /api/ps (empty) ---
        print("[2] /api/ps aggregation (no upstreams)")
        r = client.get("/api/ps", headers=HEADERS)
        check("GET /api/ps -> 200", r.status_code == 200, f"got {r.status_code}: {r.text[:120]}")
        if r.status_code == 200:
            data = r.json()
            check("ps models is a list", isinstance(data.get("models"), list))
            check("ps models is empty", len(data.get("models", [])) == 0)

        # ------------------- discovery forwards upstream.headers ---
        print("[2b] /v1/models discovery sends upstream.headers")
        from config import UpstreamConfig

        captured: dict = {}

        class _FakeDiscoveryClient:
            async def get(self, url, **kwargs):
                captured["url"] = url
                captured["headers"] = kwargs.get("headers")
                return SimpleNamespace(
                    status_code=200,
                    raise_for_status=lambda: None,
                    json=lambda: {"data": [{"id": "opencode/big-pickle"}]},
                )

        original_client = app.state.client
        original_upstreams = app.state.manager._config.upstreams
        app.state.client = _FakeDiscoveryClient()
        app.state.manager._config.upstreams = [
            UpstreamConfig(
                name="opencode-auth",
                type="opencode",
                base_url="http://provider.test",
                models=["opencode/*"],
                headers={"Authorization": "Bearer t0k"},
            )
        ]
        try:
            r = client.get("/v1/models", headers=HEADERS)
            ids = [m["id"] for m in r.json().get("data", [])] if r.status_code == 200 else []
            check("/v1/models discovery -> 200", r.status_code == 200, f"got {r.status_code}")
            check("authenticated provider models are listed", "opencode/big-pickle" in ids, str(ids))
            check("discovery hit the tags path", captured.get("url") == "http://provider.test/v1/models", str(captured.get("url")))
            check(
                "discovery sent upstream.headers",
                (captured.get("headers") or {}).get("Authorization") == "Bearer t0k",
                str(captured.get("headers")),
            )
        finally:
            app.state.client = original_client
            app.state.manager._config.upstreams = original_upstreams
            main._UPSTREAM_MODELS_CACHE.pop("opencode-auth", None)

        # --------------------------------------------- /docs gate ---
        print("[3] /docs gated on the internal zone + security.internal.expose_docs")
        r = client.get("/docs")
        check("GET /docs -> 404 when expose_docs=false", r.status_code == 404, f"got {r.status_code}")

        # The internal listener serves the docs when opted in.
        app.state.manager._config.security.internal.expose_docs = True
        r = client.get("/docs")
        check("GET /docs -> 200 on the internal listener", r.status_code == 200, f"got {r.status_code}")
        r = client.get("/openapi.json")
        spec = r.json()
        paths = set(spec.get("paths", {}).keys())
        check("/openapi.json includes /v1/systemone", "/v1/systemone" in paths, str(sorted(paths)))
        check("/openapi.json includes /v1/tools/web-search", "/v1/tools/web-search" in paths, str(sorted(paths)))
        check("/openapi.json includes /api/ps", "/api/ps" in paths, str(sorted(paths)))
        check("/openapi.json includes /v1/models", "/v1/models" in paths, str(sorted(paths)))
        check("/openapi.json includes /", "/" in paths, str(sorted(paths)))
        check("/openapi.json dropped /healthz", "/healthz" not in paths, str(sorted(paths)))

        # the Ollama-native completion endpoint is explicitly documented
        gen_op = (spec.get("paths", {}).get("/api/generate", {}) or {}).get("post", {})
        check("/openapi.json includes /api/generate", bool(gen_op), str(sorted(paths)))
        gen_schema = ((gen_op.get("requestBody") or {}).get("content") or {}).get("application/json", {}).get(
            "schema", {}
        )
        check(
            "generate: request schema requires model + prompt",
            gen_schema.get("required") == ["model", "prompt"],
            str(gen_schema)[:160],
        )
        check(
            "generate: request schema covers raw / logprobs / format / options / keep_alive",
            {"raw", "logprobs", "top_logprobs", "format", "options", "keep_alive", "context"}
            <= set(gen_schema.get("properties", {})),
            str(sorted(gen_schema.get("properties", {}))),
        )
        gen_ok = ((gen_op.get("responses") or {}).get("200", {}) or {}).get("content", {})
        check(
            "generate: 200 documents both NDJSON and JSON bodies",
            {"application/x-ndjson", "application/json"} <= set(gen_ok),
            str(sorted(gen_ok)),
        )

        # The other pass-through forwards are documented too, so nothing the gateway
        # actually serves is invisible in /docs.
        documented = {
            "/api/chat": {"application/x-ndjson", "application/json"},
            "/v1/chat/completions": {"application/json", "text/event-stream"},
            "/v1/completions": {"application/json", "text/event-stream"},
            "/api/embed": {"application/json"},
            "/v1/embeddings": {"application/json"},
        }
        for doc_path, media in documented.items():
            op = ((spec.get("paths", {}).get(doc_path, {}) or {}).get("post", {}) or {})
            check(f"/openapi.json includes POST {doc_path}", bool(op), str(sorted(paths)))
            props = (((op.get("requestBody") or {}).get("content") or {})
                     .get("application/json", {}).get("schema", {}).get("properties", {}))
            check(f"{doc_path}: request schema documents model", "model" in props, str(sorted(props))[:160])
            responses = ((op.get("responses") or {}).get("200", {}) or {}).get("content", {})
            check(f"{doc_path}: 200 documents {sorted(media)}", media <= set(responses), str(sorted(responses)))
            check(f"{doc_path}: documents 404 and 405",
                  {"404", "405"} <= set(op.get("responses") or {}), str(sorted(op.get("responses") or {})))
        chat_props = (((spec["paths"]["/api/chat"]["post"].get("requestBody") or {}).get("content") or {})
                      .get("application/json", {}).get("schema", {}).get("properties", {}))
        check("/api/chat: request schema requires messages",
              "messages" in chat_props, str(sorted(chat_props))[:160])
        emb_props = (((spec["paths"]["/api/embed"]["post"].get("requestBody") or {}).get("content") or {})
                     .get("application/json", {}).get("schema", {}).get("properties", {}))
        check("/api/embed: request schema documents input + truncate",
              {"input", "truncate"} <= set(emb_props), str(sorted(emb_props))[:160])
        oai_props = (((spec["paths"]["/v1/embeddings"]["post"].get("requestBody") or {}).get("content") or {})
                     .get("application/json", {}).get("schema", {}).get("properties", {}))
        check("/v1/embeddings: OpenAI input field", "input" in oai_props, str(sorted(oai_props))[:160])

        # Swagger auth: shared Authorize schemes + an inline X-API-Key input
        # box inside every endpoint's request section (Parameters -> Try it out).
        schemes = spec.get("components", {}).get("securitySchemes", {})
        check(
            "securitySchemes exposes BearerAuth",
            schemes.get("BearerAuth", {}).get("scheme") == "bearer",
            str(schemes),
        )
        check(
            "securitySchemes exposes X-API-Key",
            schemes.get("ApiKeyAuth", {}).get("name") == "X-API-Key",
            str(schemes),
        )
        check("global security requirement set", bool(spec.get("security")), str(spec.get("security")))

        operations = [
            (path, method, op)
            for path, item in spec["paths"].items()
            for method, op in item.items()
            if isinstance(op, dict)
        ]

        def key_params(op: dict) -> list[dict]:
            return [p for p in op.get("parameters", []) if p.get("name") == "X-API-Key" and p.get("in") == "header"]

        missing = [f"{m.upper()} {p}" for p, m, op in operations if not key_params(op)]
        check("every request area exposes an X-API-Key input", not missing, str(missing))
        duplicated = [f"{m.upper()} {p}" for p, m, op in operations if len(key_params(op)) != 1]
        check("X-API-Key input appears exactly once per operation", not duplicated, str(duplicated))
        models_key = key_params(spec["paths"]["/v1/models"]["get"])
        check(
            "key input is a free-form string header",
            bool(models_key) and models_key[0].get("schema", {}).get("type") == "string",
            str(models_key),
        )
        app.state.manager._config.security.internal.expose_docs = False

        # --------------------------------------------- /v1/systemone: auth ---
        print("[4] POST /v1/systemone authentication")
        r = client.post("/v1/systemone", json={"state": {"message": "hi"}, "questions": {}})
        check("systemone without key -> 401", r.status_code == 401, f"got {r.status_code}")
        r = client.post(
            "/v1/systemone",
            headers={"Authorization": "Bearer wrong"},
            json={"state": {"message": "hi"}, "questions": {}},
        )
        check("systemone with bad key -> 403", r.status_code == 403, f"got {r.status_code}")

        # --------------------------------------------- /v1/systemone: forwarding ---
        print("[5] POST /v1/systemone forwarding to the backend")
        fake = make_fake_response()
        app.state.client.request = AsyncMock(return_value=fake)
        payload = {
            "state": {"message": "I was charged twice"},
            "questions": {"refund": {"type": "noul", "instructions": "refund?"}},
        }
        r = client.post("/v1/systemone", headers=HEADERS, json=payload)
        check("systemone forwards -> 200", r.status_code == 200, f"got {r.status_code}: {r.text[:120]}")
        check("systemone echoes body", r.json().get("answers") == {}, r.text[:120])

        # inspect the upstream call
        call = app.state.client.request.call_args
        kwargs = call.kwargs
        check("forwarded to configured backend", kwargs["url"] == "http://laya.example/v1/systemone", kwargs.get("url"))
        check(
            "forwarded with the backend API key",
            kwargs["headers"].get("Authorization") == "Bearer systemone-secret-env-key",
            kwargs["headers"].get("Authorization"),
        )
        check("concrete model injected", kwargs["json"].get("model") == "multilingual", str(kwargs.get("json")))
        # caller credentials must NOT be forwarded
        check("caller key not forwarded", "X-Api-Key" not in {k.lower() for k in kwargs["headers"]})

        # --------------------------------------------- /v1/systemone: unsigned modes ---
        # `api_key_env: ''` is a valid unsigned mode (an Ollama backend ignores Authorization),
        # NOT a missing variable with a blank name. A NAMED variable that is unset must still
        # warn - and must print its name, so the operator knows what to set.
        print("[5b] POST /v1/systemone unsigned modes (empty env NAME vs missing VALUE)")

        class _Warnings(logging.Handler):
            def __init__(self):
                super().__init__(level=logging.WARNING)
                self.messages: list[str] = []

            def emit(self, record):
                self.messages.append(record.getMessage())

        hub_log = logging.getLogger("llm_hub")
        captured = _Warnings()
        hub_log.addHandler(captured)
        old_level = hub_log.level
        hub_log.setLevel(logging.WARNING)
        so_cfg = app.state.manager._config.systemone
        saved_env = so_cfg.api_key_env
        try:
            so_cfg.api_key_env = ""
            app.state.client.request = AsyncMock(return_value=make_fake_response())
            r = client.post("/v1/systemone", headers=HEADERS, json=payload)
            call = app.state.client.request.call_args.kwargs
            check("empty api_key_env still forwards -> 200", r.status_code == 200, r.text[:120])
            check(
                "empty api_key_env sends no Authorization",
                "authorization" not in {k.lower() for k in call["headers"]},
                str(call["headers"]),
            )
            check(
                "empty api_key_env logs no nameless warning",
                not any("Environment variable  is not set" in m for m in captured.messages),
                str(captured.messages),
            )

            captured.messages.clear()
            so_cfg.api_key_env = "SYSTEMONE_SERVER_API_KEY"
            saved_value = os.environ.pop("SYSTEMONE_SERVER_API_KEY", None)
            try:
                app.state.client.request = AsyncMock(return_value=make_fake_response())
                client.post("/v1/systemone", headers=HEADERS, json=payload)
                call = app.state.client.request.call_args.kwargs
                check(
                    "named-but-unset key forwards unsigned",
                    "authorization" not in {k.lower() for k in call["headers"]},
                    str(call["headers"]),
                )
                check(
                    "warning names the missing variable",
                    any("Environment variable SYSTEMONE_SERVER_API_KEY is not set" in m for m in captured.messages),
                    str(captured.messages),
                )
            finally:
                if saved_value is not None:
                    os.environ["SYSTEMONE_SERVER_API_KEY"] = saved_value
        finally:
            so_cfg.api_key_env = saved_env
            hub_log.removeHandler(captured)
            hub_log.setLevel(old_level)

        # --------------------------------------------- /v1/systemone: "local" sentinel ---
        print("[6] 'local' model sentinel (no injection)")
        app.state.manager._config.systemone.model = "local"
        app.state.client.request = AsyncMock(return_value=make_fake_response())
        r = client.post("/v1/systemone", headers=HEADERS, json=payload)
        call = app.state.client.request.call_args
        check(
            "'local' sentinel not forwarded as model", "model" not in call.kwargs["json"], str(call.kwargs.get("json"))
        )
        app.state.manager._config.systemone.model = "multilingual"

        # --------------------------------------------- /v1/systemone disabled ---
        print("[7] /v1/systemone disabled toggle")
        app.state.manager._config.systemone.enabled = False
        r = client.post("/v1/systemone", headers=HEADERS, json=payload)
        check("disabled systemone -> 404", r.status_code == 404, f"got {r.status_code}")

        # --------------------------------------------- / root + /healthz removal ---
        print("[8] GET / (system information) and /healthz removal")
        r = client.get("/")
        check("GET / -> 200 without a key", r.status_code == 200, f"got {r.status_code}: {r.text[:120]}")
        if r.status_code == 200:
            data = r.json()
            check("/ returns the system name", data.get("name") == "LLM Hub", str(data))
            check("/ returns the gateway version", bool(data.get("version")), str(data))
        r = client.get("/healthz")
        check("/healthz is no longer a health route (401)", r.status_code == 401, f"got {r.status_code}")

        # --------------------------------------------- /v1/tools/web-search ---
        print("[9] POST /v1/tools/web-search (web search proxy)")
        import httpx
        from config import UpstreamConfig

        ws = app.state.manager._config.websearch
        ws_payload = {"query": "Ollama keep_alive 参数作用", "top_k": 3, "recency": "year"}
        provider_json = {
            "query": "Ollama keep_alive 参数作用",
            "model": "opencode/big-pickle",
            "engine": "opencode-websearch",
            "answer": None,
            "truncated": True,
            "results": [{"title": "FAQ", "url": "https://x/faq", "snippet": "s", "paragraphs": ["p"]}],
            "text": "passage text",
            "sources": ["https://x/faq"],
        }
        provider_body = json.dumps(provider_json, ensure_ascii=False).encode("utf-8")

        # toggle off -> 404
        ws.enabled = False
        r = client.post("/v1/tools/web-search", headers=HEADERS, json=ws_payload)
        check("websearch disabled -> 404", r.status_code == 404, f"got {r.status_code}")
        ws.enabled = True

        # enabled but the named provider upstream does not exist -> 503
        app.state.manager._config.upstreams = []
        r = client.post("/v1/tools/web-search", headers=HEADERS, json=ws_payload)
        check("websearch with a missing upstream name -> 503", r.status_code == 503, f"got {r.status_code}: {r.text[:160]}")

        # add the provider upstream and forward
        app.state.manager._config.upstreams = [
            UpstreamConfig(name="opencode", type="opencode", base_url="http://search.example", models=["opencode/*"])
        ]
        app.state.client.request = AsyncMock(return_value=make_fake_response(provider_body))
        r = client.post("/v1/tools/web-search", headers=HEADERS, json=ws_payload)
        check("websearch forwards -> 200", r.status_code == 200, f"got {r.status_code}: {r.text[:160]}")
        check("websearch passes the provider body through", r.json().get("engine") == "opencode-websearch", r.text[:160])
        check("websearch keeps UTF-8 passages", "参数作用" in r.json().get("query", ""), r.text[:160])

        call = app.state.client.request.call_args.kwargs
        check("forwarded to the provider upstream path", call["url"] == "http://search.example/v1/web-search", call.get("url"))
        check("body forwarded verbatim", call["json"] == ws_payload, str(call.get("json")))
        check("forwarded with the provider key", call["headers"].get("Authorization") == "Bearer opencode-secret-env-key", str(call["headers"].get("Authorization")))
        check("caller key not forwarded", "x-api-key" not in {k.lower() for k in call["headers"]})
        check("per-request timeout honored", call["timeout"] == 30.0, str(call.get("timeout")))

        # unsigned when the key env var is unset (and no Authorization leaks through)
        os.environ.pop("OPENCODE_GATEWAY_API_KEY")
        app.state.client.request = AsyncMock(return_value=make_fake_response(provider_body))
        client.post("/v1/tools/web-search", headers=HEADERS, json=ws_payload)
        call = app.state.client.request.call_args.kwargs
        check("no provider key -> unsigned call", "authorization" not in {k.lower() for k in call["headers"]}, str(call["headers"]))
        os.environ["OPENCODE_GATEWAY_API_KEY"] = "opencode-secret-env-key"

        # model is never injected or alias-resolved (pure pass-through)
        app.state.client.request = AsyncMock(return_value=make_fake_response(provider_body))
        client.post("/v1/tools/web-search", headers=HEADERS, json={"query": "q"})
        call = app.state.client.request.call_args.kwargs
        check("absent model is not injected", "model" not in call["json"], str(call.get("json")))

        # upstream errors are mapped to gateway statuses
        app.state.client.request = AsyncMock(side_effect=httpx.ConnectError("down"))
        r = client.post("/v1/tools/web-search", headers=HEADERS, json=ws_payload)
        check("provider unreachable -> 502", r.status_code == 502, f"got {r.status_code}")
        app.state.client.request = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
        r = client.post("/v1/tools/web-search", headers=HEADERS, json=ws_payload)
        check("provider timeout -> 504", r.status_code == 504, f"got {r.status_code}")

        # body validation + method allowlist
        app.state.client.request = AsyncMock(return_value=make_fake_response(provider_body))
        r = client.post(
            "/v1/tools/web-search", headers={**HEADERS, "Content-Type": "application/json"}, content=b"not json"
        )
        check("non-JSON body -> 400", r.status_code == 400, f"got {r.status_code}")
        r = client.post("/v1/tools/web-search", headers=HEADERS, json=["array"])
        check("JSON array body -> 400", r.status_code == 400, f"got {r.status_code}")
        r = client.get("/v1/tools/web-search", headers=HEADERS)
        check("GET /v1/tools/web-search -> 405", r.status_code == 405, f"got {r.status_code}")
        r = client.post("/v1/tools/web-search", json=ws_payload)
        check("websearch without a key -> 401", r.status_code == 401, f"got {r.status_code}")

        # the endpoint is documented with a real request schema (query required)
        app.state.manager._config.security.internal.expose_docs = True
        spec = client.get("/openapi.json").json()
        op = spec["paths"]["/v1/tools/web-search"]["post"]
        schema = op["requestBody"]["content"]["application/json"]["schema"]
        check("docs: websearch documents the request schema", schema.get("required") == ["query"], str(schema)[:160])
        check("docs: websearch request has top_k/recency fields", {"top_k", "recency"} <= set(schema["properties"]), str(sorted(schema["properties"])))
        check("docs: websearch response schema declares text/sources", set(op["responses"]["200"]["content"]["application/json"]["schema"]["required"]) >= {"text", "sources"})
        app.state.manager._config.security.internal.expose_docs = False

        # --------------------------------------------- public zone envelope ---
        print("[10] public listener: config-driven path allowlist + mandatory key")
        pub = app.state.manager._config.security.public
        pub.port = 80  # TestClient's scope["server"] port; makes this socket the public zone
        pub.path_allowlist = {"GET": [r"^/v1/models$"], "POST": [r"^/v1/chat/completions$"]}
        app.state.public_ports = frozenset({80})
        try:
            for probe in ("/health", "/docs", "/openapi.json", "/probe", "/v1/management/config", "/admin/"):
                r = client.get(probe, headers=HEADERS)
                check(f"public GET {probe} -> 404", r.status_code == 404, f"got {r.status_code}")
            r = client.get("/v1/models", headers=HEADERS)
            check("public GET /v1/models with key -> 200", r.status_code == 200, f"got {r.status_code}")
            r = client.get("/v1/models")
            check("public GET /v1/models without key -> 401", r.status_code == 401, f"got {r.status_code}")
            r = client.get("/v1/chat/completions", headers=HEADERS)
            check("public wrong method on an allowed path -> 404", r.status_code == 404, f"got {r.status_code}")
        finally:
            pub.port = None
            pub.path_allowlist = {}
            app.state.public_ports = frozenset()

        # cleanup: leave the fixture config as the other sections expect
        app.state.manager._config.upstreams = []
        ws.enabled = False

    return 1 if FAILED else 0


if __name__ == "__main__":
    rc = run()
    print(f"\n=== Result: {PASSED} passed, {FAILED} failed ===\n")
    raise SystemExit(rc)
