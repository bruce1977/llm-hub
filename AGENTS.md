# LLM Hub - Agent Guidelines

## Project Overview

LLM Hub is a FastAPI-based unified gateway that aggregates multiple Ollama instances, LM Studio, and OpenAI-compatible backends into a single API endpoint. It provides:
- Multi-upstream routing by model name
- API key authentication
- Rerank API implementation
- Web search pass-through (OpenCode API Gateway upstream)
- Model aliasing and parameter injection
- Config hot reload
- Read-only management API (`/v1/management`) with a SQLite request log
- Vue 3 web console (`web/`, built into `app/web`, served at `/admin`) over that API

## Architecture

```
llm-hub/
├── app/
│   ├── main.py       # FastAPI application, lifespan, special endpoints
│   ├── config.py     # Config models, loading, hot reload, alias resolution
│   ├── auth.py       # API key authentication, security helpers
│   ├── admin_auth.py # ADMIN_PASSWORD login + require_admin (session cookie or gateway API key)
│   ├── proxy.py      # Reverse proxy, streaming pass-through, request-log capture
│   ├── request_log.py # SQLite request log (WAL, async batched writes, stats queries)
│   ├── management.py # Read-only management API (/v1/management) for the web console
│   ├── bench.py      # Model speed test engine (Python port of bench_llm.mjs, no Node needed)
│   ├── bench_store.py # bench_results table in llmhub.db (latest 3 rows per upstream+model)
│   ├── rerank.py     # Rerank API implementation (logprobs/embedding modes)
│   ├── systemone.py  # System One decision API (proxied to laya-server or Ollama 0.40+)
│   ├── websearch.py  # Web search API (proxied to an OpenCode API Gateway upstream)
│   ├── static/swagger/  # Vendored Swagger UI assets (offline /docs)
│   └── web/             # Built console bundle (output of `npm run build`) - served at /admin
├── data/
│   ├── config.yaml          # Runtime configuration (mounted into container)
│   └── config.example.yaml  # Config template copied to config.yaml
├── web/                  # Vue 3 + TypeScript management console sources
│   ├── src/                  # Sources: *.ts modules (api/, stores/, router/, types/) + *.vue SFCs
│   ├── index.html
│   ├── public/favicon.svg    # Tab icon, copied verbatim into app/web and served at /admin/favicon.svg
│   ├── package.json
│   ├── tsconfig.json         # strict, moduleResolution "bundler", @/* -> src/*
│   └── vite.config.ts        # build.outDir = ../app/web (the bundle is committed, node_modules are not)
├── tests/
│   ├── smoke_test.py        # Comprehensive mock tests (123)
│   ├── test_gateway_new.py  # Aggregation / systemone / web search / /docs schema tests (63)
│   ├── test_management.py   # Management API + request log + console serving tests (198)
│   ├── verify_security.py   # Security control tests (15)
│   ├── test_api.py          # E2E tests against a live server (28)
│   └── demo.py              # Demo script (requires real Ollama)
├── docs/
│   ├── API.md               # API documentation with all endpoints
│   ├── TESTCASES.md         # Test cases documentation with examples
│   ├── DEPLOY.md            # Docker deployment guide
│   ├── Ranker.md            # Reranker configuration guide
│   ├── USAGE.md / USAGE_CN.md  # Full manual: config reference, console, endpoints, security
│   ├── web-management/      # Console spec, mockups, capture-live.js (live screenshot capture)
│   ├── screenshots/         # README gallery: en/ and zh/ sets, produced by capture-live.js
│   └── workflow.md          # Development workflow with diagrams
├── scripts/
│   ├── start-local.ps1      # Local launcher: preflight (deps/config/keys/port) then uvicorn
│   ├── start-local.bat      # Wrapper for the ps1 above (powershell -ExecutionPolicy Bypass)
│   ├── docker-build.bat     # Build the image (latest + version tag)
│   └── docker-publish.bat   # Build then push both tags to Docker Hub
├── .vscode/
│   ├── settings.json # VSCode editor settings
│   ├── extensions.json # Recommended extensions
│   └── launch.json   # Debug configurations
├── Dockerfile        # Container build (python:3.12-slim)
├── pyproject.toml    # Python tooling config (ruff, mypy, pytest)
├── requirements.txt  # Runtime dependencies
├── README.md / README_CN.md   # Short landing page: pitch, screenshots, quick start
├── CHANGELOG.md / CHANGELOG_CN.md
└── AGENTS.md         # This file
```

## Code Conventions

### Python Style
- Python 3.12+
- Use type hints consistently
- Follow PEP 8 style guidelines
- Use `from __future__ import annotations` for forward references
- Use PEP 604 unions (`str | None`) — `from __future__ import annotations` keeps them lazy
- Use Pydantic models for configuration and validation
- Use `logging` module with `llm_hub` logger name

### Import Organization
- Standard library → third-party → local modules
- Group imports by section with blank lines
- Use absolute imports within the project

### Error Handling
- Use `HTTPException` with appropriate status codes
- Provide meaningful error messages
- Log errors with context
- Use `from exc` chaining for exception propagation

### Configuration
- Config models use Pydantic `BaseModel`
- Hot reload via file mtime checking
- Environment variables for secrets (never in config.yaml)
- Config validation at startup

## Testing

### Running Tests
```bash
# Mock tests only (no external dependencies)
python tests/smoke_test.py

# Gateway features: aggregation, systemone/websearch forwarding, /docs schema (no external deps)
python tests/test_gateway_new.py

# Management API + request log + console static serving (spins up a local mock Ollama; no external deps)
python tests/test_management.py

# Security verification tests (no external dependencies)
python tests/verify_security.py

# E2E API tests (requires running server + real Ollama on port 11434)
# 1. Start the server using the current directory's data/config.yaml:
PYTHONPATH=app CONFIG_PATH=./data/config.yaml \
  GATEWAY_API_KEYS=sk-test-example-key \
  python -m uvicorn main:app --host 127.0.0.1 --port 8000

# 2. In another terminal, run the E2E tests:
python tests/test_api.py --base-url http://127.0.0.1:8000 --api-key sk-test-example-key

# Demo script (requires real Ollama)
python tests/demo.py
```

**Important:** E2E tests always use `data/config.yaml` from the project root (set via `CONFIG_PATH=./data/config.yaml`). This config points to the local Ollama instance at `127.0.0.1:11434`. Never hardcode paths in test commands.

### Test Structure
- `smoke_test.py` — 123 mock tests (auth, routing, aliases, streaming, `/api/generate` forwarding, rerank, security, `/` system info, upstream types), no external deps
- `test_gateway_new.py` — 63 tests for aggregation, systemone + web search forwarding and the `/docs` schema (auth schemes + inline `X-API-Key` input + the documented `/api/generate` request/response schema)
- `test_management.py` — 198 tests for admin auth (session cookie **or** gateway API key → 200; the admin password as `Bearer` / `X-API-Key` and a key in the query string → 401; wrong username → 401; `ADMIN_PASSWORD` unset → 503), the `/admin` static console (`/admin` → `/admin/` redirect, SPA fallback, immutable caching for `assets/`, `../` cannot escape the build, the tab icon served as `image/svg+xml` rather than falling back to the shell, 503 with a build hint when `app/web` is missing), upstream probes + device classification, the `upstreams/{name}` detail (declared `models` with aliases applied, `/api/ps` `running_models`), the config-driven `/models/llm` (only declared names, `display` / `alias` per row, a `qwen3:*` or `opencode/*` prefix expanded through discovery and flagged `wildcard`, an unresolvable pattern kept as its own row with `running: false`, runtime device/memory, dead upstream contributes only its declared names), hosted `opencode` rows (probe online ⇒ every declared model `running` with `device: "cloud"` and memory `null`, rows added to `/overview` `running_models` and the KPI, nothing added when offline), masked config projections (LLM / rerank / systemone / tools-websearch), request-log capture (stream / non-stream / 502, timestamps, requested vs resolved model, query string, the `tokens_in` / `tokens_out` split from both usage formats and the `NULL` when a response reports no usage), the `kpi.tokens_today` total, filters, pagination, timeseries, distribution pies and the `group_by` guard, the `request_log.enabled` switch and the legacy-database `ALTER TABLE` upgrade (including the declared type of the added columns)
- `verify_security.py` — 15 security tests (method allowlist, path allowlist, rate limit, headers)
- `test_api.py` — 28 E2E tests against a real running server + Ollama + web search provider
- `demo.py` — interactive demo (requires real Ollama)
- Tests use `fastapi.testclient.TestClient` (mock) or `httpx.Client` (E2E)
- No pytest framework - uses custom test runner with `check()` assertions

### Writing Tests
- Add new test cases to `smoke_test.py`
- Use the `check()` function for assertions
- Mock upstreams in `make_handler()` for new endpoint types
- Follow existing test patterns for consistency

## Development Workflow

### Local Development
```bash
# Easiest - the launcher preflights dependencies/config/keys/port, then runs uvicorn:
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1   # or: scripts\start-local.bat
#    flags: -Port 8888  -Bind 0.0.0.0  -Config <path>  -NoReload  -Install  -CheckOnly

# Or manually - the modules are flat inside app/, so run from app/ (same as WORKDIR /app)
# and load .env there:
pip install -r requirements.txt
cd app && python -m uvicorn main:app --reload --port 8000 --env-file ../.env
```
Never `python -m uvicorn app.main:app` - the flat modules import each other by bare name
(`import admin_auth`, `from config import ...`), so that form dies with
`ModuleNotFoundError: No module named 'admin_auth'`. From the repository root instead use
`--app-dir app` (or `PYTHONPATH=app`), which is what `scripts/start-local.ps1` does.

### Console Development
```bash
# The console is a Vue 3 + TypeScript app in web/, served by the gateway at /admin.
# Every module under src/ is .ts; every SFC uses <script setup lang="ts">.
cd web
npm install

# Type-check only (vue-tsc, no emit) - the views' prop/fetch types come from types/api.ts
npm run typecheck

# Hot reload with the gateway running on :8000 (Vite proxies /v1/management and /health)
npm run dev            # http://localhost:5173/admin/

# Build into app/web - runs `vue-tsc --noEmit` first, so a type error fails the build.
# That bundle IS committed, the Docker image copies it with the modules (no node in the image)
npm run build
```
Rebuild and commit `app/web` whenever anything under `web/src`, `web/index.html` or
`web/public/` changes; the container
never runs node. Because the bundle lives inside the app package (like `app/static`),
`app/main.py` resolves it as `Path(__file__).parent / "web"` - the same path locally
(`app/web`) and in the image (`/app/web`).

### Docker Development
```bash
# Pull from Docker Hub (recommended)
docker pull bruce1977/llm-hub:latest

# Run with Docker Hub image
docker run -d --name llm-hub \
  -p 8888:8000 \
  -v ./data:/data \
  --env-file .env \
  --restart unless-stopped \
  bruce1977/llm-hub:latest

# Or build from source
docker build -t llm-hub:latest .
docker run -d --name llm-hub \
  -p 8888:8000 \
  -v ./data:/data \
  --env-file .env \
  llm-hub:latest
```

### Configuration
1. Copy `data/config.example.yaml` to `data/config.yaml`
2. Copy `.env.example` to `.env`
3. Edit config with real upstream URLs
4. Set API keys in `.env` file

The runtime config is **YAML** (`CONFIG_PATH` defaults to `/data/config.yaml`). Because YAML 1.2 is a
superset of JSON, a `CONFIG_PATH` that still points at a `.json` file parses the same way, and a
deployment that only has the pre-YAML `config.json` next to the new default path keeps working:
`ConfigManager._active_path()` loads it with a warning instead of overwriting it with a fresh template.
Comments are allowed, which is why the format changed.

## Key Implementation Details

### Routing Logic
- Exact model match first
- Prefix wildcards (e.g., `qwen3:*`) for pattern matching
- Priority-based routing when same model exists on multiple upstreams
- Alias resolution before routing
- Fallback to default upstream when `routing.strict=false`

### Streaming
- NDJSON for Ollama native API (`/api/chat`, `/api/generate`)
- SSE for OpenAI-compatible API (`/v1/chat/completions`)
- Pass-through without modification
- `proxy.STREAM_BY_DEFAULT` (`api/chat`, `api/generate`, `v1/chat/completions`, `v1/completions`)
  decides the transport when the caller omits `stream`, matching Ollama's own defaults

### Schema-visible proxy routes
- `main.py` registers a documented `POST /api/generate` route **before** the catch-all. The handler
  is a one-liner into `ProxyService.forward(request, "api/generate")`, so routing, alias + parameter
  injection, `num_ctx_overrides` clamping, NDJSON streaming and request-log capture are unchanged;
  the route exists only to (a) put the endpoint in `/openapi.json` and `/docs` with a real request
  schema and both response media types, and (b) take part in `POST_ONLY_PATHS`, which makes any
  non-POST method a gateway **405** with `Allow: POST` instead of proxying it upstream
- `/api/chat`, `/v1/chat/completions` and `/v1/completions` remain catch-all-only (not in the schema)

### Security
- Admin endpoints blocked by default (`api/delete`, `api/pull`, etc.)
- Client IP allowlist support
- CORS configuration (no `*` by default)
- Request body size limits
- Weak key detection at startup (refuses to boot with placeholder keys)
- HTTP method allowlist (`security.allowed_http_methods`, default GET/POST -> 405 for the rest)
- Regex path allowlist (`security.path_allowlist`, 403 for non-matching paths)
- In-memory token-bucket rate limiter (`security.rate_limit`, 429 when over budget)
- Defensive response headers on every response (nosniff, X-Frame-Options, CSP, etc.)
- Admin/management console (`/admin` + `/v1/management`) authenticates with the HMAC session cookie
  minted by `POST /v1/management/auth/login` **or** a gateway API key (`GATEWAY_API_KEYS` /
  `GATEWAY_API_KEYS_FILE`); `ADMIN_PASSWORD` is a login credential only (sending it as
  `Bearer` / `X-API-Key` is **401**)
- Caller API keys come only from the environment (`GATEWAY_API_KEYS`, or the file named by
  `GATEWAY_API_KEYS_FILE` such as `data/.keys`); `auth.api_keys` is a read-only runtime view, and an
  `api_keys:` block left in `config.yaml` logs a warning and is ignored. When
  `GATEWAY_API_KEYS_FILE` is unset, `config.collect_env_api_keys()` falls back to `.keys` in the
  **same directory as `CONFIG_PATH`** (`config.default_keys_file()`), so mounting `/config/.keys`
  needs no env var — an explicit `GATEWAY_API_KEYS_FILE` always wins
- Startup requires a key **unless** `auth.allow_access_ip_whitelist` is non-empty: that is the
  trusted-network mode (whitelisted peers skip the key check anyway), logged as a warning; with no
  key and an empty whitelist the app refuses to start. The misspellings `allow_access_ip_whilelist`
  / `allow_ip_whitelist` are normalised to the real field with a warning (pydantic would otherwise
  drop them silently and leave the whitelist empty)
- `/docs` and `/openapi.json` are served only to sources matching `auth.allow_access_ip_whitelist`
  (`re.fullmatch` against the ASGI client address; the gateway never reads `X-Forwarded-For` itself
  because a caller can forge it), and those sources also bypass API-key checks. Everyone else gets
  **404**. The old `security.allow_docs` field is gone. Uvicorn's `ProxyHeadersMiddleware` may have
  already rewritten `request.client` from forwarded headers when the peer is a trusted proxy
  (`--forwarded-allow-ips`, default `127.0.0.1`), so behind a local reverse proxy the real client
  address is what matches — the proxy must sanitise `X-Forwarded-For`
- Endpoints listed in `main.POST_ONLY_PATHS` answer **405** with `Allow: POST` on other methods

### Rerank Modes
- **logprobs**: Generative scoring using token probabilities
- **embedding**: Vector cosine similarity scoring
- Per-model mode override via `model_modes` config

### Web Search (`POST /v1/tools/web-search`)
- Served by `websearch.py`, which forwards to the upstream named in `websearch.upstream`
  (or the first `type: "opencode"` upstream) at `websearch.path`
- Request and response are passed through unchanged; `model` is **never** resolved through
  the gateway's aliases, and nothing is injected. The provider implementation is
  [opencode-port](https://github.com/bruce1977/opencode-port) (OpenCode API Gateway), where
  `model` is optional: omitted, it resolves `web_search.model` → the first `ollama-local/*` model
  → `models.auto.fallback`, and that model only drives answer synthesis when `synthesize=true`
- The provider is a regular upstream, so its models appear in `/v1/models` and chat can be
  routed to it; model ids are slash-separated (`opencode/big-pickle`), so the wildcard is
  `"opencode/*"` — a colon form matches nothing. Discovery (`fetch_upstream_models`) and
  `/probe` send `upstream.headers`, so a provider that requires auth is still aggregated
  (a missing header there shows up as a `401` warning and an empty model list)
- Optional upstream signing: `websearch.api_key_env` names an env var whose value is sent as
  `Authorization: Bearer <key>`; empty (default) = unsigned. Caller credentials are stripped
  before forwarding, as everywhere else in the gateway
- Statuses: 404 when `websearch.enabled=false`, 503 when the named upstream is missing,
  502/504 on provider connect/read-timeout failures, 405 for non-POST (the path is in
  `POST_ONLY_PATHS`), 400 for a non-JSON-object body
- A connect failure (502) logs the full exception chain plus a per-address TCP probe
  (`dns(host) -> ip:port ConnectionRefusedError errno=111`, `TimeoutError`, ...) so
  `All connection attempts failed` can be traced to refused vs filtered vs DNS
- An error the provider itself returns (status + body) is passed through unchanged — e.g.
  `synthesize: true` can hit the provider's own 300s inference cap and answer **502**
  `opencode_unavailable`. Keep `websearch.timeout` >= 300 so that error reaches the caller
  instead of a gateway-side **504**

### System One (`POST /v1/systemone`)
- Served by `systemone.py`; forwards to `systemone.backend` + `systemone.path` with no
  model resolution and no body rewriting beyond the `model` injection below
- `systemone.model` **does reach the backend** — it is injected into the forwarded body only
  when it is a concrete value and the caller did not supply `model`; the sentinel `"local"`
  means "do not inject". The Pydantic default is `auto` (a laya-server enum value), while
  `build_default_config()` emits `"local"` and the shipped `data/config.yaml` uses a real
  model name (`tev1`) against Ollama
- Two backends answer the same path with different contracts:
  - **laya-server** — `model` is a literal enum (`auto` / `english` / `multilingual` /
    `typed-decisions`), anything else is **422**; unknown top-level fields are rejected;
    responses include `routing`
  - **Ollama 0.40+** — `model` must be an installed model name (`auto` → **404**
    `model 'auto' not found`, missing → **400** `model is required`); unknown top-level
    fields are ignored; no `routing` in the response
  - `choice` questions need a non-empty `criteria` **object** on Ollama (array → **400**)
    while `score` questions need a **2–10 item array** (object → **400**); laya-server
    accepts either shape for `choice`. Using object+array respectively is valid on both
- Signing is unconditional: the value of the env var named by `systemone.api_key_env`
  (default `SYSTEMONE_SERVER_API_KEY`, renamed from `LAYA_SERVER_API_KEY`) is sent as
  `Authorization: Bearer <key>` even to an Ollama backend, which ignores it. A missing env
  var logs a warning and forwards unsigned
- Statuses: 404 when `systemone.enabled=false`, 502 on connect/HTTP errors, 504 on read
  timeout; backend status and body are passed through unchanged

### Management API (`/v1/management`) + request log
- `management.py` defines an `APIRouter(prefix="/v1/management")`; `main.py` registers it
  **before** the catch-all, otherwise those paths would be proxied to an upstream
- Auth accepts **two** credentials: the signed session cookie (12h, HttpOnly, SameSite=Lax,
  Secure only on HTTPS, HMAC keyed by `ADMIN_PASSWORD`) **or** a valid gateway API key from
  `GATEWAY_API_KEYS` / `GATEWAY_API_KEYS_FILE` — so scripts can call the management API with the
  key they already have.
  The admin password stays a **login credential only**: `POST /v1/management/auth/login`
  validates it (username `admin`, compared case- and whitespace-insensitively) and mints the
  cookie; sending the password itself as `Bearer` / `X-API-Key` → **401**. The query-string
  key transport is excluded on purpose (a key in a URL lands in access logs), and when
  gateway auth is disabled there are no keys to accept, so the console can never become
  anonymous. No `ADMIN_PASSWORD` → **503**; no valid credential → **401**. The legacy
  `/admin/login|logout|ping` routes delegate to the same functions
- `/v1/management` is exempt from `security.path_allowlist` (like `/admin`) but still subject
  to the method allowlist, client-IP allowlist and rate limit
- All routes are read-only except the model speed test (it only writes `bench_results` in
  `llmhub.db`, never `config.yaml`). Probes reuse `app.state.client` with a 5s timeout so one dead
  upstream cannot stall a view; `overview` / `upstreams` use `asyncio.gather` over upstreams
- Speed test (`POST /bench`, `GET /bench/{job_id}`, `GET /bench/history`):
  `app/bench.py` mirrors `bench_llm.mjs` (1 warm-up + N streaming rounds, decode/prefill taken from
  server timings `eval_duration` / `predicted_ms` / `prompt_eval_duration` and falling back to the
  wall clock with `timing_source` recording which, `think: false` for thinking models, a preflight
  model-id resolve before measuring). Its own `httpx.AsyncClient` sets **`trust_env=False`** like the
  gateway client — without it a system-wide `HTTP_PROXY` hijacks the upstream call and the test dies
  with `502`. `app/bench_store.py` keeps the **latest 3** rows per `(upstream, model)` in
  `bench_results` (WAL queue writer shared with `requests`); jobs live in the in-memory
  `_BENCH_JOBS` map (capped at 200, finished ones swept), so a restart answers `404` for old ids.
  A benchmark runs on a **worker thread with its own event loop** (`asyncio.run`), never as a task on
  the request's loop — the latter is cancelled when the request scope ends and leaves the job stuck
  in `running`. `store.record()` is queued before the job flips to `done`, and `/bench/history`
  awaits `BenchStore.flush()` so a fresh result is never missed; `BenchStore.record()` routes rows
  from the worker thread to the app loop with `call_soon_threadsafe`.
  **Route order matters**: `/bench/history` must be registered *before* `/bench/{job_id}`, else the
  literal `history` segment is captured as a job id and every hover returns
  `404 任务不存在或已过期`
- Ollama device classification: `size_vram / size` → `gpu` (≥ 0.999) / `partial` / `cpu`;
  non-Ollama and unreachable upstreams report `device: null` at the API level, except a reachable
  hosted `opencode` upstream, whose rows report `device: "cloud"`
- Model **names** shown by the console come from `config.yaml` (`upstreams[].models`), never from
  the instance: an installed-but-undeclared model is not routable, so it is not listed either.
  A prefix wildcard (`qwen3:*`, `opencode/*`) is the only case that queries the upstream, through
  `main.fetch_upstream_models()` (the same 60s-cached discovery routing uses) imported **inside the
  handler** because `main` already imports `management` at module scope; that call runs only for
  upstreams that actually declare a pattern. A pattern nothing matched stays visible as the pattern
  itself and is never reported as running
- Every model row carries `model` (real upstream id), `display` (the configured alias when there is
  one, else the declared name) and `alias` (`null` when unaliased), via `_alias_by_target()` — the
  inverse of `Config.aliases` keyed by `(upstream, real id)`
- An `opencode` (hosted) upstream has no `/api/ps`: while its health probe answers, every model it
  declares counts as online with `device: "cloud"` (it runs on the provider's infrastructure) and
  `device_ratio` / `size_gib` / `vram_gib` = `null`, which the console renders as `Cloud` and `-`;
  `_hosted_models()` builds those rows for both `/overview` and `upstreams/{name}` so the two views
  cannot disagree, and an offline provider contributes none
- `upstreams/{name}` detail returns **503** with `"unreachable": true` when the health probe
  fails; unknown names are **404**. Its `models` are the declared names (aliases applied, wildcards
  expanded) and `running_models` the `/api/ps` runtime view (device, `size_gib`, `vram_gib`,
  `num_ctx`, `display`, `alias`)
- `/models/llm` lists one row per declared name per upstream with `wildcard` marking the rows that
  came from expanding a pattern, plus `running` / `device` / `device_ratio` / `num_ctx` / `size_gib`
  / `vram_gib` and the `aliases` / `num_ctx_overrides` blocks
- `kpi.tokens_today` is the day's summed token usage (`tokens`, falling back to
  `tokens_in + tokens_out`), so a row whose provider reported nothing contributes 0
- Credentials never leave the server: header-based upstream keys and the System One env key
  are returned as the fixed mask `••••••`
- `tools/websearch` projects the `websearch` block and derives `base_url` / `upstream_type` from
  `Config.websearch_upstream_obj()` — the same resolver `WebSearchService` uses, so the console can
  never show an address the forwarder would not use; `api_key` only reports whether the env var is set
- `stats/distribution?group_by=upstream|model|status` feeds the Dashboard pies: `status` collapses to
  `ok` (< 400) / `error` (≥ 400) and rows that never reached an upstream (blocked / oversized /
  unrouted) bucket as `-`, so the items always sum to the log total. Any other `group_by` → **400**;
  the column expression comes from `request_log._DISTRIBUTION_KEYS`, never from the caller
- `UpstreamConfig.vram_total_gb` feeds the overview VRAM gauge; it is config-only because no
  upstream API exposes the host total
- `request_log.RequestLog` owns `llmhub.db` (default next to `config.yaml`, override with
  `REQUEST_LOG_DB`), WAL + `synchronous=NORMAL`, 30-day retention (`REQUEST_LOG_RETENTION_DAYS`,
  0 = forever, pruned hourly). Rows are queued (bounded 10 000, drops + warns when full) and
  written by one background task in batches of 100 via `asyncio.to_thread`
- One row per call, grouped as: timing (`ts`, `ts_end`, `duration_ms`, `ttfb_ms` — `ts`/`ts_end`
  are second-precision ISO UTC, so `duration_ms` is the sub-second source), request
  (`method`, `path`, `query`, `requested_model`, `model`, `request_body`, `req_size`, `stream`,
  `client_ip`) and result (`status`, `upstream`, `response_body`, `res_size`, `tokens_in`,
  `tokens_out`, `tokens`, `error`),
  plus the two `*_truncated` flags. `_init_db` diffs the live table against `PRAGMA table_info`
  and `ALTER TABLE ADD COLUMN`s anything missing; `_ADDED_COLUMNS` pairs every late-added column
  with its declared type, so a new numeric column is not added as `TEXT` on old databases
- `config.yaml` -> `request_log.enabled` (default `true`) is the write switch and is evaluated
  **per request** (`ProxyService._logging_on`), so hot reload turns recording on or off without a
  restart; when off, bodies are not captured and nothing is queued. `/overview` and `/requests`
  echo the state as `request_log: {enabled, db, retention_days, dropped}`. `ConfigManager` polls
  the mtime **at most once per second**, so the switch lands within ~1s — live scripts must wait
  out that window (or call `manager.reload()`) before asserting, otherwise the first request after
  the edit still uses the previous config
- `proxy.py` records every outcome: blocked (403) / oversized (413) / unrouted (404),
  streaming (captures ≤ 8 KB of body in the generator, writes after `aclose`), non-streaming,
  and connect/read/HTTP failures (502/504). `model` is the resolved target name.
  `request_log.extract_token_usage()` returns the **(input, output, total)** triple from either
  `usage.prompt_tokens` / `usage.completion_tokens` (total prefers `usage.total_tokens`, which can
  exceed the sum when it counts reasoning tokens) or Ollama's `prompt_eval_count` / `eval_count`;
  a response that never reports usage stores `NULL` in all three columns, which is distinguishable
  from a real `0`. Only the non-streaming path reads usage — streaming rows still leave the three
  token columns empty
- Bodies are truncated to 8 KB; `sanitize_body` re-dumps JSON with `authorization`, `api_key`,
  `token`, `password`, ... replaced by `***`
- Query timestamps are normalized to naive UTC (strings compare correctly); ranges are
  inclusive at both ends. Percentiles: `ORDER BY duration_ms LIMIT 1 OFFSET rank-1`;
  per-bucket P95 uses window functions
- `pyproject.toml` ignores `S608` for `request_log.py` only: SQL fragments are code-controlled
  literals, every value is bound as a parameter

### Web console (`web/`, served at `/admin`)
- Vue 3 + TypeScript + Vite + Element Plus + vue-router + axios + ECharts, sources under `web/src`
  (`*.ts` modules plus `<script setup lang="ts">` SFCs, `strict` in `web/tsconfig.json`), and
  `build.outDir` is `../app/web` so the bundle lands **inside the app package** (committed;
  `web/node_modules` and the Vite cache are ignored). The image does `COPY app/web /app/web`
  together with `COPY app/*.py /app/`, so node never appears in the deployment
- Response shapes live in `web/src/types/api.ts` (taken from live payloads) and are wired to the
  axios wrappers in `web/src/api/management.ts`, so a renamed backend field surfaces as a
  `vue-tsc` error before it becomes a blank table; `npm run build` runs the type check first
- **i18n**: `vue-i18n@11` Composition mode, messages in `web/src/i18n/messages/{zh-CN,en}.ts`,
  namespaced per view. `en.ts` is typed as `MessageSchema = typeof zhCN`, so every locale must have
  exactly the same keys — a forgotten translation is a `vue-tsc` error, never a silent Chinese
  fallback. Locale persists in `localStorage` (`llmhub-console-locale`), the first visit guesses
  from `navigator.language` (`en*` → `en`), and `App.vue` mirrors it into Element Plus via
  `el-config-provider` (plus `<html lang>`). Plain TS modules (`utils/format.ts`,
  `api/client.ts`) call `i18n.global.t()`. When adding any user-facing string: put it in **both**
  message files and reference it with `t('ns.key')` — no literal Chinese in templates
- `main.py` registers the static routes **after** `/admin/login|logout|ping` and **before** the
  catch-all: `/admin` redirects to `/admin/`, the bundle is `Path(__file__).parent / "web"` -
  one path for both layouts because the modules are flat in the image (`/app/web`) and in the
  repo (`app/web`); `../` cannot escape the build directory (`resolve()` + `is_relative_to`),
  and any unmatched path returns `index.html` so deep links such as `/admin/requests` work on a
  hard refresh
- Static files are anonymous on purpose (the login screen must be reachable) - protection lives in
  the API: a missing `app/web` returns **503** with the build command instead of a broken page
- `index.html` is served `no-cache`, `assets/*` (content-hashed by Vite) `immutable`, so a rebuild
  lands on the next reload; ADMIN_CSP (`self` scripts/styles, `data:` images, `connect-src 'self'`)
  applies to every `/admin` path
- Anything in `web/public/` (currently `favicon.svg`) is copied verbatim to `app/web/` and served by
  the same `/admin/{asset_path}` route; Vite rewrites the `<link rel="icon" href="/favicon.svg">`
  to `/admin/favicon.svg` from `base`, and `FileResponse` guesses `image/svg+xml` - which both the
  CSP and an offline container accept
- Sidebar highlighting keys off `.nav-item.router-link-exact-active`, **not** `router-link-active`:
  the Dashboard link sits on `/`, which is an ancestor of every route, so the active class alone
  leaves it lit on every page
- Every refreshable view owns a `refreshing` ref that guards re-entry, is cleared in `finally`, and is
  bound to `TopBar`'s `loading` prop; the bar then shows the spinning icon, an indeterminate bottom
  progress line, a `刷新中…` label, dimmed KPI tiles and a veil over the charts. `TopBar` has no
  local timer of its own - a fake 600 ms spin would claim completion while the probes are still running
- Dashboard and Upstreams both poll at `POLL_MS = 60000` (one minute): every tick probes all upstreams
  with a 5 s budget each, so the old 5 s / 10 s cadence could keep the gateway busy back-to-back
- Model names in the console come from `config.yaml`, not from the instance: rows are
  `upstreams[].models` with the alias shown as `display` (hover/`title` keeps the real id) and a
  `wildcard` chip on the rows an expanded `qwen3:*` / `opencode/*` produced. Hosted (`opencode`) rows
  report `device: "cloud"` with `memory null`, which `deviceBadge` renders as a violet `Cloud` badge and
  `fmtInt` / `memoryText()` as `-`, so views need no per-type branches. Device badges never show a
  percentage - GPU / 部分 / CPU / Cloud only
- The Requests 模型 dropdown follows the chosen 上游: `/models/llm` rows carry their upstream, and an
  alias is offered under every upstream that serves its target (`ModelChoice.upstreams: string[]`)
- Models · LLM is two tabs (`tab = 'models' | 'api'`, no router change, no extra fetch): 模型清单 holds the
  three config tables, API 用法 renders two `ApiDoc` blocks - `POST /api/generate` and
  `POST /v1/chat/completions` - whose parameter tables mirror `app/main.py`'s `openapi_extra` schema and
  `docs/API.md`. That content is static in the view: the endpoints are gateway contracts, not config projections
- Models · Embed (`models/embed`, between LLM and Rerank in the sidebar) documents the two
  embedding pass-throughs - `POST /api/embed` (Ollama native) and `POST /v1/embeddings`
  (OpenAI-compatible) - with an endpoint/config card and two `ApiDoc` blocks. It fetches nothing:
  there is no `embed` config block, so rendering can never disagree with the gateway
- Tools · Web Search is two tabs (`tab = 'models' | 'api'`, same pattern as Rerank): 模型清单 holds
  the config card plus **可用模型** - the `/models/llm` rows whose `upstream` equals `websearch.upstream`,
  so aliases, expanded `opencode/*` wildcards and the `Cloud` device badge behave exactly as they do
  on Models · LLM - and API 用法 holds the single `ApiDoc`. Both endpoints are fetched together
  (`Promise.all`) by one `load()`. The 可用模型 note explains that the search capability comes from a
  backend `opencode-port` deployment (https://github.com/bruce1977/opencode-port): one
  `type: "opencode"` upstream pointed at it (default `127.0.0.1:10000`), `websearch.upstream` pointed
  at that upstream, and that `model` names an opencode-port model (not an llm-hub-configured one)
  and may be omitted, in which case opencode-port uses its own default
- Embed / Rerank / System One / Web Search each open their 模型清单 tab with a **功能介绍** card
  (~200 characters) as the first card, answering "what is this for" before any config or table: embedding for
  semantic search / clustering / dedupe / RAG, rerank for precision after recall, System One for
  low-latency decisions, web search for fresh external knowledge. All four share
  `.feature-intro` in `web/src/styles/theme.css` (12.5px, line-height 1.85), so the prose stays
  readable next to the denser `api-note`
- The sidebar order is Dashboard / Upstreams / Requests, then the Models group (LLM · Embed · Rerank ·
  System one), then Tools (Web Search); Requests sits directly under Upstreams. `Settings` has no
  sidebar row - its entry moved into the account menu. Rerank uses `⇅` and Web Search `◎`, in the
  same glyph family as the other items
- The account card is **fixed bottom-left** (`.user-dock`, `position: fixed`, aligned to the centred
  1180px shell via `left: max(38px, calc((100vw - 1180px)/2 + 18px))`, `bottom: 34px`), so a long page
  never scrolls it out of view; `.app` keeps `min-height: max(640px, calc(100vh - 48px))` so on a short
  page the card still sits inside the panel instead of floating below it. The avatar is a gradient
  circle with a double ring for contrast
- Clicking the account card opens a popup menu: 语言 / Language (a **right-side flyout submenu** that
  does not expand the current menu - only 中文 is selectable, English is a `soon` placeholder),
  深色模式 switch, 设置 (`/admin/settings`), 关于 (`/admin/about`), 退出登录 (`signOut()` then the
  login gate). Outside `pointerdown` or `Esc` closes it; the flyout closes 150ms after the pointer
  leaves so it can be crossed. There is no sidebar or floating theme toggle any more - the theme
  switch lives in this menu
- About (`/admin/about`, no sidebar row) answers "what is this project, what can it do, which build
  is running": 项目简介 + a two-column 主要功能 grid + **动态** 版本信息 (`gateway_version` from
  `/overview`, alongside online upstreams, running models, today's requests/tokens, the request-log
  state and `generated_at`) + GitHub / Docker / docs links + the WeChat QR. The version row carries a
  hollow ring that breathes while the probe is in flight and fills once the value lands. The QR is
  `web/public/wechat-qr.jpg`, copied verbatim into `app/web` and served at `/admin/wechat-qr.jpg`
  (`img-src 'self' data:` already allows it); the src comes from `import.meta.env.BASE_URL`, never a
  hardcoded `/admin/...`, and a missing file shows a "where to put it" note instead of a broken image
- Charts read their colors from the same CSS custom properties as the theme store, so switching
  between dark and light repaints them; the theme is persisted in `localStorage` and also toggles
  `html.dark` for Element Plus' own dark variables
- Only **401** sends the SPA back to the login gate. A 503 is a domain answer (`upstreams/{name}`
  for an unreachable instance, `ADMIN_PASSWORD` unset) and must not expire the session
- `request_log.enabled === false` is shown as a "logging disabled" strip on Dashboard/Requests
  rather than an empty list; `overview.gateway_version` feeds the sidebar version

## Common Tasks

### Adding New Endpoint
1. Add route handler in `main.py`
2. Register it **before** `gateway()`'s catch-all (`@app.api_route("/{full_path:path}", ...)`) or the
   path is silently proxied to an upstream instead of served locally
3. If it is a documented pass-through route, add the path to `POST_ONLY_PATHS` so other methods get 405
4. Add tests in `smoke_test.py`
5. Update documentation

### Adding New Upstream Type
1. Extend `UpstreamConfig` with new type
2. Implement `tags_path()`, `health_path()`, `parse_model_names()`
3. Add tests for new backend
4. Update README with configuration example

### Modifying Config Schema
1. Update Pydantic models in `config.py`
2. Update `build_default_config()` for defaults
3. Update config validation
4. Add migration logic if needed
5. Update documentation

## Debugging

### Log Levels
- `debug`: Detailed routing, alias resolution, request forwarding
- `info`: Startup, config reload, request routing
- `warning`: Failed upstream connections, weak keys
- `error`: Upstream failures, config parse errors

### Common Issues
- **Config not reloading**: Check file mtime, restart container
- **Auth failures**: Verify `GATEWAY_API_KEYS` environment variable
- **Upstream unreachable**: Check `host.docker.internal` resolution
- **Rerank scores all 0**: Switch to `embedding` mode or check logprobs
- **Local start refuses to boot**: `scripts\start-local.bat -CheckOnly` re-runs the app's own
  rules (Pydantic config + `auth.find_weak_keys`) and names the cause - missing dependency,
  invalid config, auth enabled without keys, weak keys, or a port already in use

## Dependencies

### Production
- `fastapi>=0.115.0`
- `uvicorn[standard]>=0.30.0`
- `httpx>=0.27.0`
- `pydantic>=2.7.0`
- `PyYAML>=6.0` (reads `config.yaml`; the Docker image installs it from `requirements.txt`)

### Development
- No additional dev dependencies currently
- Consider adding: `pytest`, `black`, `ruff`, `mypy`

## Performance Considerations

- Connection pooling via `httpx.AsyncClient`
- Model list caching (60s TTL)
- Concurrency limits for rerank operations
- Config hot reload without restart
- Streaming pass-through without buffering