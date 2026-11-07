# LLM Hub — usage guide

The complete documentation: configuration reference, API endpoints, web console, deployment and
security. The short version — what the project is, screenshots and a 60-second start — lives in
[README.md](../README.md). Chinese version: [USAGE_CN.md](USAGE_CN.md).
Endpoint reference: [API.md](API.md). Deployment: [DEPLOY.md](DEPLOY.md).

**Contents** ·
[Features](#features) ·
[Directory structure](#directory-structure) ·
[Quick start](#quick-start) ·
[Config templates](#config-templates-example-files) ·
[Building the image](#building-the-image-docker-build) ·
[Configuration reference](#configuration-reference-configyaml) ·
[Integrating LM Studio](#integrating-lm-studio) ·
[Web console](#web-console) ·
[API usage](#api-usage) ·
[Second Ollama instance](#running-a-second-ollama-instance) ·
[FAQ](#faq) ·
[Security](#security-must-read-for-public-exposure) ·
[Local development](#local-development-no-docker)

## Features

| Capability | Description |
|------|------|
| **Multi-upstream routing** | Route by the `model` field in the request body to the matching backend instance |
| **API key auth** | Multiple keys, `Bearer` / `X-API-Key` compatible; every call is checked against the SHA-256 hashes held in memory (`api_keys` in SQLite), so no plaintext is ever stored or compared |
| **Rerank API** | `/v1/rerank` with generative (`logprobs`) and vector (`embedding`) scoring |
| **Web search** | `POST /v1/tools/web-search` proxies keyword search to an OpenCode API Gateway upstream and returns the retrieved passages |
| **Anthropic Messages** | `POST /v1/messages` (plus the singular alias `/v1/message`): the Anthropic request is mapped onto the upstream chat format (Ollama NDJSON or OpenAI SSE) and the answer is wrapped back into an Anthropic envelope (`content` blocks, `stop_reason`, `usage`); streaming is re-sequenced as `message_start → content_block_delta → message_stop`, not passed through |
| **Unified model list** | `/v1/models` aggregates models & aliases from all upstreams instead of proxying to one instance |
| **Alias + param injection** | `qwen3.5:4b-nothink` → forwards `qwen3.5:4b` and force-injects `think: false`. Every entry under `aliases:` is a model of its own (the console lists it as a separate row); `upstreams[].model_aliases` only renames |
| **Streaming pass-through** | NDJSON (`/api/chat`, `/api/generate`) and SSE (`/v1/chat/completions`) forwarded as-is |
| **Management API** | Read-only `/v1/management/*`: upstream & VRAM overview with today's request and token totals, the models `config.yaml` declares per upstream (`model_aliases` renames applied, a root `aliases:` entry listed as its own model, prefix wildcards expanded through the upstream's model list) plus runtime device/memory, config projections (Rerank / System One / Web Search), request log, timeseries and distribution stats (SQLite), the per-model speed test (start / poll / history) and the per-endpoint API tester (usable models per family + live execution); authenticated with the console session cookie or a gateway API key |
| **Web console** | Vue 3 + TypeScript + Element Plus + ECharts single-page app served by the gateway itself at `/admin` (no node in the image): overview, upstreams, the configured model inventory (display renames, derived models, wildcards expanded, running device/memory), request details and charts, a per-model speed test, an API tester popup behind a **Test** button on every documented endpoint, and API key management (mint / revoke, masked list). Apart from the Settings page (the one editor that saves `config.yaml`), the views offer no other configuration writes |
| **API key management** | Caller keys are rows of the `api_keys` table in `llmhub.db`, stored as `sha256(<plaintext>)` plus a display mask — the database never holds a secret. The console mints (`sk-hub-…`, 47 characters) and revokes them, showing a new plaintext exactly once. `GATEWAY_API_KEYS` / `data/.keys` only seed an empty table on first start |
| **Model speed test** | `测速` on **Models · LLM** runs the same prompt N times through the gateway and reports TTFT, decode tok/s and prefill tok/s — taken from the server's own timings (`eval_duration` / `prompt_eval_duration` / `predicted_ms`) when the upstream provides them, falling back to client-side measurement (flagged `timing_source`) otherwise. The latest 3 runs per upstream + model stay in SQLite and show up on hover |
| **API tester** | Every `API usage` tab carries a **`Test` button** at the right end of each endpoint's *Request parameters* header, opening the tester in a popup (`/api/generate`, `/v1/chat/completions`, `/v1/messages`, `/api/embed`, `/v1/embeddings`, `/v1/rerank`, `/v1/systemone`, `/v1/tools/web-search`): a model picker narrowed by `api_models` (the two native `/api` testers list models of `type: ollama` upstreams only) that lists **names/aliases only**, one input per documented parameter **pre-filled with its default**, a request-body preview, then the status, timings, the model the gateway really routed to with the parameters an alias injected, and the answer pulled out of JSON / NDJSON / SSE. The call runs server-side through the gateway's own service, so alias injection and the request log behave exactly as for an external client, and any extra API key appears only as a read-only `••••••` |
| **Trilingual console** | Simplified Chinese (source), Traditional Chinese and English, switched from the account menu and remembered per browser via `localStorage`; a first visit guesses from `navigator.language` (`zh-TW` / `zh-HK` / `zh-MO` / `zh-Hant` → Traditional, `en*` → English, else Simplified), and Element Plus' own widgets (date panels, pagination) follow the choice. Missing keys fail the build (`MessageSchema`) instead of rendering raw ids |
| **Config hot reload** | `config.yaml` changes take effect within ~1s, no restart required |
| **Docker volume mount** | Config file mounted via `/data` |
| **Routing priority** | When the same model appears in multiple upstreams, the lowest `priority` wins |

## Directory structure

```
llm-hub/
├── Dockerfile
├── requirements.txt
├── app/
│   ├── main.py       # FastAPI entrypoint & special endpoints (the /v1/messages routes live here)
│   ├── config.py     # Config model, loading, hot reload, alias resolution
│   ├── auth.py       # API key authentication
│   ├── admin_auth.py # Console login sessions (admin password, 12-hour cookie, login throttling)
│   ├── key_store.py  # Caller API keys in SQLite: sha256 hashes, masks, mint/revoke
│   ├── proxy.py      # Reverse proxy & streaming pass-through
│   ├── messages.py   # Anthropic Messages compatibility layer (request mapping, response & SSE rebuild)
│   ├── rerank.py     # Rerank API implementation
│   ├── systemone.py  # System One decision proxy (laya-server / Ollama)
│   ├── request_log.py # Request log (SQLite, async batched writes)
│   ├── bench.py      # Model speed test: TTFT / decode / prefill measurement
│   ├── bench_store.py # Speed-test history (shares llmhub.db, keeps 3 runs per model)
│   ├── api_test.py   # Engine behind the console's API testers (closed endpoint allowlist)
│   ├── management.py # Web console API (/v1/management: read-only views + speed test + API testers + API key management + Settings save)
│   ├── websearch.py  # Web search proxy (OpenCode API Gateway upstream)
│   └── web/          # Built console bundle (output of `npm run build`), served at /admin
├── web/
│   ├── src/          # Console frontend sources (Vue 3 + TypeScript + Vite + Element Plus + ECharts)
│   └── ...           # i18n messages under src/i18n/messages (zh-CN.ts is the source language, zh-TW.ts / en.ts mirror its keys)
├── docs/
│   ├── images/       # Images the README embeds: architecture, screenshots, WeChat QR
│   ├── screenshots/  # Full per-page console captures (en/ and zh/, shot by capture-live.js)
│   └── ...           # API.md / API_CN.md, DEPLOY.md / DEPLOY_CN.md, console implementation notes
├── tests/            # Standalone test scripts (smoke / gateway / management / security / live E2E)
└── data/
    └── config.yaml   # Config file (mounted into the container at /data)
```

## Quick start

### Option 1: Pull from Docker Hub (Recommended)

```bash
# 1. Pull the latest image
docker pull bruce1977/llm-hub:latest

# 2. Prepare config & secrets
mkdir -p llm-hub/data
cp data/config.example.yaml llm-hub/data/config.yaml
cp .env.example llm-hub/.env
# Edit llm-hub/data/config.yaml and llm-hub/.env with your settings

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

# 1. Prepare config & secrets (config.yaml in the mount dir, keys in .env)
#    notepad data/config.yaml

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

Web console: open `http://localhost:8888/admin/` and sign in as `admin` with the `ADMIN_PASSWORD`
set in `.env`. Without that variable the page reports the console as disabled and every
`/v1/management/*` call returns 503. The console itself ships inside the image at `app/web`
(the output of `npm run build`, next to the modules), so step 3 above - which mounts the host
`app` directory over `/app` - needs that copy to be built too, otherwise `/admin` answers 503.

> **Secrets come from `.env`**: `--env-file` reads `GATEWAY_API_KEYS` and injects it into the
> container, so `config.yaml` never needs to hold any key. Keep `.env` private and out of version
> control; change keys in this one place only.
> On Windows, `--env-file` / `-v` paths must use a drive-letter form (`${target folder}`), not
> `/d/...` (Docker cannot find the file).

Logs: `docker logs -f llm-hub`

After changing config (e.g. `priority`, `upstreams`) you don't need to rebuild — just run
`docker restart llm-hub`. If only `config.yaml` changed, it hot-reloads within ~1s anyway.

## Config templates (example files)

The repo ships two templates with **no real information**; copy and fill them in:

| Template | Copy to | Description |
|------|--------|------|
| `data/config.example.yaml` | `data/config.yaml` | Sample config with full `upstreams` / `aliases` / `rerank` / `security` fields, including a `priority` example |
| `.env.example` | `.env` | Sample secrets; just replace `GATEWAY_API_KEYS` with your own random strings (≥16 chars). These values seed the empty `api_keys` table on the first start — afterwards mint keys at `/admin` instead of editing this file |

```bash
cp data/config.example.yaml data/config.yaml
cp .env.example .env
# then edit config.yaml with real upstream base_urls and .env with real keys
```

> `config.yaml` and `.env` are gitignored; the templates `config.example.yaml` / `.env.example`
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

- `-v ${target folder}:/data`: mounts the host `config.yaml` into the container `/data`, matching
  the default `CONFIG_PATH` (Windows uses drive-letter paths)
- `-v ${target folder}/app:/app` (optional): mounts the source. Every `*.py` sits directly in `/app`
  (`main.py`, `config.py`, `auth.py`, `proxy.py`, `rerank.py`); after editing code, just restart the
  container — no rebuild needed
- `--add-host host.docker.internal:host-gateway`: lets the container reach the host Ollama on Linux;
  Docker Desktop on Windows / macOS already resolves this and can be omitted
- `--env-file ${target folder}/.env`: injects keys from an env file instead of writing them in
  `config.yaml` (see Security below)

Push to a registry (optional):

```bash
docker tag llm-hub:1.0.0 registry.example.com/llm-hub:1.0.0
docker push registry.example.com/llm-hub:1.0.0
```

## Configuration reference (config.yaml)

The runtime config is YAML — comments and `#` notes are allowed, secrets still do not belong in it.
An existing `data/config.json` is still read (with a warning) when there is no `config.yaml` beside
it, so an older deployment keeps working; rename it to `config.yaml` when convenient, since JSON is
valid YAML and needs no conversion.

### `server` — the gateway itself

| Field | Default | Description |
|------|------|------|
| `host` / `port` | `0.0.0.0` / `8000` | Listen address & port |
| `log_level` | `info` | `debug` / `info` / `warning` / `error` |
| `request_timeout` | `600` | Default upstream timeout (seconds) |
| `max_concurrency` | `64` | HTTP connection-pool cap |

### `auth` — authentication

```yaml
auth:
  enabled: true
  allow_anonymous_health: true
  allow_access_ip_whitelist: []
```

- **Keys are no longer configurable in `config.yaml`**: the `api_keys` field is gone, so the file
  carries no credential at all (the live store is the `api_keys` table described below). A leftover
  `api_keys` block logs a warning and is ignored
- Key sources: the `GATEWAY_API_KEYS` env var and the file named by `GATEWAY_API_KEYS_FILE`
  (e.g. `data/.keys`); both merge and de-duplicate
- `GATEWAY_API_KEYS`: `GATEWAY_API_KEYS=key1,key2` (comma / newline separated)
- `GATEWAY_API_KEYS_FILE`: path to a file holding the keys, ideal for Docker / K8s secrets (won't
  leak via `docker inspect`)
- **Those two only seed an empty `api_keys` table.** The live source of truth is that table, inside
  `llmhub.db`, where every row keeps `sha256(<plaintext>)` plus a display mask and **never the
  secret**. The first start imports the environment keys as `source: "file"`; from then on the file
  is not read again, so revoking a key in the console survives restarts and editing the file adds
  nothing. A startup warning lists, masked, any environment key the table does not serve. Mint and
  revoke at `/admin` → account menu → **API keys**, where the plaintext is shown exactly once
  (see [Web console](#web-console))
- **Key format**: `sk-hub-` + 40 base62 characters = **47 characters**, ~238 random bits. The prefix
  is not a secret; it is what makes a key recognisable as one in a log or a client config. The mask
  keeps the first and last 4 body characters (`sk-hub-FNdc…SVvw`); an imported key of another shape
  shows head 8 + tail 4. SHA-256 (not bcrypt) because a 238-bit random key has no passphrase to
  brute-force and every client request would otherwise pay a slow KDF
- `allow_access_ip_whitelist`: list of regexes matched with **fullmatch** against the ASGI client
  address (the **TCP peer** on a direct connection; the gateway code itself never reads
  `X-Forwarded-For`, which a caller can forge). Matching addresses may open `/docs` and
  `/openapi.json` and call the API without a key. Default `[]` (nobody exempt); a malformed pattern
  logs a warning and is skipped. Note that uvicorn's `ProxyHeadersMiddleware` rewrites the client
  address from forwarded headers when the peer is a **trusted proxy** (`--forwarded-allow-ips`,
  default `127.0.0.1`), so behind a local reverse proxy the real source is what matches — make sure
  that proxy sets and sanitises `X-Forwarded-For`
- ⚠️ With auth enabled (`enabled: true`) and **no row in `api_keys`** (after seeding), the gateway
  **refuses to start** and names the fix: seed the empty table with `GATEWAY_API_KEYS` /
  `GATEWAY_API_KEYS_FILE` and restart, or set `auth.enabled: false`. This avoids an accidentally
  unauthenticated service
- `allow_anonymous_health`: let `/health` skip auth (keep `true` or the container health check
  fails)
- `enabled: false` temporarily disables auth

### `upstreams` — backend instances (Ollama / LM Studio / OpenCode)

```yaml
upstreams:
- name: local-main
  type: ollama
  base_url: http://host.docker.internal:11434
  models:
  - qwen3:8b
  - qwen3.5:4b
  - bge-m3:latest
  timeout: 600
  description: Main local instance
  headers: {}
  vram_total_gb: 32.0
```

- `type`: backend flavor; decides which endpoints are used for model discovery & health probes.
  Default `ollama`
  - `ollama`: model list `/api/tags`, health check `/api/version`
  - `lmstudio`: OpenAI-compatible, model list `/v1/models`, health check `/v1/models`
  - `opencode`: [OpenCode API Gateway](API.md#web-search-opencode-gateway-proxy) — OpenAI-compatible model
    list `/v1/models`, health check `/health`; also serves the web search endpoint (see `websearch`)
- `models`: supports **prefix wildcards** — `"qwen3:*"` matches `qwen3:4b`, `qwen3:8b`, `qwen3:14b`.
  The wildcard is a literal prefix, so slash-separated ids need the slash form: `"opencode/*"`
  matches `opencode/big-pickle` ( `"opencode:*"` would match nothing)
- `model_aliases`: a **display rename** — maps a verbose upstream id to a short name that is used
  wherever that model is shown or called. It does not create a new model (see the LM Studio section
  below; for a model with its own pinned parameters, declare it under the root `aliases` node)
- `timeout`: overrides the global timeout; raise it for large models
- `headers`: optional extra headers added when forwarding (e.g. when the upstream has its own auth).
  Model discovery (`/v1/models`, `/api/tags`, and the `/probe` health/model checks) sends them too,
  so an authenticated upstream still shows up in the aggregated catalog
- `priority`: routing priority (integer, **default `0`**). When the same model name is declared by
  several upstreams, the gateway picks the one with the **smallest** `priority`; ties keep the
  declaration order in `upstreams`. Useful for mounting the same model on a GPU instance and an
  iGPU / CPU instance so the high-priority one serves first and the rest act as fallback
- `vram_total_gb`: optional total VRAM (GiB) of the host running this upstream. It cannot be
  discovered over the API and is only used by the management console's "used / total" gauge;
  when omitted the console shows the used amount but no total

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

```yaml
aliases:
  qwen3.5:4b-nothink:
    model: qwen3.5:4b
    params:
      think: false
```

Requesting `qwen3.5:4b-nothink` replaces `model` with `qwen3.5:4b` and **force-overwrites** the
request body with the fields in `params` (the alias wins on name collisions).

An entry here is a **model of its own**: `qwen3.5:4b` stays callable and still runs with thinking
on, and the management console lists `qwen3.5:4b-nothink` as a separate model (its device comes
from the instance its target is loaded on, its memory shows `-` because that instance is already
counted under its own name). For a pure display rename with no parameters of its own, use
`upstreams[].model_aliases` instead — that only changes the name a model is shown and called
under, it does not add a model.

### `num_ctx_overrides` — pin context length per model (avoid Ollama re-spawning)

Ollama creates a **separate instance** for each "model + `options.num_ctx`" combination (the KV
cache is pre-allocated up front). If clients keep sending different `num_ctx` values, the same model
gets re-spawned repeatedly and VRAM stacks up. This mapping lets the gateway **clamp** the context
length for specific models, ignoring any `num_ctx` the client sends:

```yaml
num_ctx_overrides:
  qwen3.6:35b-pruned: 65536
  qwen3.5:9b: 16384
```

- **Keys are real model names** (the resolved `resolved.target` after alias expansion — the actual `model` field forwarded upstream, i.e. what you see in `ollama list` / `/api/tags`). E.g. alias `fast` → `qwen3:4b`; to clamp it, use `qwen3:4b`.
- **Values are fixed `num_ctx`** (integer, token count).
- **Only applies to `type=="ollama"` upstreams.** LM Studio / OpenAI-compatible backends use `max_tokens` and don't recognize `num_ctx`, so they are unaffected.
- Whatever the client sends, the value here wins; when an override is applied the gateway logs `num_ctx clamped`.
- **Hot-reloaded**: takes effect on new requests ~1 second after editing, no gateway restart needed.

> Example use: pin a large-context model (e.g. 35B-pruned) to 64K so a client can't blow up VRAM with a bigger ctx; or clamp small models to 8K to stop different clients from triggering repeated KV-cache re-allocation on Ollama.

### `api_models` — which models each API family may offer

`upstreams[].models` says what an instance declares, not what role it plays: nothing there tells
the console that `bge-m3` answers `/api/embed` while `qwen3:8b` answers `/api/generate`. This block
is that missing statement - one optional list per API family. **Every** model picker on the
`API usage` tabs reads it (or its documented fallback), and so do the "Available models" tables
and the `model` in the request/response examples across the console: all of them share one
`/v1/management/api-test/models` payload.

```yaml
api_models:
  generate: []                       # /api/generate · /v1/chat/completions · /v1/messages
  embed: [bge-m3:latest]             # /api/embed · /v1/embeddings
  rerank: []                         # /v1/rerank
  systemone: []                      # /v1/systemone
  websearch: []                      # /v1/tools/web-search
```

- A list may name a real model id, a `upstreams[].model_aliases` short name or a root `aliases:`
  model. The alias stays the name that gets sent, so the gateway still injects its fixed parameters.
- **A non-empty list is a closed set.** Its names are the family's entire picker, `source`
  reports `api_models.<family>`, no provider-side narrowing is applied, and the
  「Custom model…」 entry disappears - change the config to add or remove a model. Blank lines
  (`- ''` or whitespace only) are placeholders and ignored; a list of nothing but placeholders
  counts as empty.
- **An empty list means "derive", not "none"** - a config written before this block existed needs
  no edit. `rerank` falls back to `rerank.models`, `systemone` to `systemone.model`, everything else
  to the whole set of declared (and alias) names. Each picker says which of these it read
  (`Choices come from api_models.embed` / `rerank.models` / `upstreams[].models`).
- **Ollama-native endpoints only offer Ollama models.** `POST /api/generate` and `POST /api/embed`
  are Ollama's own API; an `lmstudio` or `opencode` upstream answers only the `/v1` side, so the
  derived picker behind those two testers keeps `type: "ollama"` rows only. The same models stay
  selectable on `POST /v1/chat/completions` / `POST /v1/embeddings`, and a non-empty `api_models`
  list is still served as written (a name that cannot answer then stays visible rather than
  silently disappearing).
- A name **explicitly claimed by another family** disappears from `generate` / `chat`:
  `api_models.embed`, `rerank` and `systemone` all count, as does `rerank.models`, in
  either spelling (alias or real id), so a chat picker never offers an embedding, scoring or
  decision model. `api_models.websearch` is **not** a claim: that endpoint is a chat pass-through,
  so the model answering the synthesis prompt stays a chat model - this is what keeps a hosted
  provider's routing model (e.g. `opencode/auto`) selectable for chat. Comparison ignores a trailing
  `:latest`, since Ollama treats `tev1` and `tev1:latest` as one model. A fallback such as
  `systemone.model` is **not** a claim either: the same box may well answer decision requests, and
  it must stay selectable for chat.
- `websearch` lists the search provider's own models. **When left empty** (or filled only with
  placeholders), the backend narrows the candidates to the models declared by
  `websearch.upstream` and reports `source: "upstreams[].models (search provider)"`. An explicit
  list is used as written, without that narrowing.
- The 「Custom model…」 entry appears only for **derived** families: opencode-port resolves
  `model` itself (omitting it uses the provider's default), and a laya-server backend takes a
  routing enum (`auto` / `english` / `multilingual` / `typed-decisions`) rather than a model id.
  Once `api_models` spells the choices out, the entry goes away - the picker offers exactly those.
- A name nothing serves stays **visible** and marked `未被声明` / `Unserved`: a dead entry is a
  misconfiguration to show, not to hide.
- These lists only narrow what the console offers. **Routing is untouched** - the gateway still
  accepts exactly the model names it accepted before, so this block cannot lock a caller out of a
  model that config already declared.

## Integrating LM Studio

LM Studio exposes an OpenAI-compatible API, and its model ids usually carry a publisher prefix and
are long (e.g. `qwen/qwen3.5-9b`). The gateway supports it natively and offers a "short-name
mapping" to collapse it to `qwen3.5:9b`.

**1. Make sure LM Studio's local API server is running**

Load a model in LM Studio and start the Local Server (default port `1234`), then confirm:

```bash
curl http://127.0.0.1:1234/v1/models
```

**2. Register it as an upstream in `config.yaml`**

```yaml
name: lmstudio
type: lmstudio
base_url: http://host.docker.internal:1234
models: []
model_aliases:
  qwen3.5:9b: qwen/qwen3.5-9b
timeout: 600
```

- `type: "lmstudio"`: the gateway discovers models & probes liveness via `/v1/models` (Ollama uses
  `/api/tags`, `/api/version`)
- `model_aliases`: maps "exposed short name → LM Studio real id"; the key is the public name, the
  value is the upstream's real id. It only renames that model for display and calling — the model
  itself is unchanged, no new model is added

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

```yaml
fast:
  model: qwen3:4b
  upstream: local-main
  params:
    think: false
    num_ctx: 8192
    temperature: 0.2
```

### `rerank` — rerank configuration

```yaml
rerank:
  enabled: true
  mode: logprobs
  models:
  - qwen3-reranker:4b
  normalize: false
  max_concurrency: 8
  return_documents: true
  model_modes:
    bge-m3:latest: embedding
```

Two scoring modes:

| Mode | For | How it works |
|------|----------|------|
| `logprobs` | Generative rerankers like Qwen3-Reranker | Build a yes/no binary prompt, softmax over the first token's logprobs |
| `embedding` | Vector models like bge-m3 | Compute cosine similarity of the query and document vectors |

`mode` is the global default. When mixing reranker types (e.g. generative `qwen3-reranker:4b` +
vector `bge-m3:latest`), use `model_modes` to set a per-model mode, e.g.
`{"bge-m3:latest": "embedding"}`.

### `websearch` — web search proxy

```yaml
websearch:
  enabled: true
  upstream: opencode
  path: v1/web-search
  api_key_env: ''
  timeout: 300.0
```

Turns the gateway into the front door for a web search provider: `POST /v1/tools/web-search`
forwards the body unchanged to `<upstream.base_url>/v1/web-search` and returns the provider's
response as-is (`query`, `results[]`, concatenated `text`, `sources`, and `answer` when
`synthesize: true`).

- The provider is a normal `upstreams` entry (`type: "opencode"`), so its address, timeout and
  headers are configured once and shared with model routing and the aggregated model list
- `upstream`: provider name; leave it empty to pick the **first `type: "opencode"` upstream**.
  A name that matches no upstream returns **503** instead of silently falling back
- `api_key_env`: name of the environment variable holding the provider key, sent as
  `Authorization: Bearer <key>`. Empty (default) = the call is unsigned. Keys stay in the
  environment, never in `config.yaml`
- `model` is **never rewritten**: it is not resolved against the gateway's `aliases`
- Callers authenticate with the gateway's own key (`GATEWAY_API_KEYS`); the endpoint is
  POST-only (**405** otherwise) and returns **502** / **504** when the provider is unreachable
  or slow

Full request/response contract and curl examples: [API.md → Web Search](API.md#web-search-opencode-gateway-proxy).

### `request_log` — request detail switch

```yaml
request_log:
  enabled: true
```

Feeds the console's "Requests" view: every call through the gateway becomes one SQLite row
(`llmhub.db`, WAL).

- `enabled`: the write switch, read **per request**, default `true`. With `false` the proxy stops
  capturing request/response bodies and writes no row at all; because the flag is re-read on every
  request, saving `config.yaml` applies without a restart — hot reload polls the file mtime at most
  once per second, so the switch takes effect within ~1s (verified against a live server)
- While it is off, `/v1/management/overview` and `/requests` report `request_log.enabled: false`,
  so the console can say "logging disabled" instead of showing an empty list that looks like no traffic
- Database path and retention stay deployment-level environment variables: `REQUEST_LOG_DB`,
  `REQUEST_LOG_RETENTION_DAYS`
- **`HEAD` is forwarded but never recorded**: it carries no body and no answer, and liveness
  monitors send it continuously, so logging it would drown the real calls. Under the default
  `allowed_http_methods: [GET, POST]` a HEAD never reaches the proxy at all (it answers `405`);
  allow `HEAD` there and the request is forwarded normally, just without a row
- **Statistics count API calls only**: the list, the charts, the KPIs and the failure view all add
  `path LIKE '/v1/%' OR path LIKE '/api/%'` (`request_log.API_PATH_SCOPE`), so `/health`, `/metrics`,
  `/openapi.json` and the `/admin/*` asset requests cannot dilute the numbers. Rows for those paths
  are still **written** — nothing is thrown away, and `GET /requests/{id}` deliberately stays
  unscoped, so a deep link to any row still opens
- Recorded columns: `ts` / `ts_end` / `duration_ms` / `ttfb_ms` (timing — `ts` and `ts_end` are
  second precision, so read `duration_ms` for sub-second cost), `method` / `path` /
  `query` / `requested_model` / `model` / `request_body` / `req_size` / `stream` / `client_ip`
  (request parameters), `status` / `upstream` / `response_body` / `res_size` / `tokens_in` /
  `tokens_out` / `tokens` / `error` (result - tokens are stored as an input and an output column,
  `tokens` being the total). Bodies are capped at 8 KB, credentials are stored as `***`, and an
  older database is upgraded with `ALTER TABLE` at startup

## Web console

The console is a Vue 3 + TypeScript + Element Plus + ECharts single-page app. Sources live in
`web/src` (`*.ts` modules and `<script setup lang="ts">` SFCs, `strict`); `npm run build` writes
the bundle into **`app/web`**, which is committed and served by the
gateway itself at **`http://localhost:8888/admin/`**, so the image needs no node toolchain
(the bundle sits next to the modules, so `app/main.py` resolves one and the same path locally
and inside the container). Sign in as `admin`
with the `ADMIN_PASSWORD` from `.env`; a successful login mints a 12h HttpOnly session cookie.
The views — Dashboard, Upstreams, Requests, Models (LLM / Embed / Rerank / System One), Tools (Web Search),
API keys, Settings and About — write only in four places: the
per-model speed test on Models · LLM, the API testers on every `API usage` tab, minting or
revoking a caller key on the **API keys** page, and the Settings page itself (the single editor that
validates and saves `config.yaml`); the first three never touch `config.yaml`. A 401 from any management call
signs the user out and returns to the login page. A 503 is never treated as an auth failure:
it means an unreachable upstream, `ADMIN_PASSWORD` unset, or the frontend not built yet.
The account chip sits at the bottom-left of the panel and opens the account menu: language
(简体中文 / 繁體中文 / English — a right-side flyout, remembered per browser), the dark/light switch,
Settings (its old sidebar row is
gone), **API keys** (its own page, also without a sidebar row), About (project introduction, features, live version info, repository link and the WeChat QR)
and sign-out.

**API keys** (`/admin/api-keys`) is where caller credentials are born and die. The table lists every
key as a mask (`sk-hub-FNdc…SVvw`) with its label and creation time — the plaintext is not in any
list response, and neither is the hash. The response's `source` (`console` / `file`) stays a field
for scripts; the table gives it no column.
**New key** opens a dialog that asks for a label (required, so a key is recognisable later), then
shows the generated 47-character key once, in a box with a copy button and a confirmation that it
was saved; closing that dialog reloads the list, and from then on the value can only be revoked,
never recovered. Revoking is deliberately two steps on the row itself (*Revoke* → *Confirm revoke* /
*Cancel*), because one stray click must not kill a credential some client is using right now. Those
three routes accept the console session cookie **only** — a gateway API key is what any client
already holds, so it cannot be used to mint more access.
Models · LLM and the Upstreams row-expander list the models each upstream declares in
`config.yaml` (`upstreams[].models`, shown under its `model_aliases` short name when one is
configured, while every entry of the root `aliases:` node becomes a row of its own; a prefix
wildcard such as `opencode/*` is expanded through the upstream's own model list into the concrete
models it matches, and nothing else is listed). Ollama rows carry the running models' device and
VRAM/memory; a derived (`derived: true`) row inherits its target's device and reports `-` for memory,
because the instance is already counted under its own name and the KPI never double-counts it;
an OpenCode-style hosted upstream has no load state, so when its probe answers every
declared model counts as online, its device reads **Cloud** and its memory `-`. Device badges only
distinguish **GPU / 部分 / CPU / Cloud** and never show a percentage. Expanding an Upstreams row never
collapses the others: several instances can stay open at once and each is re-probed on the automatic
refresh. Models · LLM is split into two tabs:
**model inventory** (those tables, plus **Aliases & derived models** with name / kind / target /
upstream / pinned params — the two kinds share one chip style and the description is no longer a
column, it is the tooltip on the name and target cells) and **API usage**, which documents the three generation endpoints the
gateway supports today - `POST /api/generate` (Ollama native), `POST /v1/chat/completions`
(OpenAI-compatible) and `POST /v1/messages` (Anthropic-compatible) - with their parameter tables,
request and response examples, and the note that `POST /api/generate` and `POST /v1/chat/completions`
**stream by default** (omitting `stream` yields NDJSON and SSE respectively), while `POST /v1/messages`
defaults to a single JSON envelope. Models · Embed
(sits above Rerank) documents the two embedding pass-throughs - `POST /api/embed` (Ollama native) and
`POST /v1/embeddings` (OpenAI-compatible). Its "Available models" table and the `model` in its
examples come from `api_models.embed`, just like its testers - no model name is hardcoded.
Each of those documents carries a **`Test` button** at the right end of its *Request parameters*
header; it opens the **API tester** in a popup over the page (closing and reopening keeps what you
typed and the last answer). Its model picker is narrowed by `api_models`; models on an `lmstudio` or
`opencode` upstream appear only on the `/v1` testers, because `/api/generate` and `/api/embed` are
Ollama's own API. Every option shows the **name only** - the alias (`display`) when one is
configured, else the declared id - with the
upstream and runtime state in the hover tooltip and an unserved name still listed and marked
`Unserved`. One input per documented parameter, **already filled with its initial value when
the popup opens**, plus `Restore defaults` / `Clear`, a `Show request body` preview, and the result -
status, duration, first-token time for a real stream, the model the gateway actually routed to plus
the parameters an alias injected, the answer pulled out of the JSON / NDJSON / SSE payload and the
raw body. **The model is a real dropdown**, preset to the default and listing every candidate. An explicit
`api_models` list is a closed set and every sample body on the page uses those same names; only
**derived** `systemone` / `websearch` families add a final 「Custom model…」 entry that swaps in a
free-text box with the same suggestions, and re-picking a listed name closes it again. **It can be cleared**: the `×`
next to the field (or the select's empty option) leaves it out of the body - a family that needs one
answers the local 「pick a model first」 guard, while `systemone` / `websearch` let the endpoint
decide (System One still injects `systemone.model`). `Restore defaults` re-selects `default_model`,
and `Clear` clears the model too. A documented
boolean is a **checkbox**: it always sends `true` / `false`, so an unchecked
box means false rather than "omit"; everything left blank is **omitted** from the request rather
than sent as an empty value, so the endpoint answers with its own default. The two LLM windows start
with **`stream` off** (the endpoints stream when the flag is omitted, but one Run here should show a
single JSON), and the generate window adds a **`think`** checkbox (off by default) while the chat
window exposes the OpenAI-compatible **`reasoning_effort`** (`none` disables thinking on Ollama). `systemone` and `websearch` also let you type a name the
list does not carry, because those two endpoints never resolve the model through the gateway.
The call is executed **server-side** (`POST /v1/management/api-test`): the console's session cookie is
deliberately not a caller API key, so the gateway rebuilds the request and hands it to the same
service the public route uses - alias injection, `num_ctx` clamping and the request log all behave
exactly as they would for an external client, and a refusal the endpoint itself produced (a `404`
unrouted model or disabled feature, a `400` invalid body, a `502`/`504` upstream failure) comes back
with its own status instead of being hidden. An endpoint whose feature is switched off keeps
its tester and shows `Disabled`; a response is capped at 128 KB. Extra credentials (an upstream
`Authorization` header, `systemone.api_key_env`, `websearch.api_key_env`) appear as a read-only
`••••••` row naming the variable they came from - the value never leaves the server.
The Requests model filter narrows
to the selected
upstream. Every refresh is visibly in progress (spinning button, top progress bar, dimmed tiles,
veiled charts) instead of silently swapping numbers. The tab icon comes from `web/public/favicon.svg`,
which Vite copies into `app/web` and the gateway serves at `/admin/favicon.svg`.

```bash
cd web
npm install
npm run typecheck # vue-tsc --noEmit: view props, fetch payloads and ECharts options
npm run dev      # dev server at http://localhost:5173/admin/, /v1/management proxied to :8000
npm run build    # type-checks, then writes app/web - commit it, the image copies it with the modules
```

All data comes from `/v1/management/*`, which accepts **two credentials**: the console session
cookie, or a gateway API key via `Bearer` / `X-API-Key` so scripts can reuse the key they already
have. `ADMIN_PASSWORD` itself is a login credential only — sending it as a request header answers
401, and a key in the query string is rejected. Without `ADMIN_PASSWORD` every route answers 503.

```bash
# Set the administrator password (without it every management call returns 503)
ADMIN_PASSWORD=<16+ random chars>

# Log in for a 12h HttpOnly session cookie
curl -c cookie.txt http://localhost:8888/v1/management/auth/login \
  -H "Content-Type: application/json" -d '{"password": "'"$ADMIN_PASSWORD"'"}'

# Later calls authenticate either with that cookie ...
curl -b cookie.txt http://localhost:8888/v1/management/overview

# ... or with a gateway API key
curl -H "X-API-Key: $GATEWAY_API_KEYS" http://localhost:8888/v1/management/overview
```

| Path | Purpose |
|------|---------|
| `GET /v1/management/overview` | KPIs, upstream summary, running models with GPU / partial / CPU classification; a model declared under the root `aliases:` is appended as its own row (`derived: true`, device inherited from its target, memory `null`) while `kpi.running_models` still counts only the physical + hosted rows |
| `GET /v1/management/upstreams[/{name}]` | Instance health, VRAM usage, `/api/ps` running models (hosted providers: every declared model with `device: "cloud"` and memory `null`; a derived alias of that upstream is appended as its own row) and the models `config.yaml` declares for that upstream — `model_aliases` renames applied, prefix wildcards expanded through the upstream's model list, the root `aliases:` names last |
| `GET /v1/management/models/{llm,rerank,systemone}` | Per-upstream model inventory built from `config.yaml` declarations, with runtime device and memory where the instance reports them (LLM; hosted rows report `device: "cloud"`; a root `aliases:` entry is its own row carrying `derived` / `target` / `params` / `description`, and `aliases[].kind` tells a `rename` from a `derived` model), plus read-only config projections (Rerank / System One); API keys always masked as `••••••` |
| `GET /v1/management/tools/websearch` | Web search config projection, with `base_url` derived from the provider upstream |
| `GET /v1/management/requests[/{id}]` | Request log with time/upstream/model/status/path filters, pagination and per-entry detail |
| `GET /v1/management/stats/{timeseries,distribution,failures}` | Hourly/daily counts, P95 and error counts; grouped counts per upstream / model / ok-vs-error for the three pie charts; failure breakdown by status and upstream |
| `GET /v1/management/api-test/models` | Per-family model choices for the API testers (`api_models` narrowed; the Ollama-native `generate` / `embed_ollama` kinds keep `type: "ollama"` rows only), each row carrying `display` / `alias` / `upstream` / `running` / `device` and the masked upstream key, plus `default_model`, `free_model`, `enabled` and the masked provider key with its env-var name; the whole payload probes every upstream, so the console caches it and only refetches on **Re-probe** |
| `POST /v1/management/api-test` | `{kind, body}` — execute that request server-side through the endpoint's own gateway service and return `status` / `ok` / `duration_ms` / `ttft_ms` / `content_type` / `stream` / `requested_model` → `model` / `upstream` / `injected` / `body` / `text` / `truncated` / `error`; a `kind` outside the allowlist or a missing model for a family that needs one is `400`, and a gateway refusal comes back as data, not as a hidden failure |
| `POST /v1/management/bench` | Start a speed test: `{upstream, model, rounds?, max_tokens?, think?}` → `{job_id, status: "pending"}`. Runs on its own thread + event loop, so closing the browser does not cancel it |
| `GET /v1/management/bench/{job_id}` | `pending` / `running` (with `{completed, total, warmup}`) / `done` (full per-round payload) / `failed` (+ `error`) |
| `GET /v1/management/bench/history?upstream=&model=` | The latest records kept for that upstream + model (3 by default) — the data behind the hover panel |
| `GET /v1/management/api-keys` | Caller key list (**session cookie only**): `{items: [{id, masked, label, source, created_at}], total}`, `source` being `console` (minted here) or `file` (imported on first start). Masks only — no plaintext, no hash |
| `POST /v1/management/api-keys` | Mint a key (**session cookie only**): `{label}` (blank → `400`, over 100 chars → `422`) → `201` `{item, secret}`; **that response is the only time the plaintext leaves the server** |
| `DELETE /v1/management/api-keys/{id}` | Revoke (**session cookie only**): `200` → `{"deleted": id}`, unknown id → `404`. The in-memory verification set reloads immediately, so the next request already fails |

**Speed test (`测速` on Models · LLM):** one fixed prompt is sent through the gateway `rounds`
times (4 by default, first round as warm-up, `temperature: 0`) with `max_tokens` 500. Per round it
records TTFT, decode tok/s and prefill tok/s; `timing_source` is `server` when the upstream reports
its own `eval_duration` / `prompt_eval_duration` / `predicted_ms`, `client` when the wall clock had
to be used instead, and `client*` when a reasoning channel leaked into the stream (which skews
TTFT). `think: auto` keeps the model's default thinking behaviour; `off` forces `think: false`.
Results land in the same `llmhub.db` as the request log, through the same batched write queue.

**Request log storage:** every forwarded call (streaming, non-streaming, upstream failure) is
appended to SQLite in WAL mode, by default `llmhub.db` next to `config.yaml`
(`/data/llmhub.db` in the container). Set `request_log.enabled: false` in `config.yaml` to stop
writing altogether — the switch is read per request, so it applies without a restart.

| Env var | Default | Meaning |
|---------|---------|---------|
| `ADMIN_PASSWORD` | unset (management API → 503) | Console login password; exchanged for a session cookie, not a request credential |
| `REQUEST_LOG_DB` | `<config dir>/llmhub.db` | Request log database path |
| `REQUEST_LOG_RETENTION_DAYS` | `30` | Prune rows older than N days; `0` keeps everything |
| `LLMHUB_DB` | `<config dir>/llmhub.db` | Shared store for `bench_results` (speed-test history) and `api_keys` (hashed caller keys); point it at the same file the request log uses (the default) to keep one data file per deployment |

Writes go through a bounded in-memory queue and batched `executemany`, so forwarding never waits
on disk. Bodies are truncated to 8 KB and `authorization` / `api_key` / `token` / `password`
fields are replaced with `***` before storage. Total VRAM is not discoverable over the Ollama
API — set `vram_total_gb` on an upstream to show it in the overview gauge.

## API usage

The examples below assume the gateway runs at `http://localhost:8000`.

### Authentication

Either header works:

```bash
-H "Authorization: Bearer sk-gateway-example-key"
-H "X-API-Key: sk-gateway-example-key"
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
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.6:35b-pruned","messages":[{"role":"user","content":"hello"}],"stream":false}'

# Ollama-native completion - like Ollama itself it streams NDJSON unless stream=false,
# and raw=true skips the model's chat template
curl http://localhost:8000/api/generate \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:8b","prompt":"Write a poem about programming:","raw":true,"stream":false}'

# OpenAI-compatible endpoint
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"hello"}]}'

# Embeddings
curl http://localhost:8000/api/embed \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"bge-m3:latest","input":["hello world"]}'
```

> `/api/generate` only makes sense against **Ollama upstreams**: the path is appended to the routed
> upstream's `base_url` unchanged, and OpenAI-compatible backends (LM Studio, vLLM, ...) do not
> serve it. Use `/v1/chat/completions` for something that works across every backend type.

### Anthropic Messages (Claude clients)

`POST /v1/messages` (the singular `/v1/message` behaves identically) lets an Anthropic SDK, Claude
Code and similar clients talk to the gateway directly. `max_tokens` is required (> 0); `model` may be
omitted, in which case `auto` picks the first model of the default upstream. Authentication accepts
either `x-api-key` or `Authorization: Bearer`.

```bash
curl http://localhost:8000/v1/messages \
  -H "x-api-key: sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5:9b",
    "max_tokens": 256,
    "system": "Answer in one sentence.",
    "messages": [{"role": "user", "content": "What does a reranker do?"}]
  }'
```

The reply is a standard Anthropic envelope (`type: "message"`, `content: [{"type":"text", ...}]`,
`stop_reason`, and `usage.input_tokens` / `output_tokens` taken from the upstream's own token counts).
Without `stream` you get a single JSON object; with `stream: true` the gateway emits SSE in the order
`message_start → content_block_start → content_block_delta* → content_block_stop → message_delta →
message_stop`. `system` accepts a string or an array of content blocks, and `image` blocks inside
`content` are mapped onto the upstream's image field. Field-by-field details live in
[API.md](API.md#messages-anthropic-compatible).

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
  -H "Authorization: Bearer sk-gateway-example-key"
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
  -H "Authorization: Bearer sk-gateway-example-key" \
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

### Web search

```bash
curl -X POST http://localhost:8000/v1/tools/web-search \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"query": "Ollama keep_alive default", "top_k": 3}'
```

Response (passed through from the provider):

```json
{
  "query": "Ollama keep_alive default",
  "model": "ollama-local/qwen3.5:9b",
  "engine": "opencode-websearch",
  "truncated": false,
  "answer": null,
  "text": "By default, models are loaded into memory for five minutes ...",
  "sources": ["https://docs.ollama.com/faq"],
  "results": [
    {
      "title": "FAQ",
      "url": "https://docs.ollama.com/faq",
      "snippet": "Alternatively, you can change the amount of time all models stay loaded ...",
      "paragraphs": ["By default, models are loaded into memory for five minutes."]
    }
  ]
}
```

Request fields: `query` (required), `top_k`, `max_chars_per_page`, `locale`, `recency`
(`day`/`week`/`month`/`year`), `synthesize`, `include_sources`, `model` (forwarded verbatim).
`text` is every retrieved paragraph concatenated — ready to drop into a prompt.

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

Then point `base_url` at `http://host.docker.internal:11435` in `config.yaml`.

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
| `cors_origins` | `[]` | Explicitly allowed cross-origin sources; empty means no CORS headers at all (no more `*`) |
| `max_body_bytes` | `33554432` | Request-body cap (32MB); returns 413 above it, preventing oversized bodies |
| `allow_query_api_key` | `false` | Whether to accept `?api_key=` query param (off by default, avoids keys in logs / browser history) |
| `expose_health_details` | `false` | Accepted for compatibility and currently unused: `/health` always answers only `{"ok": true}`, no path or upstream address is echoed |
| `fail_on_weak_keys` | `true` | Refuse to start when weak / placeholder keys are detected |
| `allowed_client_ips` | `[]` | Client IP allowlist (CIDR supported, e.g. `10.0.0.0/8`); empty = unrestricted |
| `allowed_http_methods` | `["GET","POST"]` | Only these HTTP verbs are served; anything else (PUT / PATCH / DELETE / HEAD / OPTIONS) is rejected with **405**. An LLM gateway only needs GET (models / health / tags) and POST (chat / rerank), so this is the safe default |
| `path_allowlist` | `[]` | Regex perimeter control: when non-empty, a request path must match at least one pattern or it is rejected with **403**. Empty = allow all paths. Invalid regexes are logged and ignored (a bad pattern never opens the gateway), e.g. `["^/health$", "^/v1/(models\|chat/completions\|rerank)$", "^/api/tags$"]` |
| `rate_limit` | `{"enabled":false,"per_minute":60,"burst":20,"by":"ip"}` | In-memory token-bucket limiter (single process): `by` selects the bucket key (`ip` or `key`), `burst` = bucket capacity (max immediate requests), `per_minute` = sustained refill rate; over-budget requests get **429**. Enable behind a reverse proxy or as a cheap first line of defence |

> `/docs` and `/openapi.json` visibility is **not** a `security` switch: only sources matching
> `auth.allow_access_ip_whitelist` can open them (everyone else gets **404**), and those same
> sources may call the API without an API key. The docs page carries the scoped CSP, every
> endpoint's **Parameters** section carries an `X-API-Key` input, and the **Authorize** dialog
> covers `Authorization: Bearer` / `X-API-Key`.

Every response also gets defensive headers by default: `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin`,
`Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, and a restrictive
`Permissions-Policy`. These cannot be turned off (they only harden the response, never leak data).
The one relaxation is `/docs` / `/openapi.json`: they carry a scoped CSP
(`script-src`/`style-src 'self' 'unsafe-inline'`) so the vendored Swagger UI can boot — without it
the page would render as a blank white screen; every other response keeps `default-src 'none'`.

> **Container health check:** the Docker `HEALTHCHECK` now probes `/health` once per hour
> (`--interval=1h`) instead of every 30s. The gateway itself performs no periodic polling; it only
> answers `/health` on demand, so the interval is purely a Docker-side liveness cadence.

### Keep secrets out of the config file (the only way)

`config.yaml` no longer accepts `api_keys`; keys are injected at runtime:

```bash
# Container / process environment; comma or newline separated
export GATEWAY_API_KEYS="sk-gateway-example-key,sk-gateway-example-two"
# Or point at a key file (Docker / K8s secret), e.g. data/.keys
export GATEWAY_API_KEYS_FILE=/data/.keys
```

When `GATEWAY_API_KEYS_FILE` is unset, a `.keys` file **next to `config.yaml`** is read
automatically — so mounting `/config/.keys` (or `/data/.keys`, the default layout) is enough,
with no extra env var. An explicit `GATEWAY_API_KEYS_FILE` always wins.

The gateway reads all of them at startup and de-duplicates them; a leftover `api_keys` block only
logs an "ignored" warning. `auth.allow_access_ip_whitelist` (regexes, matched against the TCP peer,
never `X-Forwarded-For`) opens `/docs` + `/openapi.json` and skips the key check for those
addresses; with a non-empty whitelist the gateway also starts **without any key** (trusted-network
mode, warned at startup). The common misspelling `allow_access_ip_whilelist` is accepted and
normalised with a warning.

### Public-deployment checklist

- [ ] Keep `admin_endpoints` at `deny` (or at least `readonly`); never `allow`
- [ ] Every key is random — 47-char `sk-hub-…` keys minted in the console (≥ 16 random chars for a
      legacy key imported from `GATEWAY_API_KEYS` / `GATEWAY_API_KEYS_FILE`, e.g. `data/.keys`); the
      store keeps hashes only, so protect `llmhub.db` as you would protect the file
- [ ] A reverse proxy (Nginx / Caddy) in front terminates TLS
- [ ] To restrict origins, set `security.allowed_client_ips` to trusted ranges
- [ ] Keep `auth.allow_access_ip_whitelist` at `[]`; if `/docs` must be reachable, list only trusted sources — the check uses the ASGI client address (the TCP peer on a direct connection), which uvicorn rewrites from forwarded headers when a trusted proxy is in front, so make sure that proxy sanitises `X-Forwarded-For`
- [ ] Don't expose upstream ports like 11434 / 11435 — only expose the gateway's 8000

## Local development (no Docker)

### Launcher (Windows)

`scripts\start-local.bat` (or `scripts\start-local.ps1`) is the shortest path: it locates
python, reads `.env`, finds `data\config.yaml`, then runs the app's **own** checks before
launching — missing dependencies, a config rejected by the Pydantic models, auth enabled with
no API key, weak/placeholder keys, a port already in use, an unbuilt console — and prints the
URLs once the gateway is up.

```powershell
scripts\start-local.bat                      # preflight, then uvicorn with auto-reload
scripts\start-local.bat -CheckOnly           # only run the checks, start nothing
scripts\start-local.bat -Install -Port 8888  # pip install -r requirements.txt first
scripts\start-local.bat -NoReload            # do not restart on every saved .py
```

| Flag | Meaning |
| --- | --- |
| `-Port` / `-Bind` | Override `server.port` / `server.host` from the config |
| `-Config` | Alternate `config.yaml` (default: `CONFIG_PATH` from `.env`, resolved from `app/` like in the image, else `data\config.yaml`) |
| `-EnvFile` | Alternate env file (default `<repo>\.env`; variables already set in the shell win) |
| `-Python` | Alternate interpreter (default `python`, then the `py -3` launcher) |
| `-LogLevel` | Override `server.log_level` |
| `-NoReload` | Serve without `--reload` |
| `-Install` | `pip install -r requirements.txt` first |
| `-CheckOnly` | Print the verdict and exit |

The gateway is started with `--app-dir app` from `app/`, i.e. the same working directory as the
image's `WORKDIR /app`, so the relative paths in `.env` (`../data/config.yaml`,
`../data/.keys`) resolve exactly as they do in the container.

### Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Prepare config
cp data/config.example.yaml data/config.yaml
# Edit data/config.yaml with your upstream URLs

# 3. Prepare secrets (.env) — uvicorn loads these via --env-file below
cp .env.example .env
# Edit .env: set GATEWAY_API_KEYS, SYSTEMONE_SERVER_API_KEY, etc.

# 4. Run the server (loads ../.env automatically)
cd app
python -m uvicorn main:app --reload --port 8000 --env-file ../.env
```

### Windows PowerShell

```powershell
# 1. Install dependencies
pip install -r requirements.txt

# 2. Prepare secrets (.env)
Copy-Item .env.example .env
# Edit .env: set GATEWAY_API_KEYS, SYSTEMONE_SERVER_API_KEY, etc.
$env:CONFIG_PATH="D:\workspace\github\llm-hub\data\config.yaml"

# 3. Run the server (loads ../.env automatically)
cd app
python -m uvicorn main:app --reload --port 8000 --env-file ../.env
```

### Run with specific config

```bash
cd app
CONFIG_PATH=../data/config.yaml python -m uvicorn main:app --host 0.0.0.0 --port 8000 --env-file ../.env
```

```powershell
# Windows PowerShell
$env:CONFIG_PATH="D:\workspace\github\llm-hub\data\config.yaml"
cd app
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --env-file ../.env
```

### Run in background (Linux/macOS)

```bash
cd app
nohup python -m uvicorn main:app --host 0.0.0.0 --port 8000 --env-file ../.env > ../llm-hub.log 2>&1 &
```

### Run as Windows service (using NSSM)

```powershell
# Install NSSM: choco install nssm
nssm install LLMHub "C:\path\to\python.exe" "-m" "uvicorn" "main:app" "--host" "0.0.0.0" "--port" "8000" "--env-file" "../.env"
nssm set LLMHub AppDirectory "D:\workspace\github\llm-hub\app"
nssm set LLMHub AppEnvironmentExtra "CONFIG_PATH=D:\workspace\github\llm-hub\data\config.yaml"
nssm start LLMHub
```

> **Note:** `uvicorn --env-file` reads `.env` once at startup and injects its variables as environment variables (e.g. `GATEWAY_API_KEYS`, `SYSTEMONE_SERVER_API_KEY`). It does **not** hot-reload — after editing `.env`, restart uvicorn. `config.yaml` changes still hot-reload without restart.

Interactive docs: <http://localhost:8000/docs> (open only to sources matching
`auth.allow_access_ip_whitelist`; everyone else gets **404**, and matching sources may also call the
API without a key; each endpoint has an `X-API-Key` input box in its own **Parameters** section —
"Try it out" → "Execute" sends it with that request — and the header **Authorize** dialog accepts
`Authorization: Bearer` or `X-API-Key` for all requests)
