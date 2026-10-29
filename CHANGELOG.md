# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
