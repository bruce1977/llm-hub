# LLM Hub

Aggregate multiple Ollama instances — plus LM Studio and any OpenAI-compatible backend —
into a single unified API endpoint: route by model automatically, apply uniform authentication,
fill in Ollama's missing Rerank API, and inject request parameters through aliases (e.g. disabling
thinking mode).

## Features

| Capability | Description |
|------|------|
| **Multi-upstream routing** | Route by the `model` field in the request body to the matching backend instance |
| **API key auth** | Multiple keys, constant-time comparison, `Bearer` / `X-API-Key` compatible |
| **Rerank API** | `/v1/rerank` with generative (`logprobs`) and vector (`embedding`) scoring |
| **Unified model list** | `/v1/models` aggregates models & aliases from all upstreams instead of proxying to one instance |
| **Alias + param injection** | `qwen3.5:4b-nothink` → forwards `qwen3.5:4b` and force-injects `think: false` |
| **Streaming pass-through** | NDJSON (`/api/chat`) and SSE (`/v1/chat/completions`) forwarded as-is |
| **Config hot reload** | `config.json` changes take effect within ~1s, no restart required |
| **Docker volume mount** | Config file mounted via `/data` |
| **Routing priority** | When the same model appears in multiple upstreams, the lowest `priority` wins |

## Directory structure

```
llm-hub/
├── Dockerfile
├── requirements.txt
├── app/
│   ├── main.py       # FastAPI entrypoint & special endpoints
│   ├── config.py     # Config model, loading, hot reload, alias resolution
│   ├── auth.py       # API key authentication
│   ├── proxy.py      # Reverse proxy & streaming pass-through
│   └── rerank.py     # Rerank API implementation
└── data/
    └── config.json   # Config file (mounted into the container at /data)
```

## Quick start

### Option 1: Pull from Docker Hub (Recommended)

```bash
# 1. Pull the latest image
docker pull bruce1977/llm-hub:latest

# 2. Prepare config & secrets
mkdir -p llm-hub/data
cp data/example.config.json llm-hub/data/config.json
cp .example.env llm-hub/.env
# Edit llm-hub/data/config.json and llm-hub/.env with your settings

# 3. Run
docker run -d --name llm-hub \
  -p 8888:8000 \
  -v $(pwd)/llm-hub/data:/data \
  --env-file llm-hub/.env \
  --restart unless-stopped \
  bruce1977/llm-hub:latest
```

### Option 2: Build from source

Deploy with `docker build` + `docker run` (no compose required). The config directory is
volume-mounted and secrets are injected via `--env-file`; neither is baked into the image.

```bash
cd llm-hub

# 1. Prepare config & secrets (config.json in the mount dir, keys in .env)
#    notepad data/config.json

# 2. Build the image
docker build -t llm-hub:latest .

# 3. Run: mount the config dir at /data, source at /app, inject keys from .env
#    (below assumes the config lives in ${target folder} and maps to port 8888)
docker run -d --name llm-hub \
  -p 8888:8000 \
  --add-host host.docker.internal:host-gateway \
  -v ${target folder}:/data \
  -v ${target folder}/app:/app \
  --env-file ${target folder}/.env \
  llm-hub:latest

# 4. Verify
curl http://localhost:8888/health
```

> **Secrets come from `.env`**: `--env-file` reads `GATEWAY_API_KEYS` and injects it into the
> container, so `config.json` never needs to hold any key. Keep `.env` private and out of version
> control; change keys in this one place only.
> On Windows, `--env-file` / `-v` paths must use a drive-letter form (`${target folder}`), not
> `/d/...` (Docker cannot find the file).

Logs: `docker logs -f llm-hub`

After changing config (e.g. `priority`, `upstreams`) you don't need to rebuild — just run
`docker restart llm-hub`. If only `config.json` changed, it hot-reloads within ~1s anyway.

## Config templates (example files)

The repo ships two templates with **no real information**; copy and fill them in:

| Template | Copy to | Description |
|------|--------|------|
| `data/example.config.json` | `data/config.json` | Sample config with full `upstreams` / `aliases` / `rerank` / `security` fields, including a `priority` example |
| `.example.env` | `.env` | Sample secrets; just replace `GATEWAY_API_KEYS` with your own random strings (≥16 chars) |

```bash
cp data/example.config.json data/config.json
cp .example.env .env
# then edit config.json with real upstream base_urls and .env with real keys
```

> `config.json` and `.env` are gitignored; the templates `example.config.json` / `.example.env`
> stay in the repo so others can reuse them.
> The deployment directory (e.g. `${target folder}`) also holds these two templates; usage is identical.

## Building the image (docker build)

The repo root has a `Dockerfile` (based on `python:3.12-slim`). You can build explicitly with `docker build` — handy for custom tags, specific
platforms, or pushing to a registry.

```bash
cd llm-hub

# Build & tag
docker build -t llm-hub:1.0.0 -t llm-hub:latest .

# When deploying to Linux/amd64, pin the platform to avoid pulling an ARM image
docker build --platform linux/amd64 -t llm-hub:1.0.0 .
```

After building, run directly with `docker run` and a mounted config volume:

```bash
docker run -d --name llm-hub \
  -p 8888:8000 \
  --add-host host.docker.internal:host-gateway \
  -v ${target folder}:/data \
  -v ${target folder}/app:/app \
  --env-file ${target folder}/.env \
  llm-hub:1.0.0
```

Notes:

- `-v ${target folder}:/data`: mounts the host `config.json` into the container `/data`, matching
  the default `CONFIG_PATH` (Windows uses drive-letter paths)
- `-v ${target folder}/app:/app` (optional): mounts the source. Every `*.py` sits directly in `/app`
  (`main.py`, `config.py`, `auth.py`, `proxy.py`, `rerank.py`); after editing code, just restart the
  container — no rebuild needed
- `--add-host host.docker.internal:host-gateway`: lets the container reach the host Ollama on Linux;
  Docker Desktop on Windows / macOS already resolves this and can be omitted
- `--env-file ${target folder}/.env`: injects keys from an env file instead of writing them in
  `config.json` (see Security below)

Push to a registry (optional):

```bash
docker tag llm-hub:1.0.0 registry.example.com/llm-hub:1.0.0
docker push registry.example.com/llm-hub:1.0.0
```

## Configuration reference (config.json)

### `server` — the gateway itself

| Field | Default | Description |
|------|------|------|
| `host` / `port` | `0.0.0.0` / `8000` | Listen address & port |
| `log_level` | `info` | `debug` / `info` / `warning` / `error` |
| `request_timeout` | `600` | Default upstream timeout (seconds) |
| `max_concurrency` | `64` | HTTP connection-pool cap |

### `auth` — authentication

```json
"auth": {
  "enabled": true,
  "api_keys": [],
  "allow_anonymous_health": true
}
```

- `api_keys`: multiple keys; any single one passing is enough. **Recommended to leave empty** and
  provide keys via the environment instead
- Key source precedence: `config.json` `api_keys` → `GATEWAY_API_KEYS` env var →
  `GATEWAY_API_KEYS_FILE`; all three merge and de-duplicate
- `GATEWAY_API_KEYS`: `GATEWAY_API_KEYS=key1,key2` (comma / newline separated)
- `GATEWAY_API_KEYS_FILE`: path to a file holding the keys, ideal for Docker / K8s secrets (won't
  leak via `docker inspect`)
- ⚠️ With auth enabled (`enabled: true`) and zero keys, the gateway **refuses to start** and prints
  the configuration options, avoiding an accidentally unauthenticated service
- `allow_anonymous_health`: let `/health` skip auth (keep `true` or the container health check
  fails); whether `/docs` is public is controlled by `security.allow_docs` (default `false`)
- `enabled: false` temporarily disables auth

### `upstreams` — backend instances (Ollama / LM Studio)

```json
"upstreams": [
  {
    "name": "local-main",
    "type": "ollama",
    "base_url": "http://host.docker.internal:11434",
    "models": ["qwen3:8b", "qwen3.5:4b", "bge-m3:latest"],
    "timeout": 600,
    "description": "Main local instance",
    "headers": {}
  }
]
```

- `type`: backend flavor; decides which endpoints are used for model discovery & health probes.
  Default `ollama`
  - `ollama`: model list `/api/tags`, health check `/api/version`
  - `lmstudio`: OpenAI-compatible, model list `/v1/models`, health check `/v1/models`
- `models`: supports **prefix wildcards** — `"qwen3:*"` matches `qwen3:4b`, `qwen3:8b`, `qwen3:14b`
- `model_aliases`: maps a verbose upstream id to a short name (see the LM Studio section below)
- `timeout`: overrides the global timeout; raise it for large models
- `headers`: optional extra headers added when forwarding (e.g. when the upstream has its own auth)
- `priority`: routing priority (integer, **default `0`**). When the same model name is declared by
  several upstreams, the gateway picks the one with the **smallest** `priority`; ties keep the
  declaration order in `upstreams`. Useful for mounting the same model on a GPU instance and an
  iGPU / CPU instance so the high-priority one serves first and the rest act as fallback

#### Routing when the same model matches several upstreams

The final upstream is decided in this order:

1. If the request is an **alias** (a key in `aliases`), its `upstream` wins; if the alias doesn't
   specify `upstream`, go to step 2
2. Collect all upstreams whose `models` match the name (exact name or prefix wildcard `qwen3:*`)
3. Among those candidates pick the **smallest** `priority`; ties pick the one **earlier** in `upstreams`
4. If nothing matches and `routing.strict=false`, fall back to `default_upstream`

> Example: model `qwen3:8b` appears on both `gpu` (priority `0`) and `cpu` (priority `10`), so
> requests always hit `gpu`; set `gpu`'s `priority` to `20` to make `cpu` win.
> Note: `/v1/models` also reports the winning `upstream` for that model.

### `routing` — routing policy

- `strict: false` (default): when a model matches no instance, forward to `default_upstream`
- `strict: true`: undeclared models return 404, avoiding accidentally hitting the default instance

### `aliases` — aliases & parameter injection

```json
"aliases": {
  "qwen3.5:4b-nothink": {
    "model": "qwen3.5:4b",
    "params": { "think": false }
  }
}
```

Requesting `qwen3.5:4b-nothink` replaces `model` with `qwen3.5:4b` and **force-overwrites** the
request body with the fields in `params` (the alias wins on name collisions).

## Integrating LM Studio

LM Studio exposes an OpenAI-compatible API, and its model ids usually carry a publisher prefix and
are long (e.g. `qwen/qwen3.5-9b`). The gateway supports it natively and offers a "short-name
mapping" to collapse it to `qwen3.5:9b`.

**1. Make sure LM Studio's local API server is running**

Load a model in LM Studio and start the Local Server (default port `1234`), then confirm:

```bash
curl http://127.0.0.1:1234/v1/models
```

**2. Register it as an upstream in `config.json`**

```json
{
  "name": "lmstudio",
  "type": "lmstudio",
  "base_url": "http://host.docker.internal:1234",
  "models": [],
  "model_aliases": {
    "qwen3.5:9b": "qwen/qwen3.5-9b"
  },
  "timeout": 600
}
```

- `type: "lmstudio"`: the gateway discovers models & probes liveness via `/v1/models` (Ollama uses
  `/api/tags`, `/api/version`)
- `model_aliases`: maps "exposed short name → LM Studio real id"; the key is the public name, the
  value is the upstream's real id

**3. Access via the short name**

```bash
curl http://<gateway>:8000/v1/chat/completions \
  -H "Authorization: Bearer <your API key>" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.5:9b","messages":[{"role":"user","content":"hi"}]}'
```

Upon receiving `qwen3.5:9b`, the gateway swaps the request's `model` to `qwen/qwen3.5-9b` before
forwarding to LM Studio — the client is none the wiser. `/v1/models` also expands these aliases, so
clients like Dify / RAGFlow / WorkBuddy see a tidy list of short names.

> To reach the **host's** LM Studio from inside the container, use `host.docker.internal:1234`;
> if LM Studio runs on another machine, use that machine's LAN IP.

`params` accepts any parameter Ollama supports, e.g.:

```json
"fast": {
  "model": "qwen3:4b",
  "upstream": "local-main",
  "params": { "think": false, "num_ctx": 8192, "temperature": 0.2 }
}
```

### `rerank` — rerank configuration

```json
"rerank": {
  "enabled": true,
  "mode": "logprobs",
  "models": ["qwen3-reranker:4b"],
  "normalize": false,
  "max_concurrency": 8,
  "return_documents": true,
  "model_modes": { "bge-m3:latest": "embedding" }
}
```

Two scoring modes:

| Mode | For | How it works |
|------|----------|------|
| `logprobs` | Generative rerankers like Qwen3-Reranker | Build a yes/no binary prompt, softmax over the first token's logprobs |
| `embedding` | Vector models like bge-m3 | Compute cosine similarity of the query and document vectors |

`mode` is the global default. When mixing reranker types (e.g. generative `qwen3-reranker:4b` +
vector `bge-m3:latest`), use `model_modes` to set a per-model mode, e.g.
`{"bge-m3:latest": "embedding"}`.

## API usage

The examples below assume the gateway runs at `http://localhost:8000`.

### Authentication

Either header works:

```bash
-H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70"
-H "X-API-Key: sk-gateway-9f2c8a1b4d5e6f70"
```

### Error responses

All error responses follow a consistent format:

```json
{
  "detail": "Error message describing the problem"
}
```

| Status Code | Error | Description |
|-------------|-------|-------------|
| `400` | Bad Request | Invalid JSON, missing required fields, or malformed request |
| `401` | Unauthorized | Missing API key (when auth is enabled) |
| `403` | Forbidden | Invalid API key, IP not allowed, or endpoint blocked |
| `404` | Not Found | Unknown endpoint or model not found on any upstream |
| `405` | Method Not Allowed | HTTP method not supported (e.g., GET on `/v1/rerank`) |
| `413` | Request Entity Too Large | Request body exceeds `security.max_body_bytes` (default 32MB) |
| `422` | Unprocessable Entity | Request body is valid JSON but fails validation |
| `502` | Bad Gateway | Upstream unreachable or returned an error |
| `504` | Gateway Timeout | Upstream did not respond within the configured timeout |

**Examples:**

```bash
# Missing API key
curl http://localhost:8000/v1/models
# 401 {"detail":"Missing API key. Provide it via 'Authorization: Bearer <key>' or 'X-API-Key: <key>' header."}

# Invalid API key
curl http://localhost:8000/v1/models -H "Authorization: Bearer wrong-key"
# 403 {"detail":"Invalid API key."}

# Model not found (when routing.strict=true)
curl http://localhost:8000/api/chat \
  -H "Authorization: Bearer sk-key" \
  -d '{"model":"unknown-model","messages":[{"role":"user","content":"hi"}]}'
# 404 {"detail":"No upstream configured for model 'unknown-model'."}

# Blocked admin endpoint
curl http://localhost:8000/api/delete \
  -H "Authorization: Bearer sk-key" \
  -d '{"name":"model"}'
# 403 {"detail":"Endpoint '/api/delete' is disabled on this public gateway."}
```

### Forward requests (fully Ollama-compatible)

```bash
# Native API — auto-routes to the instance that has this model
curl http://localhost:8000/api/chat \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.6:35b-pruned","messages":[{"role":"user","content":"hello"}],"stream":false}'

# OpenAI-compatible endpoint
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"hello"}]}'

# Embeddings
curl http://localhost:8000/api/embed \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  -H "Content-Type: application/json" \
  -d '{"model":"bge-m3:latest","input":["hello world"]}'
```

### Alias to disable thinking

```bash
# Plain forward, keeps the default `think`
curl .../api/chat -d '{"model":"qwen3.5:4b","messages":[...]}'

# Hits the alias; the gateway auto-injects think:false
curl .../api/chat -d '{"model":"qwen3.5:4b-nothink","messages":[...]}'
```

Both requests hit the same model, but the latter won't emit the `<think>` reasoning.

### Unified model list

```bash
curl http://localhost:8000/v1/models \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70"
```

```json
{
  "object": "list",
  "data": [
    { "id": "qwen3:8b", "object": "model", "owned_by": "ollama",
      "upstream": "local-main", "type": "upstream", "wildcard": false },
    { "id": "qwen3.5:4b-nothink", "object": "model", "owned_by": "ollama",
      "upstream": "local-main", "type": "alias", "target": "qwen3.5:4b" }
  ]
}
```

`GET /api/tags` returns the same aggregated list in Ollama's native format, so Ollama-native
clients / Open WebUI can treat the gateway as a single instance.

### Rerank

```bash
curl http://localhost:8000/v1/rerank \
  -H "Authorization: Bearer sk-gateway-9f2c8a1b4d5e6f70" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-reranker:4b",
    "query": "How do you brew pour-over coffee?",
    "documents": [
      "Pour-over coffee needs water around 90°C",
      "Python is a programming language",
      "Grind size affects extraction speed"
    ],
    "top_n": 3,
    "return_documents": true
  }'
```

Response:

```json
{
  "id": "rerank-9f2c8a1b4d5e6f70",
  "model": "qwen3-reranker:4b",
  "object": "list",
  "results": [
    { "index": 0, "relevance_score": 0.91, "rank": 0, "document": { "text": "Pour-over coffee needs..." } },
    { "index": 2, "relevance_score": 0.63, "rank": 1, "document": { "text": "Grind size affects..." } },
    { "index": 1, "relevance_score": 0.02, "rank": 2, "document": { "text": "Python is a..." } }
  ],
  "usage": { "total_tokens": 0, "prompt_tokens": 0, "rerank_count": 3 }
}
```

Request fields: `model`, `query`, `documents` (string array or `{text:...}` object array), `top_n`,
`return_documents`, `instruction` (overrides the default instruction).

### Health check

```bash
curl http://localhost:8000/health
```

Returns each upstream's connectivity, model count and Ollama version. Returns 503 if any upstream
is unreachable. **No API key required** (when `auth.allow_anonymous_health` is `true`, which is the default).

## Running a second Ollama instance

To run multiple Ollama instances on one machine, give each a distinct port and data directory:

```powershell
# Windows PowerShell
$env:OLLAMA_HOST = "0.0.0.0:11435"
$env:OLLAMA_MODELS = "D:\path\to\models"
ollama serve
```

```bash
# Linux / macOS
OLLAMA_HOST=0.0.0.0:11435 OLLAMA_MODELS=/data/ollama-big ollama serve
```

Then point `base_url` at `http://host.docker.internal:11435` in `config.json`.

## FAQ

**Can't reach the host's Ollama from inside the container?**

- Docker Desktop on Windows / macOS: use `host.docker.internal` directly (already in compose)
- Linux: need `extra_hosts: ["host.docker.internal:host-gateway"]` (already in compose)
- If the gateway is on a separate machine, use that machine's LAN IP, e.g. `http://<host-ip>:11434`

**Health check keeps failing?**

If you set `allow_anonymous_health` to `false`, `/health` requires auth and the container's built-in
HEALTHCHECK fails with 401. Keep it `true`.

**`think: false` has no effect?**

Confirm your Ollama version supports the `think` parameter (0.9+). On older versions, use
`{"stop": ["<think>"]}` in the alias params as a workaround.

**Rerank scores are all 0?**

The logprobs couldn't be extracted from the upstream response. Set `log_level` to `debug` to inspect
the raw response, or switch to `embedding` mode (requires a vector model).

**Config changes not taking effect?**

Hot reload depends on the file's mtime. Some editors or network mounts (NFS / SMB) may not update
mtime; run `docker restart llm-hub` in that case.

**Tool calls return 400 / `cannot unmarshal object into ... arguments of type string`?**

Some OpenAI-compatible clients send `tool_calls[].function.arguments` as a JSON **object** (e.g.
`{"path":"main.py"}`) instead of the JSON **string** OpenAI requires when streaming tool-call
assembly. Ollama's gin parser rejects this with a 400. The gateway re-serializes the object / array
back into a string before forwarding, so **routing the client through the gateway (rather than
directly to Ollama:11434) avoids it**. If the client talks to Ollama directly, make sure its SDK
always sends `arguments` as a string.

## Security (must-read for public exposure)

The gateway is designed as "untrusted public network" by default. The `security` section consolidates
the hardening options:

| Field | Default | Description |
|------|------|------|
| `admin_endpoints` | `deny` | `deny` blocks pull/push/create/delete/copy/blobs; `readonly` also allows `pull`; `allow` forwards everything (internal only) |
| `blocked_paths` | `[]` | Extra path prefixes to block, e.g. `["api/show"]` or `["api/*"]` |
| `allow_docs` | `false` | Whether to expose `/docs`, `/redoc`, `/openapi.json` (keep off publicly) |
| `cors_origins` | `[]` | Explicitly allowed cross-origin sources; empty means no CORS headers at all (no more `*`) |
| `max_body_bytes` | `33554432` | Request-body cap (32MB); returns 413 above it, preventing oversized bodies |
| `allow_query_api_key` | `false` | Whether to accept `?api_key=` query param (off by default, avoids keys in logs / browser history) |
| `expose_health_details` | `false` | Whether `/health` echoes `config_path` and upstream `base_url` (redacted by default) |
| `fail_on_weak_keys` | `true` | Refuse to start when weak / placeholder keys are detected |
| `allowed_client_ips` | `[]` | Client IP allowlist (CIDR supported, e.g. `10.0.0.0/8`); empty = unrestricted |

### Keep secrets off disk (recommended)

Leaving `api_keys` in plaintext in `config.json` writes keys to disk. A safer approach is injecting
them at runtime:

```bash
# Container / process environment; comma or newline separated
export GATEWAY_API_KEYS="sk-gateway-9f2c8a1b4d5e6f70,sk-gateway-2b3c4d5e6f7a8b9c"
```

The gateway merges environment keys into `auth.api_keys` at startup, so `config.json` can stay `[]`
or omit it entirely.

### Public-deployment checklist

- [ ] Keep `admin_endpoints` at `deny` (or at least `readonly`); never `allow`
- [ ] All `api_keys` ≥ 16 random chars, or inject via `GATEWAY_API_KEYS`
- [ ] A reverse proxy (Nginx / Caddy) in front terminates TLS and forwards `X-Forwarded-For`
- [ ] To restrict origins, set `allowed_client_ips` to trusted ranges
- [ ] Keep `allow_docs` at `false`
- [ ] Don't expose upstream ports like 11434 / 11435 — only expose the gateway's 8000

## Local development (no Docker)

### Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Prepare config
cp data/example.config.json data/config.json
# Edit data/config.json with your upstream URLs

# 3. Set API keys (required, or add to auth.api_keys in config.json)
export GATEWAY_API_KEYS="${your_secret_api_key}"

# 4. Run the server
cd app
python -m uvicorn main:app --reload --port 8000
```

### Windows PowerShell

```powershell
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set environment variables (required)
$env:CONFIG_PATH="D:\workspace\github\llm-hub\data\config.json"
$env:GATEWAY_API_KEYS="${your_secret_api_key}"

# 3. Run the server
cd app
python -m uvicorn main:app --reload --port 8000
```

### Run with specific config

```bash
CONFIG_PATH=./data/config.json GATEWAY_API_KEYS="${your_secret_api_key}" python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

```powershell
# Windows PowerShell
$env:CONFIG_PATH="D:\workspace\github\llm-hub\data\config.json"
$env:GATEWAY_API_KEYS="${your_secret_api_key}"
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### Run in background (Linux/macOS)

```bash
nohup python -m uvicorn main:app --host 0.0.0.0 --port 8000 > llm-hub.log 2>&1 &
```

### Run as Windows service (using NSSM)

```powershell
# Install NSSM: choco install nssm
nssm install LLMHub "C:\path\to\python.exe" "-m" "uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8000"
nssm set LLMHub AppDirectory "D:\workspace\github\llm-hub\app"
nssm set LLMHub AppEnvironmentExtra "CONFIG_PATH=D:\workspace\github\llm-hub\data\config.json"
nssm start LLMHub
```

Interactive docs: <http://localhost:8000/docs> (requires `security.allow_docs: true`)
