# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed (Web console sources → TypeScript)

- `web/src` is now fully TypeScript: every non-SFC module is `.ts` (`main.ts`, `api/client.ts`, `api/management.ts`, `stores/{session,theme,gateway}.ts`, `composables/chartTheme.ts`, `plugins/echarts.ts`, `router/index.ts`, `utils/format.ts`) plus `vite.config.ts`, and all SFCs use `<script setup lang="ts">` with typed `defineProps<T>()` / `defineEmits<T>()` / `defineModel<T>()`. The old `.js` files and `vite.config.js` are deleted; `index.html` loads `/src/main.ts`
- New `web/tsconfig.json` (`strict`, `moduleResolution: "bundler"`, `verbatimModuleSyntax`, `isolatedModules`, `noUnusedLocals` / `noUnusedParameters`, `@/*` → `src/*`) and `vue-tsc` as the checker. `npm run typecheck` runs `vue-tsc --noEmit`; `npm run build` runs the same check **before** Vite, so a prop or payload mismatch fails the build instead of shipping a blank table
- Response contracts derived from live management payloads: `src/types/api.ts` holds one interface per `/v1/management` response with the real nullability (`stream` is the SQLite 0/1 integer in a log row and a boolean in the detail; `loaded`, `vram_total_gib`, `ttfb_ms`, `tokens`, `query`, `requested_model`, `models_error` … are optional/nullable), `src/types/ui.ts` types the presentational props, `Badge` / `BadgeClass` live in `utils/format.ts`, and `plugins/echarts.ts` exports `ComposeOption`-narrowed `ChartOption` plus `ChartInstance`. `api/management.ts` wraps each route in an axios generic, so a renamed backend field is a compile error in the wrapper first
- Views now catch `unknown` and use `statusOf()` / `errorResponseBody()` from `api/client.ts` instead of `error.response?.status`; behaviour is unchanged (401 still bounces to the login gate, 503 stays a domain answer). No template or styling changes
- `app/web` rebuilt and the console re-verified live: login, Dashboard KPIs and charts, Upstreams expand (online rows and the 503 `unreachable` branch), Models·LLM discovery, Rerank / System One / Web Search / Requests, the request-detail drawer and the light/dark toggle. `npm run typecheck` is clean and all four mock suites stay green (123 / 63 / 158 / 15)
- Docs: AGENTS.md (directory tree, Console Development, web-console section), README / README_CN, `docs/DEPLOY.md` and `docs/web-management/web-management-implementation.md` now describe the TypeScript sources and the `npm run typecheck` step

### Added (`/api/generate` route)

- `POST /api/generate` - the Ollama-native text-completion endpoint - is now an explicit route in `app/main.py` instead of only a catch-all pass-through. Behaviour is unchanged (the proxy already routed it by `model`, applied aliases and injected parameters, clamped `num_ctx`, streamed NDJSON and wrote the request log); the route makes it **visible in `/openapi.json` and `/docs`** with a request schema (`model` + `prompt` required; `system`, `template`, `context`, `raw`, `images`, `format`, `stream`, `keep_alive`, `logprobs`, `top_logprobs`, `options`, …; `additionalProperties` stays open so future Ollama fields pass through) and a 200 response that documents both `application/json` and `application/x-ndjson`
- `/api/generate` joined `POST_ONLY_PATHS`, so `GET` / `PUT` / `DELETE` on it now answer **405** with `Allow: POST` from the gateway instead of being forwarded to an upstream
- The path only means something against `type: "ollama"` upstreams - an OpenAI-compatible or opencode upstream answers its own 404, which the gateway passes through
- Tests: `tests/smoke_test.py` gains group `[18] Ollama-native text generation (/api/generate)` (19 checks - single JSON object vs NDJSON chunk count, default-stream when `stream` is omitted, `prompt` / `raw` / `format` / `keep_alive` / `options` forwarded, alias `big-nothink` pinning an upstream while replacing `model` and injecting `think:false` + `num_ctx:8192`, 401 without a key, 405 + `Allow`), now **123** mock tests; `tests/test_gateway_new.py` asserts the documented schema (path present, required fields, property coverage, both response media types), now **63** tests
- Docs: `docs/API.md` 文本补全 rewritten with the full parameter table, the NDJSON example and the POST-only/405 note; `docs/TESTCASES.md` 测试 12 rewritten for the explicit route; README / README_CN list `/api/generate` in the streaming feature row and the request examples; AGENTS.md gains a "Schema-visible proxy routes" section and the corrected endpoint-adding steps

### Added (local start script)

- New `scripts/start-local.ps1` (+ `scripts/start-local.bat`, a cmd wrapper that calls it with `-ExecutionPolicy Bypass`): locates python (`python`, else the `py -3` launcher, overridable with `-Python`), reads `.env`, finds `config.json` and **runs the app's own rules before launching**, so the checks cannot drift - `config.json` is parsed by the real Pydantic models and the keys are screened by `auth.find_weak_keys`
- Preflight covers the usual local failures: missing dependency (with the `-Install` hint), a config the validator rejects, `auth.enabled` with no key, weak/placeholder keys (fatal only when `security.fail_on_weak_keys` says the boot would fail, otherwise a warning), `ADMIN_PASSWORD` unset (console and `/v1/management` would answer 503), `app/web/index.html` missing (a rebuilt console is needed), and a port already in use - named with the owning PID and process name
- It starts uvicorn the way the flat modules need: `--app-dir app` with the working directory set to `app/`, exactly the image's `WORKDIR /app`, so the relative paths written in `.env` (`../data/config.json`, `../data/.keys`) resolve identically on the host and in the container. The old documented form `python -m uvicorn app.main:app` is **removed** from AGENTS.md - it cannot work, the modules import each other by bare name (`ModuleNotFoundError: No module named 'admin_auth'`)
- Config resolution is `-Config` (relative to the caller's directory) > `CONFIG_PATH` from `.env` (relative to `app/`, used only when it exists) > `<repo>\data\config.json`, which is created from `data\example.config.json` with a warning when missing. Values already exported in the session win over `.env`, an unreadable `GATEWAY_API_KEYS_FILE` is dropped with a warning when inline `GATEWAY_API_KEYS` are available, and `--reload` is silently downgraded when `watchfiles` is not installed
- Flags: `-Port` / `-Bind` / `-LogLevel` (default to `server.*` from the config), `-EnvFile`, `-Python`, `-NoReload`, `-Install`, `-CheckOnly` (run the checks, start nothing). Only secret **names** are ever printed
- Docs: README / README_CN gain a "Launcher / 启动脚本" section with the flag table; `docs/DEPLOY.md` documents the script next to the build/publish scripts; AGENTS.md lists both files in the `scripts/` tree, replaces the broken Local Development command and adds a Common Issues entry

### Added (Web console frontend)

- New `web/` app: Vue 3 + Vite + Element Plus + axios + ECharts single-page console. Sources live in `web/src`, and `npm run build` writes the bundle into **`app/web`**, which is committed and served by FastAPI at **`/admin`** - the bundle sits next to the modules, so the local `app/web` and the image's `/app/web` are the same resolved path (`Dockerfile` only does `COPY app/web /app/web`, so the image needs no node toolchain; develop with `cd web && npm install && npm run dev`, ship with `npm run build`)
- Eight views: Dashboard (KPIs, 24h/7d trend, upstream/model/status pies), Upstreams (expandable rows, VRAM gauge, GPU/partial/CPU device badges, offline-upstream notice), Models·LLM (upstream model mapping, aliases and `num_ctx_overrides` as a read-only projection of `config.json`), Models·Rerank, Models·System One, Tools·Web Search (parameter reference plus request/response examples), Requests (time/upstream/model/status/path filters, pagination and a detail drawer with the truncated request/response bodies) and Settings (placeholder). Every chart is ECharts and reads its colors from CSS custom properties, so switching theme recolors the charts too
- The login form fixes the username to `admin`; the password comes from `ADMIN_PASSWORD` and the session is a 12h HttpOnly cookie. The router guard probes `/auth/ping` before any protected view, and an axios interceptor signs the user out and bounces to the login page (keeping `redirect`) whenever a management call answers **401**. **503 is never treated as an auth failure** - it means an unreachable upstream, `ADMIN_PASSWORD` unset (the login card switches to a locked state) or the console has not been built
- When `request_log.enabled` is `false`, Dashboard and Requests show a "logging disabled" strip instead of an empty table; tables show a loading state until the first load completes, so slow upstream discovery is not misread as "no models"
- Static serving details: `/admin` 307-redirects to `/admin/`, unknown sub-paths fall back to `index.html` for SPA routing, content-hashed `assets/*` are `immutable` while `index.html` is `no-cache`, and every file path goes through `resolve()` + `is_relative_to()` so `..` cannot escape the build directory. A missing `app/web` answers **503** with the `npm run build` hint. The shell itself is intentionally unauthenticated (the login page must load) while every data call stays behind `require_admin`
- Theme (dark/light) persists in `localStorage` and drives Element Plus's `html.dark` variables; the sidebar collapses and the version shown in the footer comes from `/overview`'s `gateway_version`
- Tests: `tests/test_management.py` gains group `[12] web console static serving` (11 cases) covering the 307 redirect, the anonymous shell and SPA deep-link fallback, the `frame-ancestors 'self'` CSP, `no-cache` / `immutable`, the traversal fallback and the unbuilt-console 503
- Docs/config: `Dockerfile` gains `COPY app/web /app/web`; `.gitignore` ignores `web/node_modules/`, `web/.vite/`, `web/.vite-temp/` (while `app/web` is deliberately **committed**); README / README_CN add the console feature row, the `web/` + `app/web` directories, the `/admin` URL and the build steps; `docs/DEPLOY.md` explains the bundled frontend; `docs/API.md` now describes "session cookie or gateway API key"; `docs/web-management/web-management-implementation.md` records the implementation status; `docs/TESTCASES.md` gains 测试 41 (static `/admin` serving, and 测试 34 rewritten for the cookie-or-key contract); `.env.example` and AGENTS.md updated

### Added (Management API for the web console)

- New read-only API under `/v1/management`, implemented in `app/management.py` and protected by `require_admin`: `auth/login|logout|ping`, `overview`, `upstreams[/{name}]`, `models/llm|rerank|systemone`, `tools/websearch`, `requests[/{id}]`, `stats/timeseries`, `stats/distribution`, `stats/failures`. Two credentials are accepted: a valid **12h HMAC session cookie** (minted by the login page) or a **gateway API key** from `auth.api_keys`, via `Bearer` / `X-API-Key`, for scripts and curl. `ADMIN_PASSWORD` is a login credential only - sending it as a request header still answers **401**, and a key in the query string is rejected. Without `ADMIN_PASSWORD` every route answers **503**
- `overview` / `upstreams` probe each upstream in parallel (5s timeout, one dead instance cannot stall the view) and classify running Ollama models by `size_vram / size` as GPU / partial / CPU; unreachable upstreams report `status: offline` and the detail route answers **503** with `unreachable: true`
- Config projections are read-only and never echo credentials: header-based upstream keys, the System One env key and the web search provider key are returned as `••••••` (only whether the env var is set is reported); `models/llm` includes aliases and `num_ctx_overrides`, `models/rerank` carries `upstream_api` and `on_error` alongside the scoring and limit groups, and `models/rerank` / `models/systemone` include request/response examples
- `tools/websearch` projects the `websearch` block for the console's Tools page and derives `base_url` / `upstream_type` / `provider_configured` from `Config.websearch_upstream_obj()` - the very resolver `WebSearchService` uses, so the view cannot show an address the forwarder would not call
- New `app/request_log.py`: SQLite request log (`llmhub.db`, WAL, `synchronous=NORMAL`) fed by a bounded in-memory queue (10 000 rows, drop + warn when full) and written by one background task in batches of 100 via `asyncio.to_thread`, so forwarding never waits on disk. Retention is 30 days by default (pruned hourly), configurable with `REQUEST_LOG_DB` / `REQUEST_LOG_RETENTION_DAYS` (0 = keep forever)
- The `requests` table records the whole exchange per row - timing (`ts` arrival, `ts_end` completion written when the stream closes, `duration_ms`, `ttfb_ms`), request parameters (`method`, `path`, `query`, `requested_model` as the caller wrote it, `model` after alias resolution, `request_body`, `req_size`, `stream`, `client_ip`) and result (`status`, `upstream`, `response_body`, `res_size`, `tokens`, `error`) plus the two `*_truncated` flags. `_init_db` compares `PRAGMA table_info(requests)` with the schema and `ALTER TABLE ADD COLUMN`s what is missing, so an existing database is upgraded in place
- New `config.json` block `request_log` with the write switch `enabled` (default `true`). `ProxyService._logging_on` evaluates it **per request**, so saving the file starts or stops recording without a restart - the config mtime is polled at most once per second, so the switch lands within ~1s (verified against a live Ollama: zero new rows while off, exactly one row after re-enabling); while off, bodies are not captured and no row is queued. `/v1/management/overview` and `/v1/management/requests` report the state as `request_log: {enabled, db, retention_days, dropped}` so the console can say "logging disabled" instead of showing an empty list
- `proxy.py` instruments every outcome - blocked (403), oversized (413), unrouted (404), streaming, non-streaming and connect/read/HTTP failures (502/504) - recording method, path, resolved model, upstream, status, duration, TTFB, stream flag, token usage (`usage.total_tokens` or Ollama's `prompt_eval_count + eval_count`), sizes, client IP and truncated bodies
- Bodies are stored truncated to 8 KB with `authorization` / `api_key` / `token` / `password` fields replaced by `***`; the request-detail route returns them for the console's drawer
- `GET /v1/management/requests` supports `start` / `end` / `upstream` / `model` / `status` (2xx|3xx|4xx|5xx) / `path` / `page` / `page_size` and returns `stats` (count, P50/P95/P99, error rate), `rows` and `pagination`; `stats/timeseries` buckets by hour or day with per-bucket count/errors/P95, `stats/distribution?group_by=upstream|model|status` returns the grouped counts that drive the three Dashboard pies (`status` collapses to `ok` below 400 and `error` at 400+, rows that never reached an upstream bucket as `-`, so the items always sum to the log total; any other `group_by` answers **400**), and `stats/failures` breaks failures down by status code and upstream with recent samples
- `UpstreamConfig.vram_total_gb`: optional host VRAM total for the overview gauge (not discoverable over the API)
- `/v1/management` is registered before the catch-all and exempted from `security.path_allowlist` (like `/admin`); legacy `/admin/login|logout|ping` now delegate to the same implementation
- Tests: new `tests/test_management.py` (158 cases, including the 11 console-serving cases above) with a local mock Ollama - auth (session cookie **and** gateway API key → 200; the admin password as `Bearer` / `X-API-Key` → 401; unknown key → 401; key in the query string → 401; wrong username → 401; username trimmed and lowercased; 503 when unset), probing and device classification, masked projections including `tools/websearch` and the rerank `upstream_api` / `on_error`, streaming/non-stream/502 capture, the new columns (arrival/finish timestamps, alias `requested_model` vs resolved `model`, `query`), filters, pagination, timeseries, distribution (bucket conventions, ordering, totals and the `group_by` guard), the `request_log.enabled` off/on behaviour and the legacy-database `ALTER TABLE` upgrade
- Docs/config: `docs/API.md` gains a "管理 API（Web 管理台）" section with the `requests` column list, the `request_log.enabled` switch and worked `stats/distribution` / `tools/websearch` responses; README / README_CN add the feature row, directory entries, a `request_log` config block, `ADMIN_PASSWORD` / `REQUEST_LOG_*` variables and `vram_total_gb`; `docs/TESTCASES.md` adds 测试 34-40; `.env.example`, `data/example.config.json` (now shipping `request_log`), `.gitignore` (`data/*.db*`), `docs/DEPLOY.md` and AGENTS.md updated

### Added (Web Search)

- New endpoint `POST /v1/tools/web-search`: keyword web search served by an OpenCode API Gateway. The request body and the provider response are passed through unchanged (`query`, `results[]`, concatenated `text`, `sources`, plus `answer` when `synthesize: true`)
- `POST /v1/tools/web-search` now logs a diagnostic line when the provider is unreachable: the full exception chain plus a per-address TCP probe (`dns(host) -> ip:port ConnectionRefusedError errno=111`, `TimeoutError`, `gaierror`), since httpx collapses all of them into `All connection attempts failed`
- New `websearch` config block: `enabled`, `upstream`, `path` (default `v1/web-search`), `api_key_env`, `timeout`. `upstream` selects the provider by name; left empty it picks the first `type: "opencode"` upstream, and an unknown name returns 503 instead of silently falling back
- New upstream type `opencode`: OpenAI-compatible model discovery (`/v1/models`, `data[].id`), liveness via `/health`. Registering the provider as a normal upstream means its models also join the aggregated `/v1/models` and chat can be routed to it
- Optional provider signing through `websearch.api_key_env` (empty by default = unsigned, which is what the current gateway needs); `OPENCODE_GATEWAY_API_KEY` documented in `.env.example` and `docs/DEPLOY.md`
- The endpoint appears in `/docs` with a real request/response schema (`query` required, `top_k`, `recency`, `synthesize`, ...), is POST-only (405 otherwise) and requires the gateway API key (401/403)
- `data/config.json` and `data/example.config.json` ship an `opencode` upstream (declaring `auto` plus the `opencode/*` wildcard) and a `websearch` block
- API.md: new "Web Search（OpenCode 网关代理）" section; README / README_CN: feature row, `websearch` config reference, curl example, `opencode` upstream type and the slash-wildcard caveat (`opencode/*`, not `opencode:*`)
- Tests: `smoke_test.py` +13 (104 total) for the `opencode` type and `websearch` config resolution; `test_gateway_new.py` +22 (55 total) for the endpoint's auth, forwarding, pass-through, signing, status mapping and OpenAPI schema; `test_api.py` +8 (28 total) E2E cases; docs/TESTCASES.md adds 测试 31-33 with measured responses

### Added (Build & publish scripts)

- `scripts/docker-build.bat`: builds `bruce1977/llm-hub:latest` plus `:<version>`, reading the version from `__version__` in `app/main.py` (overridable: `docker-build.bat [image] [version]`); aborts with exit code 1 when Docker is unreachable
- `scripts/docker-publish.bat`: runs the build first and only then pushes `latest` and `:<version>` to Docker Hub, stopping with a `docker login` hint if a push fails. Neither script touches git - commit and tag by hand before publishing
- `docs/DEPLOY.md`: new "一键构建 / 发布脚本（Windows）" section documenting both scripts

### Changed (Repository layout)

- `API.md`, `DEPLOY.md`, `workflow.md`, `Ranker.md` and `tests/TESTCASES.md` moved into `docs/` (history preserved); `README*`, `CHANGELOG*` and `AGENTS.md` stay at the repository root, and cross-links were updated accordingly
- `.example.env` renamed to `.env.example`, the conventional template name; every copy instruction in README / README_CN / `docs/DEPLOY.md` / `docs/workflow.md` / `docs/TESTCASES.md` / AGENTS.md follows the new name
- Removed two orphaned root files: `opencode.jsonc` (its `.opencode/skills` directory was deleted in 9a8bfea, so it pointed at nothing) and `Modelfile.reranker` (the authoritative reranker Modelfile lives outside the repo and is documented in `docs/Ranker.md`); the cross-reference in `data/example.config.json` now points at that document

### Changed (System One backend)

- **Breaking (deployment):** the environment variable naming the System One backend key is renamed `LAYA_SERVER_API_KEY` → `SYSTEMONE_SERVER_API_KEY`, because the endpoint is no longer laya-server only. Rename it in `.env` / the container environment; `systemone.api_key_env` may still be set to any other variable name
- Ollama 0.40+ serves `POST /v1/systemone` natively, so `systemone.backend` can now point at any Ollama port (e.g. `http://llm.home:11435`) - no gateway code change; forwarding stays pure pass-through
- `systemone.model` is confirmed to travel to the backend on the wire. It is injected only when it is a concrete value and the caller supplied no `model`; `"local"` remains the "do not inject" sentinel. The code default stays `auto` (valid for laya-server); `data/config.json` and `data/example.config.json` now ship `model: "tev1"` against an Ollama backend, since Ollama answers **404** for `auto` and **400** when the model is missing
- Error and log wording in `app/systemone.py` is now backend-neutral ("System One backend") and the missing-env-var warning explains that signing only matters if the backend requires a key
- Docs: `docs/API.md` and README_CN gained the laya-vs-Ollama contract comparison - measured differences are the accepted `model` values, `choice` requiring a non-empty `criteria` object on Ollama (array → 400) while `score` requires a 2-10 item array, unknown top-level fields being rejected by laya-server but ignored by Ollama, and `routing` being present only in laya-server responses - plus a request example valid on both and the observed Ollama response

### Fixed

- Model discovery for `/v1/models`, `/api/tags` and `/probe` now sends `upstreams[].headers`. Previously only chat forwarding (`proxy.py`) and web search (`websearch.py`) applied them, so an upstream requiring auth (e.g. a keyed `opencode`) answered **401** to the discovery call and its models never reached the aggregate - the catalog showed only `auto` while chat and search worked
- Tests: `test_gateway_new.py` +4 (59 total), using a fake client to assert discovery hits `v1/models` with the `Authorization` header

---

## [1.2.0] - 2026-09-30

### Security

- Add HTTP method allowlist (`security.allowed_http_methods`, default GET/POST → 405 for others)
- Add regex path allowlist (`security.path_allowlist`, 403 for non-matching paths)
- Add in-memory token-bucket rate limiter (`security.rate_limit`, 429 when over budget)
- Add defensive response headers on every response (nosniff, X-Frame-Options, CSP, etc.)
- Document new hardening options in README and README_CN

### Added (Docs)

- Swagger `/docs`: every operation gets an `X-API-Key` **header parameter**, so an API-key input box sits in each endpoint's request section (`Parameters` → `Try it out` → `Execute` sends it with that call); the classic **Authorize** dialog (`BearerAuth` / `ApiKeyAuth`, persisted via `persistAuthorization`) still covers "one key for all requests"
- Vendored local `favicon.svg` for `/docs` (replaces the external FastAPI CDN favicon, which CSP/air-gapped deployments reject)

### Changed

- `GET /` now returns system information (`{"name": "LLM Hub", "version": "1.2.0"}`) instead of the `{"ok": true}` health payload; health probing stays on `/health`
- Dockerfile HEALTHCHECK interval changed from 30s to 1h (passive gateway needs less probing)

### Fixed

- `/docs` rendered a blank white page: the blanket `default-src 'none'` CSP blocked the Swagger UI stylesheet, bundle and inline bootstrapper. Docs routes now serve a scoped CSP (`script-src`/`style-src 'self' 'unsafe-inline'`); every other response keeps `default-src 'none'`
- Docs ↔ code sync pass: API.md documents `/probe` as **GET only** (other verbs fall through to the catch-all proxy) and its TOC now lists every section; documented JSON shapes (`/`, `/health`, `/v1/models`, `/api/tags`, `/api/ps`, `/probe`, error payloads) were re-verified against the live handlers

### Removed

- `/healthz` health alias (use `/health`)
- Dead helpers with no callers: `ConfigManager.snapshot()` and `rerank.softmax_pair()` (Pydantic validators like `Config._expand_upstream_aliases` / `RerankRequest._coerce_aliases` are invoked by Pydantic and stay)

### Added (Rerank)

- Add `keep_alive` config for Ollama reranker VRAM management (0 = immediate unload, "5m" = keep warm, -1 = server default)
- Add `RateLimitConfig` model with `enabled`, `per_minute`, `burst`, `by` (ip/key)
- Default rerank `options.num_ctx` set to 8192, `keep_alive` set to "3m"
- Add `Modelfile.reranker` for low-VRAM reranker variant (8192 ctx ≈ 4 GB vs 40960 ctx ≈ 9.3 GB)
- Add `Ranker.md` comprehensive reranker configuration guide
- Add `tests/verify_security.py` security verification test suite (15 tests)

### Documentation

- AGENTS.md: document new security features
- AGENTS.md: project tree now lists `systemone.py`, vendored `static/swagger/`, all four test suites and `requirements.txt`; the type-hint convention line was corrected to PEP 604 unions (`X | None`), which is what `app/` actually uses
- `.env.example` / README / README_CN / API.md / TESTCASES.md: example API keys replaced with obviously-fake placeholders (the previous realistic-looking `sk-gateway-…` + hex values tripped secret scanning)
- README.md / README_CN.md: add security hardening tables and container health check notes

---

## [1.1.0] - 2026-09-09

### Changed

- Rerank API: refactor to support logprobs/embedding scoring modes with fallback chain
- Add `upstream_api`, `raw_prompt`, `disable_thinking` config options for rerank
- Rerank payload structure updated (logprobs/top_logprobs as top-level fields for Ollama)

### Added

- Rerank fallback chain: `/api/generate` → `/api/chat` → `/v1/chat/completions` → text fallback
- Per-model rerank mode override via `model_modes`
- Rerank smoke tests expanded with logprobs routing scenarios (87 tests total)

### Documentation

- Sync API.md, DEPLOY.md, README.md, README_CN.md with new rerank capabilities

---

## [1.0.1] - 2026-09-08

### Added

- `GATEWAY_AUTH_DISABLED` environment variable to bypass API key authentication (`true`/`1`/`yes`)
- Smoke test case for `GATEWAY_AUTH_DISABLED`

---

## [1.0.0] - 2026-09-07

### Added

- Initial release of LLM Hub — unified Ollama/LM Studio gateway

#### Core

- Multi-upstream routing by model name with priority-based selection
- API key authentication (Bearer / X-API-Key) with constant-time comparison
- Rerank API implementation (`/v1/rerank`)
- Model aliasing with automatic parameter injection (e.g., `think: false`)
- Streaming pass-through (NDJSON for Ollama, SSE for OpenAI-compatible)
- Config hot reload via file mtime checking
- `/health` endpoint (lightweight, no resource consumption)
- `/probe` endpoint for upstream instance and model availability checking

#### Security

- Admin endpoints blocked by default (`api/delete`, `api/pull`, etc.)
- Client IP allowlist support (CIDR notation)
- Request body size limits (DoS protection)
- Weak key detection at startup (refuses to boot with placeholder keys)
- CORS configuration

#### Deployment

- Docker support with volume mounting
- Docker Compose configuration
- Docker Hub image (`bruce1977/llm-hub:latest`)
- Local Python development support (Linux / macOS / Windows)

#### Testing

- 57 mock tests covering auth, routing, aliases, streaming, rerank, security
- E2E test support for real Ollama instances

#### Dependencies

- Python 3.12+
- FastAPI, httpx, Pydantic
