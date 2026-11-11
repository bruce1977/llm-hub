# LLM Hub - Agent Guidelines

## Project Overview

LLM Hub is a FastAPI-based unified gateway that aggregates multiple Ollama instances, LM Studio, and OpenAI-compatible backends into a single API endpoint. It provides:
- Multi-upstream routing by model name
- API key authentication (keys live in the `api_keys` table of `llmhub.db` as SHA-256 hashes,
  minted and revoked from the console; there is no environment or file seed — an empty table is
  a normal first-run state until the first key is created at `/admin`)
- Rerank API implementation
- Web search pass-through (OpenCode API Gateway upstream)
- Model aliasing and parameter injection (an entry under root `aliases:` is a model of its own with
  its own pinned parameters; `upstreams[].model_aliases` only renames an existing model)
- Config hot reload
- Read-only management API (`/v1/management`) with a SQLite request log; the only actions that
  actually reach out are the per-model speed test and the per-endpoint API testers, and the only
  rows it writes are `bench_results`, request-log rows and `api_keys` (create / revoke)
- Vue 3 web console (`web/`, built into `app/web`, served at `/admin`) over that API

## Architecture

```
llm-hub/
├── app/
│   ├── main.py       # FastAPI application, lifespan, special endpoints
│   ├── config.py     # Config models, loading, hot reload, alias resolution
│   ├── auth.py       # API key auth, zone() by kernel listening socket, peer_ip() (XFF ignored)
│   ├── bridge.py     # Public-zone policy, two steps: bridge_denial() = path allowlist + red-line 404s, public_auth_denial() = key / console session
│   ├── serve.py      # Hardened uvicorn entrypoint (proxy_headers=False, multi-listener)
│   ├── logging_config.py # Loads logging.json and pins every level to server.log_level
│   ├── logging.json  # uvicorn log format: timestamped access/error lines (serve.py + --log-config)
│   ├── http_clients.py # Two outbound httpx pools: queued inference gate + ungated control plane
│   ├── key_store.py  # api_keys table in llmhub.db: SHA-256 hashes, sk-hub- keys, masks only
│   ├── admin_auth.py # ADMIN_PASSWORD login + require_admin / require_admin_session (cookie only)
│   ├── proxy.py      # Reverse proxy, streaming pass-through, request-log capture
│   ├── messages.py   # Anthropic Messages compatibility (/v1/messages): request mapping, envelope & SSE rebuild
│   ├── request_log.py # SQLite request log (WAL, async batched writes, stats queries)
│   ├── management.py # Read-only management API (/v1/management) for the web console
│   ├── bench.py      # Model speed test engine (Python port of bench_llm.mjs, no Node needed)
│   ├── bench_store.py # bench_results table in llmhub.db (latest 3 rows per upstream+model)
│   ├── api_test.py   # Server-side execution for the console's API testers (closed KINDS allowlist)
│   ├── rerank.py     # Rerank API implementation (generative logprobs scoring)
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
│   ├── smoke_test.py        # Comprehensive mock tests (233)
│   ├── test_gateway_new.py  # Aggregation / systemone / web search / /docs schema tests (100)
│   ├── test_management.py   # Management API + request log + API keys + console serving tests (419)
│   ├── verify_security.py   # Security control tests (200)
│   ├── test_api.py          # E2E tests against a live server (28)
│   └── demo.py              # Demo script (requires real Ollama)
├── docs/
│   ├── API.md / API_CN.md       # API reference: every endpoint, streaming, errors, aliases (EN / ZH)
│   ├── CONFIG.md / CONFIG_CN.md # Node-by-node config.yaml reference: every key, default, trap (EN / ZH)
│   ├── TESTCASES.md         # Test cases documentation with examples
│   ├── DEPLOY.md / DEPLOY_CN.md # Deployment: Docker, local run, build/publish scripts (EN / ZH)
│   ├── Ranker.md            # Reranker configuration guide
│   ├── USAGE.md / USAGE_CN.md  # Full manual: config reference, console, endpoints, security (EN / ZH)
│   ├── web-management/      # Console spec, mockups, capture-live.js (live screenshot capture)
│   ├── images/              # Everything the READMEs embed (architecture, screenshots, WeChat QR)
│   ├── screenshots/         # Full per-page capture sets: en/ and zh/, produced by capture-live.js
│   └── workflow.md          # Development workflow with diagrams
├── scripts/
│   ├── start-local.ps1      # Local launcher: preflight (deps/config/keys/ports/zones), then
│   │                        # uvicorn --no-proxy-headers (internal-only) or serve.py (public/NoReload)
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
├── README.md / README_CN.md   # Landing page: intro, architecture diagram, console, deployment, docs map, license
├── CHANGELOG.md
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
- Only three environment variables: `CONFIG_PATH`, `ADMIN_PASSWORD`, optional
  `SYSTEMONE_SERVER_API_KEY`; everything else lives in `config.yaml`
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
PYTHONPATH=app CONFIG_PATH=./data/config.yaml ADMIN_PASSWORD=... \
  python -m uvicorn main:app --host 127.0.0.1 --port 8000 --no-proxy-headers

# 2. Mint a caller key at http://127.0.0.1:8000/admin (API keys -> Create), then run the
#    E2E tests with that plaintext:
python tests/test_api.py --base-url http://127.0.0.1:8000 --api-key sk-hub-...

# Demo script (requires real Ollama)
python tests/demo.py
```

**Important:** E2E tests always use `data/config.yaml` from the project root (set via `CONFIG_PATH=./data/config.yaml`). This config points to the local Ollama instance at `127.0.0.1:11434`. Never hardcode paths in test commands.

### Test Structure
- `smoke_test.py` — 233 mock tests (auth, routing, aliases, streaming, `/api/generate` forwarding, rerank, security, `/` system info, upstream types, the internal/public two-zone split by listening socket, public-zone hidden paths + key enforcement, public-hidden `/health`/`/healthz`, console-session cookies on API routes, session expiry and sliding renewal, the closed forwardable set — undeclared path **404** in both zones, a listed-but-undeclared path still 404 on the public socket, `api/blobs/*` blocked under `deny`/`readonly` and forwarded under `allow`, `routing.strict: true` still forwarding a model-less `api/pull` while refusing an unknown named model — and **405 + `Allow: POST`** on every documented POST-only route), no external deps
- `test_gateway_new.py` — 100 tests for aggregation, systemone + web search forwarding (including the empty-`api_key_env` unsigned mode vs a named-but-unset variable: no blank-name warning, and the real name in the warning when one is configured) and the `/docs` schema (auth schemes + inline `X-API-Key` input + the documented `/api/generate` request/response schema + the five newly documented forwards `/api/chat`, `/v1/chat/completions`, `/v1/completions`, `/api/embed`, `/v1/embeddings`: `model` in the request schema, the right response media types, 404/405 entries)
- `test_management.py` — 419 tests for admin auth (session cookie → 200; a caller API key, the admin password as `Bearer` / `X-API-Key` and a key in the query string → 401, plus the 5-failure sign-in lockout; wrong username → 401; `ADMIN_PASSWORD` unset → 503), the `/admin` static console (`/admin` → `/admin/` redirect, SPA fallback, immutable caching for `assets/`, `../` cannot escape the build, the tab icon served as `image/svg+xml` rather than falling back to the shell, 503 with a build hint when `app/web` is missing), upstream probes + device classification, the `upstreams/{name}` detail (declared `models` with renames applied plus the derived alias names, `/api/ps` `running_models`), the config-driven `/models/llm` (only declared names, `display` / `alias` / `derived` per row, a `model_aliases` rename replacing `display` without touching the target, a root `aliases:` entry becoming its own row right after its target — device inherited, memory `null`, `aliases[].kind` separating `rename` from `derived`, a `qwen3:*` or `opencode/*` prefix expanded through discovery and flagged `wildcard`, an unresolvable pattern kept as its own row with `running: false`, runtime device/memory, dead upstream contributes only its declared names), hosted `opencode` rows (probe online ⇒ every declared model `running` with `device: "cloud"` and memory `null`, rows added to `/overview` `running_models` and the KPI, nothing added when offline), `/overview` appending only **running** derived rows to `running_models` while `kpi.running_models` counts just the physical + hosted rows, masked config projections (LLM / rerank / systemone / tools-websearch), the `api_models` derivation and narrowing (names explicitly claimed by embed/rerank/systemone removed from generate/chat while a `websearch` claim does not hide a chat model or its alias, a tagless `tev1` claim matching a declared `tev1:latest`, alias and derived names accepted, `free_model` on derived websearch/systemone families, an unserved name kept visible, the per-kind default and mask, a non-Ollama (`lmstudio`) model absent from the native `generate` / `embed_ollama` kinds while `chat` / `embed_openai` keep it, an explicit list served as written as a closed set that beats the web-search provider filter and turns `free_model` off, and blank placeholder entries ignored rather than becoming empty options), the `/api-test` envelope (closed `kind` allowlist → 400, a missing `model` where the family needs one → 400, alias + `think` injection, `num_ctx` clamping, the envelope's requested/resolved model + upstream + injected params (flat keys, not nested), NDJSON and SSE assembly with `stream`/`content_type`/`ttft_ms`, an unreachable backend's **502** and a disabled capability's **404** arriving as data rather than as an error, the 128 KB `truncated` cap and the request-log row a tester call leaves), request-log capture (stream / non-stream / 502, timestamps, requested vs resolved model, query string, an allowed `HEAD` forwarded but never written to the log, the `tokens_in` / `tokens_out` split from both usage formats and the `NULL` when a response reports no usage), the `kpi.tokens_today` total, filters, pagination, timeseries, distribution pies and the `group_by` guard, the `request_log.enabled` switch and the legacy-database `ALTER TABLE` upgrade (including the declared type of the added columns), the `/v1` + `/api` **stats scope** (a non-API path is still written to the raw table yet counted by neither the list, the timeseries, the pies, the KPI or `failures`, and `?path=` cannot bring it back, while its `/requests/{id}` deep link still opens) and the **caller API key routes** (session cookie only — a gateway key alone is **401** on all three; the fixture's hash-only row is masked with no plaintext anywhere in the payload; **201** create returns a `sk-hub-` key of exactly 47 base62 characters whose mask equals `mask_key(secret)`, and that plaintext never reappears in a later listing; blank label **400** / over-long **422**; the fresh key authenticates `/api/tags` and `/v1/management/auth/ping` at once, a tampered last character does not; revoke answers **200** then **404**, the row disappears and the key stops authenticating — checked after `logout`, because a live session cookie would otherwise satisfy `require_admin` on its own), plus a direct `ApiKeyStore` unit section (50 generated keys are unique, prefixed and fixed-length; `hash_key` is the lowercase sha256 hex; the three `mask_key` shapes; `create` row fields and label truncation; `list_keys` ordering and its exact column set; `add_plaintext` de-duplicates operator-supplied keys and marks them `source: "console"`; a deleted key fails `verify()`), and the **local-day dashboard window** (`today_start` / `today_end` replacing the UTC fallback, malformed or reversed windows → 400) with `tz_offset` day bucketing (UTC by default, shifted to the caller's calendar, ±840 clamp)
- `verify_security.py` — 200 security tests (2026-11-11 retired knobs: no `allow_query_api_key` / `trusted_proxies` fields or defaults, old values ignored with a warning, `?api_key=` → 401 while headers still work, `peer_ip()` unaffected by a forged XFF chain; 2026-11-11 inference concurrency gate: separate inference/control pools, a burst of 8 queued without loss and never exceeding `max_concurrency` at the backend, `PoolTimeout` → 503 + `Retry-After` on buffered and streaming calls; public-zone method+path allowlist, rate limit, headers, log timestamps (both formatters in `logging.json` plus a real re-format of a handler uvicorn installed, so a CLI start cannot silently lose the time field), request-log coverage (`test_request_log_coverage`: standalone handlers and middleware refusals write metadata-only rows; proxied calls write exactly one rich row; `HEAD`/`OPTIONS`/management paths stay out; the switch suppresses both writers), plus the 2026-11-09 audit regressions: forged XFF, full-match allowlist, body cap, `Retry-After`, NUL path, hidden version, cookie/`set-cookie` hygiene, log redaction, config URL/env guards, dot segments, redirect policy, config masking + restore, cookie-only config writes; and the 2026-11-10 two-zone/IP-chain regressions: config-driven public port parsing, `local_port`, port-0 XFF-rewrite fingerprint, real TCP peer, forwarded headers ignored, listener zones at HTTP level, public-hidden management/docs/introspection paths, public-hidden `/health`/`/healthz` even with a key, `/admin` and `/v1/management/*` still 404 on the public zone under `deny`/`readonly`, no-key + no anonymous internal zone failing closed, legacy `auth`/flat-`security` migration, and the path-surface regressions: `admin_endpoints: allow` cannot open an unlisted Ollama path (but opens the management prefix as a unit — shell and login handshake anonymous, every other call still 401 without the cookie), `reachable_admin_paths()` reports the mode ∩ allowlist intersection (empty under `allow` with no rule, `pull`-only under `readonly`, `[]` under `deny` even when listed), `allow` + a listed `api/pull` / `api/blobs/*` passes the admin filter while `readonly` still blocks blobs, the internal zone refuses an undeclared path yet still forwards model management, and the credential split: a route registered with **no** auth dependency is still 401 on the public socket (`public_auth_denial` runs in the middleware, after the rate limiter and the CORS pre-flight), invalid key 403, valid key 200, while the internal listener stays unchecked by the zone policy; and the CORS pre-flight: a listed origin gets a **204 with zero body bytes** — a `JSONResponse({}, 204)` still writes its two bytes and uvicorn drops the connection with "Response content longer than Content-Length" — the public socket answers it *before* the credential check yet only when `OPTIONS` is listed for that path, and the public **401 carries `Access-Control-Allow-Origin`** so a browser can read the refusal instead of seeing an opaque CORS error)
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
# Easiest - the launcher preflights dependencies/config/keys/ports/zones, then starts the
# right entrypoint. Default internal-only listener uses uvicorn --no-proxy-headers with auto-reload:
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1   # or: scripts\start-local.bat
#    flags: -Config <path>  -EnvFile <path>  -Python <exe>  -Install  -CheckOnly  -NoReload
#    -NoReload -> hardened serve.py, no auto-reload (also required to run serve.py on a single port)
# Ports/zones are NOT CLI flags: set server.port / security.public.port in data/config.yaml.
# A distinct public port (or -NoReload) selects serve.py; there are no port/bind/zone CLI flags.

# Or manually - the modules are flat inside app/, so run from app/ (same as WORKDIR /app)
# and load .env there. Add --no-proxy-headers so XFF cannot rewrite the TCP peer, and
# --log-config logging.json so access lines carry a timestamp (uvicorn's stock format has none):
pip install -r requirements.txt
cd app && python -m uvicorn main:app --reload --port 8000 --env-file ../.env --no-proxy-headers --log-config logging.json

# Two-zone / production / Docker entrypoint (also what Dockerfile CMD uses): disables uvicorn's
# proxy-header rewrite and binds every listener declared in config.yaml (internal + optional public).
# Set security.public.port in data/config.yaml first, then:
cd app && python serve.py
```
`scripts/start-local.ps1` switches to `serve.py` automatically when `security.public.port` in
`data/config.yaml` differs from `server.port` (or when `-NoReload` is given); `serve.py` has no
auto-reload because it binds all listeners in one process. Its preflight reads the same config the
app uses, checks every declared listener port, and fails closed when no usable key exists and there
is no anonymous internal zone (`security.internal.allow_anonymous: false`). Stale `LLMHUB_*` port
variables in `.env` are honoured only as one-time migration hints (explicit config wins) and warned
about — remove them from `.env` / compose.
Never `python -m uvicorn app.main:app` - the flat modules import each other by bare name
(`import admin_auth`, `from config import ...`), so that form dies with
`ModuleNotFoundError: No module named 'admin_auth'`. From the repository root instead use
`--app-dir app` (or `PYTHONPATH=app`), which is what `scripts/start-local.ps1` does.
**Never omit `--no-proxy-headers` on a uvicorn start**: its `ProxyHeadersMiddleware` rewrites
`scope["client"]` from a caller-supplied `X-Forwarded-For` when the peer is `127.0.0.1`.
`auth.peer_ip()` rejects the port-0 fingerprint of that rewrite (so such requests fail closed),
but `python serve.py` is the supported path for anything reachable from outside the host.

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

## Documentation

Docs live in `docs/`. Only the two landing pages, `CHANGELOG.md` and this file stay at the repository root.

| File | Language | Role |
|---|---|---|
| `README.md` / `README_CN.md` | EN / ZH | Landing page: intro, architecture diagram, admin console, deployment, doc map, license |
| `docs/USAGE.md` / `docs/USAGE_CN.md` | EN / ZH | Full manual: every config field, LM Studio, console, calling examples, FAQ, security, local dev |
| `docs/API.md` / `docs/API_CN.md` | EN / ZH | Every endpoint with request/response examples, streaming, error codes, alias injection, tool calling |
| `docs/CONFIG.md` / `docs/CONFIG_CN.md` | EN / ZH | Node-by-node `config.yaml` reference: every key, its default, who reads it, and the traps (unknown keys ignored, dangling `default_upstream`, the public path surface) |
| `docs/DEPLOY.md` / `docs/DEPLOY_CN.md` | EN / ZH | Deployment: Docker, local run, build/publish scripts, environment variables, troubleshooting, operations |
| `CHANGELOG.md` | EN | Release notes |
| `docs/web-management/`, `docs/TESTCASES.md`, `docs/Ranker.md`, `docs/workflow.md` | ZH | Internal specs, test notes and workflow diagrams — deliberately absent from the README doc map |

### Rules

- **One language pair per doc, English unsuffixed.** `X.md` is English and `X_CN.md` is its Chinese
  counterpart; both change in the same commit. There is no `_EN` suffix and the Chinese file is never the
  only version. `USAGE`, `API`, `DEPLOY` and `CONFIG` all follow this pattern.
- **The changelog is English only.** Do not add `CHANGELOG_CN.md` back — one file, one language.
- **The README doc map has exactly four rows**: USAGE, API, DEPLOY, CONFIG. Internal specs, test cases
  and workflow notes stay out of the landing page.
- **Doc-map cells show the file name alone**, never a path: `[API.md](docs/API.md)`, not link text
  `docs/API.md`.
- **A one-line description has to match that document's real headings.** Reread the target's table of
  contents before editing the blurb. Do not describe chapters that never existed — `API.md` has no
  "forwarding rules" section, `DEPLOY.md` has no standalone reverse-proxy section, and `USAGE.md`
  documents how to *use* the console rather than how it is implemented.
- **Images the READMEs embed belong in `docs/images/`** — architecture diagram, the two screenshots, the
  WeChat QR (`wechat-qrcode.png`). `docs/screenshots/{en,zh}/` keeps the full per-page capture sets for
  the console spec.
- **A language pair keeps identical heading structure and anchors**, so a link can be localised by
  swapping the file name (`API.md#rerank` ↔ `API_CN.md#rerank`). Anchors are GitHub slugs of the heading
  text, so the Chinese file's anchors stay Chinese (`API_CN.md#消息补全anthropic-兼容`).
- **Copy code, translate prose.** Commands, JSON, YAML, field names and status codes are identical in both
  languages; only the explanatory text is translated.
- **Cross-link in the same pass.** Adding an endpoint or config block means updating USAGE (both
  languages), API (both), the README calling example when it is user-visible, and the matching subsection
  here.
- **Relative links inside `docs/` are file-relative** (`API_CN.md#web-search…` from USAGE_CN, `../README.md`
  from USAGE), because the files sit side by side.

## Key Implementation Details

### Routing Logic
- Exact model match first
- Prefix wildcards (e.g., `qwen3:*`) for pattern matching
- Priority-based routing when same model exists on multiple upstreams
- Alias resolution before routing
- Fallback to default upstream when `routing.strict=false`
- `routing.strict` refuses an unknown **named** model only. An empty model name is not a guess:
  `Config.resolve("")` always falls back to `default_upstream`, strict or not, because that is the
  "this body has no `model`" case — a GET, or an Ollama admin call (`api/pull`, `api/create`,
  `api/delete`, `api/copy`, `api/blobs/*`) whose body carries `name`. Before 2026-11-10 those calls
  dead-ended in `404 No upstream configured for model ''` under `strict: true`, which made
  `admin_endpoints: allow` look broken. `default_upstream` must still name a real `upstreams[].name`

### Streaming
- NDJSON for Ollama native API (`/api/chat`, `/api/generate`)
- SSE for OpenAI-compatible API (`/v1/chat/completions`)
- Pass-through without modification
- `proxy.STREAM_BY_DEFAULT` (`api/chat`, `api/generate`, `v1/chat/completions`, `v1/completions`)
  decides the transport when the caller omits `stream`, matching Ollama's own defaults

### Schema-visible proxy routes
- `main.py` registers a documented route for **every** inference pass-through **before** the
  catch-all: `POST /api/generate`, `/api/chat`, `/v1/chat/completions`, `/v1/completions`,
  `/api/embed`, `/v1/embeddings`. Each handler is a one-liner into
  `ProxyService.forward(request, "<path>")`, so routing, alias + parameter injection,
  `num_ctx_overrides` clamping, streaming and request-log capture are unchanged; the routes exist
  only to (a) put the endpoint in `/openapi.json` and `/docs` with a real request schema, examples,
  both response media types (NDJSON+JSON for `api/chat`, JSON+SSE for the two `v1/*` completion
  routes, JSON for the embeds) and 404/405 entries, and (b) take part in `POST_ONLY_PATHS`
- `POST_ONLY_PATHS` = `/v1/rerank`, `/rerank`, `/api/rerank`, `/v1/systemone`,
  `/v1/tools/web-search`, `/api/generate`, `/api/chat`, `/v1/chat/completions`, `/v1/completions`,
  `/api/embed`, `/v1/embeddings`, `/v1/messages`, `/v1/message`. Any other method is a gateway
  **405** with `Allow: POST` instead of being proxied upstream. The guard is needed because
  Starlette prefers the catch-all's FULL match over a documented route's PARTIAL (wrong-method) match
- Nothing is catch-all-only any more: a path that is not a registered route and not in
  `proxy.FORWARDABLE_PATHS` is a **404**, not a forward

### Security
- **Closed forwardable set** (`proxy.FORWARDABLE_PATHS` + `FORWARDABLE_PREFIXES = ("api/blobs/",)`):
  the catch-all used to forward whatever it could not match locally, which made the internal
  listener an open proxy for the whole upstream API. Both zones now answer **404**
  (`Endpoint '/<path>' is not part of this gateway's API…`) for anything outside the set:
  `api/generate`, `api/chat`, `api/embed`, `api/embeddings`, `api/show`, `api/version`,
  `v1/chat/completions`, `v1/completions`, `v1/embeddings`, `api/pull`, `api/push`, `api/create`,
  `api/delete`, `api/copy`, `api/blobs/*`. The management entries stay in the set on purpose so a
  **403** from `admin_endpoints` is a clearer answer than a 404. Paths the gateway answers locally
  (`/`, `/health`, `/probe`, `/v1/models`, `/models`, `/api/tags`, `/api/ps`, rerank, messages,
  systemone, web-search, `/v1/management/*`, `/admin/*`, `/docs`) are never in it. Check order in
  `forward()`: dot-segment/NUL → forwardable **404** → admin **403** (public) → body cap **413** →
  unrouted model **404**. `security.blocked_paths` therefore only yields 403 for a real API path
- Ollama admin endpoints (`api/delete`, `api/pull`, `api/blobs/*`, …) blocked by default **on the
  public listener only** (`security.public.admin_endpoints`: `deny` / `readonly` / `allow`); the
  internal LAN listener never applies the mode. `allow` forwards everything **including
  `api/blobs/*`** (fixed 2026-11-10: the prefix test used to sit outside the mode list, so blobs
  stayed blocked under `allow`). `api/show` / `api/version` are never admin-blocked
- **`admin_endpoints` never opens an Ollama path.** `bridge_denial()` 404s anything missing from
  `security.public.path_allowlist` in the middleware, *before* the admin filter inside
  `proxy.forward()`. Exposing `pull` publicly needs BOTH `admin_endpoints: readonly|allow` **and**
  a `POST: [^/api/pull$]` entry; without it the 404 comes from the allowlist and the mode change
  looks like a no-op. The management plane is the one exception because it is a *prefix* rule, not
  an entry: `allow` routes `/admin*` and `/v1/management/*` to their real handlers without
  consulting the allowlist (and it is still never anonymous). On the internal listener the mode is
  a no-op by design
- **No client-IP allowlist, no trusted proxies**: the `security.allowed_client_ips` fence and
  `security.trusted_proxies` were both removed on 2026-11-11. Access control is entirely the
  two-zone listener split (internal `allow_anonymous` vs. mandatory public key) plus
  `admin_endpoints`. `auth.peer_ip()` is the only client address: the real TCP peer, used for
  rate-limit buckets and audit rows. `X-Forwarded-For` / `X-Real-IP` are never honoured.
  `allow_query_api_key` was removed with them — caller keys are header-only
- CORS configuration (no `*` by default). A pre-flight is answered with a **bodyless** `204`
  (`Response`, not `JSONResponse({}, 204)`: Starlette omits `content-length` for 204 but still sends
  the body, and uvicorn then kills the connection with "Response content longer than Content-Length").
  On the public socket `OPTIONS` is a method+path like any other, so browser clients need an `OPTIONS:`
  entry in `security.public.path_allowlist` — the pre-flight is answered *before* the credential check,
  and a denial carries `Access-Control-Allow-Origin` so the browser can read the 401
- Request body size limits
- Public-zone method→path allowlist (`security.public.path_allowlist`, `re.fullmatch`): the COMPLETE
  list of what exists on the public socket; unlisted method+path → **404** with or without a key.
  Listing a path does not invent one — it must also be forwardable, or the proxy answers its own
  404. There is no global HTTP-method gate; the public allowlist plus the forwardable set are the
  only route lists in the app, and an allowlist entry never waives the key requirement
- In-memory token-bucket rate limiter (`security.rate_limit`, 429 when over budget)
- Queued inference concurrency gate (`app/http_clients.py`, `server.max_concurrency`,
  default 4): at most N in-flight requests against model backends; excess is **queued,
  never 429-rejected**. `server.upstream_queue_timeout` (default 120s) caps the wait
  and maps `httpx.PoolTimeout` to 503 + `Retry-After`. Control-plane traffic
  (System One, web search, probes, management) uses a separate ungated pool.
  Startup-only settings, orthogonal to `security.rate_limit`
- Defensive response headers on every response (nosniff, X-Frame-Options, CSP, etc.)
- Admin/management console (`/admin` + `/v1/management`) authenticates with the HMAC session cookie
  minted by `POST /v1/management/auth/login` **only**. A caller API key is deliberately not an admin
  credential (it is handed to every client); `ADMIN_PASSWORD` is a login credential only (sending it
  as `Bearer` / `X-API-Key` is **401**). Five failed logins per TCP peer lock sign-in for 15 min.
  Since 2026-11-11 the token is `<sid>.<expiry>.<sig>` with a per-session key derived from
  `ADMIN_PASSWORD`, the random `sid` and a process-scoped random seed; the cookie carries no secret,
  so it cannot be used to brute-force the password offline, and a restart rotates the seed
- Caller API keys are **rows of `api_keys` in `llmhub.db`** (`app/key_store.py`): `id`, `hash`,
  `masked`, `label`, `source`, `created_at`. Only `sha256(<plaintext>)` lowercase hex and a display
  mask are stored, never the secret, so a stolen database cannot be replayed. SHA-256 rather than a
  slow KDF on purpose: a generated key is 238 bits of randomness (nothing to brute-force) and the
  gateway verifies a key on **every** client request, where bcrypt/argon2 would add tens of
  milliseconds and turn the endpoint into a DoS amplifier
- **No environment seed, no key file.** Caller keys exist only as rows of the `api_keys` table in
  `llmhub.db`, created from the console (⚿ API Key 管理): `ADMIN_PASSWORD` buys a 12-hour session
  cookie and the plaintext is shown exactly once. An empty table is a normal first-run state -
  every request that needs a key answers 401 - not an error. `GATEWAY_API_KEYS` and
  `GATEWAY_API_KEYS_FILE` were removed: `scripts/start-local.ps1` strips a stale value from the
  environment with a warning, and its preflight reports the table's row count instead of a seed.
  An `api_keys:` block left in `config.yaml` (or the removed top-level `auth` node's `api_keys`)
  logs a warning and is ignored - keys live only in the SQLite table
- **The environment surface is three variables**: `CONFIG_PATH` (default `/data/config.yaml`),
  `ADMIN_PASSWORD` (console login; one input to the derived per-session signing key) and the optional
  `SYSTEMONE_SERVER_API_KEY` (the **outbound** backend key named by `systemone.api_key_env`,
  formerly `LAYA_SERVER_API_KEY`; startup logs the resolved URL and whether it is set). A few
  legacy/test knobs (`TZ`, `ADMIN_SESSION_TTL`, `REQUEST_LOG_DB`, `REQUEST_LOG_RETENTION_DAYS`,
  `LLMHUB_DB`, `OPENCODE_GATEWAY_API_KEY`) remain honoured but are documented in `docs/DEPLOY.md`
  only. The app does **not** read `.env` itself - Docker's `--env-file`, `start-local.ps1` or a
  compose `environment:` block has to export the values
- Verification is `key_store.ApiKeyStore.verify()`: a `frozenset` of hashes held **in memory**
  (`app/auth.py` reaches it through the process-wide `get_store()`), so no client call waits on
  sqlite. Every write (`create` / `delete` / `add_plaintext`) reloads that cache, which is why a
  new key works immediately and a revoked one stops working immediately
- Generated key format: `sk-hub-` + 40 base62 characters from `secrets` = **47 characters, always**
  (~238 bits). The prefix is not a secret; it is what makes a leaked-looking string recognisable in
  a log or a client config. `mask_key()` keeps the prefix plus 4+4 body characters
  (`sk-hub-FNdc…SVvw`); an imported key of any other shape shows head 8 + tail 4, and one of 12
  characters or fewer is fully dotted
- **Startup is fail-closed only at a dead end**: an empty `api_keys` table is a normal first-run
  state (keyed calls answer 401), and it refuses to boot only when `ADMIN_PASSWORD` is also unset
  **and** there is no **anonymous internal zone** — i.e. `security.internal.allow_anonymous: true`
  on a real `server.port` listener (that case only logs a warning). The remedy in the error is to
  set `ADMIN_PASSWORD`, start the gateway, then mint the first key at `/admin` -> API keys. The
  public zone can never be anonymous: even with `allow_anonymous: true` it rejects
  every unauthenticated request. Trust is the **listening socket**, never a client IP or header
- **2026-11-10: the client IP is a TCP peer, never a header.** `app/serve.py` is the container
  entrypoint (`Dockerfile CMD`), starting uvicorn with `proxy_headers=False`. Plain
  `uvicorn main:app` lets `ProxyHeadersMiddleware` rewrite `scope["client"]` from XFF when the
  peer is `127.0.0.1` (a same-host nginx) and set the client port to `0`; `peer_ip()` rejects
  that fingerprint, so a legacy start fails closed. No forwarded header is ever parsed:
  `peer_ip()` supplies the address for rate-limit buckets and audit rows, and it is never used
  to decide a zone. Behind a reverse proxy all requests share one IP — prefer `rate_limit.by: key`
- **2026-11-10: logs always carry a timestamp.** uvicorn's stock `LOGGING_CONFIG` replaces
  `logging.basicConfig()` and its formatters have no time field, so access lines printed as
  `INFO: 1.2.3.4:5678 - "GET /v1/models HTTP/1.1" 404 Not Found` — unusable for correlating a
  probe burst with a restart. `app/logging.json` owns the format
  (`2026-11-10 20:31:18.718 | INFO    | uvicorn.access | 1.2.3.4:5678 - "GET /v1/models HTTP/1.1" 404 Not Found`),
  `serve.py` hands the loaded dict to `uvicorn.Config(log_config=...)`, `start-local.ps1` passes
  `--log-config logging.json`, and `main.setup_logging()` re-formats handlers uvicorn installed
  before the lifespan ran (the bare `uvicorn main:app` path). `logging_config.uvicorn_log_config()`
  pins every logger and root to `server.log_level`, so that one key stays authoritative
- **Empty `systemone.api_key_env` means unsigned by design, not a missing variable named ''.**
  `systemone.backend_key()` returns `(Authorization, unsigned reason)`: no per-request warning for
  an empty name (one startup WARNING names the setting), a real warning with the variable name when
  a configured variable is unset. The old code logged `Environment variable  is not set` on every
  request. Startup logs `backend key from $NAME (set / NOT set - forwarding unsigned)` at WARNING
  when the named key is missing. `websearch.api_key_env: ''` already had the `if` guard; both now
  follow the same "name it or don't complain" rule
- **Two config-driven zones** (ports and policy come ONLY from `data/config.yaml`, no port env vars):
  `server.port` is the **internal** (trusted LAN) listener; `security.public.port` is the **public**
  (internet-facing) listener — `null` = no public listener, a value ≠ `server.port` = two listeners
  in ONE process (shared key cache, rate-limit buckets, login lockout), a value == `server.port` =
  public-only single socket with **no anonymous HTTP path at all** (use a TCP port probe for the
  container HEALTHCHECK). `local_port()` reads `scope["server"]`, which is kernel-assigned; `zone()`
  returns `internal` or `public`, and a request with no socket info fails closed as `public`. Two
  host ports mapped to one container port do NOT form two zones
- **Internal zone** (`server.port`, and NOT the public port): `/` and `/health` (`PROBE_PATHS`) are
  always anonymous so the container HEALTHCHECK works; other business APIs are key-free iff
  `security.internal.allow_anonymous: true`, otherwise they need a key or console session.
  `/docs`, `/openapi.json` and Swagger assets are gated by `security.internal.expose_docs`, the
  version string on `/` by `security.internal.expose_version`. `/admin` is reachable and management
  (`/v1/management/*`) stays cookie-only
- **Public zone** (`security.public.port`): the policy is **two separate decisions in `app/bridge.py`**,
  both evaluated by the request middleware before routing (the file name stays; `bridge` is no longer
  a zone name):
  * `bridge_denial(method, path, cfg)` — *does this path exist here*: only method+path pairs in
    `security.public.path_allowlist` exist (a dict of HTTP method → `re.fullmatch` regexes); anything
    unlisted returns **404** with or without a key. Paths with `..` segments or NUL are always
    **404** even if an operator lists them. `/v1/management/*` and `/admin*` are a **prefix** rule
    decided *before* the allowlist: hard **404** under `deny`/`readonly`, routed to their real
    handlers under `admin_endpoints: allow` without needing an allowlist entry.
  * `public_auth_denial(request, cfg)` — *may this caller use it*: **every** surviving request needs a
    valid key or console-session cookie — mandatory, no toggle, and it is a **zone** rule, not the
    route's `Depends(require_api_key)` (a handler that ever forgot that dependency would still be
    authenticated from the internet; `verify_security.py` proves it with a dependency-less route). It
    delegates to `auth.authorize()` so the rules live in one place, runs **after** the rate limiter (a
    throttled caller still gets 429) and **after** the CORS pre-flight answer, and returns the same
    401 + `WWW-Authenticate: Bearer` / 403 envelope the dependency produced. The internal listener is
    deliberately *not* credential-checked there: its anonymous paths (`/admin` static, `/`, `/health`,
    everything under `internal.allow_anonymous`) are not business endpoints. On the public socket the
    only exemption is `admin_endpoints: allow`: the static console shell (`/admin*`) and
    `POST /v1/management/auth/login` skip the credential check because a cookie cannot be presented
    before it is issued (`bridge.is_anonymous_admin_path`); every other management route still
    demands that cookie, and a gateway key is never an admin credential.
  There is no anonymous public business path — `/health` and `/` included (unless explicitly listed);
  point probes at the internal port, or use a TCP check for a public-only instance
- A valid console session cookie also authenticates business API routes, not just
  `/v1/management/*`. Its lifetime is `ADMIN_SESSION_TTL` seconds (default 12h, clamped
  5 min..7d) and is renewed past half the TTL; logout only clears the browser cookie
- `/docs`, `/openapi.json` and `/static/swagger/*` are served only on the internal zone, and only
  when `security.internal.expose_docs: true`; the public zone never serves them (**404**) regardless
  of that flag or any key. The old `security.allow_docs` field is gone
- **2026-11-09 audit hardening** (all covered by `verify_security.py` regressions):
  - `X-Forwarded-For` / `X-Real-IP` are ignored outright (2026-11-11: `trusted_proxies` removed);
    `peer_ip()` is the only recorded address, so access control, rate-limit
    buckets and audit rows cannot be steered by a forged header. The public zone's
    `security.public.path_allowlist` uses `re.fullmatch` (prefix rules must be written `^/v1/.*$`).
  - `auth.read_limited_body()` is the single body reader for proxy / messages / websearch /
    systemone: `Content-Length` is refused before reading and chunked bodies are counted while
    streaming. The middleware repeats the header pre-check; 429 carries `Retry-After`.
  - Forwarding strips `cookie` / `referer` / `x-forwarded-*` from every outbound request and
    drops upstream `set-cookie`; only `upstreams[].headers` are attached (messages.py now does
    this too). Paths are `posixpath.normpath`-ed and any `..` / NUL segment is rejected before
    the deny list and URL building; the shared httpx client has `follow_redirects=False`.
  - `request_log.sanitize_body()` masks the full buffer before truncating (a >8 KB body used to
    skip redaction) and `sanitize_query()` masks `api_key`/`token`/`password` values in the URL.
    `security.internal.expose_version` hides the version from `/`; `%00` in a console path is a clean 404,
    and Swagger assets use `Path.is_relative_to()`.
  - Weak/example `ADMIN_PASSWORD` values are rejected at startup (documented examples refuse to
    start) and the check logs no secret; `find_weak_keys()` survives only as a library helper with
    no startup caller.
  - `key_store` serialises write+cache-reload under a lock; `PUT /config` is serialised and uses a
    unique temp file; `/bench` clamps `rounds<=50` / `max_tokens<=8192` and caps concurrent jobs;
    analytics windows are capped at 90 days.
- Endpoints listed in `main.POST_ONLY_PATHS` answer **405** with `Allow: POST` on other methods

### Rerank Modes
- **logprobs**: the only supported mode. Generative yes/no scoring using token probabilities;
  `MODE_ALIASES` folds every other accepted spelling (`generation`, `cross-encoder`, `yes_no`, ...)
  onto it
- The former **embedding** (vector cosine) mode was removed: re-scoring with the model that produced
  the retrieval embeddings adds no signal. Selecting it returns **400** with that explanation, so an
  old configuration fails loudly instead of scoring silently
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
- Auth accepts **exactly one** credential: the signed session cookie (12h, HttpOnly, SameSite=Lax,
  Secure on HTTPS or when the proxy sends `X-Forwarded-Proto: https`). The token is
  `<sid>.<expiry>.<sig>`; its HMAC key is derived from `ADMIN_PASSWORD`, the random session id
  and a process-scoped random seed that never leaves the server, so a leaked cookie cannot be
  used to brute-force the password offline. Changing the password or restarting the process
  invalidates all sessions. Every route now uses `require_admin_session`; `require_admin` is an alias
  with the same cookie-only rule. A gateway API key used to be accepted here, which made every
  caller an administrator: it could read `GET /config` (including provider credentials), export
  all request bodies via `/requests/{id}` and rewrite upstreams via `PUT /config`. Scripts must
  log in once and keep a cookie jar.
  The admin password stays a **login credential only**: `POST /v1/management/auth/login`
  validates it (username `admin`, compared case- and whitespace-insensitively) and mints the
  cookie; sending the password itself as `Bearer` / `X-API-Key` → **401**. The query-string
  key transport is excluded on purpose. No `ADMIN_PASSWORD` → **503**; no valid cookie → **401**.
  The legacy `/admin/login|logout|ping` routes delegate to the same functions.
- `GET /config` masks every `upstreams[].headers` value; `PUT /config` restores masked values
  from disk, only overwrites segments actually present in the payload, and writes through a
  unique temp file under an `asyncio.Lock`. `api_key_env` may only name a provider key
  (`*_API_KEY` / `*_API_TOKEN`), and upstream URLs reject non-HTTP schemes and metadata targets —
  private LAN addresses remain allowed because that is the gateway's purpose.
- The three `/api-keys` routes take `require_admin_session`: **cookie only**. A gateway API key is
  what any client already holds, so accepting one there would let a caller mint itself more access;
  a request that arrives with a key but no console session is told to sign in (**401**)
- On the **public** zone `/v1/management/*` and `/admin*` are hard **404** under `deny` /
  `readonly`; `security.public.admin_endpoints: allow` opens that prefix on purpose (there is no
  allowlist entry to add), and the plane is still cookie-only there. On the internal zone they are
  cookie-only. There is no global method allowlist — only the public `security.public.path_allowlist`
  decides which method+path pairs exist — but the rate limit still applies
- All routes are read-only except four: the model speed test (it writes `bench_results` in
  `llmhub.db`), the API testers (they send one ordinary call, so they only add a `requests` row),
  the API key list (which creates and deletes `api_keys` rows) and the Settings page's
  `PUT /v1/management/config` (the one route that validates and overwrites `config.yaml`).
  The first three never touch `config.yaml`.
  Probes reuse `app.state.client` with a 5s timeout so one dead
  upstream cannot stall a view; `overview` / `upstreams` use `asyncio.gather` over upstreams
- Caller API keys (`GET` / `POST` / `DELETE /v1/management/api-keys`, store in `app/key_store.py`):
  the list returns `{items: [{id, masked, label, source, created_at}], total}` — never a hash and
  never a plaintext; `source` is `"console"` for a key minted there and `"file"` only on a legacy
  row carried over from a pre-console database (nothing imports keys from the environment any more). `POST` body is `{label}` with a **required** non-empty label
  (blank → **400**, over `LABEL_MAX_CHARS = 100` → **422**) and answers **201** with
  `{item, secret}` — that response is the only place the plaintext ever appears, so the console
  shows it once, offers a copy button, and reloads the masked list when the dialog closes.
  `DELETE /api-keys/{id}` returns `{deleted: id}` or **404** for an unknown id; revoking is the only
  removal the console offers, and it takes effect on the next request because the in-memory
  verification set is reloaded by the same call
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
- API testers (`GET /api-test/models`, `POST /api-test`, engine in `app/api_test.py`): the console
  executes **server-side** because the management session cookie satisfies `require_admin_session` but is not
  a caller API key — a browser-side fetch would be rejected by gateway auth. `KINDS` is a **closed
  allowlist** of 8 entries (`generate`, `chat`, `embed_ollama`, `embed_openai`, `rerank`, `systemone`,
  `websearch`, `messages` → executor `proxy` / `rerank` / `systemone` / `websearch` / `messages`,
  plus `family`, `model_required`, `free_model`); an unknown `kind` is **400**, never a pass-through to a URL the
  caller picked. `execute()` builds a minimal synthetic ASGI `Request` and calls the gateway's own
  services, so alias + parameter injection, `num_ctx_overrides` clamping, the blocked-admin paths and
  request-log capture behave exactly as for a real client. `_drain` consumes *and closes* the response
  iterator so the proxy's streaming generator runs its `finally` and writes the log row. Bodies are
  capped at `MAX_RESPONSE_BYTES = 128*1024` and the envelope reports `truncated`. A refusal the
  endpoint itself produced (400 bad body, 404 unrouted or feature disabled, 413 oversized, 502/504
  upstream, 503 missing provider) arrives as **envelope data** with its status, not as an error from
  `/api-test`, so the tester shows what a real client would have seen; only the route's own guard
  (unknown `kind`, a missing `model` where the family needs one) is a real HTTP error
- `stream` in that envelope is the **transport the gateway chose** (`proxy._should_stream`, i.e.
  `STREAM_BY_DEFAULT` when the caller omits the flag) — the same value the request-log `stream`
  column carries; the body's `stream` key is never injected. Whether the answer really came back
  chunked is what `content_type` says, so the console gates the streaming/TTFT chips on
  `/ndjson|event-stream/i` instead of trusting `stream`
- `api_models` (`ApiModelsConfig` in `config.py`: `generate` / `embed` / `rerank` / `systemone` /
  `websearch`) narrows each family's selector and is a **display/config-derivation concern only** —
  routing never consults it. Empty means derive (`rerank.models`, then `systemone.model`, else the
  callable pool from `upstreams[].models` + aliases); a listed item may be a real id, a
  `model_aliases` short name or a derived `aliases:` name and is matched through
  `_pool_row_for_name`, so a name nothing serves stays visible with `unknown: true`. When
  `api_models.generate` is left empty the derived pool drops every name **explicitly claimed** by
  another family — `api_models.embed`, `api_models.rerank`, `api_models.systemone` and
  `rerank.models`, plus each claim's resolved target — because that instance answers it as an
  embedding / scoring / decision model rather than a chat one. `api_models.websearch` is
  deliberately **not** a claim: that endpoint is a chat pass-through, so the model that answers
  the synthesis prompt is a chat model first (this is what keeps a hosted provider's routing
  model, e.g. `opencode/auto`, selectable for chat). `_claimed_names()` also ignores
  fallbacks such as `systemone.model`: a deployment may
  point System One at the model it chats with, and that model must stay selectable for chat. Claims
  are compared through `_bare()`, which drops a trailing `:latest`, because Ollama and `api_models`
  treat `tev1` and `tev1:latest` as one model. An explicit list is served as written and is a
  **closed set**: `source` becomes `api_models.<family>`, blank entries (`- ''`, whitespace only)
  are dropped instead of becoming empty `<option>`s, and no provider-side narrowing or
  `free_model` escape hatch is applied — every picker, "Available models" table and sample body
  reads that same list. A list of only placeholders counts as empty. `websearch` / `systemone`
  keep `free_model: true` **only when their family is derived** — `model` is optional there and the
  provider resolves it, so the selector preselects nothing and the box accepts any name.
  `websearch` derived from an empty list is additionally narrowed to `websearch_upstream_obj()`'s
  models (`source: "upstreams[].models (search provider)"`); an explicit list skips that filter
- Dialect: `/api/generate` and `/api/embed` are Ollama's **native** API (`Kind.native` in
  `api_test.py`), while an `lmstudio` or `opencode` upstream answers only the `/v1` side. The
  derived selector of a native kind therefore keeps `type: "ollama"` rows only (rows are dropped,
  not the upstream: `/v1` kinds still offer them), because listing a hosted model there would be a
  guaranteed upstream 404. An explicit `api_models.<family>` list is still served as written, so a
  misconfiguration stays visible. `/v1/chat/completions` and `/v1/embeddings` are unfiltered —
  Ollama serves those too
- `/api-test/models` never leaks a secret: when a family signs with an extra key it returns the fixed
  mask `••••••` next to the **name** of the environment variable (`api_key_env`), exactly like the
  rerank / systemone / websearch config projections
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
- **An entry under root `aliases:` is a model of its own; an entry under `upstreams[].model_aliases`
  is only a display rename.** `Config._expand_upstream_aliases` folds the renames into
  `Config.aliases` (so routing and `/v1/models` treat both kinds the same) and remembers which names
  it generated in the `PrivateAttr` `_renamed_alias_names`; `Config.is_renamed_model(name)` and
  `Config.derived_aliases()` (private attrs stay out of `model_dump()`) are what the management API
  uses to tell the two apart. The **owner** of a derived model resolves exactly like `Config.resolve()`
  — `get_upstream(alias.upstream)` → `find_upstream_for_model(alias.model)` →
  `default_upstream_obj()`, so the console can never hide a callable alias
- Every model row carries `model` (the name the caller sends), `display`, `alias` (`null` when
  unaliased) and `derived`. A **rename** replaces `display` with the short name and leaves `alias`
  holding it — `_alias_by_target()` only maps names `is_renamed_model()` accepts, so a root alias
  never renames its target. A **derived** row is its own row: `alias` is `null`, and it carries
  `target` / `params` / `description` plus `running` and `device` inherited from the target's
  `/api/ps` entry, with `size_gib` / `vram_gib` = `null` so the VRAM gauge never double-counts
  (`_derived_row()` / `_derived_rows()`; in `/models/llm` a derived row is inserted right after its
  target). `/overview` counts the derived rows in the `running_models` **list** but keeps
  `kpi.running_models` at the physical rows, so the KPI can't be inflated
- `aliases` in `/models/llm` reports `kind: "rename" | "derived"` per entry, so the console can label
  a display alias differently from a model that brings its own pinned `params`
- An `opencode` (hosted) upstream has no `/api/ps`: while its health probe answers, every model it
  declares counts as online with `device: "cloud"` (it runs on the provider's infrastructure) and
  `device_ratio` / `size_gib` / `vram_gib` = `null`, which the console renders as `Cloud` and `-`;
  `_hosted_models()` builds those rows for both `/overview` and `upstreams/{name}` so the two views
  cannot disagree, and an offline provider contributes none
- `upstreams/{name}` detail returns **503** with `"unreachable": true` when the health probe
  fails; unknown names are **404**. Its `models` are the renamed declared names followed by the
  derived aliases owned by that upstream (wildcards expanded), and `running_models` is the `/api/ps`
  runtime view (device, `size_gib`, `vram_gib`, `num_ctx`, `display`, `alias`, `derived`) plus hosted
  and derived rows
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
- `stats/distribution?group_by=upstream|model|status` feeds the Dashboard pies. `status` is a
  **three-way split** in `request_log._DISTRIBUTION_KEYS`: `ok` (2xx), `failed` (≥ 500) and
  `warning` (3xx / 4xx / NULL / anything else). `failed` keeps the ≥ 500 threshold shared with
  `error_rate`, the timeseries error bars and `failures`, so the red slice and the three "error"
  numbers still agree; `warning` makes caller-side 4xx rejections visible instead of hiding them
  in `ok`. Rows that never reached an upstream (blocked / oversized / unrouted) bucket as `-`,
  so the items always sum to the log total. Any other `group_by` → **400**; the column expression
  comes from `_DISTRIBUTION_KEYS`, never from the caller
- `management._display_by_model()` adds an alias-priority `display` to each by-model item:
  the `upstreams[].model_aliases` short name wins, else the real id; root `aliases:` entries are
  models of their own and must never relabel their target. `key` stays the resolved id so the
  Requests-page filter and the pie colours (which switch on `key`) keep working. The frontend
  `pieOption()` renders `display || key` but passes `key` to `colorFor`
- `UpstreamConfig.vram_total_gb` feeds the overview VRAM gauge; it is config-only because no
  upstream API exposes the host total
- `request_log.RequestLog` owns the `requests` table of `llmhub.db` (default next to
  `config.yaml`, override with
  `REQUEST_LOG_DB`), WAL + `synchronous=NORMAL`, 30-day retention (`REQUEST_LOG_RETENTION_DAYS`,
  0 = forever, pruned hourly). Rows are queued (bounded 10 000, drops + warns when full) and
  written by one background task in batches of 100 via `asyncio.to_thread`. That one database file
  holds three tables — `requests`, `bench_results` and `api_keys` — each created by its own module
- **`request_log.API_PATH_SCOPE`** (`"(path LIKE '/v1/%' OR path LIKE '/api/%')"`) is the read
  scope: the `/requests` list, `stats/timeseries`, `stats/distribution`, `failures` and the
  `kpi.requests_today` / `kpi.tokens_today` aggregates all count only the API calls callers actually
  make. Non-API **business** paths such as `/health` and `/metrics-probe` are still recorded (by the
  middleware fallback, see below) and stay queryable in the database and in the detail view, but they
  never appear in those aggregates. Management/console/docs/static paths (`/v1/management/*`,
  `/admin*`, `/docs`, `/openapi.json`, `/redoc`, `/static/swagger/*`) are **never written** — the
  console polls them continuously and they are control-plane noise. (`GET /requests/{id}` deliberately
  stays unscoped, so a deep link from another view still opens.) The constant is prepended inside
  `_where()` and the three `*_sync` stat builders, never from a caller-supplied value
- One row per call, grouped as: timing (`ts`, `ts_end`, `duration_ms`, `ttfb_ms` — `ts`/`ts_end`
  are second-precision ISO UTC, so `duration_ms` is the sub-second source), request
  (`method`, `path`, `query`, `requested_model`, `model`, `request_body`, `req_size`, `stream`,
  `client_ip`) and result (`status`, `upstream`, `response_body`, `res_size`, `tokens_in`,
  `tokens_out`, `tokens`, `error`),
  plus the two `*_truncated` flags. `_init_db` diffs the live table against `PRAGMA table_info`
  and `ALTER TABLE ADD COLUMN`s anything missing; `_ADDED_COLUMNS` pairs every late-added column
  with its declared type, so a new numeric column is not added as `TEXT` on old databases
- `config.yaml` -> `request_log.enabled` (default `true`) is the write switch, evaluated **per
  request by both writers** — `ProxyService._logging_on` for the rich row and the security
  middleware's `log_enabled` for the metadata row — so hot reload turns recording on or off without a
  restart; when off, neither writer captures bodies or queues a row. `/overview` and `/requests`
  echo the state as `request_log: {enabled, db, retention_days, dropped}`. `ConfigManager` polls
  the mtime **at most once per second**, so the switch lands within ~1s — live scripts must wait
  out that window (or call `manager.reload()`) before asserting, otherwise the first request after
  the edit still uses the previous config
- **The middleware is the second writer.** Two kinds of traffic never reach `ProxyService.forward`:
  (a) requests this same middleware refuses *before* `call_next` (public-zone 401/404, rate limit
  429, body cap 413) and (b) the in-process handlers that answer on their own
  (`/v1/systemone`, `/v1/tools/web-search`, `/v1/rerank`, `/v1/messages`, `/v1/models`, `/api/tags`,
  `/api/ps`, `/health`, root). All of them get a **metadata-only row** from `basic_entry()`: bodies,
  tokens, `model` and `upstream` are `NULL`, but `error` carries the denial reason (e.g.
  `Missing API key: POST /v1/systemone`), so a missing-key probe from the public zone is auditable.
  The rich row from a proxied call and the fallback row from `call_next` are coordinated by a claim
  flag on the shared ASGI scope (`request.state.request_log_claimed = True` in `forward()`), which is
  essential for streams because their rich row is written later, in the body iterator's `finally`,
  after the middleware has already returned. `HEAD` and `OPTIONS` are excluded by **both** writers
  (probes / pre-flights would otherwise bury real calls); a CORS pre-flight still gets its bodyless
  `204`. `deny()` centralises "record then reject" for every middleware refusal so no branch can
  silently skip the log
- `proxy.py` records every outcome: blocked (403) / oversized (413) / unrouted (404),
  streaming (captures ≤ 8 KB of body in the generator, writes after `aclose`), non-streaming,
  and connect/read/HTTP failures (502/504). `model` is the resolved target name.
  **`HEAD` and `OPTIONS` are forwarded but never recorded**: they carry no bodies and probes /
  pre-flights send them continuously, so `logging_on = self._logging_on(cfg) and method not in
  ("HEAD", "OPTIONS")` is the proxy's decision point — the row is skipped, not the forward, and the
  middleware applies the same exclusion. (There is no global method gate any more: `HEAD` is accepted
  by the catch-all `HTTP_METHODS`, an `OPTIONS` pre-flight is answered by the middleware with a
  bodyless `204`, and on the public zone both must appear as method+path pairs in
  `security.public.path_allowlist`; `POST_ONLY_PATHS` still answers **405** for non-POST on those
  specific paths.)
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
- **i18n**: `vue-i18n@11` Composition mode, messages in
  `web/src/i18n/messages/{zh-CN,zh-TW,en}.ts`, namespaced per view. `zh-CN.ts` is the source
  language and exports `MessageSchema = typeof zhCN`; every other locale file is typed
  `const x: MessageSchema`, so a forgotten translation is a `vue-tsc` error, never a silent Chinese
  fallback. The locale list is **data-driven** (`SUPPORTED_LOCALES` in `i18n/index.ts`: code, full
  label, short chip), which is what the account-menu flyout and `App.vue` render from, so adding a
  fourth language means one message file plus one array entry - no per-view branching.
  `localeFromTag()` maps a browser tag (`en*` → `en`; `zh-TW` / `zh-HK` / `zh-MO` / any `zh-Hant*` →
  `zh-TW`; everything else → `zh-CN`). Locale persists in `localStorage`
  (`llmhub-console-locale`), and `App.vue` mirrors it into Element Plus via
  `el-config-provider` (the `zh-cn` / `zh-tw` / `en` locale bundles, so a date panel reads
  「現在 / 確認」 vs 「现在 / 确定») plus `<html lang>`. Plain TS modules (`utils/format.ts`,
  `api/client.ts`) call `i18n.global.t()`. When adding any user-facing string: put it in **all
  three** message files and reference it with `t('ns.key')` — no literal Chinese in templates
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
- Anything in `web/public/` (currently `favicon.svg`, `wechat-qr.jpg`) is copied verbatim to `app/web/` and served by
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
  `upstreams[].models` with a `model_aliases` rename shown as `display` (hover/`title` keeps the real
  id), a `derived` chip on a row declared under root `aliases:` (hover says which target it runs on),
  and a `wildcard` chip on the rows an expanded `qwen3:*` / `opencode/*` produced. Hosted (`opencode`)
  rows report `device: "cloud"` with `memory null`, which `deviceBadge` renders as a violet `Cloud`
  badge and `fmtInt` / `memoryText()` as `-`, so views need no per-type branches — a derived row's
  `-` memory uses the same path. Device badges never show a
  percentage - GPU / 部分 / CPU / Cloud only
- The Models · LLM 别名与派生模型 table renders both kinds side by side (`kind` `显示别名` vs
  `派生模型`) with the target, upstream and pinned `params` (`think=false` style). Both kinds share
  **one chip style** (`.chip.s2`) — the text is the only difference — and there is **no 说明 column**:
  `description` is the native `title` on the 名称 and 目标模型 cells, and `table-layout: fixed` +
  `colgroup` pins the five columns at 24/12/24/14/26% so a long name clips with an ellipsis instead of
  pushing the table apart. The speed test always benches `m.target ?? m.model` (the upstream knows
  nothing about alias names) and passes `params.think` as `BenchStart.think`
- Upstreams rows expand **independently**: `expanded` and `detailBusy` are arrays of upstream names, so
  clicking one row leaves the already-open panels open, two probes can be in flight at once, and the
  per-minute refresh re-probes every open row (`Promise.all`), not just the last one clicked
- The Requests 模型 dropdown follows the chosen 上游: `/models/llm` rows carry their upstream, and an
  alias is offered under every upstream that serves its target (`ModelChoice.upstreams: string[]`).
  A derived alias is now *also* its own row, so the choices are deduped by name
- The Requests 时间 column stacks `yy-MM-dd` above `HH:mm:ss` (both local time, like `fmtClock`),
  with the full `yyyy-MM-dd HH:mm:ss` on the cell's `title`. `fmtDateTimeParts()` in
  `utils/format.ts` parses the naive UTC ISO timestamp once and returns all three parts, so a
  two-line stamp cannot split across a day boundary; the dimmed `.date-line` matches the table's
  other sub-lines and `white-space: nowrap` keeps the column narrow
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
- Every `ApiDoc` block carries a 「测试」 button (`Test` in English) at the right end of its
  **Request parameters** header; it opens the tester in a **popup** over the page, not inline below
  the document. `ApiDoc.vue` mounts `ApiTester` on the first open and only toggles `v-show`
  afterwards, so the model probe stays lazy while closing and reopening keeps what was typed and the
  answer that came back. The modal reuses the bench window's `.modal-overlay` / `.modal` pattern
  (z-index 100, `min(940px, 94vw)`, `max-height: 88vh`, scroll in the body). 7 windows across the 5
  API-usage views: `generate`, `chat`, `embed_ollama`, `embed_openai`, `rerank`, `systemone`,
  `websearch`. The view passes the `kind` and a `TestField[]` (`web/src/types/ui.ts`): `text` /
  `textarea` / `number` / `bool` / `lines` / `json` / `select`, each with the tester's initial
  value in `def`, a dot path (`options.temperature`) for nesting, and an empty input **omitting the
  key entirely** so the endpoint answers with its own default. A `bool` **with** a `def` is a
  documented flag and renders as a **checkbox** that always sends `true` / `false` (unchecked means
  false, never "omit"); a `bool` without one stays a three-state select, so an optional field such
  as System One's `stream` can still be left out
- **Defaults are already in the form when the popup opens**: `defaultsOf()` seeds every field with
  `field.def ?? ''`, so the form shows what the endpoint would have used anyway, flags start in the
  right checkbox state and a select with no default still displays its "not set" option instead of
  rendering nothing. 「恢复默认值」 puts those values back (it used to be called 「填入默认值」);
  「清空」 blanks every field, unchecks every flag (which then sends `false`) and clears the model
  selection, which is how a key gets left out of the body; 「查看请求体」 shows the JSON that will be
  sent. A late-arriving `TestField[]` fills any still-empty field but never overwrites typing
- The two LLM windows start with **`stream` off** even though both endpoints stream when the flag is
  omitted: a streaming test hides the answer behind chunk assembly, so the window sends an explicit
  `stream: false` and one Run shows a single JSON. The generate window also has a **`think`**
  checkbox (Ollama's native top-level field, verified on 0.40: false → no `thinking`), defaulting to
  false like the benchmark and the shipped `-nothink` aliases; the chat window exposes
  **`reasoning_effort`** instead, because the OpenAI-compatible endpoint ignores `think` and Ollama
  maps `none` to "no thinking"
- The tester never fetches per-instance: its model choices, preselected default, `free_model`,
  `enabled` and masked keys all come from `useApiTestContext()`
  (`web/src/composables/apiTestContext.ts`), a **module-scope** ref holding one
  `/api-test/models` payload for the whole console because that call probes every upstream —
  five views and eight windows share it, and only the first mount or 「重新探测」 re-probes.
  The same payload drives the per-page "Available models" tables and the `model` in every
  request/response example, so no view hardcodes a model name
- Every window picks the model from a plain `<select>` (the tester uses no Element Plus form
  controls at all), preset to `default_model`. **Derived** `free_model` kinds (`systemone`,
  `websearch`) add one entry, 「自定义模型…」, which swaps in an `<input>` + `<datalist>` for a name
  the list does not carry; choosing a listed name again closes that box. An explicit `api_models`
  list sets `free_model: false` and the entry disappears. It is a real dropdown on purpose — a
  text box pre-filled with the default was what hid the other names, because Chromium filters a
  datalist popup by the text already in the box.
  **Every window can clear the model**: the `×` beside the field (or the select's empty option) drops
  it, and the body then carries no `model` at all. A kind whose family requires `model` answers the
  tester's own 「请先选择模型」 guard; no `model` is not "no model" server-side, e.g. System One
  still injects `systemone.model`. 「恢复默认值」 re-selects `default_model` and 「清空」 clears it.
  An option shows the **name only** — the alias (`display`) when one is configured, else the
  declared id — with no upstream / device / running marker; the tooltip (`optionTitle`) carries the
  routing detail (`realModel`, `derivedFrom`, the unserved warning) and the upstream name
- `v-model` on a `type="number"` input hands back a **number**, and the tester's `type` attribute is
  bound dynamically, so Vue's `vModelDynamic` applies `looseToNumber` even without `.number`.
  `trimmed()` therefore coerces with `String(values.value[key] ?? '')` before trimming — otherwise
  typing into `options.temperature` crashed the whole subtree with `.trim is not a function`
- Results report the transport the gateway chose plus the observed `content_type`, so the
  「流式」/「首字」 chips appear only when the body really came back as NDJSON / SSE
  (`/ndjson|event-stream/i`) — a single JSON answer is never labelled streaming. `answer` is pulled
  out of `body.response` / `choices[0].message|delta.content`, falling back to assembling the chunked
  `text`, and a refusal from the gateway itself renders as a normal result line
- The run button sends the body through `POST /api-test` **server-side** (the console's session
  cookie is not a caller API key), so nothing in the tester can leak a gateway or provider key; the
  masked `••••••` rows are read-only and the env-var name is the only clue about where the value lives
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
  does not expand the current menu - the three `SUPPORTED_LOCALES` entries are all selectable, each
  marked with `✓` + `.um-item.on` when current and `·` otherwise; the old `soon` placeholder for
  English is gone),
  深色模式 switch, 设置 (`/admin/settings`), **API Key 管理** (`/admin/api-keys`, `⚿`), 关于
  (`/admin/about`), 退出登录 (`signOut()` then the
  login gate). Outside `pointerdown` or `Esc` closes it; the flyout closes 150ms after the pointer
  leaves so it can be crossed. There is no sidebar or floating theme toggle any more - the theme
  switch lives in this menu
- API keys (`/admin/api-keys`, `ApiKeysView.vue`, reached from that menu rather than the sidebar) is
  the console's one page that changes gateway credentials: a 功能介绍 card, the masked 密钥清单 table
  (掩码 / 描述 / 建立时间 / 操作 — the `source` field is in the API payload but not shown as a column)
  and a 密钥格式与储存 card that states the `sk-hub-` + 40 base62
  shape and the SHA-256 rule, so the contract is readable without opening a doc. **新建立鑰** opens the
  same `.modal-overlay` / `.modal` window the speed test uses; a non-empty label is required (the
  backend answers **400** for a blank one) and the created key is shown exactly once in a readonly
  textarea with a copy button and an "I have saved it" acknowledgement. `navigator.clipboard` is
  tried first and falls back to a `document.execCommand('copy')` textarea, then to focusing the box
  with a "copy it manually" error strip, because the console is often reached over plain http where
  the async API is unavailable. Closing that dialog reloads the list, so the plaintext never has a
  second home in the DOM. Revoking is **two steps inline** (the row's 撤銷 button becomes 確認撤銷 /
  取消) - a single click on a table row must not destroy a credential in use
- Every refreshable control box is one height: `.sel`, `input.inp` and `.filters .btn` are pinned to
  `height: 32px` in `theme.css`, which is what Element Plus' own `.el-input__wrapper` already is, so a
  hand-rolled `<select>` next to a date picker no longer sits 2px short. Native checkboxes take
  `accent-color: var(--accent)` rather than the browser default (and `ApiTester` no longer overrides
  it with its own violet), so a tick matches the theme instead of clashing with it
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
   path is answered by the proxy instead of being served locally
3. A pass-through the catch-all should forward needs its path in `proxy.FORWARDABLE_PATHS` (or
   `FORWARDABLE_PREFIXES`) — otherwise **both** zones answer 404. A documented POST-only route also
   needs an entry in `main.POST_ONLY_PATHS` so other methods get 405 with `Allow: POST`
4. Add tests in `smoke_test.py`
5. Expose it to the console tester: a `Kind` in `KINDS` (`app/api_test.py`) plus the matching `ApiDoc` /
   tester fields in the view, `ApiTestKind` in `types/api.ts` and the three i18n files
6. Document it in **both** languages: `docs/API.md` + `docs/API_CN.md` (endpoint section with a
   request/response example), `docs/USAGE.md` + `docs/USAGE_CN.md` when it changes how people call the
   gateway, and `docs/CONFIG.md` + `docs/CONFIG_CN.md` when it adds or changes a config node. Add the
   README calling example only if it is a headline capability, and keep the doc-map blurbs
   in sync when the doc grows a new chapter

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
- `warning`: Failed upstream connections, empty `api_keys` table on first start, weak `ADMIN_PASSWORD`
- `error`: Upstream failures, config parse errors

### Common Issues
- **Config not reloading**: Check file mtime, restart container
- **Auth failures**: a **403** means the key you sent hashes to no row of `api_keys` — check the list
  at `/admin` (⚿ API Key 管理) and mint a new one there. There is no environment seed any more, so
  there is nothing to edit and restart for: a key that is not in the table does not exist. A **401**
  is a missing credential entirely
- **Upstream unreachable**: Check `host.docker.internal` resolution
- **Rerank scores all 0**: the upstream gave no usable yes/no logprobs, so the text fallback scored
  it unknown (`rerank.fallback_unknown` = `0.0`). Check the model really answers `yes` / `no`, and set
  `rerank.yes_token` / `no_token` (or `yes_aliases` / `no_aliases`) to its vocabulary
- **Local start refuses to boot**: `scripts\start-local.bat -CheckOnly` re-runs the app's own
  rules (Pydantic config, the `api_keys` row count in `llmhub.db`, the same `Config.listen_ports()`
  the app binds) and names the cause — missing dependency, invalid config, a busy internal/public
  port, or an empty key table with no `ADMIN_PASSWORD` and no anonymous internal zone
  (fail-closed). It also strips stale `GATEWAY_API_KEYS` / `GATEWAY_API_KEYS_FILE` with a warning
  and warns about stale
  `LLMHUB_*` port variables in `.env` (honoured once as migration hints, then ignored — remove
  them). A public port distinct from `server.port`, or `-NoReload`, switches the launcher to
  `serve.py`; every uvicorn path still passes `--no-proxy-headers`

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