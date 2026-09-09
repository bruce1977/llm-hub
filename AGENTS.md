# LLM Hub - Agent Guidelines

## Project Overview

LLM Hub is a FastAPI-based unified gateway that aggregates multiple Ollama instances, LM Studio, and OpenAI-compatible backends into a single API endpoint. It provides:
- Multi-upstream routing by model name
- API key authentication
- Rerank API implementation
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
│   └── rerank.py     # Rerank API implementation (logprobs/embedding modes)
├── data/
│   └── config.json   # Runtime configuration (mounted into container)
├── tests/
│   ├── smoke_test.py # Comprehensive mock tests
│   ├── demo.py       # Demo script (requires real Ollama)
│   └── TESTCASES.md  # Test cases documentation with examples
├── .vscode/
│   ├── settings.json # VSCode editor settings
│   ├── extensions.json # Recommended extensions
│   └── launch.json   # Debug configurations
├── Dockerfile        # Container build (python:3.12-slim)
├── pyproject.toml    # Python tooling config (ruff, mypy, pytest)
├── API.md            # API documentation with all endpoints
├── workflow.md       # Development workflow with diagrams
├── DEPLOY.md         # Docker deployment guide
└── AGENTS.md         # This file
```

## Code Conventions

### Python Style
- Python 3.12+
- Use type hints consistently
- Follow PEP 8 style guidelines
- Use `from __future__ import annotations` for forward references
- Prefer `Optional[T]` over `T | None` for compatibility
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

# With real Ollama (requires local instance on port 11434)
E2E=1 python tests/smoke_test.py

# Demo script (requires real Ollama)
python tests/demo.py
```

### Test Structure
- Tests use `fastapi.testclient.TestClient`
- Mock upstreams simulate Ollama instances
- Tests cover: auth, routing, aliases, streaming, rerank, security
- No pytest framework - uses custom test runner

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
2. Copy `.example.env` to `.env`
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
- CORS configuration
- Request body size limits
- Weak key detection at startup

### Rerank Modes
- **logprobs**: Generative scoring using token probabilities
- **embedding**: Vector cosine similarity scoring
- Per-model mode override via `model_modes` config

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