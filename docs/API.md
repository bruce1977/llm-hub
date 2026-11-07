# LLM Hub API Documentation

Chinese version: [API_CN.md](API_CN.md).

## Table of contents

- [Basics](#basics)
- [Authentication](#authentication)
- [Endpoints](#endpoints)
  - [Health check](#health-check)
  - [System info](#system-info)
  - [Instance probe](#instance-probe)
  - [Model list](#model-list)
  - [Ollama-native model list](#ollama-native-model-list)
  - [Instance runtime status (Process Status)](#instance-runtime-status-process-status)
  - [System One decision API (laya-server and Ollama proxy)](#system-one-decision-api-laya-server-and-ollama-proxy)
  - [Web Search (OpenCode gateway proxy)](#web-search-opencode-gateway-proxy)
  - [Rerank](#rerank)
  - [Chat completions](#chat-completions)
  - [Messages (Anthropic compatible)](#messages-anthropic-compatible)
  - [Text completion](#text-completion)
  - [Embeddings](#embeddings)
  - [Model management](#model-management)
  - [Management API (web console)](#management-api-web-console)
- [Streaming responses](#streaming-responses)
- [Error responses](#error-responses)
- [Alias parameter injection](#alias-parameter-injection)
- [Tool calling](#tool-calling)

---

## Basics

| Item | Value |
|------|-----|
| Base URL | `http://localhost:8888` |
| Protocol | HTTP/HTTPS |
| Data format | JSON |
| Character encoding | UTF-8 |

---

## Authentication

Most endpoints require an API key, supplied in one of two ways:

```bash
# Option 1: Authorization header
-H "Authorization: Bearer sk-gateway-example-key"

# Option 2: X-API-Key header
-H "X-API-Key: sk-gateway-example-key"
```

**Exceptions:** `/` and `/health` need no authentication by default (controlled by
`auth.allow_anonymous_health`).

**Where keys come from:** a caller key is a row in the `api_keys` table inside `llmhub.db`, where only
`sha256(plaintext)` and a mask are stored. Keys are minted and revoked in the console at `/admin`
(account menu → ⚿ API keys). `GATEWAY_API_KEYS` / `GATEWAY_API_KEYS_FILE` (e.g. `data/.keys`) are imported
**once, on the first start while the table is empty**; the file is not read again after that. A newly minted
key looks like `sk-hub-` plus 40 base62 characters, 47 in total. See **Caller API keys** in the
[Management API](#management-api-web-console) section.

**Client IP allowlist:** sources matching `auth.allow_access_ip_whitelist` (an array of regex strings,
each matched with `re.fullmatch`) may open `/docs` and `/openapi.json`, and may call any business API without
an API key. For everything else `/docs` and `/openapi.json` return **404** (not 401/403, so from the outside
the paths simply do not exist). Matching runs against the ASGI client address (`request.client.host`); the
gateway never reads `X-Forwarded-For` itself, but uvicorn's `ProxyHeadersMiddleware` rewrites
`request.client` from forwarding headers when the TCP peer is a trusted proxy (`--forwarded-allow-ips`,
`127.0.0.1` by default), so a front proxy must sanitise `X-Forwarded-For` on its own.

**Swagger UI:** open `/docs` (after satisfying the allowlist above). **Every endpoint's Parameters section
carries an `X-API-Key` input** — click **Try it out**, fill the key in, and **Execute** sends it with that one
request. You can also use **Authorize** in the top right to set `Authorization: Bearer` or `X-API-Key` once,
after which every request carries it (`persistAuthorization` keeps it across reloads). A source covered by the
allowlist can hit Execute without any key at all.

---

## Endpoints

### Health check

| Item | Value |
|------|-----|
| Path | `/health` |
| Method | `GET` |
| Auth | ❌ not required |
| Description | Lightweight health check that consumes no resources |

**Request example:**

```bash
curl http://localhost:8888/health
```

**Response (200 OK):**

```json
{
  "ok": true
}
```

---

### System info

| Item | Value |
|------|-----|
| Path | `/` |
| Method | `GET` |
| Auth | ❌ not required |
| Description | Returns the service name and gateway version, for client discovery/handshake |

**Request example:**

```bash
curl http://localhost:8888/
```

**Response (200 OK):**

```json
{
  "name": "LLM Hub",
  "version": "1.2.0"
}
```

---

### Instance probe

| Item | Value |
|------|-----|
| Path | `/probe` |
| Method | `GET` |
| Auth | ✅ required |
| Description | Probes connectivity and model availability of every upstream. Only `GET` is handled here; other verbs fall through to the catch-all forwarder and are proxied as an upstream path (usually ending in 502/404) |

**Request example:**

```bash
curl http://localhost:8888/probe \
  -H "Authorization: Bearer sk-gateway-example-key"
```

**Response (200 OK):**

```json
{
  "upstreams": [
    {
      "name": "local-main",
      "type": "ollama",
      "base_url": "http://host.docker.internal:11434",
      "reachable": true,
      "version": "0.32.13",
      "models": [
        {
          "name": "qwen3:8b",
          "declared": true,
          "available": true
        },
        {
          "name": "qwen3:*",
          "declared": true,
          "available": true,
          "wildcard": true
        }
      ]
    }
  ]
}
```

**Response fields:**

| Field | Type | Description |
|------|------|------|
| `upstreams[].name` | string | Upstream name |
| `upstreams[].type` | string | Backend type: `ollama` or `lmstudio` |
| `upstreams[].base_url` | string | Upstream base URL |
| `upstreams[].reachable` | boolean | Whether the upstream is reachable |
| `upstreams[].version` | string | Ollama version (Ollama only) |
| `upstreams[].error` | string | Error message (when unreachable) |
| `upstreams[].models[].name` | string | Model name |
| `upstreams[].models[].declared` | boolean | Whether the model is declared in the config |
| `upstreams[].models[].available` | boolean | Whether the model is available upstream |
| `upstreams[].models[].wildcard` | boolean | Whether the entry is a wildcard declaration |

---

### Model list

| Item | Value |
|------|-----|
| Path | `/v1/models`, `/models` |
| Method | `GET` |
| Auth | ✅ required |
| Description | Aggregates the models of every configured upstream (declared models, live models, aliases) |

**Request example:**

```bash
curl http://localhost:8888/v1/models \
  -H "Authorization: Bearer sk-gateway-example-key"
```

**Response (200 OK):**

```json
{
  "object": "list",
  "data": [
    {
      "id": "qwen3:8b",
      "object": "model",
      "created": 0,
      "owned_by": "ollama",
      "upstream": "local-main",
      "type": "upstream",
      "wildcard": false
    },
    {
      "id": "qwen3.5:4b-nothink",
      "object": "model",
      "created": 0,
      "owned_by": "ollama",
      "upstream": "local-main",
      "type": "alias",
      "target": "qwen3.5:4b"
    }
  ]
}
```

---

### Ollama-native model list

| Item | Value |
|------|-----|
| Path | `/api/tags` |
| Method | `GET` |
| Auth | ✅ required |
| Description | Model list in Ollama's own format |

**Request example:**

```bash
curl http://localhost:8888/api/tags \
  -H "Authorization: Bearer sk-gateway-example-key"
```

**Response (200 OK):**

```json
{
  "models": [
    {
      "name": "qwen3:8b",
      "model": "qwen3:8b",
      "modified_at": "2026-09-07T06:00:00Z",
      "size": 0,
      "digest": "sha256:gateway",
      "details": {
        "parent_model": "",
        "format": "gguf",
        "family": "gateway",
        "families": ["gateway"],
        "parameter_size": "",
        "quantization_level": ""
      },
      "gateway": {
        "upstream": "local-main",
        "type": "upstream",
        "target": null
      }
    }
  ]
}
```

---

### Instance runtime status (Process Status)

| Item | Value |
|------|-----|
| Path | `/api/ps` |
| Method | `GET` |
| Auth | ✅ required |
| Description | Aggregates `/api/ps` from every Ollama upstream (models loaded/running right now). OpenAI-compatible upstreams such as LM Studio have no such endpoint and are skipped automatically. Every entry carries the source `upstream`. |

**Request example:**

```bash
curl http://localhost:8888/api/ps \
  -H "Authorization: Bearer sk-gateway-example-key"
```

**Response (200 OK):**

```json
{
  "models": [
    {
      "name": "qwen3:8b",
      "model": "qwen3:8b",
      "size": 5368709120,
      "digest": "sha256:abc...",
      "details": { "parameter_size": "8.0B" },
      "expires_at": "2026-09-07T06:00:00Z",
      "size_vram": 5368709120,
      "upstream": "local-main"
    }
  ],
  "errors": [
    { "upstream": "ollama-ipex", "error": "connect timeout" }
  ]
}
```

> One unreachable upstream does not abort the whole response; failures are collected in `errors`.

---

### System One decision API (laya-server and Ollama proxy)

| Item | Value |
|------|-----|
| Path | `/v1/systemone` |
| Method | `POST` |
| Auth | ✅ required (the gateway's own API key, from `.env` / `GATEWAY_API_KEYS`) |
| Description | Forwards the request body unchanged to the configured System One backend (`1panel/laya-server`, or **Ollama 0.40+**, which ships `POST /v1/systemone` natively). The gateway signs with the key named by `systemone.api_key_env` (default `SYSTEMONE_SERVER_API_KEY`); both request and response bodies pass through untouched. |

**Configuration (the `systemone` block of `config.yaml`):**

```yaml
systemone:
  enabled: true
  backend: http://llm.home:11435
  model: tev1
  api_key_env: SYSTEMONE_SERVER_API_KEY
  path: v1/systemone
  timeout: 60.0
```

- `backend` may point at either backend; the gateway behaves identically — pure forwarding, no model
  resolution, no response rewriting. From inside a container, use `http://host.docker.internal:11435` to reach
  an Ollama on the host.
- `model` **is copied verbatim into the forwarded body**: it is injected only when it holds a concrete value
  and the caller sent no `model` of its own. `"local"` is a **sentinel** meaning "do not inject, the caller
  decides".
- laya-server's `model` is a literal enum and **accepts only `auto` / `english` / `multilingual` /
  `typed-decisions`**; anything else returns **HTTP 422** `literal_error`.
- Ollama's `model` must be a **real model name**: `auto` → **404** `model 'auto' not found`; omitted → **400**
  `model is required`. So when the backend is Ollama, configure `model` with an actual model (like `tev1`
  above).
- The backend key is read from the environment and never written into `config.yaml`. That `Authorization`
  header is harmless for backends that do not authenticate — Ollama simply ignores it.

**Request body (`state` / `questions` are required on both backends; the difference: laya-server **rejects
unknown top-level fields**, while Ollama was measured to ignore them):**

| Field | Type | Required | Description |
|------|------|------|------|
| `state` | object | ✅ | Free-form context carrier, e.g. `{"message": "..."}` |
| `questions` | object | ✅ | Question id → definition, at least one entry |
| `model` | string | ❌ | Model name / routing hint, governed by the rules above; when omitted the gateway injects the configured value |

`questions.<id>` is a **union discriminated by `type`** (the tag is `type`) with three variants. `criteria`
is the only shape difference between the two backends; the table gives the form that satisfies both:

| `type` | Extra required fields | Meaning | `criteria` compatibility | Key response fields |
|--------|--------------|------|--------------------|--------------|
| `noul` | `instructions: string` | Binary judgement | not needed | `noul` (probability); laya also returns `confidence` and `action`, Ollama only `noul` |
| `choice` | `instructions: string`, `criteria` | Pick one | laya accepts `string[]` or an object; **Ollama accepts only a non-empty object** (array → 400) | `choice`, `probabilities`, `confidence` |
| `score` | `instructions: string`, `criteria` | Score / rank | laya requires `string[]` (≥2 items); **Ollama requires an array of 2–10 items** (object → 400) | `score`, `legend`, `probabilities`, `confidence` |

> In short: use an object for `choice` and an array for `score`, and one body validates on both laya-server
> and Ollama.

**Request example (valid on both backends):**

```bash
curl -X POST http://localhost:8888/v1/systemone \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "state": {"message": "I was charged twice and need a refund today."},
    "questions": {
      "refund":  {"type": "noul",   "instructions": "Does the customer ask for a refund?"},
      "intent":  {"type": "choice", "instructions": "Primary intent?", "criteria": {
                    "refund": "asking money back", "cancel": "cancel account", "other": "neither"}},
      "urgency": {"type": "score",  "instructions": "How urgent?",      "criteria": ["urgent", "normal"]}
    }
  }'
```

**Response example (measured on Ollama 0.40+ / `model: tev1`):**

```json
{
  "model": "tev1",
  "answers": {
    "refund": {"type": "noul", "noul": 0.9734541266310229},
    "intent": {"type": "choice", "choice": "refund", "probabilities": {"refund": 0.9864521057032967, "cancel": 0.001990846275500757, "other": 0.011557048021202679}, "confidence": 0.979678158554945},
    "urgency": {"type": "score", "score": 0.13496998422642553, "legend": {"0": "urgent", "1": "normal"}, "probabilities": {"0": 0.8650300157735745, "1": 0.13496998422642553}, "confidence": 0.7300600315471489}
  },
  "usage": {"input_tokens": 369, "output_tokens": 0}
}
```

**Response example (measured on laya-server):**

```json
{
  "model": "laya-rl-agent",
  "answers": {
    "refund": {"type": "noul", "noul": 0.9937, "confidence": 0.9937, "action": {"act_probability": 1.0}},
    "intent": {"type": "choice", "choice": "refund", "probabilities": {"refund": 0.9999, "cancel": 0.0, "other": 0.0001}, "confidence": 0.9992}
  },
  "usage": {"input_tokens": 83, "output_tokens": 0},
  "routing": {"model": "multilingual", "repo": "/opt/models/multilingual", "reason": "explicit model='multilingual'"}
}
```

> The response (`answers`, `model`, `usage`, plus laya-server's own `routing`) is returned by the backend
> as-is; the gateway rewrites nothing. Backend errors (status code + body) are passed through the same way.

---

### Web Search (OpenCode gateway proxy)

| Item | Value |
|------|-----|
| Path | `/v1/tools/web-search` |
| Method | `POST` |
| Auth | ✅ required (the gateway's own API key, from `.env` / `GATEWAY_API_KEYS`) |
| Description | Searches the web and returns body paragraphs by keyword. The body is forwarded to the upstream named by `websearch.upstream` (`websearch.path`, default `v1/web-search`) and the upstream response comes back untouched. The search itself is implemented by [opencode-port](https://github.com/bruce1977/opencode-port) (OpenCode API Gateway). |

**Configuration (the `websearch` block of `config.yaml` plus the matching upstream):**

```yaml
upstreams:
- name: opencode
  type: opencode
  base_url: http://vm.gateway:11430
  models:
  - auto
  - opencode/*
  timeout: 300
  priority: 30
websearch:
  enabled: true
  upstream: opencode
  path: v1/web-search
  api_key_env: ''
  timeout: 300.0
```

- The search backend is an ordinary `upstreams` entry (`type: "opencode"`): its address, timeout and extra
  `headers` are configured once and reused by model aggregation (`/v1/models`, `/api/tags`), chat routing and
  `/probe`. The `opencode` type uses the OpenAI-compatible discovery endpoint (`GET /v1/models`, parsing
  `data[].id`) and probes health through `GET /health`.
- With `websearch.upstream` empty, the **first upstream of `type: "opencode"`** is picked; a name that matches
  no upstream returns **503** (it never silently falls back to another one).
- `api_key_env` defaults to the empty string = no signing (the current OpenCode gateway is anonymous). If the
  upstream requires a key, set it to a variable name (e.g. `OPENCODE_GATEWAY_API_KEY`) and the gateway calls
  it with `Authorization: Bearer <value>`; the key is read from the environment only, never stored in
  `config.yaml`.
- `model` is **pure passthrough**: it takes a model name that opencode-port supports (**not a model configured
  in llm-hub's `config.yaml`**), whatever the caller sends is forwarded, and when omitted the upstream chooses.
  The gateway does not resolve it through its own `aliases`. The upstream's model list is visible in the
  console under Tools · Web Search → "model list"; when `model` is omitted, opencode-port picks
  `web_search.model` → the first local model (`ollama-local/*`) → `models.auto.fallback` as its default, and
  that model only takes part in answer synthesis when `synthesize=true`.
- `synthesize: true` drives one extra model summarisation, whose duration follows the upstream's inference
  speed and can far exceed plain retrieval (5–15 s measured). The upstream itself caps a single inference at
  **300 seconds**: hitting `vm.gateway:11430` directly with `synthesize` measured **502**
  `{"error":{"message":"inference timed out (>300s)","code":"opencode_unavailable"}}` at 311 s. Keep
  `websearch.timeout` at 300 or above (the default is `300.0`) so the upstream's own error passes through
  (**502**) instead of being reported as a gateway **504**; raising it further does not make an answer arrive
  sooner. For `answer`, point `model` at a faster upstream.

**Request body fields:**

| Field | Type | Required | Description |
|------|------|------|------|
| `query` | string | ✅ | Search keywords (non-empty) |
| `model` | string | ❌ | A model name opencode-port supports (not one configured in llm-hub), forwarded verbatim; when omitted opencode-port's default is used |
| `top_k` | int | ❌ | Pages to fetch, 1–50 |
| `max_chars_per_page` | int | ❌ | Character cap per page; exceeding it sets `truncated: true` |
| `locale` | string | ❌ | Language hint for the search, e.g. `zh-CN` |
| `recency` | string | ❌ | Time window: `day` / `week` / `month` / `year` |
| `synthesize` | bool | ❌ | Also drive a model summarisation, filling in `answer` (noticeably slower) |
| `include_sources` | bool | ❌ | Whether to return a flat `sources` list of URLs |

**Request example:**

```bash
curl -X POST http://localhost:8888/v1/tools/web-search \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"query": "Ollama keep_alive default", "top_k": 2}'
```

**Response example (measured):**

```json
{
  "query": "Ollama keep_alive default",
  "model": "ollama-local/qwen3.5:9b",
  "engine": "opencode-websearch",
  "truncated": false,
  "answer": null,
  "text": "Documentation Index\nFetch the complete documentation index at: /llms.txt\n\nI want the model to stay loaded on the GPUs for an extended period of time, ...",
  "sources": [
    "https://docs.ollama.com/faq",
    "https://www.reddit.com/r/ollama/comments/1cnxnrv/how_to_set_keepalive_1_on_ollama_linux/"
  ],
  "results": [
    {
      "title": "FAQ",
      "url": "https://docs.ollama.com/faq",
      "snippet": "Alternatively, you can change the amount of time all models are loaded into memory by setting the OLLAMA_KEEP_ALIVE environment variable ...",
      "paragraphs": ["Documentation IndexFetch the complete documentation index at: /llms.txt"]
    }
  ]
}
```

- `text` is the concatenation of every fetched paragraph and can go straight into a prompt; `results[]` keeps
  title/URL/snippet/paragraphs per page; `answer` is non-null only with `synthesize: true`.
- Status codes: `websearch.enabled=false` → **404**; upstream missing → **503**; upstream unreachable →
  **502**; gateway wait exceeding `websearch.timeout` → **504**; non-POST → **405**; a body that is not a JSON
  object → **400**. Errors the upstream raises itself (such as the **502** from its 300 s inference cap when
  `synthesize` is on) are passed through with their body intact.

> Once this upstream is enabled, its free models also show up in `/v1/models` and can be routed for chat
> directly: `{"model": "opencode/big-pickle", ...}` against `POST /v1/chat/completions` just works.

---

### Rerank

| Item | Value |
|------|-----|
| Path | `/v1/rerank`, `/rerank`, `/api/rerank` |
| Method | `POST` |
| Auth | ✅ required |
| Description | Reorders documents by relevance (logprobs and embedding modes) |

**Request example:**

```bash
curl http://localhost:8888/v1/rerank \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-reranker:4b",
    "query": "How do I brew pour-over coffee?",
    "documents": [
      "Pour-over coffee needs water at about 90°C",
      "Python is a programming language",
      "Grind size affects extraction speed"
    ],
    "top_n": 3,
    "return_documents": true
  }'
```

**Request parameters:**

| Parameter | Type | Required | Description |
|------|------|------|------|
| `model` | string | ✅ | Rerank model name |
| `query` | string | ✅ | Query text |
| `documents` | array | ✅ | Documents to rank (`string` or `{"text": "..."}`) |
| `top_n` | integer | ❌ | Return only the top N results |
| `return_documents` | boolean | ❌ | Whether to echo document text (defaults to `rerank.return_documents`) |
| `instruction` | string | ❌ | Overrides the default instruction |

Compatibility aliases (for clients and gateways that use other field names):

| Alias | Maps to | Notes |
|------|----------|------|
| `model_name` / `model_id` | `model` | Precedence: `model` > `model_name` > `model_id` |
| `input` | `query` | String or single-element array |
| `texts` / `passages` | `documents` | Alternative spelling of the document list |

> With no `model`, it falls back to the first entry of `rerank.models`.

**Response (200 OK):**

```json
{
  "id": "rerank-abc123def456",
  "object": "list",
  "model": "qwen3-reranker:4b",
  "results": [
    {
      "index": 0,
      "rank": 0,
      "relevance_score": 0.91234567,
      "document": {
        "text": "Pour-over coffee needs water at about 90°C"
      }
    },
    {
      "index": 2,
      "rank": 1,
      "relevance_score": 0.63456789,
      "document": {
        "text": "Grind size affects extraction speed"
      }
    }
  ],
  "usage": {
    "rerank_count": 3,
    "returned_count": 2,
    "failed_count": 0
  },
  "meta": {
    "mode": "logprobs",
    "upstream": "ollama",
    "model": "qwen3-reranker:4b",
    "total_documents": 3,
    "returned_documents": 2,
    "failed_documents": 0,
    "normalized": false,
    "took_ms": 412.35
  }
}
```

**Scoring modes:**

| Mode | Suitable models | How it works |
|------|----------|------|
| `logprobs` (default) | Generative rerankers such as Qwen3-Reranker | A yes/no prompt, then the logprobs of the **first answer token** go through a two-class softmax to yield a probability |
| `embedding` | Vector models such as bge-m3 | Query and documents are embedded in one batch and scored by cosine similarity |

`relevance_score` is an **absolute probability** by default (0–1, comparable across requests). Set
`rerank.normalize: true` to stretch each batch's scores into `[0,1]`.

**Key configuration (`rerank` block):**

| Setting | Default | Description |
|------|--------|------|
| `mode` / `model_modes` | `logprobs` | Global mode / per-model override, e.g. `{"bge-m3:latest": "embedding"}` |
| `upstream_api` | `auto` | Ollama uses `/api/generate` (`raw_prompt=true`, so no `<think>` is injected), OpenAI-compatible uses `/v1/chat/completions`; force `chat` if needed |
| `top_logprobs` | `20` | Candidate tokens requested from the upstream, used to compute the yes/no probability |
| `max_tokens` | `2` | Only a couple of tokens are generated to decide yes/no |
| `normalize` | `false` | Whether to min-max normalise |
| `return_documents` | `true` | Whether responses carry the document text back |
| `on_error` | `score_zero` | When a single document fails: zero it or return 502 (all failing always returns 502) |
| `max_documents` / `max_document_chars` | `0` | Caps on document count and per-document length; 0 means unlimited |
| `embedding_query_prefix` / `embedding_clamp` | `""` / `true` | Query prefix for embedding mode; clamp cosine values into `[0,1]` |

---

### Chat completions

#### OpenAI-compatible format

| Item | Value |
|------|-----|
| Path | `/v1/chat/completions` |
| Method | `POST` |
| Auth | ✅ required |
| Description | OpenAI-compatible chat completions |

> Claude SDK / Claude Code speak the Anthropic envelope — use [`/v1/messages`](#messages-anthropic-compatible)
> instead. Same upstream, same alias resolution, only the outer request/response shape differs.

**Request example:**

```bash
curl http://localhost:8888/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant"},
      {"role": "user", "content": "What is machine learning?"}
    ],
    "temperature": 0.7,
    "max_tokens": 500,
    "stream": false
  }'
```

**Request parameters:**

| Parameter | Type | Required | Description |
|------|------|------|------|
| `model` | string | ✅ | Model name |
| `messages` | array | ✅ | Message list |
| `temperature` | float | ❌ | Sampling temperature (0–2) |
| `max_tokens` | integer | ❌ | Maximum tokens to generate |
| `stream` | boolean | ❌ | Whether to stream the response |
| `reasoning_effort` | string | ❌ | OpenAI-compatible reasoning effort (`none` / `low` / `medium` / `high`); on Ollama `none` disables thinking, unset lets the backend decide |
| `tools` | array | ❌ | Tool definitions |

**Response (200 OK):**

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1694000000,
  "model": "qwen3:8b",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Machine learning is a branch of AI..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 25,
    "completion_tokens": 100,
    "total_tokens": 125
  }
}
```

#### Ollama-native format

| Item | Value |
|------|-----|
| Path | `/api/chat` |
| Method | `POST` |
| Auth | ✅ required |
| Description | Ollama's native chat endpoint |

**Request example:**

```bash
curl http://localhost:8888/api/chat \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [
      {"role": "user", "content": "hello"}
    ],
    "stream": false
  }'
```

**Response (200 OK):**

```json
{
  "model": "qwen3:8b",
  "message": {
    "role": "assistant",
    "content": "Hello! What can I help you with?"
  },
  "done": true,
  "total_duration": 1234567890,
  "eval_count": 42
}
```

---

### Messages (Anthropic compatible)

#### Anthropic Messages format

| Item | Value |
|------|-----|
| Path | `/v1/messages` (plus the singular alias `/v1/message`) |
| Method | `POST` |
| Auth | ✅ required (`Authorization: Bearer` or `x-api-key`) |
| Description | Anthropic Messages-compatible endpoint: the gateway maps the request onto the OpenAI chat format, forwards it to the upstream (Ollama `/api/chat` or `/v1/chat/completions`), then wraps the answer back into an Anthropic envelope |

> An empty `model` or `"auto"` falls back to the first model of the **default upstream**; with no
> `default_upstream` configured and the model unresolvable, the call returns `404`. Unknown fields are
> preserved, and request logging plus alias resolution behave exactly like every other upstream path — but
> **streaming is not passthrough**: the upstream's NDJSON / SSE chunks are re-sequenced into the Anthropic
> event stream described below.

**Request example:**

```bash
curl http://localhost:8888/v1/messages \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "max_tokens": 256,
    "system": "You are a helpful assistant",
    "messages": [
      {"role": "user", "content": "What is machine learning?"}
    ],
    "stream": false
  }'
```

**Request parameters:**

| Parameter | Type | Required | Description |
|------|------|------|------|
| `model` | string | ❌ | Model name or alias; empty / `auto` falls back to the default upstream's first model (a gateway route name, not the upstream's raw name) |
| `system` | string / array | ❌ | System prompt; a string or an array of text blocks `[{"type":"text","text":"..."}]` |
| `messages` | array | ✅ | Message list of `{"role","content"}` (`role` ∈ `user`/`assistant`); `content` is a string or an array of blocks (including `image` blocks) |
| `max_tokens` | integer | ✅ | Maximum tokens to generate (must be > 0) |
| `temperature` | float | ❌ | Sampling temperature (0–2) |
| `top_p` | float | ❌ | Nucleus sampling cutoff (0–1) |
| `top_k` | integer | ❌ | Number of candidate tokens kept (Ollama upstreams only) |
| `stop_sequences` | array | ❌ | Stop sequences; generation ends when one is hit |
| `stream` | boolean | ❌ | `false` returns one JSON envelope, `true` returns an Anthropic SSE event stream |

**Response (non-streaming, 200 OK):**

```json
{
  "id": "msg_abc123",
  "type": "message",
  "role": "assistant",
  "model": "qwen3:8b",
  "content": [
    {"type": "text", "text": "Machine learning is a branch of AI..."}
  ],
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 25,
    "output_tokens": 100
  }
}
```

> `usage` is rebuilt from the upstream's own token counts: Ollama's `prompt_eval_count` / `eval_count`, or
> `prompt_tokens` / `completion_tokens` on OpenAI-compatible upstreams.

**Streaming (SSE) event order:** `message_start` → `content_block_start` → `content_block_delta*` (each
`delta.text` carries an incremental piece) → `content_block_stop` → `message_delta` (final `stop_reason` and
`usage`) → `message_stop`. Upstream failures arrive as an `event: error` instead of dropping the connection.

---

### Text completion

| Item | Value |
|------|-----|
| Path | `/v1/completions` |
| Method | `POST` |
| Auth | ✅ required |
| Description | OpenAI-compatible text completions |

#### Ollama-native format

| Item | Value |
|------|-----|
| Path | `/api/generate` |
| Method | `POST` (every other verb returns **405** with `Allow: POST`) |
| Auth | ✅ required |
| Description | Ollama's native completion endpoint: routed by `model` and forwarded as-is; full schema in `/docs` |

**Request parameters:**

| Parameter | Type | Required | Description |
|------|------|------|------|
| `model` | string | ✅ | Model name or gateway alias (aliases are resolved to the real model before forwarding) |
| `prompt` | string | ✅ | Prompt to continue |
| `raw` | boolean | ❌ | `true` skips the model's embedded chat template and continues `prompt` directly (needed by scoring/rerank-style prompts) |
| `system` / `template` / `context` | string/string/array | ❌ | Ollama-native fields, passed through untouched |
| `images` | array | ❌ | Base64 images for vision models |
| `format` | `"json"` / object | ❌ | Structured output: `json`, or a JSON schema directly |
| `stream` | boolean | ❌ | **Streams by default** (matching Ollama, NDJSON); `false` returns a single JSON object |
| `think` | boolean | ❌ | Ollama's native thinking switch: `false` disables thinking (no `thinking` in the response); unset leaves it to the model |
| `keep_alive` | string/number | ❌ | How long the model stays loaded, e.g. `"3m"` |
| `logprobs` / `top_logprobs` | boolean / int(1-20) | ❌ | Per-token log probabilities and how many candidates to return |
| `options` | object | ❌ | `temperature` / `top_p` / `num_predict` / `num_ctx` / `seed` / `stop` …; `num_ctx` may be overridden by `num_ctx_overrides` |

Any other field is preserved; the gateway injects nothing except the `params` declared on an alias.

**Request example:**

```bash
curl http://localhost:8888/api/generate \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "prompt": "Write a poem about programming:",
    "raw": true,
    "format": "json",
    "keep_alive": "3m",
    "options": {"temperature": 0, "num_predict": 256},
    "stream": false
  }'
```

**Response (200 OK):**

```json
{
  "model": "qwen3:8b",
  "response": "Code like verse,\nlogic like a river...",
  "done": true,
  "done_reason": "stop",
  "total_duration": 2345678901,
  "eval_count": 50
}
```

When streaming (`stream` true or omitted) the response is `application/x-ndjson`, one fragment of the
structure above per line, with `done: true` on the last one:

```
{"model":"qwen3:8b","response":"Code","done":false}
{"model":"qwen3:8b","response":" like verse","done":true,"done_reason":"stop","eval_count":8}
```

> As with chat, `/api/generate` only works against **Ollama upstreams**; route a model to LM Studio or an
> OpenAI-compatible upstream and the path lands on that `base_url` verbatim (usually a 404). Use
> `/v1/chat/completions` when you need something that works across backends.

---

### Embeddings

| Item | Value |
|------|-----|
| Path | `/api/embed`, `/v1/embeddings` |
| Method | `POST` |
| Auth | ✅ required |
| Description | Generates text embeddings |

**Request example:**

```bash
curl http://localhost:8888/api/embed \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "bge-m3:latest",
    "input": ["hello world", "intro to machine learning"]
  }'
```

**Request parameters:**

| Parameter | Type | Required | Description |
|------|------|------|------|
| `model` | string | ✅ | Embedding model name |
| `input` | string/array | ✅ | Input text (a string or an array) |

**Response (200 OK):**

```json
{
  "model": "bge-m3:latest",
  "embeddings": [
    [0.123, 0.456, 0.789, ...],
    [0.987, 0.654, 0.321, ...]
  ]
}
```

---

### Model management

These endpoints are blocked by default (`security.admin_endpoints: "deny"`) and need a config change to use.

| Path | Method | Description |
|------|------|------|
| `/api/pull` | `POST` | Pull a model |
| `/api/push` | `POST` | Push a model |
| `/api/delete` | `POST` | Delete a model |
| `/api/create` | `POST` | Create a model |
| `/api/copy` | `POST` | Copy a model |
| `/api/show` | `POST` | Show model information |
| `/api/blobs/*` | `GET/POST/DELETE` | Blob operations |

**Request example (requires `security.admin_endpoints: "allow"`):**

```bash
curl http://localhost:8888/api/pull \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "llama3"}'
```

---

### Management API (web console)

The read-only interface behind the web console, all under the `/v1/management` prefix (implemented in
`app/management.py`, with request details persisted by `app/request_log.py`). The console frontend itself is
served by the gateway as static files at **`/admin/`** (the bundle lives in `app/web`, see the "Web console"
section of USAGE); the static shell is unauthenticated — the login page has to open — but every data request
inside it goes through `require_admin`. Apart from login, every endpoint accepts **one of two credentials**:

- **Console session cookie**: `POST /v1/management/auth/login` exchanges `ADMIN_PASSWORD` for a 12-hour
  HMAC-signed HttpOnly cookie (what the browser uses);
- **Gateway API key**: any key in the `api_keys` table (`llmhub.db`), submitted as `Authorization: Bearer` or
  `X-API-Key`, so scripts and curl can reuse an existing credential. The table is imported from
  `GATEWAY_API_KEYS` / `GATEWAY_API_KEYS_FILE` (e.g. `data/.keys`) on the **first start** and the file is
  never read again; with `GATEWAY_API_KEYS_FILE` unset, `.keys` next to `CONFIG_PATH` is picked up
  automatically (e.g. `/config/.keys`).

The three `/api-keys` endpoints are the one exception: they accept **only the session cookie**
(`require_admin_session`). A gateway API key is something every client already holds, so accepting it there
would let a client mint itself another key — minting and revoking require a console login.

**Authentication:**

```bash
# Log in and receive a 12-hour HttpOnly session cookie
curl -c cookie.txt http://localhost:8888/v1/management/auth/login \
  -H "Content-Type: application/json" -d '{"password": "your-admin-password"}'

# From then on authenticate with the cookie
curl -b cookie.txt http://localhost:8888/v1/management/overview

# Or present a gateway API key directly
curl -H "X-API-Key: $GATEWAY_API_KEY" http://localhost:8888/v1/management/overview
```

`ADMIN_PASSWORD` is only a login credential, never a request credential: presenting it as `Bearer` /
`X-API-Key` is always **401**, and a key in the query string is not accepted either (it would end up in access
logs and browser history). When gateway auth is off (`auth.enabled: false`, or the first start found no keys
in the environment or key file, leaving the `api_keys` table empty) there is no API key to accept, so the
management endpoints fall back to cookie-only — they never become anonymously reachable.

| Method | Path | Description |
|------|------|------|
| `POST` | `/v1/management/auth/login` | Body `{"password": "..."}`; `200` with a cookie on success, `401` on a wrong password |
| `POST` | `/v1/management/auth/logout` | Clears the session cookie |
| `GET` | `/v1/management/auth/ping` | Protected heartbeat, returns `{"ok": true, "admin": true}` |
| `GET` | `/v1/management/overview` | KPI (including today's request count and today's token total `tokens_today`; optional query params `today_start` / `today_end` set the window, which the console fills with the browser's local midnight converted to UTC because a server-side UTC day would hide 00:00-08:00 local at UTC+8; omitted means UTC midnight to now), upstream summary, available model inventory (Ollama contributes the models resident in `/api/ps`; a managed upstream such as OpenCode has no `/api/ps`, so when the probe succeeds its declared models are counted too, with device `"cloud"` and VRAM `null`; derived models from a root `aliases:` entry are appended last with `derived: true`, the target model's device and `null` VRAM, and they are not counted again in `kpi.running_models`), each row carrying `model` / `display` (the `model_aliases` short name) / `alias`; plus the `request_log` switch and database information |
| `GET` | `/v1/management/upstreams` | Upstream list (status / loaded count / VRAM / probe latency) |
| `GET` | `/v1/management/upstreams/{name}` | Single-instance detail: `models` comes from `upstreams[].models` in `config.yaml` (`model_aliases` short names as the display name), prefix wildcards (such as `opencode/*`) are expanded into concrete models using the same discovery result routing uses, and root `aliases:` derived models belonging to this upstream are appended; `running_models` is what `/api/ps` reports (with device and VRAM/occupancy), while a managed upstream lists its declared models (device `"cloud"`, VRAM `null`) and derived rows are marked `derived: true` (target model's device, `null` VRAM); an unreachable upstream returns `503` with `"unreachable": true` |
| `GET` | `/v1/management/models/llm` | Per-upstream model inventory, sourced from `upstreams[].models` in `config.yaml` (models installed but not declared are not listed, since they cannot be routed through the gateway anyway); prefix wildcards are expanded from discovery results, and root `aliases:` derived models get their own row right after their target. Each row has `model` (the real id; the alias name for derived rows) / `display` (the `model_aliases` short name, or the declared name) / `alias` / `derived` / `wildcard` (came from a wildcard expansion) / `running` / `device` / `device_ratio` / `num_ctx` / `size_gib` / `vram_gib`; derived rows also carry `target` / `params` / `description` and a `null` VRAM. An OpenCode upstream that probes successfully reports every row `running: true` with device `"cloud"` and `null` VRAM. The response also carries `aliases` (name / `kind`: `rename` only renames, `derived` is a standalone model with pinned parameters / target / upstream / `params` / `description`) and `num_ctx_overrides` |
| `GET` | `/v1/management/models/rerank` | Rerank config projection (`api_key` always masked as `••••••`), including `upstream_api` / `on_error` and request/response examples |
| `GET` | `/v1/management/models/systemone` | System One config projection (masked the same way), with request/SSE examples |
| `GET` | `/v1/management/tools/websearch` | Web Search config projection: `enabled` / `endpoint` / `upstream` / `upstream_type` / `path` / `base_url` (derived from the provider upstream) / `api_key_env` / `api_key` (masked) / `timeout` / `provider_configured` |
| `GET` | `/v1/management/requests` | Request details: `start`/`end`/`upstream`/`model`/`status`(2xx\|3xx\|4xx\|5xx)/`path`/`page`/`page_size`, returning `stats` + `rows` + `pagination` + `request_log` (switch state). **API calls only**: the query always carries `request_log.API_PATH_SCOPE` (`path LIKE '/v1/%' OR path LIKE '/api/%'`), so rows such as `/health`, `/metrics`, `/openapi.json` and `/admin/*` are still stored but never appear in lists, charts, KPIs or failure stats |
| `GET` | `/v1/management/requests/{id}` | A single detail row (request/response bodies truncated to 8 KB, sensitive fields redacted) |
| `GET` | `/v1/management/stats/timeseries` | Time-series aggregation: `bucket=hour\|day`, each bucket with `count` / `errors` / `p95`. `tz_offset` (minutes to add to UTC to reach the caller, UTC+8 = `480`, range -840..840) shifts **day** buckets so each bar is one local calendar day instead of one UTC day; hour buckets stay UTC because the console renders them as instants |
| `GET` | `/v1/management/stats/distribution` | Grouped counts: `group_by=upstream\|model\|status`, returning `{group_by, start, end, items:[{key, count}]}`; `status` splits into `ok` (<500, 4xx included) / `error` (≥500) — the same threshold as the KPI error rate, the timeseries error bars and `/stats/failures` — and rows with no matched upstream are counted under `-` |
| `GET` | `/v1/management/stats/failures` | Failure stats: `by_code` / `by_upstream` / `samples` |
| `GET` | `/v1/management/api-test/models` | Available models, defaults and masked keys per API family: `kinds` is keyed by `generate` / `chat` / `messages` / `embed_ollama` / `embed_openai` / `rerank` / `systemone` / `websearch`, each with `method` / `endpoint` / `family` / `model_required` / `free_model` / `enabled` / `source` (which config field the options came from) / `models[]` (`model` / `display` / `alias` / `derived` / `target` / `upstream` / `type` / `running` / `device` / `api_key` (masked) / `unknown`) / `default_model` / `api_key` (masked) / `api_key_env`; the top level also carries `max_response_bytes`. With `api_models` empty the lists are derived from the config; the native `/api/...` endpoints (`generate`, `embed_ollama`) list only models of `type: "ollama"` upstreams, while the `/v1/...` endpoints (`chat`, `messages`, `embed_openai`) also list `lmstudio` / `opencode` models; an explicit `api_models` list is returned as written (a closed set: blank placeholder entries ignored, `free_model=false`). All upstreams are probed in one pass, so the whole console shares a single cache, refreshed on first open and on "probe again" |
| `POST` | `/v1/management/api-test` | Body `{"kind": "generate", "body": {...}}`; the server issues the request through the gateway's own service and returns an envelope with `status` / `ok` / `duration_ms` / `ttft_ms` / `content_type` / `stream` / `requested_model` / `model` / `upstream` / `injected` / `body` / `text` / `truncated` / `error`. A `kind` outside the allowlist, or a model-requiring family without `model` → `400` |
| `POST` | `/v1/management/bench` | Starts a speed-test job, body `{"upstream": "...", "model": "...", "rounds"?: 4, "max_tokens"?: 500, "think"?: "auto"}`, returning `{"job_id": "...", "status": "pending"}` immediately; the job runs in the background |
| `GET` | `/v1/management/bench/history` | The most recent **3** speed tests for that `upstream` + `model` (`?upstream=&model=`), newest first, each row with `decode_tok_s` / `decode_sd` / `prefill_tok_s` / `ttft_ms` / `timing_source` and the full `payload`. **Registered before `/bench/{job_id}`**, otherwise "history" would be read as a job id |
| `GET` | `/v1/management/bench/{job_id}` | Polls a job: `status` is `pending` / `running` / `done` / `failed`, `progress` is `{completed, total, warmup}`; on success `result` carries the per-round detail and the `steady` summary, on failure `error` explains why |
| `GET` | `/v1/management/api-keys` | Caller API key list (**session cookie only**): `{items: [{id, masked, label, source, created_at}], total}`, where `source` is `console` (minted in the console) or `file` (imported from the environment on first start). Masks only — neither plaintext nor hashes |
| `POST` | `/v1/management/api-keys` | Mints a new key (**session cookie only**): body `{"label": "..."}`, `label` required (empty → `400`, longer than 100 characters → `422`), `201` returns `{item, secret}`. **This response is the only time the plaintext ever leaves the server**; no endpoint can return it again |
| `DELETE` | `/v1/management/api-keys/{id}` | Revokes a key (**session cookie only**): `200` returns `{"deleted": id}`, an unknown `id` → `404`. The in-memory verification set reloads immediately, so the very next request fails |

**Status codes:**

| Code | Situation |
|----|------|
| `200` | Normal |
| `400` | Malformed timestamps, `start > end`, an invalid `page`/`page_size`/`status`/`bucket`/`group_by`, or an `api-test` `kind` outside the allowlist / a model-requiring family without `model` |
| `401` | Neither a valid session cookie nor a gateway API key; a wrong login password or user name; `ADMIN_PASSWORD` presented as a request credential; or `/api-keys` reached with only a gateway API key (those three accept the session cookie alone) |
| `404` | Unknown `upstream` name, request-log `id`, or `api-keys/{id}` |
| `503` | `ADMIN_PASSWORD` not configured; the probed upstream is unreachable (`upstreams/{name}`); the `/admin` frontend has not been built (`app/web` missing) |

**Distribution and config projection examples:**

```json
// GET /v1/management/stats/distribution?group_by=upstream
{ "group_by": "upstream", "start": "2026-11-02T05:00:00", "end": "2026-11-03T05:00:00",
  "items": [ { "key": "ollama-gpu", "count": 642 },
             { "key": "lmstudio", "count": 380 },
             { "key": "-", "count": 12 } ] }

// group_by=status has only two keys: ok (status < 500, 4xx included) and error (status >= 500)

// GET /v1/management/tools/websearch
{ "enabled": true, "endpoint": "/v1/tools/web-search", "upstream": "opencode",
  "upstream_type": "opencode", "path": "v1/web-search",
  "base_url": "http://vm.gateway:11430", "api_key_env": "OPENCODE_API_KEY",
  "api_key": "••••••", "timeout": 300.0, "provider_configured": true }
```

`base_url` / `upstream_type` are derived from `websearch.upstream` (the first `type: "opencode"` upstream when
none is named) through the same helper `WebSearchService` uses, `Config.websearch_upstream_obj()`;
`api_key` only reports whether the environment variable named by `websearch.api_key_env` is set — the value
never leaves the server.

**Request log (SQLite):**

Every request occupies one row in the `requests` table, covering request parameters, timings and the result:

| Group | Fields |
|------|------|
| Timing | `ts` (arrival), `ts_end` (completion; for streams, when the stream closes), `duration_ms`, `ttfb_ms`; `ts`/`ts_end` are second-precision, so read sub-second durations from `duration_ms` |
| Request | `method`, `path`, `query`, `requested_model` (the name the client sent), `model` (after alias resolution), `request_body`, `req_size`, `stream`, `client_ip` |
| Result | `status`, `upstream`, `response_body`, `res_size`, `tokens_in`, `tokens_out`, `tokens`, `error` |
| Flags | `request_body_truncated`, `response_body_truncated` |

- **Token usage is stored per direction**: `tokens_in` / `tokens_out` are the input (prompt) and output
  (generation) token counts, taken from `usage.prompt_tokens` / `usage.completion_tokens` on
  OpenAI-compatible responses or `prompt_eval_count` / `eval_count` on Ollama responses; `tokens` keeps the
  total and prefers the upstream's own `total_tokens` when reported (it exceeds the sum of the two once
  reasoning tokens are counted). When the upstream reports no usage, all three are `NULL`, which stays
  distinguishable from a genuine `0`.
- **Logging switch**: `request_log.enabled` in `config.yaml` (default `true`) is read per request, no restart
  needed. Turned off, the proxy stops capturing bodies and writing rows, and `request_log.enabled` in
  `/overview` and `/requests` reports `false`; turning it back on resumes logging. Config hot reload compares
  the file mtime at most once per second, so the switch takes effect in **about a second** (measured). The
  database path and retention stay environment-driven.
- Rows go to `llmhub.db` next to `config.yaml` (`/data/llmhub.db` in the container), WAL mode;
  `REQUEST_LOG_DB` overrides the path, `REQUEST_LOG_RETENTION_DAYS` (default 30, 0 = keep forever) controls
  pruning.
- Old databases upgrade automatically: on startup `PRAGMA table_info(requests)` is compared and missing
  columns are added with `ALTER TABLE ADD COLUMN`.
- Writes go through a bounded in-memory queue and land in batches, never blocking the forwarding path; a full
  queue drops rows and logs a warning.
- Request/response bodies are truncated to 8 KB, and `authorization`, `api_key`, `token`, `password` and
  similar fields are replaced with `***` before they are stored.
- **`HEAD` requests are forwarded but not logged**: they carry no bodies, and uptime probes send them
  constantly, so recording them would only bury real calls. Forwarding is unchanged; the only gate is
  `logging_on = self._logging_on(cfg) and method != "HEAD"`. (With the default
  `allowed_http_methods: [GET, POST]`, HEAD never reaches the proxy anyway — it gets a `405`.)

**Caller API keys (stored in SQLite, implemented in `app/key_store.py`):**

A key is no longer a line in a key file but a row in the `api_keys` table of `llmhub.db`, minted and revoked
from the console at any time:

| Field | Description |
|------|------|
| `id` | Autoincrement primary key, used by `DELETE /api-keys/{id}` |
| `hash` | Lowercase hex `sha256(plaintext)`, `UNIQUE` — **no plaintext in the database** |
| `masked` | Display mask, computed and stored at mint time so the list view reconstructs nothing |
| `label` | Description (max 100 characters), required at mint time |
| `source` | `console` = minted in the console; `file` = imported from the environment on first start |
| `created_at` | Creation time, second-precision ISO UTC |

- **Fixed format and length**: `sk-hub-` + 40 base62 characters (sampled from `secrets`) = **47 characters**,
  about 238 bits of randomness. The prefix is not part of the secret; it makes a leaked string recognisable at
  a glance in logs and client configs. The mask keeps the prefix plus the first and last 4 characters of the
  body (`sk-hub-FNdc…SVvw`); keys imported from a file may have any shape (e.g. `sk-...`), so they degrade to
  the first 8 and last 4 characters of the whole string, and anything 12 characters or shorter is all dots.
- **Why SHA-256 and not bcrypt/argon2**: a generated key is 238 bits of randomness with no weak passphrase to
  brute-force, and the gateway verifies a key on **every** client request; a slow KDF would add tens of
  milliseconds to each call, turning the gateway into its own DoS target, without protecting a secret nobody
  can guess. A database leak reproduces no usable credential either.
- **Verification stays in memory**: `ApiKeyStore` loads every hash into a `frozenset` at startup and `verify()`
  is a single set lookup, so the request path never touches SQLite. Every write (mint / revoke / import)
  reloads the set, so a new key works immediately and a revoked one dies immediately, with no restart.
- **Imported once, only into an empty table**: the first start writes the keys from `GATEWAY_API_KEYS` /
  `GATEWAY_API_KEYS_FILE` (or `.keys` next to `CONFIG_PATH`) with `source: "file"`, and **the file is never
  read again** — a key revoked in the console does not come back after a restart, and editing the file adds
  nothing. With `auth.enabled` and zero keys in the table (and no IP allowlist) startup is refused, with an
  error telling you to feed the empty table through the environment first. After the import, any key left in
  the environment that is not in the table is logged at startup, masked, one by one, because those credentials
  no longer work.
- The legacy `auth.api_keys:` in `config.yaml` is still shown read-only; a key written there is ignored with a
  warning. The secrets themselves never live in `config.yaml`.

**Model speed test (bench, implemented in `app/bench.py` + `app/bench_store.py`):**

The methodology matches `bench_llm.mjs`, in pure Python with no Node dependency:

- **Warm-up**: one round by default, excluded from the stats; then 4 measured rounds, reported as mean /
  standard deviation / min / max / coefficient of variation.
- **TTFT**: wall-clock time until the first token (milliseconds). Note that a non-streaming Ollama request
  never exposes that moment, so the test **always streams**.
- **Decode (tok/s)**: prefers the server-reported `eval_duration` (Ollama `/api/chat`) or `usage` +
  `timings.predicted_ms` (OpenAI-compatible), falling back to the client's wall clock; the basis is recorded in
  `timing_source`: `server` / `client` / `client*` (streamed but without server timings).
- **Prefill (tok/s)**: `prompt_eval_duration` / `prompt_ms` → prompt tokens per second, falling back the same
  way.
- **Thinking models**: when the model name matches patterns such as `qwen3` / `deepseek-r1` / `reasoning`, the
  test adds `think: false` (Ollama) or a minimal reasoning budget so a chain of thought does not drag decode
  down; the `think` parameter overrides this explicitly (`auto` / `true` / `false`).
- **Model name alignment**: the upstream's model list is fetched first as a preflight, then resolved exact →
  normalised (dropping `:latest`) → substring; no match is an error rather than silently testing another model.
- **Proxy**: the internal `httpx.AsyncClient` uses `trust_env=False`, like the gateway's main client — with
  `HTTP_PROXY` exported on the host, requests to the upstream would otherwise be routed through it
  (manifesting as `502`).

Results land in the `bench_results` table of `llmhub.db` (same file as `requests`, WAL):

| Group | Fields |
|------|------|
| Identity | `id`, `upstream`, `model`, `created_at` (when the job finished) |
| Target | `server` (`ollama` / `lm`), `host`, `port` |
| Parameters | `rounds`, `warmup`, `max_tokens` |
| Metrics | `decode_tok_s` (mean), `decode_sd`, `prefill_tok_s`, `ttft_ms`, `timing_source`, `prompt_tokens`, `gen_tokens` |
| Detail | `payload` (full JSON: per-round data + `steady` summary + `config`) |

Only the newest **3** rows are kept per `(upstream, model)`: after each insert, rows from the 4th on are
deleted by `created_at DESC, id DESC`. Jobs themselves live in memory only (`_BENCH_JOBS`, capped at 200
entries, swept once finished), so old job ids return `404` after a restart — use `/bench/history` for past
results.

A job runs on its **own thread and event loop**, so it does not live inside the `POST /bench` request; the
result row enters the write queue before the job turns `done`, and `/bench/history` flushes the queue before
reading, so querying history the instant a job reports `done` still finds the row.

**API tester (api-test, implemented in `app/api_test.py`):**

Every API doc block in the console has a **Test** button to the right of its "Request parameters" heading,
opening the tester in a popup (not inline under the docs). The browser cannot call a business endpoint
directly: `/api/generate`, `/v1/rerank` and friends authenticate the **caller** with a gateway API key, and the
console session cookie deliberately is not one (see `admin_auth.require_admin`). So the request runs
**server-side** — `POST /v1/management/api-test` rebuilds the body from the form into a real ASGI `Request` and
hands it to the **same service** the gateway uses for that endpoint (`ProxyService.forward` /
`RerankService.handle` / `SystemOneService.handle` / `WebSearchService.handle` / `MessagesService.handle`).
Alias resolution, pinned parameter injection, `num_ctx_overrides` clamping, blocked paths and request-log
capture all behave exactly as they do for an external caller, so the page shows the API's real behaviour.

- `KINDS` is a **closed allowlist** (8 kinds mapping onto `proxy` / `rerank` / `systemone` / `websearch` /
  `messages` executors), and each kind's upstream is still chosen by normal routing, so this route cannot be
  turned into a proxy for an arbitrary address.
- The model selector is narrowed by the `api_models` family (see the config reference in USAGE): every option
  shows **only a name or alias**, with upstream, device and running state in the tooltip; names explicitly
  claimed by another family no longer appear under generate/chat (`api_models.embed` / `rerank` / `systemone`
  plus `rerank.models`, comparing with `:latest` ignored). `api_models.websearch` does not count as a claim —
  the search endpoint is chat passthrough at heart, so a managed provider's routing model (such as
  `opencode/auto`) must stay selectable; fallbacks like `systemone.model` do not count either. **A non-empty
  `api_models` list is a closed set**: returned as written, with no provider-side narrowing, blank placeholder
  entries ignored, and `free_model` set to `false`. The "Available models" tables and every request/response
  example read that same payload. Only **derived** `systemone` / `websearch` families keep `free_model: true`
  (a laya-server routing enum, an opencode-port model id), where the dropdown ends with a "custom model…"
  item that swaps into a free-text input; picking a listed name again collapses it. The tester opens pre-filled with each
  parameter's initial value, "restore defaults" refills them and "clear" empties every field. **The model can be
  cleared too**: after the × next to the field (or the blank item in the dropdown), the body carries no
  `model` — families that need one are stopped by the tester with "pick a model first", while
  `systemone` / `websearch` leave the decision to the endpoint (System One still injects `systemone.model` from
  config). "Restore defaults" re-selects `default_model`, and "clear" clears the model as well. **Boolean
  parameters with a documented default render as checkboxes**, and both states are sent explicitly as `true` /
  `false`; other empty values are omitted from the body so the endpoint applies its own default. The three LLM
  testers start with `stream` off (the endpoints stream by default; the tester wants one JSON object first);
  generate also offers a `think` checkbox (off by default, Ollama's native top-level field), and chat offers
  the OpenAI-compatible `reasoning_effort` (Ollama uses `none` to disable thinking).
- Credentials never leak: keys in upstream `headers` and the environment variables named by
  `systemone.api_key_env` / `websearch.api_key_env` are always reported as `••••••`, and the page renders them
  as read-only mask rows.
- Responses are capped at `max_response_bytes` (128 KB) with `truncated` set — a tester is for reading an
  answer, not downloading one. A streaming response is collected chunk by chunk and `ttft_ms` is when the
  first chunk arrived; `_drain` closes the iterator, and the proxy's generator writes the log row on
  `aclose`, so tested calls show up in "request details" as well.
- The envelope's `stream` is **the transport the gateway chose**, from the same source as the `stream` column
  in the request log: when the caller sent no `stream`, `proxy.STREAM_BY_DEFAULT` decides. Whether the response
  actually arrived in chunks is a question for `content_type` — `application/x-ndjson` / `text/event-stream` —
  and the console keys the "streaming" badge and the first-token timing off that, so a single JSON object is
  never described as a stream.
- Gateway and upstream refusals are not swallowed: `400` (invalid body), `404` (model unroutable / feature
  disabled), `413` (too large), `503` (no provider / provider not deployed), `502`·`504` (upstream unreachable
  or timed out) all land in the envelope (`ok: false`, `error: "HTTP 404"`), so the page sees exactly what a
  real caller would. Only refusals by the routing layer itself use the HTTP status (`kind` outside the
  allowlist, or a model-requiring family without `model` → `400`).

**Gateway boundary:** `/v1/management` is exempt from `security.path_allowlist` the same way `/admin` is (it
uses session authentication of its own), but it is still subject to the HTTP method allowlist, the client IP
allowlist and rate limiting. The console is read-only: it exposes no write endpoint for `config.yaml`. Two
groups of endpoints **do** send real requests outward: the speed test (`/bench`) additionally writes to
`bench_results`, and the API tester (`/api-test`) only writes a request-log row like any other call — neither
touches the config.

---

## Streaming responses

### OpenAI format (SSE)

Set `"stream": true` to enable streaming:

```bash
curl http://localhost:8888/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [{"role": "user", "content": "hello"}],
    "stream": true
  }'
```

**Response format (Server-Sent Events):**

```
data: {"id":"chatcmpl-abc","choices":[{"delta":{"role":"assistant","content":""},"index":0}]}

data: {"id":"chatcmpl-abc","choices":[{"delta":{"content":"he"},"index":0}]}

data: {"id":"chatcmpl-abc","choices":[{"delta":{"content":"llo"},"index":0}]}

data: {"id":"chatcmpl-abc","choices":[{"finish_reason":"stop","index":0}]}

data: [DONE]
```

### Ollama format (NDJSON)

```bash
curl http://localhost:8888/api/chat \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [{"role": "user", "content": "hello"}],
    "stream": true
  }'
```

**Response format (NDJSON):**

```json
{"model":"qwen3:8b","message":{"role":"assistant","content":"he"},"done":false}
{"model":"qwen3:8b","message":{"role":"assistant","content":"llo"},"done":false}
{"model":"qwen3:8b","done":true,"done_reason":"stop"}
```

`/api/generate` (the `response` field), like `/api/chat`, `/v1/chat/completions` and `/v1/completions`, is a
**stream-by-default** endpoint: omit `stream` and the request is forwarded as a stream, matching Ollama.

### Anthropic Messages format (SSE)

With `"stream": true` on `/v1/messages`, the gateway re-sequences the upstream chunks into the Anthropic event
stream (`message_start` → `content_block_start` → `content_block_delta*` → `content_block_stop` →
`message_delta` → `message_stop`). Each delta looks like this:

```
event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"he"}}
```

---

## Error responses

Every error response follows one shape:

```json
{
  "detail": "description of what went wrong"
}
```

### Error status codes

| Status | Type | Description |
|--------|----------|------|
| `400` | Bad Request | Invalid JSON, a missing required field, or a malformed request |
| `401` | Unauthorized | No API key supplied |
| `403` | Forbidden | Invalid API key, IP outside the allowlist, or a blocked endpoint |
| `404` | Not Found | Unknown endpoint, or the model was not found |
| `405` | Method Not Allowed | Unsupported HTTP method (governed by `security.allowed_http_methods`, GET/POST only by default); POST-only endpoints such as `/api/generate`, `/v1/rerank`, `/v1/systemone` and `/v1/tools/web-search` also send `Allow: POST` |
| `413` | Request Entity Too Large | Body exceeds the size limit (32 MB by default) |
| `422` | Unprocessable Entity | Body validation failed |
| `502` | Bad Gateway | The upstream is unreachable or returned an error |
| `504` | Gateway Timeout | The upstream timed out |

### Error examples

**Missing API key (401):**

```bash
curl http://localhost:8888/v1/models
```

```json
{
  "detail": "Missing API key. Provide it via 'Authorization: Bearer <key>' or 'X-API-Key: <key>' header."
}
```

**Invalid API key (403):**

```bash
curl http://localhost:8888/v1/models -H "Authorization: Bearer wrong-key"
```

```json
{
  "detail": "Invalid API key."
}
```

**Model not found (404):**

```bash
curl http://localhost:8888/api/chat \
  -H "Authorization: Bearer sk-key" \
  -d '{"model":"unknown","messages":[{"role":"user","content":"hi"}]}'
```

```json
{
  "detail": "No upstream configured for model 'unknown'. Add it to an upstream's 'models' list, or set 'default_upstream'."
}
```

**Blocked endpoint (403):**

```bash
curl http://localhost:8888/api/delete \
  -H "Authorization: Bearer sk-key" \
  -d '{"name":"model"}'
```

```json
{
  "detail": "Endpoint '/api/delete' is disabled on this public gateway."
}
```

**Body too large (413):**

```json
{
  "detail": "Request body too large."
}
```

**Upstream unreachable (502):**

```json
{
  "detail": "Upstream 'local-main' is unreachable: Connection refused"
}
```

**Upstream timeout (504):**

```json
{
  "detail": "Upstream 'local-main' timed out after 600s."
}
```

---

## Alias parameter injection

An alias both renames the model and injects parameters.

> Every entry under a root `aliases:` block is **a model of its own**: the target model stays callable and
> keeps running with its own defaults, while the alias carries its own pinned parameters. That is why the
> console lists it as a separate row (device inherited from the target's instance, VRAM shown as `-`, and not
> counted twice in the KPI). If you only want a shorter name for an existing model with no injected
> parameters, use `upstreams[].model_aliases` — that changes the display and call name without creating a new
> model.

**Configuration example:**

```yaml
aliases:
  qwen3.5:4b-nothink:
    model: qwen3.5:4b
    params:
      think: false
```

**Usage example:**

```bash
# Using the alias (mapped to qwen3.5:4b with think=false injected)
curl http://localhost:8888/api/chat \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -d '{
    "model": "qwen3.5:4b-nothink",
    "messages": [{"role": "user", "content": "1+1=?"}],
    "stream": false
  }'
```

---

## Tool calling

OpenAI-style tool calls (`tool_calls`) are supported.

**Request example:**

```bash
curl http://localhost:8888/v1/chat/completions \
  -H "Authorization: Bearer sk-gateway-example-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:8b",
    "messages": [{"role": "user", "content": "Read main.py"}],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "read_file",
          "description": "Read a file's contents",
          "parameters": {
            "type": "object",
            "properties": {
              "path": {"type": "string", "description": "File path"}
            },
            "required": ["path"]
          }
        }
      }
    ]
  }'
```

**Response example:**

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": null,
        "tool_calls": [
          {
            "id": "call_abc123",
            "type": "function",
            "function": {
              "name": "read_file",
              "arguments": "{\"path\":\"main.py\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ]
}
```
