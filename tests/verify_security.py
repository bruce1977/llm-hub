"""Ad-hoc verification of the two-zone security controls.

Plain runner (no pytest needed)::

    python tests/verify_security.py

Covers the config-driven public path allowlist, mandatory public auth, the
internal/public listener split, shared hardening (headers, body cap, rate limit,
forwarding hygiene, log redaction, config masking) and the migration
of the pre-1.3 `auth` / flat `security` schema.

NOTE: CONFIG_PATH must be set *before* importing `config` (its CONFIG_PATH global
is bound at import time), so we create one fixed temp config and write each case to it.
"""

import logging
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import yaml

# Fixed temp config path, exported to the environment BEFORE config is imported.
_TMP = tempfile.mktemp(prefix="llm-hub-sec-", suffix=".yaml")
os.environ["CONFIG_PATH"] = _TMP
# One data file per run: llmhub.db also holds the api_keys table, and that is seeded only while
# empty, so it must not be shared with another suite through the system temp directory.
os.environ["LLMHUB_DB"] = f"{_TMP}.db"

API_KEY = "sk-strong-audit-key-0123456789"
AUTH_HEADERS = {"Authorization": f"Bearer {API_KEY}"}

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "app"))

from auth import is_public, local_port, peer_ip, zone  # noqa: E402
from bridge import bridge_denial  # noqa: E402
from config import Config, SecurityConfig, build_default_config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from starlette.requests import Request  # noqa: E402

EXAMPLE = Path("data/config.example.yaml").read_text(encoding="utf-8")


def seed_api_keys(*secrets: str) -> None:
    """Create caller-key rows in llmhub.db.

    Keys are minted in the console only - there is no environment variable or key file - so
    the fixture writes the rows itself, before the first lifespan starts.
    """
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
        print(f"  [FAIL] {name}  {detail}")


class _LogCollector(logging.Handler):
    """Capture (level, rendered message) for one logger, without touching others."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[tuple[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append((record.levelname, record.getMessage()))


@contextmanager
def capture_logs(*names: str):
    """Collect log records from the named loggers; restore levels/handlers after.

    A collector is attached to each logger separately, so an assertion still knows
    which logger emitted a line even if a display-name filter rewrote
    ``record.name`` on its way through another handler.
    """
    collectors = {name: _LogCollector() for name in names}
    saved = []
    try:
        for name in names:
            lg = logging.getLogger(name)
            saved.append((lg, lg.level))
            lg.setLevel(logging.DEBUG)
            lg.addHandler(collectors[name])
        yield collectors
    finally:
        for lg, level in saved:
            lg.removeHandler(collectors[lg.name])
            lg.setLevel(level)


DEFAULT_RULES = {
    "GET": [r"^/health$", r"^/v1/models$"],
    "POST": [r"^/v1/chat/completions$"],
}


@contextmanager
def client_with(*, public: bool = False, rules=None, allow_anonymous: bool = True,
                public_cfg=None, request_log=None, client_addr=("127.0.0.1", 41000),
                **sec_overrides):
    """Start the app with a fresh config.

    TestClient's ``scope["server"]`` port is 80. Setting ``public=True`` declares
    80 as ``security.public.port``, so every request lands in the public zone
    without rebinding a real socket. Otherwise the single TestClient socket is
    the internal zone. ``client_addr`` sets the fake TCP peer, which is what the
    IP deny-list reads (the ASGI fallback path needs a blocked peer here).
    """
    cfg = yaml.safe_load(EXAMPLE)
    cfg["security"]["internal"]["allow_anonymous"] = allow_anonymous
    cfg["security"]["public"]["port"] = 80 if public else None
    cfg["security"]["public"]["path_allowlist"] = DEFAULT_RULES if rules is None else rules
    if public_cfg is not None:  # a whole security.public node, e.g. an admin_endpoints mode
        cfg["security"]["public"] = dict(public_cfg)
    if request_log is not None:  # a whole top-level request_log node
        cfg["request_log"] = dict(request_log)
    cfg["security"].update(sec_overrides)
    Path(_TMP).write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with TestClient(app, client=client_addr) as c:  # enters lifespan -> ConfigManager
        yield c


# 1. shared hardening ---------------------------------------------------------
def test_headers() -> None:
    with client_with() as c:
        r = c.get("/health")
        check("X-Content-Type-Options: nosniff", r.headers.get("X-Content-Type-Options") == "nosniff")
        check("X-Frame-Options: DENY", r.headers.get("X-Frame-Options") == "DENY")
        check("CSP present", "default-src 'none'" in (r.headers.get("Content-Security-Policy") or ""))


def test_rate_limit() -> None:
    with client_with(rate_limit={"enabled": True, "per_minute": 3, "burst": 2, "by": "ip"}) as c:
        codes = [c.get("/health").status_code for _ in range(6)]
        check("at least one 429 after burst", 429 in codes, f"codes={codes}")
        check("burst allows only a couple 200s", codes.count(200) <= 3, f"codes={codes}")


def test_xff_is_not_a_decision_input() -> None:
    """XFF is ignored entirely; it must never grant or deny access."""
    with client_with() as c:
        plain = c.get("/health").status_code
        forged = c.get(
            "/health", headers={"X-Forwarded-For": "10.0.0.5", "X-Real-IP": "10.0.0.9"}
        ).status_code
        check("forged XFF leaves an internal decision unchanged", plain == forged == 200,
              f"plain={plain} forged={forged}")
    with client_with(public=True) as c:
        r = c.get("/v1/models", headers={"X-Forwarded-For": "127.0.0.1"})
        check("forged XFF cannot make a public call anonymous", r.status_code in (401, 403),
              f"got {r.status_code}")


def test_body_cap_and_retry_after() -> None:
    with client_with(max_body_bytes=32) as c:
        r = c.post("/v1/chat/completions", json={"model": "x", "messages": [{"role": "user", "content": "x" * 200}]})
        check("oversized body is rejected by Content-Length before reading", r.status_code == 413, f"got {r.status_code}")
    with client_with(rate_limit={"enabled": True, "per_minute": 3, "burst": 1, "by": "ip"}) as c:
        seen = False
        for _ in range(4):
            r = c.get("/health")
            if r.status_code == 429:
                seen = True
                check("429 carries Retry-After", "retry-after" in {k.lower() for k in r.headers}, str(dict(r.headers)))
                break
        check("rate limit produced a 429", seen, "no 429 seen")


def test_nul_path_and_hidden_version() -> None:
    with client_with(internal={"allow_anonymous": True, "expose_version": True, "expose_docs": True}) as c:
        r = c.get("/")
        check("internal expose_version=true shows the build", "version" in r.json(), r.text[:160])
        r2 = c.get("/admin/%00foo")
        check("NUL in a console path never answers 500", r2.status_code < 500, f"got {r2.status_code}")
    with client_with(internal={"allow_anonymous": True, "expose_version": False, "expose_docs": True}) as c:
        check("version is hidden when internal.expose_version=false", "version" not in c.get("/").json())


def test_forwarding_hygiene() -> None:
    from types import SimpleNamespace

    from proxy import EXCLUDED_RESPONSE_HEADERS, ProxyService

    scope = {
        "type": "http", "method": "POST", "path": "/", "raw_path": b"/",
        "query_string": b"", "scheme": "http", "server": ("test", 80),
        "client": ("1.2.3.4", 1234), "http_version": "1.1",
        "headers": [
            (b"cookie", b"llmhub_admin=session-token"),
            (b"referer", b"http://gateway/admin/"),
            (b"authorization", b"Bearer caller-key"),
            (b"content-type", b"application/json"),
        ],
    }
    built = ProxyService(None, None)._build_headers(Request(scope), SimpleNamespace(headers={"Authorization": "Bearer provider"}))
    lowered = {k.lower(): v for k, v in built.items()}
    check("console session cookie is not forwarded upstream", "cookie" not in lowered, str(lowered))
    check("referer is not forwarded upstream", "referer" not in lowered, str(lowered))
    check("caller API key is not forwarded upstream", lowered.get("authorization") == "Bearer provider", str(lowered))
    check("upstream set-cookie is filtered", "set-cookie" in EXCLUDED_RESPONSE_HEADERS)


def test_log_redaction() -> None:
    from request_log import sanitize_body, sanitize_query

    raw = b'{"api_key":"SECRET-PLAIN","messages":[{"role":"user","content":"' + b"x" * 9000 + b'"}]}'
    text, truncated = sanitize_body(raw)
    check("oversized body is still redacted before truncation", truncated and "SECRET-PLAIN" not in text, text[:120])
    q = sanitize_query("q=hello&api_key=SECRET&token=ABC")
    check("query-string credentials are masked", bool(q) and "SECRET" not in q and "ABC" not in q and "q=hello" in q, str(q))


def test_standalone_log_detail() -> None:
    """Standalone handlers publish model/upstream/raw bodies on ``request.state``; the
    middleware merges them into ONE row. No duplicate, no leaked credentials, and the
    console detail pane gets the same req/res a proxy row would.
    """
    import time
    from types import SimpleNamespace

    from request_log import attach_request_detail, basic_entry

    req = SimpleNamespace(state=SimpleNamespace())

    # Repeated calls accumulate; a later value for the same key wins. Bodies stay raw.
    attach_request_detail(req, requested_model="q35", request_raw=b'{"model":"q35","api_key":"SECRET-REQ"}')
    attach_request_detail(
        req,
        model="qwen3:8b",
        upstream="mock-ollama",
        stream=True,
        response_raw=b'{"model":"qwen3:8b","usage":{"input_tokens":5,"output_tokens":7},"answer":"ok"}',
    )
    detail = req.state.request_log_detail
    check(
        "standalone detail accumulates across calls",
        detail["requested_model"] == "q35" and detail["model"] == "qwen3:8b"
        and detail["upstream"] == "mock-ollama" and detail["stream"] is True,
        str(detail),
    )
    check(
        "standalone detail keeps raw bodies for one-shot redaction",
        b"SECRET-REQ" in detail["request_raw"] and b"usage" in detail["response_raw"],
        str(detail)[:160],
    )

    row = basic_entry(
        method="POST", path="/v1/messages", status=200,
        ts="2026-11-11T00:00:00", started=time.perf_counter(), detail=detail,
    )
    check(
        "standalone row carries requested + resolved model and upstream",
        row["model"] == "qwen3:8b" and row["requested_model"] == "q35" and row["upstream"] == "mock-ollama",
        str([row[k] for k in ("model", "requested_model", "upstream")]),
    )
    check(
        "standalone row redacts the request body",
        "SECRET-REQ" not in (row["request_body"] or "") and "***" in (row["request_body"] or ""),
        str(row["request_body"]),
    )
    check("standalone row carries the response body", "ok" in (row["response_body"] or ""),
          str(row["response_body"]))
    check(
        "standalone row extracts Anthropic-style token split",
        (row["tokens_in"], row["tokens_out"], row["tokens"]) == (5, 7, 12),
        str((row["tokens_in"], row["tokens_out"], row["tokens"])),
    )
    check("standalone row marks the stream flag", row["stream"] == 1, str(row["stream"]))
    check(
        "standalone row sizes bodies from captured bytes, not headers",
        row["req_size"] == len(detail["request_raw"]) and row["res_size"] == len(detail["response_raw"]),
        str((row["req_size"], row["res_size"])),
    )

    # An oversized body is redacted over the FULL buffer before being truncated.
    big = b'{"api_key":"SECRET-BIG","text":"' + b"y" * 9000 + b'"}'
    row2 = basic_entry(
        method="POST", path="/v1/systemone", status=200,
        ts="2026-11-11T00:00:00", started=time.perf_counter(), detail={"request_raw": big},
    )
    check(
        "oversized standalone body is redacted then truncated",
        row2["request_body_truncated"] == 1 and "SECRET-BIG" not in (row2["request_body"] or "")
        and len(row2["request_body"] or "") <= 8192,
        str((row2["request_body_truncated"], len(row2["request_body"] or ""))),
    )

    # A detail-free row (pure probe / middleware denial) stays metadata-only.
    row3 = basic_entry(
        method="GET", path="/v1/models", status=401,
        ts="2026-11-11T00:00:00", started=time.perf_counter(),
    )
    check(
        "detail-free row stays metadata-only",
        all(row3[k] is None for k in ("model", "requested_model", "upstream", "request_body",
                                      "response_body", "tokens", "ttfb_ms")),
        str(row3),
    )


def test_request_log_coverage() -> None:
    """Every API call lands in the request log exactly once: proxy, standalone handlers
    and middleware denials. HEAD/OPTIONS probes and console traffic stay out.

    Before the middleware wrote a metadata fallback, only ``ProxyService.forward``
    produced rows, so ``/v1/systemone``, ``/v1/tools/web-search``, ``/v1/rerank``,
    ``/v1/messages``, ``/v1/models``, ``/api/tags`` and ``/api/ps`` (all standalone
    handlers) were invisible, as were every public-zone 401/404 refused in the middleware.
    """
    import sqlite3

    log_db = f"{_TMP}.requests.db"
    saved_db = os.environ.get("REQUEST_LOG_DB")
    os.environ["REQUEST_LOG_DB"] = log_db
    Path(log_db).unlink(missing_ok=True)
    seed_api_keys(API_KEY)

    def read_rows() -> list[dict]:
        # client_with()'s lifespan shutdown calls RequestLog.stop(), which drains the
        # queue via join(), so every row is committed by the time the with-block exits.
        con = sqlite3.connect(log_db)
        con.row_factory = sqlite3.Row
        try:
            cols = {row[1] for row in con.execute("PRAGMA table_info(requests)")}
            wanted = [c for c in ("path", "method", "status", "error", "duration_ms", "model", "upstream",
                                  "request_body", "response_body", "tokens") if c in cols]
            return [dict(r) for r in con.execute(f"SELECT {', '.join(wanted)} FROM requests ORDER BY id")]
        finally:
            con.close()

    try:
        # --- internal zone: standalone handlers + proxy + probes -----------------
        with client_with() as c:
            check("standalone GET /v1/models", c.get("/v1/models").status_code == 200)
            check("standalone POST /v1/rerank (bad body)", c.post("/v1/rerank", json={}).status_code >= 400)
            check("standalone GET /api/tags", c.get("/api/tags").status_code == 200)
            check("standalone GET /api/ps", c.get("/api/ps").status_code == 200)
            check("standalone GET /health", c.get("/health").status_code == 200)
            # proxy path: no upstream reachable -> 502, but it must still be recorded once
            check("proxy POST /v1/chat/completions",
                  c.post("/v1/chat/completions", json={"model": "missing:latest", "messages": []}).status_code >= 400)
            c.head("/v1/models")
            c.options("/v1/models")
        rows = read_rows()
        paths = [r["path"] for r in rows]
        check("standalone /v1/models recorded", "/v1/models" in paths, str(paths))
        check("standalone /v1/rerank recorded", "/v1/rerank" in paths, str(paths))
        check("standalone /api/tags recorded", "/api/tags" in paths, str(paths))
        check("standalone /api/ps recorded", "/api/ps" in paths, str(paths))
        check("standalone /health recorded", "/health" in paths, str(paths))
        check("proxy /v1/chat/completions recorded once", paths.count("/v1/chat/completions") == 1, str(paths))
        check("HEAD /v1/models never recorded", not any(r["method"] == "HEAD" for r in rows), str(paths))
        check("OPTIONS /v1/models never recorded", not any(r["method"] == "OPTIONS" for r in rows), str(paths))
        check("a middleware-independent row has timing but no phantom model",
              all(r["duration_ms"] is not None for r in rows), str(rows[:1]))

        # metadata-only rows carry no body/token/model; the proxy row keeps its rich fields.
        standalone = [r for r in rows if r["path"] in ("/v1/models", "/health", "/api/tags", "/api/ps")]
        check("standalone rows are metadata-only",
              all(r.get("model") is None and r.get("upstream") is None
                  and r.get("request_body") is None and r.get("tokens") is None for r in standalone),
              str(standalone[:1]))
        proxy_rows = [r for r in rows if r["path"] == "/v1/chat/completions"]
        check("proxy row keeps upstream/model",
              bool(proxy_rows) and all(r.get("upstream") and r.get("model") for r in proxy_rows),
              str(proxy_rows))

        # --- public zone: refused calls are auditable ---------------------------
        public_rules = {"GET": [r"^/v1/models$"], "POST": [r"^/v1/systemone$"]}
        with client_with(public=True, rules=public_rules) as c:
            check("public POST /v1/systemone without key is 401",
                  c.post("/v1/systemone", json={}).status_code == 401)
            check("public unlisted path is 404", c.get("/api/never").status_code == 404)
        rows = read_rows()
        denied = [r for r in rows if r["path"] == "/v1/systemone" and r["status"] == 401]
        check("public 401 refusal recorded", len(denied) >= 1, str(rows[-4:]))
        check("public 401 carries the denial detail",
              bool(denied[0].get("error")) if denied else False, str(denied[:1]))
        check("public 404 refusal recorded", any(r["path"] == "/api/never" and r["status"] == 404 for r in rows), str(rows[-4:]))
        check("management polling is NOT self-logged",
              not any(r["path"].startswith("/v1/management") for r in rows), str([r["path"] for r in rows]))

        # --- request_log.enabled=false records nothing --------------------------
        Path(log_db).unlink(missing_ok=True)
        with client_with(request_log={"enabled": False}) as c:
            c.get("/v1/models")
            c.get("/health")
        rows = read_rows()
        check("enabled=false: no standalone rows", not any(r["path"] in ("/v1/models", "/health") for r in rows), str(rows))
    finally:
        if saved_db is None:
            os.environ.pop("REQUEST_LOG_DB", None)
        else:
            os.environ["REQUEST_LOG_DB"] = saved_db
        Path(log_db).unlink(missing_ok=True)


def test_log_timestamps() -> None:
    """Every log line carries a timestamp, and uvicorn's logger names read neutrally.

    uvicorn hard-codes ``uvicorn.error`` for *all* server lifecycle messages, so an
    INFO line like "Application startup complete." prints ``uvicorn.error`` and looks
    like a failure. A display-only filter renames it to ``gateway`` (access to
    ``gateway.access``) without touching the real logger, level or routing. All three
    entrypoints must agree: serve.py and ``--log-config`` load logging.json's filters,
    while a bare ``uvicorn main:app`` is repaired by ``apply_to_existing_handlers``.
    """
    import io
    import logging
    import re

    from logging_config import (
        LOG_NAME_ALIASES,
        LoggerNameAliasFilter,
        apply_to_existing_handlers,
        uvicorn_log_config,
    )

    spec = uvicorn_log_config("warning")
    for name in ("default", "access"):
        fmt = spec["formatters"][name]
        check(f"logging.json {name} formatter is timestamped", "%(asctime)s" in fmt["fmt"], fmt["fmt"])
        check(f"logging.json {name} formatter keeps milliseconds",
              fmt["datefmt"] == "%Y-%m-%d %H:%M:%S" and "%(msecs)" in fmt["fmt"], fmt["fmt"])
        check(f"logging.json {name} handler installs the name-alias filter",
              "name_alias" in spec["handlers"][name].get("filters", []), str(spec["handlers"][name]))
    check("logging.json declares a name-alias filter from logging_config",
          spec["filters"]["name_alias"]["()"].endswith("LoggerNameAliasFilter"), str(spec["filters"]))
    check("logging.json pins every logger to server.log_level",
          {v["level"] for v in spec["loggers"].values()} == {"WARNING"}, str(spec["loggers"]))
    check("logging.json pins the root level", spec["root"]["level"] == "WARNING", str(spec["root"]))

    check("uvicorn.error is displayed as gateway", LOG_NAME_ALIASES["uvicorn.error"] == "gateway")
    check("uvicorn.access is displayed as gateway.access", LOG_NAME_ALIASES["uvicorn.access"] == "gateway.access")
    check("application logger keeps its own name", "llm_hub" not in LOG_NAME_ALIASES)

    # The CLI / bare-uvicorn path: uvicorn installs its handlers *before* the app's
    # lifespan runs, so setup_logging() must both re-format and attach the filter.
    # Mirror the real layout: uvicorn.error has no own handler and propagates to the
    # `uvicorn` parent, uvicorn.access has its own access handler, llm_hub its own.
    from uvicorn.logging import AccessFormatter, DefaultFormatter

    fmt = "%(asctime)s.%(msecs)03d | %(levelname)-7s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    parent = logging.getLogger("uvicorn")
    access = logging.getLogger("uvicorn.access")
    err = logging.getLogger("uvicorn.error")
    app_log = logging.getLogger("llm_hub")
    loggers = (parent, access, err, app_log)
    saved = [(list(lg.handlers), lg.propagate, lg.level) for lg in loggers]
    streams: dict[str, io.StringIO] = {}
    streams["parent"] = io.StringIO()
    parent.handlers = [logging.StreamHandler(streams["parent"])]
    parent.propagate = False
    streams["access"] = io.StringIO()
    access.handlers = [logging.StreamHandler(streams["access"])]
    access.propagate = False
    err.handlers = []
    err.propagate = True  # the bare-start reality: lifecycle lines bubble to `uvicorn`
    streams["app"] = io.StringIO()
    app_handler = logging.StreamHandler(streams["app"])
    # apply_to_existing_handlers() only touches uvicorn* loggers, so the app logger
    # keeps the formatter that setup_logging()/basicConfig gave it. Mirror that here.
    app_handler.setFormatter(logging.Formatter(fmt, datefmt))
    app_log.handlers = [app_handler]
    app_log.propagate = False
    for lg in loggers:
        lg.setLevel(logging.INFO)
    try:
        # twice: a reload must not stack duplicate filters
        apply_to_existing_handlers("info")
        apply_to_existing_handlers("info")
        check("alias filter attached exactly once on the parent handler",
              sum(isinstance(f, LoggerNameAliasFilter) for f in parent.handlers[0].filters) == 1,
              str(parent.handlers[0].filters))
        check("access handler uses AccessFormatter after repair",
              isinstance(access.handlers[0].formatter, AccessFormatter), str(access.handlers[0].formatter))
        check("parent handler uses DefaultFormatter after repair",
              isinstance(parent.handlers[0].formatter, DefaultFormatter), str(parent.handlers[0].formatter))
        err.info("Application startup complete.")
        err.warning("Exception in ASGI application")
        access.info('%s - "%s %s HTTP/%s" %s', "1.2.3.4:5678", "GET", "/v1/models", "1.1", 404)
        app_log.warning("Environment variable is not set")
    finally:
        for lg, (handlers, propagate, level) in zip(loggers, saved):
            lg.handlers, lg.propagate, lg.level = handlers, propagate, level

    err_lines = streams["parent"].getvalue().splitlines()
    access_lines = streams["access"].getvalue().splitlines()
    app_lines = streams["app"].getvalue().splitlines()
    check("INFO lifecycle line shows the neutral gateway name, not uvicorn.error",
          bool(re.match(
              r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \| INFO\s+\| gateway \| '
              r'Application startup complete\.$', err_lines[0])), str(err_lines))
    check("the real severity survives the alias",
          bool(re.match(
              r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \| WARNING\s+\| gateway \| '
              r'Exception in ASGI application$', err_lines[1])), str(err_lines))
    check("an access line is re-formatted with a timestamp and the status phrase",
          bool(re.match(
              r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \| INFO\s+\| gateway\.access \| '
              r'1\.2\.3\.4:5678 - "GET /v1/models HTTP/1\.1" 404 Not Found$', access_lines[0])), str(access_lines))
    check("application logs keep the llm_hub name",
          bool(re.match(
              r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \| WARNING\s+\| llm_hub \| '
              r'Environment variable is not set$', app_lines[0])), str(app_lines))

    # The filter is a pure, idempotent name map: unknown names pass through untouched.
    flt = LoggerNameAliasFilter()
    for original, aliased in (
        ("uvicorn", "gateway"),
        ("uvicorn.error", "gateway"),
        ("uvicorn.access", "gateway.access"),
        ("llm_hub", "llm_hub"),
        ("some.other.logger", "some.other.logger"),
    ):
        rec = logging.LogRecord(original, logging.INFO, __file__, 1, "x", (), None)
        check(f"alias filter maps {original} -> {aliased}", flt.filter(rec) and rec.name == aliased, rec.name)


def test_config_guards() -> None:
    from config import validate_key_env, validate_service_url

    for bad in ("ADMIN_PASSWORD", "ADMIN_SESSION_TTL", "../../etc/passwd", "lower_case"):
        rejected = False
        try:
            validate_key_env(bad)
        except ValueError:
            rejected = True
        check(f"api_key_env rejects {bad!r}", rejected)
    check("a provider key env name is accepted", validate_key_env("OPENCODE_API_KEY") == "OPENCODE_API_KEY")
    for bad in ("http://169.254.169.254/latest/meta-data/", "file:///etc/passwd", "gopher://x/", "http://u:p@host/"):
        rejected = False
        try:
            validate_service_url(bad)
        except ValueError:
            rejected = True
        check(f"service URL rejects {bad!r}", rejected)
    check("a LAN Ollama upstream is still allowed", validate_service_url("http://192.168.10.5:11434").endswith(":11434"))


def test_dot_segments_and_redirects() -> None:
    # HTTP clients normalise `..` before sending, so the raw-path guard is tested
    # directly against bridge_denial (see the red-line test below). Here we only
    # assert that a normalised admin path is still outside the public surface.
    with client_with(public=True) as c:
        r = c.post("/api/../api/delete", json={"model": "x"})
        check("a normalised admin path is not on the public surface",
              r.status_code == 404, f"got {r.status_code}")
        check("the upstream client does not follow redirects", app.state.client.follow_redirects is False)


# 2. public path allowlist ----------------------------------------------------
def test_public_allowlist_and_key() -> None:
    with client_with(public=True) as c:
        check("allowed GET /health with key -> 200", c.get("/health", headers=AUTH_HEADERS).status_code == 200)
        check("allowed GET /v1/models with key -> 200", c.get("/v1/models", headers=AUTH_HEADERS).status_code == 200)
        posted = c.post("/v1/chat/completions", headers=AUTH_HEADERS, json={})
        check("allowed POST passes the policy layer (not 401/403/bridge 404)",
              posted.status_code not in (401, 403) and (posted.status_code != 404 or "Not found." not in posted.text),
              f"got {posted.status_code}: {posted.text[:100]}")
        check("unlisted GET /api/tags -> 404 even with a key",
              c.get("/api/tags", headers=AUTH_HEADERS).status_code == 404)
        check("no key on an allowed path -> 401", c.get("/v1/models").status_code == 401)
        check("wrong key on an allowed path -> 403",
              c.get("/v1/models", headers={"X-API-Key": "not-a-key"}).status_code == 403)
        check("wrong verb on an allowed path -> 404",
              c.get("/v1/chat/completions", headers=AUTH_HEADERS).status_code == 404)
        check("trailing slash is a different path -> 404",
              c.get("/v1/models/", headers=AUTH_HEADERS).status_code == 404)


def test_public_bad_regex() -> None:
    with client_with(public=True, rules={"GET": ["(", r"^/v1/models$"]}) as c:
        check("invalid regex is dropped, valid one still works",
              c.get("/v1/models", headers=AUTH_HEADERS).status_code == 200)
        check("invalid regex does not widen the surface",
              c.get("/health", headers=AUTH_HEADERS).status_code == 404)


def test_public_full_match() -> None:
    with client_with(public=True, rules={"GET": [r"^/v1/"]}) as c:
        check("a prefix-only rule does not fullmatch /v1/models",
              c.get("/v1/models", headers=AUTH_HEADERS).status_code == 404)
    # A broad regex must still not admit dot-segment paths: proxy.forward() would
    # reject them with 400, but on the public zone an unknown path must not exist.
    broad = Config(**{"security": {"public": {"path_allowlist": {"GET": [r"^/v1/.*$"]}}}})
    check("a broad regex cannot admit a dot-segment path",
          bridge_denial("GET", "/v1/../v1/models", broad) == (404, "Not found."))
    check("a broad regex cannot admit a NUL path",
          bridge_denial("GET", "/v1/model\x00s", broad) == (404, "Not found."))


def test_admin_endpoints_never_opens_a_path() -> None:
    """`admin_endpoints` decides *whether* a listed path is forwarded, never whether it exists.

    This is the answer to "I set admin_endpoints: allow and pull still fails": on the
    public socket the path has to be in `path_allowlist` first, because `bridge_denial`
    runs in the middleware, before the admin filter inside proxy.forward().
    """
    with client_with(public=True) as c:
        c.app.state.manager.config.security.public.admin_endpoints = "allow"
        r = c.post("/api/pull", headers=AUTH_HEADERS, json={"name": "qwen3:4b"})
        check("allow cannot open a path the allowlist does not list",
              r.status_code == 404 and "Not found." in r.text, f"got {r.status_code}: {r.text[:120]}")

    listed = {"GET": [r"^/v1/models$"], "POST": [r"^/api/pull$", r"^/api/blobs/.*$"]}
    with client_with(public=True, rules=listed) as c:
        pub = c.app.state.manager.config.security.public
        pub.admin_endpoints = "allow"
        r = c.post("/api/pull", headers=AUTH_HEADERS, json={"name": "qwen3:4b"})
        check("allow + a listed path passes the admin filter",
              r.status_code != 403, f"got {r.status_code}: {r.text[:120]}")
        blob = c.post("/api/blobs/sha256:0000", headers=AUTH_HEADERS, json={})
        check("allow forwards a blob upload too", blob.status_code != 403, f"got {blob.status_code}: {blob.text[:120]}")
        pub.admin_endpoints = "readonly"
        blob = c.post("/api/blobs/sha256:0000", headers=AUTH_HEADERS, json={})
        check("readonly still blocks a blob upload", blob.status_code == 403, f"got {blob.status_code}: {blob.text[:120]}")

    # What the startup log reports: the intersection of the mode and the allowlist. The
    # point is that `allow` alone is never a surface, so "I set allow and nothing changed"
    # gets an answer in the log instead of a bare 404.
    def reachable(mode: str, post: list[str]) -> list[str]:
        return Config(security={"public": {"admin_endpoints": mode,
                                          "path_allowlist": {"POST": post}}}).security.public.reachable_admin_paths()

    check("reachable_admin_paths(): allow with no rule reports nothing", reachable("allow", []) == [])
    check("reachable_admin_paths(): readonly + listed pull reports pull",
          reachable("readonly", [r"^/api/pull$"]) == ["api/pull"])
    check("reachable_admin_paths(): readonly still hides push/delete",
          reachable("readonly", [r"^/api/(pull|push|delete)$"]) == ["api/pull"])
    check("reachable_admin_paths(): allow reports every listed endpoint",
          reachable("allow", [r"^/api/(pull|push)$"]) == ["api/pull", "api/push"])
    check("reachable_admin_paths(): deny reports nothing even when listed",
          reachable("deny", [r"^/api/.*$"]) == [])

    # The closed forwardable set applies in BOTH zones, so the trusted listener is no
    # longer an open proxy for whatever the upstream happens to serve.
    with client_with() as c:
        r = c.post("/api/some-private-route", headers=AUTH_HEADERS, json={"model": "qwen3:4b"})
        check("internal zone refuses an undeclared path",
              r.status_code == 404 and "not part of this gateway" in r.text,
              f"got {r.status_code}: {r.text[:120]}")
        r = c.post("/api/pull", headers=AUTH_HEADERS, json={"name": "qwen3:4b"})
        check("the trusted listener still forwards model management",
              r.status_code != 403, f"got {r.status_code}: {r.text[:120]}")


def test_public_credential_is_a_zone_rule_not_a_route_rule() -> None:
    """`bridge_denial` says whether a path exists; `public_auth_denial` says who may use it.

    The proof is a handler registered with **no** auth dependency at all: on the
    internet-facing socket it is still 401 without a key, because the credential is
    a property of the zone (checked in the middleware) and not of whichever
    `Depends(require_api_key)` a route happens to declare. Listing a path in
    `path_allowlist` therefore cannot open an anonymous endpoint. The trusted
    listener is deliberately left alone - its anonymous paths (`/admin` static,
    `PROBE_PATHS`, `internal.allow_anonymous`) are answered by routes that are not
    business APIs, so a blanket check there would break the console.
    """
    from fastapi.responses import JSONResponse

    async def _open_handler() -> JSONResponse:
        return JSONResponse({"served": "without any dependency"})

    app.add_api_route("/_noauth-probe", _open_handler, methods=["GET"])
    probe = app.router.routes[-1]
    app.router.routes.remove(probe)
    app.router.routes.insert(0, probe)  # the catch-all is last, so it must not shadow the probe
    rules = {"GET": [r"^/_noauth-probe$"]}
    try:
        with client_with(public=True, allow_anonymous=True, rules=rules) as c:
            r = c.get("/_noauth-probe")
            check("a dependency-less route is still 401 on the public socket",
                  r.status_code == 401, f"got {r.status_code}: {r.text[:120]}")
            check("the public 401 keeps WWW-Authenticate: Bearer",
                  r.headers.get("www-authenticate") == "Bearer", str(dict(r.headers)))
            check("an invalid key on it is 403, not 404",
                  c.get("/_noauth-probe", headers={"X-API-Key": "nope"}).status_code == 403)
            check("a valid key opens it",
                  c.get("/_noauth-probe", headers=AUTH_HEADERS).status_code == 200)
        with client_with(allow_anonymous=True, rules=rules) as c:
            check("the internal listener is not credential-checked by the zone policy",
                  c.get("/_noauth-probe").status_code == 200)
    finally:
        app.router.routes.remove(probe)


def test_cors_preflight_is_bodyless_and_precedes_the_key_check() -> None:
    """Two contracts the pre-flight has to keep: no bytes, and no credentials.

    Starlette omits `content-length` for 204/304 but still sends the response body,
    so a `JSONResponse({}, status_code=204)` writes two stray bytes and uvicorn
    drops the connection with "Response content longer than Content-Length" - the
    browser then reports a failed pre-flight instead of a 204. And on the public
    socket the answer comes *before* `public_auth_denial`, because a pre-flight
    never carries the API key; the `OPTIONS` method+path still has to be listed in
    `security.public.path_allowlist` for the path to exist at all.
    """
    origin = "http://localhost:5173"
    rules = {"GET": [r"^/v1/models$"], "OPTIONS": [r"^/v1/models$"]}

    with client_with(cors_origins=[origin]) as c:
        r = c.options("/v1/models", headers={"Origin": origin})
        check("internal OPTIONS for a listed origin -> 204", r.status_code == 204, f"got {r.status_code}")
        check("a 204 pre-flight carries zero body bytes", r.content == b"", f"got {r.content!r}")
        check("the pre-flight echoes the origin", r.headers.get("access-control-allow-origin") == origin)
        check("the pre-flight allows the method and headers",
              r.headers.get("access-control-allow-methods") == "*"
              and r.headers.get("access-control-allow-headers") == "*")
        r = c.options("/v1/models", headers={"Origin": "http://evil.example"})
        check("an unlisted origin gets no 204 and no CORS header",
              r.status_code != 204 and "access-control-allow-origin" not in r.headers,
              f"got {r.status_code}")

    with client_with(public=True, rules=rules, cors_origins=[origin]) as c:
        r = c.options("/v1/models", headers={"Origin": origin})
        check("a listed public OPTIONS is 204 without any key", r.status_code == 204, f"got {r.status_code}")
        check("and bodyless there too", r.content == b"", f"got {r.content!r}")
        check("an unlisted public OPTIONS is still 404 with a valid key",
              c.options("/api/tags", headers={"Origin": origin, **AUTH_HEADERS}).status_code == 404)
        r = c.get("/v1/models", headers={"Origin": origin})
        check("the public 401 keeps the CORS header, so the browser can read it",
              r.status_code == 401 and r.headers.get("access-control-allow-origin") == origin,
              f"got {r.status_code}: {dict(r.headers)}")


def test_public_never_exposes_management_or_console() -> None:
    with client_with(public=True) as c:
        for hidden in ("/", "/probe", "/api/tags", "/api/ps", "/api/version",
                       "/docs", "/openapi.json", "/redoc", "/healthz",
                       "/v1/management/config", "/v1/management/auth/login", "/admin", "/admin/"):
            r = c.get(hidden, headers=AUTH_HEADERS)
            check(f"public GET {hidden} -> 404", r.status_code == 404, f"got {r.status_code}: {r.text[:80]}")
        check("an invalid console cookie cannot open the public API",
              c.get("/v1/models", cookies={"llmhub_admin": "forged"}).status_code == 401)
        check("forged internal XFF cannot bypass the key check",
              c.get("/v1/models", headers={"X-Forwarded-For": "192.168.10.5"}).status_code == 401)


def test_admin_endpoints_allow_opens_the_management_plane() -> None:
    """admin_endpoints is decided BEFORE path_allowlist: allow opens the whole plane.

    The plane is a prefix rule, so an operator who wants a remote console does not have to
    enumerate /admin/assets/* in a regex. It is still never anonymous: only the static shell
    and the login handshake skip the credential check.
    """
    cases = {
        "deny": {"port": 80, "path_allowlist": {}, "admin_endpoints": "deny"},
        "readonly": {"port": 80, "path_allowlist": {}, "admin_endpoints": "readonly"},
    }
    for mode, public in cases.items():
        with client_with(public_cfg=public) as c:
            for path in ("/admin", "/admin/", "/v1/management/api-keys", "/v1/management/auth/ping"):
                r = c.get(path, headers=AUTH_HEADERS)
                check(f"{mode}: public GET {path} -> 404", r.status_code == 404,
                      f"got {r.status_code}: {r.text[:80]}")
            r = c.post("/v1/management/auth/login", json={"password": "irrelevant"})
            check(f"{mode}: public POST /v1/management/auth/login -> 404", r.status_code == 404,
                  f"got {r.status_code}")

    opened = {"port": 80, "path_allowlist": {"GET": [r"^/v1/models$"]},
              "admin_endpoints": "allow"}
    with client_with(public_cfg=opened) as c:
        shell = c.get("/admin")
        check("allow: the console shell is no longer 404", shell.status_code != 404,
              f"got {shell.status_code}")
        check("allow: /admin/ is no longer 404", c.get("/admin/").status_code != 404)
        login = c.post("/v1/management/auth/login", json={"password": "wrong"})
        check("allow: login is routed to its handler, not 404", login.status_code != 404,
              f"got {login.status_code}: {login.text[:80]}")
        ping = c.get("/v1/management/auth/ping")
        check("allow: the management API still needs the session cookie (401)", ping.status_code == 401,
              f"got {ping.status_code}")
        with_key = c.get("/v1/management/api-keys", headers=AUTH_HEADERS)
        # 401 with a console configured (the session is missing), 503 with none configured.
        check("allow: a gateway key is not an admin credential", with_key.status_code in (401, 503),
              f"got {with_key.status_code}: {with_key.text[:80]}")
        check("allow: a listed business API still requires a key",
              c.get("/v1/models").status_code == 401,
              f"got {c.get('/v1/models').status_code}; opening the console must not open /v1/models")
        # path_allowlist is not consulted for the prefix, but it still governs everything else
        check("allow: an unlisted business path is still 404",
              c.get("/probe", headers=AUTH_HEADERS).status_code == 404)


def test_public_only_instance() -> None:
    """security.public.port == server.port -> the single socket is untrusted."""
    cfg = yaml.safe_load(EXAMPLE)
    cfg["server"]["port"] = 80
    cfg["security"]["public"]["port"] = 80  # same socket, TestClient's port
    cfg["security"]["internal"]["allow_anonymous"] = True
    Path(_TMP).write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with TestClient(app) as c:
        check("public-only GET /health -> 404 (no anonymous HTTP path)", c.get("/health").status_code == 404)
        check("public-only GET /v1/models -> 401 without key", c.get("/v1/models").status_code == 401)
        check("public-only GET /v1/models -> 200 with key",
              c.get("/v1/models", headers=AUTH_HEADERS).status_code == 200)


# 3. zone classification (pure functions) ------------------------------------
def _raw_request(client, server, headers=None, *, public_port=None):
    """Build a bare ASGI Request without a running server, for pure-function checks."""
    security = SimpleSecurity(public_port=public_port)
    fake_app = SimpleNamespace(
        state=SimpleNamespace(manager=SimpleNamespace(config=SimpleNamespace(security=security)))
    )
    raw = [(k.lower().encode(), str(v).encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": "/v1/models",
        "raw_path": b"/v1/models", "query_string": b"", "root_path": "",
        "headers": raw, "client": client, "server": server, "app": fake_app,
    }
    return Request(scope)


class SimpleSecurity:
    def __init__(self, *, public_port):
        self.public = SimpleNamespace(port=public_port)


def test_zone_uses_kernel_socket_not_headers() -> None:
    real = _raw_request(("203.0.113.7", 51000), ("0.0.0.0", 8000),
                        {"X-Forwarded-For": "192.168.10.5"}, public_port=8001)
    check("local_port reads the kernel-assigned listener", local_port(real) == 8000)
    check("peer_ip keeps the real TCP peer despite a forged XFF", peer_ip(real) == "203.0.113.7")
    check("a declared internal socket is the internal zone", zone(real) == "internal")
    check("is_public agrees with zone()", is_public(real) is False)

    public = _raw_request(("192.168.10.5", 41000), ("0.0.0.0", 8001), public_port=8001)
    check("the declared public socket is the public zone", zone(public) == "public")
    check("is_public returns True there", is_public(public) is True)

    public_only = _raw_request(("203.0.113.7", 51000), ("0.0.0.0", 8000), public_port=8000)
    check("public-only instance: the sole socket is public", zone(public_only) == "public")

    polluted = _raw_request(("192.168.10.5", 0), ("0.0.0.0", 8000),
                            {"X-Forwarded-For": "192.168.10.5"}, public_port=8001)
    check("peer_ip rejects an XFF-rewritten scope (port 0)", peer_ip(polluted) is None)

    forged = _raw_request(("127.0.0.1", 41000), ("0.0.0.0", 8000),
                          {"X-Forwarded-For": "192.168.10.5, 167.104.161.192",
                           "X-Real-IP": "10.0.0.9"}, public_port=8001)
    check("a full forged XFF/X-Real-IP chain cannot change the recorded address",
          peer_ip(forged) == "127.0.0.1", str(peer_ip(forged)))
    check("forged XFF cannot change the zone", zone(forged) == "internal")


def test_retired_security_knobs() -> None:
    """`allow_query_api_key` / `trusted_proxies` are gone: caller keys are
    header-only, forwarded headers are ignored, and old config files still load.
    """
    fields = SecurityConfig.model_fields
    check("allow_query_api_key is no longer a config field", "allow_query_api_key" not in fields)
    check("trusted_proxies is no longer a config field", "trusted_proxies" not in fields)
    defaults = build_default_config()["security"]
    check("the default config no longer emits either knob",
          "allow_query_api_key" not in defaults and "trusted_proxies" not in defaults)
    check("the example config no longer emits either knob",
          "allow_query_api_key" not in EXAMPLE and "trusted_proxies" not in EXAMPLE)

    # An old config file with non-default values must not silently resurrect the knobs.
    legacy = Config(**{"security": {"allow_query_api_key": True,
                                    "trusted_proxies": ["10.0.0.0/8"]}})
    check("old configs carrying retired keys still parse",
          not hasattr(legacy.security, "allow_query_api_key")
          and not hasattr(legacy.security, "trusted_proxies"))

    with client_with(public=True, allow_query_api_key=True,
                     trusted_proxies=["127.0.0.1/32"]) as c:
        qs = c.get("/v1/models", params={"api_key": API_KEY})
        check("?api_key= is never accepted as a credential", qs.status_code == 401,
              f"got {qs.status_code}: {qs.text[:120]}")
        check("the Authorization header still works",
              c.get("/v1/models", headers=AUTH_HEADERS).status_code == 200)
        with_xff = c.get("/v1/models", headers={**AUTH_HEADERS,
                                                "X-Forwarded-For": "10.0.0.5",
                                                "X-Real-IP": "10.0.0.9"})
        check("forwarded headers never change an authenticated call", with_xff.status_code == 200,
              f"got {with_xff.status_code}")


# 4. internal zone + legacy migration ----------------------------------------
def test_internal_anonymous_toggle() -> None:
    with client_with(allow_anonymous=True) as c:
        check("internal /v1/models is key-free when allow_anonymous=true",
              c.get("/v1/models").status_code == 200)
        check("internal /health stays anonymous in any case", c.get("/health").status_code == 200)
    with client_with(allow_anonymous=False) as c:
        check("internal /v1/models needs a key when allow_anonymous=false",
              c.get("/v1/models").status_code == 401)
        check("internal /health is still anonymous for the container probe",
              c.get("/health").status_code == 200)
        check("internal /v1/models with a key -> 200",
              c.get("/v1/models", headers=AUTH_HEADERS).status_code == 200)


def test_legacy_config_migration() -> None:
    legacy = {
        "server": {"port": 8000},
        "auth": {"enabled": True, "allow_anonymous_health": True,
                 "trusted_networks": ["10.0.0.0/8"], "allow_access_ip_whitelist": [r"^10\.0\.0\.1$"]},
        "security": {
            "admin_endpoints": "deny", "expose_version": False,
            "allowed_http_methods": ["GET", "POST"], "path_allowlist": [r"^/v1/models$"],
        },
    }
    c = Config(**legacy)
    check("the old auth node is gone from the model", not hasattr(c, "auth"))
    check("auth.enabled=true maps to internal.allow_anonymous=false",
          c.security.internal.allow_anonymous is False)
    check("legacy root admin_endpoints moves to security.public",
          c.security.public.admin_endpoints == "deny")
    check("old expose_version maps to internal.expose_version",
          c.security.internal.expose_version is False)
    check("old global path_allowlist maps to the public verbs",
          c.security.public.path_allowlist == {"GET": [r"^/v1/models$"], "POST": [r"^/v1/models$"]},
          str(c.security.public.path_allowlist))
    check("no public port is opened by the migration", c.security.public.port is None)
    check("listen_ports() is the single internal port", c.listen_ports() == [8000])

    c2 = Config(**{**legacy, "security": {**legacy["security"], "public": {"port": 8001,
          "path_allowlist": {"GET": [r"^/only/this$"]}}}})
    check("an explicit public allowlist wins over the legacy one",
          c2.security.public.path_allowlist == {"GET": [r"^/only/this$"]},
          str(c2.security.public.path_allowlist))
    check("public_ports() reads security.public.port", c2.public_ports() == frozenset({8001}))
    check("listen_ports() has both listeners", c2.listen_ports() == [8000, 8001])
    check("auth.enabled=true WITH a public listener leaves the LAN socket anonymous",
          c2.security.internal.allow_anonymous is True,
          "the internet side is keyed by definition; the LAN socket must not require a key")

    c3 = Config(**{"security": {"public": {"port": 8000}}})
    check("public-only: one listener", c3.listen_ports() == [8000] and c3.public_ports() == frozenset({8000}))

    try:
        Config(**{"security": {"public": {"path_allowlist": {"FETCH": [r"^/x$"]}}}})
        check("an unknown HTTP verb is rejected", False)
    except ValidationError:
        check("an unknown HTTP verb is rejected", True)
    try:
        Config(**{"security": {"public": {"port": 70000}}})
        check("an invalid public port is rejected", False)
    except ValidationError:
        check("an invalid public port is rejected", True)


# 5. config projection / management ------------------------------------------
def test_config_security() -> None:
    """A caller key must not read/write config, and the projection must keep the
    new security tree (including the public allowlist) intact on an empty PUT."""
    os.environ["ADMIN_PASSWORD"] = "audit-strong-password-0123456789"
    cfg = yaml.safe_load(EXAMPLE)
    cfg["security"]["internal"]["allow_anonymous"] = True
    cfg["security"]["public"]["port"] = None
    secret = "provider-secret-DO-NOT-LEAK"
    cfg["upstreams"][0]["headers"] = {"Authorization": f"Bearer {secret}"}
    Path(_TMP).write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    try:
        with TestClient(app) as c:
            check("GET /config needs a console session", c.get("/v1/management/config").status_code == 401)
            anon = TestClient(app)
            check("a caller API key cannot rewrite config",
                  anon.put("/v1/management/config", json={"config": {}},
                           headers=AUTH_HEADERS).status_code == 401)
            logged_in = c.post("/v1/management/auth/login", json={"password": os.environ["ADMIN_PASSWORD"]})
            check("console sign-in works", logged_in.status_code == 200, logged_in.text[:160])
            r = c.get("/v1/management/config")
            check("GET /config with the session -> 200", r.status_code == 200, r.text[:200])
            check("GET /config masks upstream credentials", secret not in r.text and "••••••" in r.text, r.text[:200])
            payload = r.json()
            check("GET /config exposes security.internal", "internal" in payload.get("security", {}))
            check("GET /config exposes security.public", "public" in payload.get("security", {}))
            check("GET /config has no legacy auth node", "auth" not in payload)

            empty = c.put("/v1/management/config", json={"config": {}})
            check("an empty PUT is accepted", empty.status_code == 200, empty.text[:200])
            disk = yaml.safe_load(Path(_TMP).read_text(encoding="utf-8"))
            check("an empty PUT does not clear upstreams", len(disk.get("upstreams", [])) == len(cfg["upstreams"]))
            check("an empty PUT does not drop the provider key", secret in str(disk))
            check("an empty PUT preserves security.public.path_allowlist",
                  disk["security"]["public"]["path_allowlist"] == cfg["security"]["public"]["path_allowlist"])
            check("an empty PUT preserves security.internal",
                  disk["security"]["internal"] == cfg["security"]["internal"])

            masked = c.put("/v1/management/config", json={"config": {"upstreams": payload["upstreams"]}})
            check("saving the masked projection succeeds", masked.status_code == 200, masked.text[:200])
            disk2 = yaml.safe_load(Path(_TMP).read_text(encoding="utf-8"))
            check("masked headers are restored from disk", secret in str(disk2) and "••••••" not in str(disk2))

            bad = c.put("/v1/management/config", json={"config": {"systemone": {"api_key_env": "ADMIN_PASSWORD"}}})
            check("PUT rejects an api_key_env pointing at ADMIN_PASSWORD", bad.status_code in (400, 422), bad.text[:200])
    finally:
        os.environ.pop("ADMIN_PASSWORD", None)


def test_inference_concurrency_gate() -> None:
    """High request volume is QUEUED, not rejected: at most `max_concurrency`
    requests are in flight against model backends, the rest wait for a free slot.

    Control-plane traffic (System One, web search, probes) uses a separate pool,
    so a busy model never makes the console report every upstream as offline.
    """
    import asyncio
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from unittest.mock import AsyncMock

    import httpx
    from http_clients import build_clients, inference_timeout

    expected_limit = int(yaml.safe_load(EXAMPLE)["server"]["max_concurrency"])

    with client_with() as c:
        cfg = app.state.manager.config
        control = app.state.client
        inference = app.state.inference_client
        check("control and inference are separate pools", control is not inference)
        check("inference pool is capped at max_concurrency",
              inference._transport._pool._max_connections == expected_limit,
              str(inference._transport._pool._max_connections))
        check("control-plane pool is not gated",
              control._transport._pool._max_connections > expected_limit,
              str(control._transport._pool._max_connections))
        check("model forwarding uses the gated pool",
              all(s.client is inference for s in (app.state.proxy, app.state.rerank, app.state.messages)))
        check("systemone / websearch use the control pool",
              app.state.systemone.client is control and app.state.websearch.client is control)
        check("every inference request carries the queue wait cap",
              inference_timeout(600.0, cfg).pool == cfg.server.upstream_queue_timeout)

        # A full queue is back-pressure, not an upstream failure. Cover both the
        # buffered path (client.request) and the default streaming path (client.send).
        app.state.proxy.client.request = AsyncMock(side_effect=httpx.PoolTimeout("busy"))
        app.state.proxy.client.send = AsyncMock(side_effect=httpx.PoolTimeout("busy"))
        busy = c.post("/v1/chat/completions", json={"model": "qwen3:8b", "messages": [], "stream": False})
        check("a saturated inference queue answers 503, not 502", busy.status_code == 503, busy.text[:160])
        check("the 503 advertises Retry-After", busy.headers.get("retry-after") == "5", str(dict(busy.headers)))
        busy_stream = c.post("/v1/chat/completions", json={"model": "qwen3:8b", "messages": []})
        check("a saturated queue on a streaming call also answers 503", busy_stream.status_code == 503,
              busy_stream.text[:160])

    class _SlowHandler(BaseHTTPRequestHandler):
        lock = threading.Lock()
        active = 0
        peak = 0

        def do_GET(self):
            with _SlowHandler.lock:
                _SlowHandler.active += 1
                _SlowHandler.peak = max(_SlowHandler.peak, _SlowHandler.active)
            try:
                time.sleep(0.12)
                self.send_response(200)
                self.send_header("content-length", "2")
                self.end_headers()
                self.wfile.write(b"ok")
            finally:
                with _SlowHandler.lock:
                    _SlowHandler.active -= 1

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/"
    try:
        async def burst(client, count, *, timeout=None):
            # A per-request Timeout object is what proxy.py / messages.py / rerank.py
            # actually pass; it must not silently drop the pool wait cap.
            kwargs = {} if timeout is None else {"timeout": timeout}
            results = await asyncio.gather(
                *(client.get(url, **kwargs) for _ in range(count)), return_exceptions=True
            )
            return ["timeout" if isinstance(r, httpx.PoolTimeout) else getattr(r, "status_code", repr(r))
                    for r in results]

        async def main():
            ctl1, inf1 = build_clients(cfg)
            try:
                statuses = await burst(inf1, 8)
            finally:
                await ctl1.aclose()
                await inf1.aclose()

            tiny = SimpleNamespace(server=SimpleNamespace(
                max_concurrency=2, request_timeout=10.0,
                upstream_queue_timeout=0.05, upstream_keepalive_expiry=30.0))
            ctl2, inf2 = build_clients(tiny)
            try:
                tiny_statuses = await burst(inf2, 6, timeout=inference_timeout(10.0, tiny))
            finally:
                await ctl2.aclose()
                await inf2.aclose()
            return statuses, tiny_statuses

        statuses, tiny_statuses = asyncio.run(main())
        check("a burst of 8 is queued, never dropped", statuses == [200] * 8, str(statuses))
        check("at most max_concurrency requests hit the backend at once",
              1 <= _SlowHandler.peak <= expected_limit,
              f"peak={_SlowHandler.peak} limit={expected_limit}")
        check("a tiny queue wait cap produces PoolTimeout instead of an endless wait",
              "timeout" in tiny_statuses, str(tiny_statuses))
    finally:
        server.shutdown()
        server.server_close()


# 6. security.blocked_ips: interrupt denied requests (TCP + ASGI layers) -----
def test_blocked_ips_matching() -> None:
    """Pure snapshot semantics: IP/CIDR parsing, both address families, invalid
    entries, dedup, hot swaps and the protocol-activation detector.
    """
    import ip_guard

    try:
        # Starlette inserts add_middleware() calls at index 0 and builds the
        # stack in reverse: this guard must be the outermost user middleware, or
        # the HTTP middleware would already have logged the denied request.
        check("BlockedIPMiddleware is the outermost user middleware",
              bool(app.user_middleware)
              and app.user_middleware[0].cls is ip_guard.BlockedIPMiddleware,
              str([m.cls.__name__ for m in app.user_middleware]))

        with capture_logs("llm_hub") as logs:
            count = ip_guard.configure([
                "203.0.113.7", "10.0.0.0/8", "2001:db8::/32",
                " 192.0.2.5 ", "not-an-ip", "999.999.999.999", "", None,
            ])
        check("4 valid rules compiled from a mixed list", count == 4, str(ip_guard.active_rules()))
        check("invalid / blank / non-string entries are skipped with warnings",
              len(logs["llm_hub"].records) >= 3, str(logs["llm_hub"].records))
        check("bare IPs and CIDRs normalise to networks",
              ip_guard.active_rules() == ("203.0.113.7/32", "10.0.0.0/8",
                                          "2001:db8::/32", "192.0.2.5/32"),
              str(ip_guard.active_rules()))
        check("a single IPv4 address matches", ip_guard.is_blocked("203.0.113.7"))
        check("the neighbouring IPv4 address is allowed", not ip_guard.is_blocked("203.0.113.8"))
        check("an IPv4 CIDR matches", ip_guard.is_blocked("10.99.12.3"))
        check("an address outside every CIDR is allowed", not ip_guard.is_blocked("11.0.0.1"))
        check("an IPv6 CIDR matches", ip_guard.is_blocked("2001:db8::abcd"))
        check("an IPv6 address outside the CIDR is allowed", not ip_guard.is_blocked("2001:db9::1"))
        check("IPv4 and IPv6 families never cross-match", not ip_guard.is_blocked("::ffff:10.1.2.3"))
        check("whitespace around an entry is trimmed", ip_guard.is_blocked("192.0.2.5"))
        check("missing / unparsable peer IP never blocks",
              not ip_guard.is_blocked(None) and not ip_guard.is_blocked("garbage"))

        ip_guard.configure(["203.0.113.7/24"])
        check("a host address with a prefix resolves to its containing network",
              ip_guard.active_rules() == ("203.0.113.0/24",), str(ip_guard.active_rules()))
        ip_guard.configure(["10.0.0.1", "10.0.0.1/32"])
        check("duplicate rules collapse after normalisation",
              len(ip_guard.active_rules()) == 1, str(ip_guard.active_rules()))
        ip_guard.configure(["198.51.100.9"])
        check("a reload replaces (never appends to) the deny-list",
              ip_guard.active_rules() == ("198.51.100.9/32",)
              and not ip_guard.is_blocked("203.0.113.7")
              and ip_guard.is_blocked("198.51.100.9"))
        ip_guard.reset()
        check("reset clears every rule", ip_guard.active_rules() == ()
              and not ip_guard.is_blocked("198.51.100.9"))

        # The detector drives the startup log. Test the negative branch without
        # permanently importing the protocol module into this process.
        saved_module = sys.modules.pop("ip_guard_protocol", None)
        try:
            check("guard_protocol_active() is False without the protocol module",
                  not ip_guard.guard_protocol_active())
        finally:
            if saved_module is not None:
                sys.modules["ip_guard_protocol"] = saved_module
    finally:
        ip_guard.reset()


def test_blocked_ips_config_and_hot_reload() -> None:
    """The config field, its defaults, and ConfigManager publishing the snapshot
    on every load (including hot reload and the keep-last-good error path).
    """
    import ip_guard
    from config import ConfigManager

    def write(cfg: dict) -> None:
        Path(_TMP).write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")

    try:
        check("SecurityConfig declares blocked_ips", "blocked_ips" in SecurityConfig.model_fields)
        check("the default config emits an empty blocked_ips",
              build_default_config()["security"]["blocked_ips"] == [])
        check("the example config emits an empty blocked_ips",
              yaml.safe_load(EXAMPLE)["security"]["blocked_ips"] == [])
        check("a non-empty list is accepted",
              Config(**{"security": {"blocked_ips": ["203.0.113.7"]}}).security.blocked_ips
              == ["203.0.113.7"])

        base = yaml.safe_load(EXAMPLE)
        base["server"]["host"] = "127.0.0.1"
        base["security"]["internal"]["allow_anonymous"] = True
        base["security"]["public"]["port"] = None
        base["security"]["blocked_ips"] = ["203.0.113.7", "10.0.0.0/8"]
        write(base)
        manager = ConfigManager(_TMP)
        check("ConfigManager publishes the deny-list on load",
              ip_guard.active_rules() == ("203.0.113.7/32", "10.0.0.0/8"),
              str(ip_guard.active_rules()))
        check("published rules are immediately enforced",
              ip_guard.is_blocked("203.0.113.7") and ip_guard.is_blocked("10.1.2.3"))

        base["security"]["blocked_ips"] = ["198.51.100.0/24"]
        write(base)
        manager.reload()
        check("a hot reload swaps the deny-list with no restart",
              ip_guard.active_rules() == ("198.51.100.0/24",)
              and not ip_guard.is_blocked("203.0.113.7")
              and ip_guard.is_blocked("198.51.100.42"),
              str(ip_guard.active_rules()))

        broken = yaml.safe_load(EXAMPLE)
        broken["security"]["blocked_ips"] = "not-a-list"
        write(broken)
        manager.reload()
        check("a rejected config keeps the last good deny-list",
              ip_guard.active_rules() == ("198.51.100.0/24",), str(ip_guard.active_rules()))
    finally:
        clean = yaml.safe_load(EXAMPLE)
        clean["security"]["internal"]["allow_anonymous"] = True
        clean["security"]["public"]["port"] = None
        write(clean)
        ip_guard.reset()


def test_blocked_ips_interrupts_request() -> None:
    """ASGI safety net: a denied request never reaches a route or the request
    log, and produces exactly one `deny <ip>` line. Allowed peers are untouched.
    """
    import sqlite3

    log_db = f"{_TMP}.blocked.db"
    saved_db = os.environ.get("REQUEST_LOG_DB")
    os.environ["REQUEST_LOG_DB"] = log_db
    Path(log_db).unlink(missing_ok=True)
    seed_api_keys(API_KEY)

    def read_rows() -> list[dict]:
        con = sqlite3.connect(log_db)
        con.row_factory = sqlite3.Row
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(requests)")}
            wanted = [c for c in ("path", "status", "client_ip") if c in cols]
            return [dict(r) for r in con.execute(f"SELECT {', '.join(wanted)} FROM requests")]
        finally:
            con.close()

    try:
        with capture_logs("llm_hub") as logs:
            with client_with(blocked_ips=["127.0.0.2"],
                             client_addr=("127.0.0.2", 41000)) as c:
                denied = c.get("/health")
                check("a denied request is interrupted with no body",
                      denied.status_code == 444 and denied.content == b"",
                      f"{denied.status_code} {denied.content!r}")
        deny_lines = [m for _, m in logs["llm_hub"].records if m.startswith("deny ")]
        check("exactly one 'deny <ip>' line is emitted", deny_lines == ["deny 127.0.0.2"],
              str(deny_lines))
        check("startup reports which enforcement layer is active",
              any("security.blocked_ips" in m for _, m in logs["llm_hub"].records),
              str(logs["llm_hub"].records[-6:]))
        rows = read_rows()
        check("the interrupted request writes no request-log row", not rows, str(rows))

        with capture_logs("llm_hub") as allowed_logs:
            with client_with(client_addr=("127.0.0.1", 41000)) as c:
                ok = c.get("/health")
                check("a non-blocked peer still gets the normal response",
                      ok.status_code == 200 and ok.json() == {"ok": True}, ok.text[:120])
        check("no deny line for an allowed peer",
              not any(m.startswith("deny ") for _, m in allowed_logs["llm_hub"].records),
              str(allowed_logs["llm_hub"].records[-4:]))
        rows = read_rows()
        check("the allowed request is recorded with its real peer IP",
              any(r.get("path") == "/health" and r.get("status") == 200
                  and r.get("client_ip") == "127.0.0.1" for r in rows), str(rows))
    finally:
        if saved_db is None:
            os.environ.pop("REQUEST_LOG_DB", None)
        else:
            os.environ["REQUEST_LOG_DB"] = saved_db
        Path(log_db).unlink(missing_ok=True)
        import ip_guard
        ip_guard.reset()


def test_blocked_ips_socket_drop() -> None:
    """Real uvicorn + GuardProtocol: a denied TCP peer gets ZERO HTTP bytes, no
    ASGI scope, no access-log line, no request-log row and one `deny <ip>` line.

    Also exercises the CLI `--http` import path, the two launch entrypoints and
    a deny-list hot reload on a live (otherwise idle) server.
    """
    import socket
    import sqlite3
    import threading
    import time

    import ip_guard
    import uvicorn
    from ip_guard_protocol import GuardProtocol
    from uvicorn.importer import import_from_string
    from uvicorn.protocols.http.httptools_impl import HttpToolsProtocol

    check("the CLI --http string resolves to GuardProtocol",
          import_from_string("ip_guard_protocol:GuardProtocol") is GuardProtocol)
    launcher = Path("scripts/start-local.ps1").read_text(encoding="utf-8")
    check("start-local.ps1 passes --http to uvicorn",
          "'--http', 'ip_guard_protocol:GuardProtocol'" in launcher)
    check("serve.py injects the custom protocol",
          "http=GuardProtocol" in Path("app/serve.py").read_text(encoding="utf-8"))
    check("GuardProtocol is a uvicorn HTTP protocol subclass",
          issubclass(GuardProtocol, HttpToolsProtocol))
    check("guard_protocol_active() sees the installed protocol",
          ip_guard.guard_protocol_active())

    log_db = f"{_TMP}.socket.db"
    saved_db = os.environ.get("REQUEST_LOG_DB")
    os.environ["REQUEST_LOG_DB"] = log_db
    Path(log_db).unlink(missing_ok=True)
    seed_api_keys(API_KEY)

    base = yaml.safe_load(EXAMPLE)
    base["server"]["host"] = "127.0.0.1"
    base["security"]["internal"]["allow_anonymous"] = True
    base["security"]["public"]["port"] = None
    base["security"]["blocked_ips"] = ["127.0.0.2"]
    Path(_TMP).write_text(yaml.safe_dump(base, allow_unicode=True, sort_keys=False), encoding="utf-8")

    hits: list = []

    class CountingApp:
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope.get("type") == "http":
                hits.append(scope.get("client"))
            await self.inner(scope, receive, send)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(32)
    port = sock.getsockname()[1]

    config = uvicorn.Config(
        CountingApp(app), log_config=None, log_level="info", lifespan="on",
        proxy_headers=False, forwarded_allow_ips=None, access_log=True,
        http=GuardProtocol,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)

    def fetch(source_ip: str, path: str = "/health") -> bytes:
        try:
            conn = socket.create_connection(("127.0.0.1", port), timeout=2,
                                            source_address=(source_ip, 0))
            conn.sendall(
                f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode()
            )
            conn.settimeout(2)
            data = b""
            try:
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
            except OSError:
                pass  # RST from transport.abort() surfaces here
            finally:
                conn.close()
            return data
        except OSError as exc:
            return f"ERR:{exc}".encode()

    try:
        with capture_logs("llm_hub", "uvicorn.access", "uvicorn.error") as logs:
            thread.start()
            deadline = time.monotonic() + 6
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.02)
            check("the real uvicorn server started", bool(server.started))
            if not server.started:
                return

            denied = fetch("127.0.0.2")
            check("a denied TCP peer receives zero HTTP bytes", denied == b"", repr(denied[:120]))
            check("no ASGI scope was created for the denied connection", hits == [], str(hits))
            deny_lines = [m for _, m in logs["llm_hub"].records if m.startswith("deny ")]
            check("exactly one 'deny <ip>' line for the dropped socket",
                  deny_lines == ["deny 127.0.0.2"], str(deny_lines))
            check("no uvicorn.access line for the denied peer",
                  not any("127.0.0.2" in m for _, m in logs["uvicorn.access"].records),
                  str(logs["uvicorn.access"].records))
            check("no ASGI 'returned without starting response' / 500 fallback",
                  not any("ASGI callable returned" in m or "Exception in ASGI" in m
                          for _, m in logs["uvicorn.error"].records),
                  str(logs["uvicorn.error"].records[-4:]))

            access_before = len(logs["uvicorn.access"].records)
            allowed = fetch("127.0.0.1")
            check("an allowed peer gets a normal HTTP 200",
                  allowed.startswith(b"HTTP/1.1 200"), repr(allowed[:120]))
            check("the allowed request reached ASGI", len(hits) == 1, str(hits))
            check("the allowed request produces an access-log line",
                  any('"GET /health' in m for _, m in logs["uvicorn.access"].records[access_before:]),
                  str(logs["uvicorn.access"].records[access_before:]))

            # Hot reload with no traffic in between: the background config watch
            # must republish the snapshot even though denied sockets never reach
            # the ASGI middleware that normally triggers a reload.
            base["security"]["blocked_ips"] = ["127.0.0.3"]
            Path(_TMP).write_text(yaml.safe_dump(base, allow_unicode=True, sort_keys=False),
                                  encoding="utf-8")
            deadline = time.monotonic() + 4
            while ip_guard.active_rules() != ("127.0.0.3/32",) and time.monotonic() < deadline:
                time.sleep(0.1)
            check("blocked_ips hot-reloads on a live server without a restart",
                  ip_guard.active_rules() == ("127.0.0.3/32",), str(ip_guard.active_rules()))
            former = fetch("127.0.0.2")
            newly = fetch("127.0.0.3")
            check("the removed IP is served again", former.startswith(b"HTTP/1.1 200"),
                  repr(former[:80]))
            check("the newly added IP is dropped", newly == b"", repr(newly[:80]))
            check("no ASGI scope for the newly denied IP", len(hits) == 2, str(hits))

            server.should_exit = True
        thread.join(timeout=5)
        check("the real uvicorn server shut down cleanly", not thread.is_alive())

        con = sqlite3.connect(log_db)
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in con.execute("SELECT path, status, client_ip FROM requests")]
        finally:
            con.close()
        check("no denied connection ever wrote a request-log row",
              all(r["status"] == 200 for r in rows)
              and not any(r["client_ip"] == "127.0.0.3" for r in rows), str(rows))
        check("only allowed requests are in the request log",
              sum(1 for r in rows if r["client_ip"] == "127.0.0.2") == 1
              and any(r["client_ip"] == "127.0.0.1" and r["path"] == "/health" for r in rows),
              str(rows))
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        if saved_db is None:
            os.environ.pop("REQUEST_LOG_DB", None)
        else:
            os.environ["REQUEST_LOG_DB"] = saved_db
        Path(log_db).unlink(missing_ok=True)
        clean = yaml.safe_load(EXAMPLE)
        clean["security"]["internal"]["allow_anonymous"] = True
        clean["security"]["public"]["port"] = None
        Path(_TMP).write_text(yaml.safe_dump(clean, allow_unicode=True, sort_keys=False),
                              encoding="utf-8")
        ip_guard.reset()


if __name__ == "__main__":
    print("Security verification:")
    try:
        test_headers()
        test_rate_limit()
        test_xff_is_not_a_decision_input()
        test_body_cap_and_retry_after()
        test_nul_path_and_hidden_version()
        test_forwarding_hygiene()
        test_log_redaction()
        test_standalone_log_detail()
        test_request_log_coverage()
        test_log_timestamps()
        test_config_guards()
        test_dot_segments_and_redirects()
        test_public_allowlist_and_key()
        test_public_bad_regex()
        test_public_full_match()
        test_admin_endpoints_never_opens_a_path()
        test_public_credential_is_a_zone_rule_not_a_route_rule()
        test_cors_preflight_is_bodyless_and_precedes_the_key_check()
        test_public_never_exposes_management_or_console()
        test_admin_endpoints_allow_opens_the_management_plane()
        test_public_only_instance()
        test_zone_uses_kernel_socket_not_headers()
        test_retired_security_knobs()
        test_internal_anonymous_toggle()
        test_legacy_config_migration()
        test_config_security()
        test_inference_concurrency_gate()
        test_blocked_ips_matching()
        test_blocked_ips_config_and_hot_reload()
        test_blocked_ips_interrupts_request()
        test_blocked_ips_socket_drop()
    finally:
        if os.path.exists(_TMP):
            os.unlink(_TMP)
    print(f"\nResult: {PASSED} passed, {FAILED} failed")
    raise SystemExit(1 if FAILED else 0)
