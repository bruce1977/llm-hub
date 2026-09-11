# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- Add HTTP method allowlist (`security.allowed_http_methods`, default GET/POST → 405 for others)
- Add regex path allowlist (`security.path_allowlist`, 403 for non-matching paths)
- Add in-memory token-bucket rate limiter (`security.rate_limit`, 429 when over budget)
- Add defensive response headers on every response (nosniff, X-Frame-Options, CSP, etc.)
- Document new hardening options in README and README_CN

### Changed

- Dockerfile HEALTHCHECK interval changed from 30s to 1h (passive gateway needs less probing)

### Added (Rerank)

- Add `keep_alive` config for Ollama reranker VRAM management (0 = immediate unload, "5m" = keep warm, -1 = server default)
- Add `RateLimitConfig` model with `enabled`, `per_minute`, `burst`, `by` (ip/key)
- Default rerank `options.num_ctx` set to 8192, `keep_alive` set to "3m"
- Add `Modelfile.reranker` for low-VRAM reranker variant (8192 ctx ≈ 4 GB vs 40960 ctx ≈ 9.3 GB)
- Add `Ranker.md` comprehensive reranker configuration guide
- Add `tests/verify_security.py` security verification test suite (15 tests)

### Documentation

- AGENTS.md: document new security features
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
