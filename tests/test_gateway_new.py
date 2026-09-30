#!/usr/bin/env python3
"""
Unit/integration tests for the new gateway features, exercised with FastAPI's
TestClient (no live upstreams or laya-server required).

Covers:
  - /v1/models and /api/ps aggregation with no upstreams (graceful empty result)
  - POST /v1/systemone: gateway API-key auth + correct forwarding to laya-server,
    including the "local" model sentinel (no injection) vs a concrete model (injected)
  - /docs switch (security.allow_docs): 404 when off, 200 + schema when on

Run:
    python tests/test_gateway_new.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

# Configure before importing the app so ConfigManager picks up our temp config.
_CFG = {
    "server": {"host": "127.0.0.1", "port": 8000},
    "auth": {"enabled": True, "api_keys": [], "allow_anonymous_health": True},
    "routing": {"strict": False},
    "upstreams": [],  # no live upstreams -> aggregation must return empty, never crash
    "default_upstream": None,
    "aliases": {},
    "rerank": {"enabled": True, "models": []},
    "systemone": {
        "enabled": True,
        "backend": "http://laya.example",
        "model": "multilingual",
        "api_key_env": "LAYA_SERVER_API_KEY",
        "path": "v1/systemone",
        "timeout": 60.0,
    },
    "security": {
        "admin_endpoints": "deny",
        "allow_docs": False,
        "allowed_http_methods": ["GET", "POST"],
        "path_allowlist": [],
        "rate_limit": {"enabled": False},
    },
}

with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as _tmp:
    _tmp.write(json.dumps(_CFG, ensure_ascii=False))
    _cfg_path = _tmp.name
os.environ["CONFIG_PATH"] = _cfg_path
os.environ["GATEWAY_API_KEYS"] = "sk-test-fixture-key"
os.environ["LAYA_SERVER_API_KEY"] = "laya-secret-env-key"

# Make `app/` importable so the suite also runs as `python tests/test_gateway_new.py`.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

API_KEY = "sk-test-fixture-key"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

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

        # --------------------------------------------- /docs hidden ---
        print("[3] /docs switch (off by default)")
        r = client.get("/docs")
        check("GET /docs -> 404 when allow_docs=false", r.status_code == 404, f"got {r.status_code}")

        # enable docs at runtime and re-check
        app.state.manager._config.security.allow_docs = True
        r = client.get("/docs")
        check("GET /docs -> 200 when allow_docs=true", r.status_code == 200, f"got {r.status_code}")
        r = client.get("/openapi.json")
        spec = r.json()
        paths = set(spec.get("paths", {}).keys())
        check("/openapi.json includes /v1/systemone", "/v1/systemone" in paths, str(sorted(paths)))
        check("/openapi.json includes /api/ps", "/api/ps" in paths, str(sorted(paths)))
        check("/openapi.json includes /v1/models", "/v1/models" in paths, str(sorted(paths)))
        check("/openapi.json includes /", "/" in paths, str(sorted(paths)))
        check("/openapi.json dropped /healthz", "/healthz" not in paths, str(sorted(paths)))

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
        app.state.manager._config.security.allow_docs = False

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
        print("[5] POST /v1/systemone forwarding to laya-server")
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
            "forwarded with laya API key",
            kwargs["headers"].get("Authorization") == "Bearer laya-secret-env-key",
            kwargs["headers"].get("Authorization"),
        )
        check("concrete model injected", kwargs["json"].get("model") == "multilingual", str(kwargs.get("json")))
        # caller credentials must NOT be forwarded
        check("caller key not forwarded", "X-Api-Key" not in {k.lower() for k in kwargs["headers"]})

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

    return 1 if FAILED else 0


if __name__ == "__main__":
    rc = run()
    print(f"\n=== Result: {PASSED} passed, {FAILED} failed ===\n")
    raise SystemExit(rc)
