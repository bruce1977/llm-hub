# LLM Hub - Agent Guidelines

## Project Overview

LLM Hub is a FastAPI-based unified gateway that aggregates multiple Ollama instances, LM Studio, and OpenAI-compatible backends into a single API endpoint. It provides:
- Multi-upstream routing by model name
- API key authentication
- Rerank API implementation
- Web search pass-through (OpenCode API Gateway upstream)
- Model aliasing and parameter injection
- Config hot reload

## Architecture

```
llm-hub/
├── app/
│   ├── main.py       # FastAPI application, lifespan, special endpoints
│   ├── config.py     # Config models, loading, hot reload, alias resolution
│   ├── auth.py       # API key authentication, security helpers
│   ├── proxy.py      # Reverse proxy, streaming pass-through
│   ├── rerank.py     # Rerank API implementation (logprobs/embedding modes)
│   ├── systemone.py  # System One decision API (proxied to laya-server or Ollama 0.40+)
│   ├── websearch.py  # Web search API (proxied to an OpenCode API Gateway upstream)
│   └── static/swagger/  # Vendored Swagger UI assets (offline /docs)
├── data/
│   ├── config.json          # Runtime configuration (mounted into container)
│   └── example.config.json  # Config template copied to config.json
├── tests/
│   ├── smoke_test.py        # Comprehensive mock tests (104)
│   ├── test_gateway_new.py  # Aggregation / systemone / web search / /docs schema tests (59)
│   ├── verify_security.py   # Security control tests (15)
│   ├── test_api.py          # E2E tests against a live server (28)
│   └── demo.py              # Demo script (requires real Ollama)
├── docs/
│   ├── API.md               # API documentation with all endpoints
│   ├── TESTCASES.md         # Test cases documentation with examples
│   ├── DEPLOY.md            # Docker deployment guide
│   ├── Ranker.md            # Reranker configuration guide
│   └── workflow.md          # Development workflow with diagrams
├── scripts/
│   ├── docker-build.bat     # Build the image (latest + version tag)
│   └── docker-publish.bat   # Build then push both tags to Docker Hub
├── .vscode/
│   ├── settings.json # VSCode editor settings
│   ├── extensions.json # Recommended extensions
│   └── launch.json   # Debug configurations
├── Dockerfile        # Container build (python:3.12-slim)
├── pyproject.toml    # Python tooling config (ruff, mypy, pytest)
├── requirements.txt  # Runtime dependencies
├── README.md / README_CN.md
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
- Environment variables for secrets (never in config.json)
- Config validation at startup

## Testing

### Running Tests
```bash
# Mock tests only (no external dependencies)
python tests/smoke_test.py

# Gateway features: aggregation, systemone/websearch forwarding, /docs schema (no external deps)
python tests/test_gateway_new.py

# Security verification tests (no external dependencies)
python tests/verify_security.py

# E2E API tests (requires running server + real Ollama on port 11434)
# 1. Start the server using the current directory's data/config.json:
PYTHONPATH=app CONFIG_PATH=./data/config.json \
  GATEWAY_API_KEYS=sk-test-example-key \
  python -m uvicorn main:app --host 127.0.0.1 --port 8000

# 2. In another terminal, run the E2E tests:
python tests/test_api.py --base-url http://127.0.0.1:8000 --api-key sk-test-example-key

# Demo script (requires real Ollama)
python tests/demo.py
```

**Important:** E2E tests always use `data/config.json` from the project root (set via `CONFIG_PATH=./data/config.json`). This config points to the local Ollama instance at `127.0.0.1:11434`. Never hardcode paths in test commands.

### Test Structure
- `smoke_test.py` — 104 mock tests (auth, routing, aliases, streaming, rerank, security, `/` system info, upstream types), no external deps
- `test_gateway_new.py` — 59 tests for aggregation, systemone + web search forwarding and the `/docs` schema (auth schemes + inline `X-API-Key` input)
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
# Install dependencies
pip install -r requirements.txt

# Run with hot reload
CONFIG_PATH=./data/config.json python -m uvicorn app.main:app --reload --port 8000
```

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
1. Copy `data/example.config.json` to `data/config.json`
2. Copy `.env.example` to `.env`
3. Edit config with real upstream URLs
4. Set API keys in `.env` file

## Key Implementation Details

### Routing Logic
- Exact model match first
- Prefix wildcards (e.g., `qwen3:*`) for pattern matching
- Priority-based routing when same model exists on multiple upstreams
- Alias resolution before routing
- Fallback to default upstream when `routing.strict=false`

### Streaming
- NDJSON for Ollama native API (`/api/chat`)
- SSE for OpenAI-compatible API (`/v1/chat/completions`)
- Pass-through without modification

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

### Rerank Modes
- **logprobs**: Generative scoring using token probabilities
- **embedding**: Vector cosine similarity scoring
- Per-model mode override via `model_modes` config

### Web Search (`POST /v1/tools/web-search`)
- Served by `websearch.py`, which forwards to the upstream named in `websearch.upstream`
  (or the first `type: "opencode"` upstream) at `websearch.path`
- Request and response are passed through unchanged; `model` is **never** resolved through
  the gateway's aliases, and nothing is injected
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
  `build_default_config()` emits `"local"` and the shipped `data/*.config.json` use a real
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

## Common Tasks

### Adding New Endpoint
1. Add route handler in `main.py`
2. Update `gateway()` catch-all if needed
3. Add tests in `smoke_test.py`
4. Update documentation

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

## Dependencies

### Production
- `fastapi>=0.115.0`
- `uvicorn[standard]>=0.30.0`
- `httpx>=0.27.0`
- `pydantic>=2.7.0`

### Development
- No additional dev dependencies currently
- Consider adding: `pytest`, `black`, `ruff`, `mypy`

## Performance Considerations

- Connection pooling via `httpx.AsyncClient`
- Model list caching (60s TTL)
- Concurrency limits for rerank operations
- Config hot reload without restart
- Streaming pass-through without buffering