"""Ad-hoc verification of the new security controls (method allowlist, path
allowlist, rate limit, security headers). Plain runner (no pytest needed):
    python tests/verify_security.py

NOTE: CONFIG_PATH must be set *before* importing `config` (its CONFIG_PATH global
is bound at import time), so we create one fixed temp config and write each case to it.
"""

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

# Fixed temp config path, exported to the environment BEFORE config is imported.
_TMP = tempfile.mktemp(prefix="llm-hub-sec-", suffix=".json")
os.environ["CONFIG_PATH"] = _TMP

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "app"))

from fastapi.testclient import TestClient  # noqa: E402

import config as _config  # noqa: E402  (CONFIG_PATH already bound to _TMP)
from main import app  # noqa: E402

EXAMPLE = Path("data/example.config.json").read_text(encoding="utf-8")

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


@contextmanager
def client_with(**overrides):
    cfg = json.loads(EXAMPLE)
    # These tests exercise middleware-level controls, not auth; disable auth so the
    # gateway starts without keys (auth is covered by tests/smoke_test.py).
    cfg["auth"]["enabled"] = False
    cfg.setdefault("security", {}).update(overrides)
    Path(_TMP).write_text(json.dumps(cfg), encoding="utf-8")
    with TestClient(app) as c:  # enters lifespan -> builds ConfigManager from _TMP
        yield c


# 1. GET/POST only ------------------------------------------------------------
def test_methods() -> None:
    with client_with() as c:
        check("GET /health allowed (200)", c.get("/health").status_code == 200)
        check("PUT rejected (405)", c.put("/v1/chat/completions").status_code == 405)
        check("DELETE rejected (405)", c.request("DELETE", "/health").status_code == 405)
        check("HEAD rejected (405)", c.head("/health").status_code == 405)


# 2. security headers ---------------------------------------------------------
def test_headers() -> None:
    with client_with() as c:
        r = c.get("/health")
        check("X-Content-Type-Options: nosniff", r.headers.get("X-Content-Type-Options") == "nosniff")
        check("X-Frame-Options: DENY", r.headers.get("X-Frame-Options") == "DENY")
        check("CSP present", "default-src 'none'" in (r.headers.get("Content-Security-Policy") or ""))


# 3. path allowlist (regex) ---------------------------------------------------
def test_path_allowlist() -> None:
    with client_with(path_allowlist=[r"^/health$", r"^/v1/models$"]) as c:
        check("allowed /health (200)", c.get("/health").status_code == 200)
        check("allowed /v1/models (200)", c.get("/v1/models").status_code == 200)
        check("blocked /api/tags (403)", c.get("/api/tags").status_code == 403)
        r = c.post("/v1/chat/completions", json={"model": "x", "messages": []})
        check("blocked POST chat (403)", r.status_code == 403, f"got {r.status_code}")


# 4. invalid regex ignored, valid ones still apply -----------------------------
def test_bad_regex() -> None:
    with client_with(path_allowlist=[r"(", r"^/health$"]) as c:
        check("valid pattern still matches /health (200)", c.get("/health").status_code == 200)
        check("other path blocked by valid pattern (403)", c.get("/api/tags").status_code == 403)


# 5. rate limit ---------------------------------------------------------------
def test_rate_limit() -> None:
    with client_with(rate_limit={"enabled": True, "per_minute": 3, "burst": 2, "by": "ip"}) as c:
        codes = [c.get("/health").status_code for _ in range(6)]
        check("at least one 429 after burst", 429 in codes, f"codes={codes}")
        check("burst allows only a couple 200s", codes.count(200) <= 3, f"codes={codes}")


if __name__ == "__main__":
    print("Security verification:")
    try:
        test_methods()
        test_headers()
        test_path_allowlist()
        test_bad_regex()
        test_rate_limit()
    finally:
        if os.path.exists(_TMP):
            os.unlink(_TMP)
    print(f"\nResult: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
