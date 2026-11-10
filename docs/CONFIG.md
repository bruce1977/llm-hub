# LLM Hub — `config.yaml` node by node

Every node and field the gateway really reads, with its default, the code that consumes it and
what changes when you set it. The full manual lives in [USAGE.md](USAGE.md); endpoints are in
[API.md](API.md); deployment and environment variables in [DEPLOY.md](DEPLOY.md).
Chinese version: [CONFIG_CN.md](CONFIG_CN.md).

**Contents** ·
[How the file is read](#how-the-file-is-read) ·
[Node map](#node-map) ·
[`server`](#server--the-gateway-itself) ·
[`routing`](#routing) ·
[`upstreams`](#upstreams--the-backends) ·
[`default_upstream`](#default_upstream) ·
[`aliases`](#aliases--a-model-of-its-own) ·
[`num_ctx_overrides`](#num_ctx_overrides) ·
[`api_models`](#api_models--what-each-api-family-may-offer) ·
[`rerank`](#rerank) ·
[`systemone`](#systemone) ·
[`websearch`](#websearch) ·
[`request_log`](#request_log) ·
[`security`](#security) ·
[Path surface](#path-surface--the-closed-set-of-callable-paths) ·
[Console-only fields](#fields-the-console-reads-and-routing-does-not) ·
[Removed nodes](#nodes-that-no-longer-exist) ·
[Environment variables](#settings-that-stay-outside-configyaml) ·
[Checking a node](#checking-whether-a-key-actually-does-something)

## How the file is read

| Fact | Detail |
|---|---|
| Path | `CONFIG_PATH` (default `/data/config.yaml`); `scripts/start-local.ps1` passes `./data/config.yaml` |
| Format | YAML 1.2. Secrets still do not belong here; comments do |
| Legacy file | With no `config.yaml` beside it, a pre-YAML `config.json` is loaded with a warning — JSON is valid YAML, so renaming it is enough |
| Schema | The Pydantic models in `app/config.py` (`Config` and its child models) — that file *is* the schema |
| Missing node | Every node is optional; an absent one uses the model defaults listed below. Only `upstreams[].name` and `upstreams[].base_url` are required fields |
| Hot reload | `ConfigManager` compares the file mtime at most once per second, so a save lands within ~1s |
| Unknown key | **Silently ignored** — see [Checking a node](#checking-whether-a-key-actually-does-something) |

Two read timings matter when you change a value:

- **Per request** — routing, aliases, `rerank.*`, `systemone.*`, `websearch.*`,
  `request_log.enabled` and all of `security.*`. A restart is never needed.
- **Startup only** — the listening sockets (`server.host` / `server.port` /
  `security.public.port` are bound once by `app/serve.py`), the shared upstream connection pool
  (sized from `server.max_concurrency`, `server.upstream_keepalive_expiry`,
  `server.request_timeout`) and the `api_keys` table scan. Editing these needs a restart.

## Node map

| Node | Model in `app/config.py` | Consumed by | Decides |
|---|---|---|---|
| `server` | `ServerConfig` | `main.py` lifespan, `serve.py`, `proxy.py` | Bind address, internal port, log level, upstream timeouts, connection pool |
| `routing` | `RoutingConfig` | `Config.resolve()` | Whether an undeclared **named** model 404s or falls back |
| `upstreams[]` | `UpstreamConfig` | `Config`, `proxy.py`, `main.py`, `management.py` | Every backend: address, dialect, model catalog, headers, priority |
| `default_upstream` | `str \| None` | `Config.default_upstream_obj()` | The fallback for an unmatched named model (`routing.strict: false`) and for **every model-less call** |
| `aliases` | `dict[str, AliasConfig]` | `Config.resolve()`, `proxy.py` | Derived models and their force-injected parameters |
| `num_ctx_overrides` | `dict[str, int]` | `proxy.py`, `messages.py` | Forced Ollama context length per real model |
| `api_models` | `ApiModelsConfig` | `management.py` (console testers only) | Which models each API family may offer |
| `rerank` | `RerankConfig` | `rerank.py`, `management.py` | The `/v1/rerank` scoring engine |
| `systemone` | `SystemOneConfig` | `systemone.py`, `management.py` | `POST /v1/systemone` forwarding |
| `websearch` | `WebSearchConfig` | `websearch.py`, `management.py` | `POST /v1/tools/web-search` forwarding |
| `request_log` | `RequestLogConfig` | `proxy.py`, `management.py` | Whether calls are recorded at all |
| `security` | `SecurityConfig` | `auth.py`, `bridge.py`, `main.py` middleware | Zones, key enforcement, public surface, hardening, rate limit |

## `server` — the gateway itself

| Field | Default | Read by | Effect |
|---|---|---|---|
| `host` | `0.0.0.0` | `main.py`, `serve.py` | Bind address for every listener. `0.0.0.0` **plus** `security.internal.allow_anonymous: true` logs an "INTERNAL ZONE IS FULLY OPEN" warning at startup |
| `port` | `8000` | `Config.listen_ports()`, `serve.py` | The **internal (trusted LAN)** socket. This port is the zone anchor; it is not a setting you can override by IP or header |
| `log_level` | `info` | `main.setup_logging()`, `app/logging.json` | One knob for every logger, uvicorn's access log included: `2026-11-10 20:31:18.718 \| INFO    \| uvicorn.access \| 127.0.0.1:61641 - "GET /v1/models HTTP/1.1" 404 Not Found`. `debug` prints alias resolution and routing decisions; that is the level to use when a rerank score or a route looks wrong |
| `request_timeout` | `600.0` | `main.py` (httpx client), `proxy.py`, `messages.py`, `rerank.py`, `systemone.py`, `websearch.py` | The connect-10s/read-`request_timeout` budget for a call that does not set a per-upstream or per-feature timeout |
| `max_concurrency` | `64` | `main.py` → `httpx.Limits` | Sizes the shared pool: `max_connections = max(16, it)`, `max_keepalive_connections = min(32, max(8, it))` |
| `upstream_keepalive_expiry` | `30.0` | `main.py` → `httpx.Limits` | How long an idle keep-alive connection to a backend stays open |

`follow_redirects=False` and `trust_env=False` are code-level constants, not config: the gateway
never follows an upstream redirect and never lets a system-wide `HTTP_PROXY` hijack a backend call.

## `routing`

| Field | Default | Read by | Effect |
|---|---|---|---|
| `strict` | `false` | `Config.resolve()` | `true` → a **named** model no upstream declares and no alias resolves is a **404**. `false` → the request falls back to `default_upstream` |

`strict` refuses to *guess* a named model; it does not refuse a body that carries no `model` at all.
An empty model name — a `GET`, or an Ollama admin call whose body carries `name` instead, e.g.
`POST /api/pull {"name":"qwen3:4b"}` — always resolves to `default_upstream`, **even under
`strict: true`**. Before that rule landed such a call dead-ended in
`404 No upstream configured for model ''`, which is what made `admin_endpoints: allow` look broken.

One prerequisite remains: `default_upstream` must name an existing `upstreams[].name`. A dangling
name resolves to nothing, so the **404** is then the correct answer — see
[`default_upstream`](#default_upstream).

## `upstreams` — the backends

A list; each entry is one instance. `name` and `base_url` are the only required fields anywhere in
the file.

| Field | Default | Read by | Effect |
|---|---|---|---|
| `name` | — (required) | everywhere | The key `aliases[].upstream`, `default_upstream`, `websearch.upstream` and the console refer to |
| `base_url` | — (required) | `proxy.py`, `main.py` | Instance address. Validated: must be `http(s)`, no embedded credentials, never a cloud metadata target; private LAN addresses are allowed on purpose |
| `type` | `ollama` | `UpstreamConfig.tags_path()` / `health_path()` / `parse_model_names()`, `main.py` | `ollama` → native `/api/*` + `/v1/*`, discovery via `api/tags`; `lmstudio` → OpenAI-compatible only, discovery via `v1/models`; `opencode` → hosted OpenAI-compatible chat + the web-search provider, health via `health`, rows reported as `device: "cloud"` |
| `models` | `[]` | `Config.find_upstream_for_model()`, `main.py`, `management.py` | The catalog this instance serves: exact ids or prefix wildcards (`qwen3:*`, `opencode/*`). An installed-but-undeclared model is not routable, and a wildcard is expanded through the upstream's own model list (60s-cached discovery) |
| `model_aliases` | `{}` | `Config._expand_upstream_aliases()` | **Display rename only** — short exposed name → real upstream id, for verbose LM Studio ids. It adds no model and pins no parameters; the generated names are marked internally so the console can tell them from `aliases` |
| `timeout` | `null` | `proxy.py`, `messages.py`, `rerank.py`, `websearch.py` | Per-instance read timeout; `null` falls back to `server.request_timeout` |
| `description` | `""` | `management.py` | Free text shown in the console — never sent anywhere |
| `headers` | `{}` | `proxy._build_headers()`, `main._discovery_headers` | Extra headers on every forwarded request, and also on model discovery and `/probe` health checks, so an upstream with its own auth still appears in `/v1/models`. **Never put a secret value here** — name an environment variable instead (`websearch.api_key_env` / `systemone.api_key_env`). The gateway strips the caller's `cookie` / `referer` / `x-forwarded-*` and drops upstream `set-cookie` regardless of this setting |
| `vram_total_gb` | `null` | `management.py` | The host's total VRAM for the console's used/total gauge — no API exposes it, so it has to be declared |
| `priority` | `0` | `Config.find_upstream_for_model()`, `Config` model merge | When several upstreams declare the same model, the **lowest value wins**; ties keep the order the file lists them in |

## `default_upstream`

`str | None`, default `null`. Resolved by `Config.default_upstream_obj()`, which looks the value up
**by `upstreams[].name`**.

It is consulted when nothing matched a **named** model and `routing.strict: false`, and — because an
empty model name is not a guess — for **every model-less call even under `routing.strict: true`**
(see [`routing`](#routing)). A name that matches no upstream resolves to `None`, so the fallback
silently becomes a **404** — the most common misconfiguration in this file, and worth re-checking
after any rename. `null` means the same thing as a dangling name. Config load now logs an **error**
naming the variable and the available upstreams, so a rename cannot break `api/pull`, `api/tags` or
any other model-less call without saying so.

## `aliases` — a model of its own

Each key is the name a caller sends; the entry is a model in its own right (the console lists it as
a separate row, `/v1/models` advertises it, its target stays callable under its own name).

| Field | Default | Read by | Effect |
|---|---|---|---|
| `model` | — (required) | `Config.resolve()` | The real name forwarded upstream |
| `upstream` | `null` | `Config.resolve()` | Pin the owner instance; `null` resolves like routing does (`get_upstream` → `find_upstream_for_model` → `default_upstream_obj`) |
| `params` | `{}` | `proxy.py` (`body.update(extra_params)`) | Force-injected into the forwarded body, so a client cannot override them — e.g. `think: false` |
| `description` | `""` | `management.py` | Console tooltip / hover text only |

A **pure display rename** belongs in `upstreams[].model_aliases`, not here. Putting one in `aliases`
would create a second selectable model that pins nothing.

## `num_ctx_overrides`

`dict[str, int]`, default `{}`. Keyed by the **resolved target** model name, applied by
`proxy.py` and `messages.py` to `type: "ollama"` upstreams only (LM Studio / OpenAI-compatible
backends take `max_tokens`, not `num_ctx`). It overrides whatever `options.num_ctx` the client sent.

The purpose is VRAM, not behaviour: Ollama pre-allocates the whole KV cache, so one model reached
with three different context lengths means three instances and three memory footprints. Pinning the
number per model keeps one warm shape. `management.py` shows the effective value in the console.

## `api_models` — what each API family may offer

Five optional lists. **Read only by `management.py`** — routing never consults them, so they narrow
the console's model selectors, "Available models" tables and sample bodies, nothing else.

| Field | Empty means | Non-empty means |
|---|---|---|
| `generate` | every declared model minus the names claimed by `embed` / `rerank` / `systemone` / `rerank.models` | served exactly as written |
| `embed` | same derivation | same |
| `rerank` | falls back to `rerank.models` | same |
| `systemone` | falls back to `systemone.model` (unless it is `""` or `local`) | same |
| `websearch` | the search provider's own models (the `opencode` upstream named by `websearch.upstream`) | same |

Rules worth knowing:

- A name may be a real id, a `model_aliases` short name or an `aliases` key; something nothing serves
  stays visible and is flagged `unknown: true` rather than disappearing.
- `""` / whitespace-only items are **dropped** — a placeholder never becomes an empty option, and a
  list of only placeholders counts as empty.
- A non-empty list is a **closed set**: no provider-side narrowing and no free-text 「Custom model…」
  entry (`free_model: false`). Only a derived family gets those.
- `websearch` is deliberately **not** a "claim": web search is a chat pass-through, so the model that
  synthesizes an answer stays selectable for chat.
- The two Ollama-native testers (`/api/generate`, `/api/embed`) additionally drop rows served only by
  an `lmstudio` / `opencode` upstream, which has no native endpoint.

## `rerank`

Consumed by `app/rerank.py`; most fields are mirrored into the console's Rerank projection
(`management.py`).

| Field | Default | Effect |
|---|---|---|
| `enabled` | `true` | `false` → `POST /v1/rerank` answers **404** |
| `endpoint` | `/v1/rerank` | **Label only.** The routes are hardcoded in `main.py` (`/v1/rerank`, `/rerank`, `/api/rerank`); this string only names the console card, the sample request path and the tester envelope. Changing it does not move the endpoint |
| `mode` | `logprobs` | The scoring mode. `logprobs` — build the yes/no prompt, softmax over the first answer token's logprobs — is the only supported one; the removed `embedding` (cosine) mode answers **400** with an explanation |
| `models` | `[]` | The rerankers this gateway offers; also the fallback for `api_models.rerank` and the model used in the console's sample body |
| `instruction` | the "directly answers the question" prompt | Written into the template as `{instruction}`; a request may carry its own `instruction`, which wins. This is the main lever against "topic matching" |
| `template` | the official Qwen3-Reranker chat template | The prompt each document is scored with (`{instruction}` / `{query}` / `{document}`). Override only for a reranker that expects different phrasing, and keep the yes/no answer shape — the score comes from the first answer token's logprobs |
| `yes_token` / `no_token` | `yes` / `no` | The two candidate tokens |
| `yes_aliases` / `no_aliases` | `[]` | Extra accepted spellings, matched case-insensitively with punctuation stripped (e.g. `["是"]` / `["否"]`) |
| `temperature` | `0.0` | Sent to the scoring call; keep it at 0 |
| `top_logprobs` | `20` | Alternatives sampled at the answer position, so a prefixed `\n` or `<think>` token still reveals the yes/no probability |
| `max_tokens` | `4` | Deliberately tiny — the answer is one token, and a larger budget lets a leading token swallow it |
| `token_scan_depth` | `4` | How many generated positions are searched for the yes/no candidate |
| `stop` | `[]` | Stop sequences for the upstream; empty relies on `max_tokens` alone |
| `options` | `{}` | Extra Ollama options merged into every scoring call. **`num_ctx` dominates VRAM** here: `qwen3-reranker:4b` at its default 40960 context costs ~9.3 GB, at 8192 ~4.0 GB — same scores |
| `keep_alive` | `-1` | Ollama only. `-1` leaves the server default; `0` unloads right after the burst; `"3m"` keeps it warm. Reranking is bursty, so an idle 4B reranker steals VRAM from chat models |
| `logprobs_fallback` | `true` | Try the next endpoint style (`/api/generate` → `/api/chat` → `/v1/chat/completions`) before degrading to plain text |
| `fallback_yes` / `fallback_no` / `fallback_unknown` | `0.9` / `0.1` / `0.0` | Scores used when no logprobs came back. `unknown` stays `0.0` so an unparsable answer never looks "half relevant" — if everything scores 0, this is the field to look at |
| `normalize` | `false` | Min-max rescale. Off on purpose: logprobs are already probabilities, and normalising forces the best hit to 1.0 / worst to 0.0, which makes scores incomparable across requests |
| `max_concurrency` | `8` | Semaphore over the per-document scoring calls |
| `return_documents` | `true` | Include the document text in the response; a request can override it |
| `model_modes` | `{}` | Per-model mode override (keyed by the requested name, then the resolved target). Every value folds onto `logprobs` through `MODE_ALIASES` (`generation`, `generative`, `cross-encoder`, `yes_no`, …); `embedding` is kept as a spelling only so it can produce the helpful **400** |
| `upstream_api` | `auto` | `auto` → Ollama `/api/generate` with a raw prompt, OpenAI-compatible `/v1/chat/completions`; `generate` → always `/api/generate`; `chat` → always the chat endpoint |
| `raw_prompt` | `true` | Send the rendered template verbatim instead of applying the model's chat template — required, because the Qwen3-Reranker prompt already *is* a full chat prompt |
| `disable_thinking` | `true` | Suppress thinking mode, which would spend the whole token budget on `<think>…</think>` |
| `max_documents` | `0` | Guard rail, 0 = unlimited; over the cap → **400** |
| `max_document_chars` | `0` | Guard rail, 0 = unlimited; longer documents are truncated, not refused |
| `on_error` | `score_zero` | One unscoreable document → `0.0` and keep going (`score_zero`) or **502** (`fail`). If *every* document fails, **502** either way |

## `systemone`

Consumed by `app/systemone.py` for `POST /v1/systemone`.

| Field | Default | Effect |
|---|---|---|
| `enabled` | `false` | `false` → **404** |
| `backend` | `http://127.0.0.1:8080` | Base URL of the decision backend — a laya-server deployment or Ollama 0.40+. Same URL validation as `base_url` |
| `model` | `auto` in the model, `local` in a generated config, a real name in the shipped file | Injected into the forwarded body only when concrete and the caller sent no `model`; the sentinel `"local"` means "do not inject". **The two backends disagree**: laya-server accepts only the enum `auto` / `english` / `multilingual` / `typed-decisions` (anything else **422**), while Ollama wants an installed model name (`auto` → **404**). Also the `api_models.systemone` fallback |
| `api_key_env` | `SYSTEMONE_SERVER_API_KEY` | *Name* of the env var holding the backend key; when set it is sent as `Authorization: Bearer` even to an Ollama backend, which ignores it. Must end in `_API_KEY` / `_API_TOKEN` — pointing it at `ADMIN_PASSWORD` is refused. This is the **outbound** key (llm-hub → backend); the caller's **inbound** key is a gateway API key from `api_keys`. Startup logs both the resolved URL and whether this variable is set |
| `path` | `v1/systemone` | Endpoint path on `backend` |
| `timeout` | `60.0` | Read timeout; `0`/empty falls back to `server.request_timeout` |

## `websearch`

Consumed by `app/websearch.py` for `POST /v1/tools/web-search`. The provider is a normal
`upstreams` entry, so its address, timeout and headers are configured once.

| Field | Default | Effect |
|---|---|---|
| `enabled` | `false` | `false` → **404** |
| `upstream` | `""` | Provider chosen **by name**; empty = the first `type: "opencode"` upstream. A name matching no upstream is a **503**, never a silent fallback |
| `path` | `v1/web-search` | Search endpoint path on that upstream |
| `api_key_env` | `""` | Env var holding the provider key, sent as `Authorization: Bearer`; empty = unsigned. Same name rule as `systemone.api_key_env` |
| `timeout` | `60.0` | Read timeout, falling back to `upstream.timeout` then `server.request_timeout`. Keep it **≥ 300** when `synthesize: true`: the provider's own inference cap is 300 s, and a shorter gateway timeout turns the provider's **502** into a gateway **504** |

The body is forwarded verbatim and the provider's JSON comes back unchanged. `model` is **never**
resolved through the gateway's aliases — it names a provider model (e.g. `opencode/big-pickle`) or
may be omitted, in which case the provider picks.

## `request_log`

| Field | Default | Effect |
|---|---|---|
| `enabled` | `true` | The write switch, evaluated **per request** (`proxy._logging_on`): with `false` request/response bodies are not even copied and no row is written. Because the flag is re-read on every call, saving the file applies within ~1s with no restart. While off, `/v1/management/overview` and `/requests` report `request_log.enabled: false`, so the console can say "logging disabled" instead of showing an empty list that looks like no traffic |

Storage location and retention are deliberately **not** in this file — `REQUEST_LOG_DB`,
`LLMHUB_DB` and `REQUEST_LOG_RETENTION_DAYS` stay deployment-level. A `HEAD` request is forwarded
but never recorded, so a liveness monitor cannot drown the log.

## `security`

One tree for both listeners. The zone is decided by the **kernel-assigned local socket** — never by
a client IP, an `X-Forwarded-For` header or an API key — and `app/serve.py` starts uvicorn with
`proxy_headers=False` so a header cannot move that anchor.

One process binds both sockets (`app/serve.py`); `app/bridge.py` is the public-zone **policy**
module, not a second proxy hop. The anchor is the listening port in `scope["server"]`, so no header
can move a request between zones. What that means per request:

| Zone | Request | Answer |
|---|---|---|
| public | allowlisted path, **no** key | **401** |
| public | allowlisted path, valid key | forwarded |
| public | unlisted path, valid key | **404** `Not found.` |
| internal, `allow_anonymous: true` | business API, no key | forwarded — no key needed |
| internal, `allow_anonymous: false` | business API, no key | **401** |

A console session cookie is a credential on either zone. A key that is present but wrong is **403**,
a missing one **401**; `/` and `/health` stay anonymous on the internal socket whatever
`allow_anonymous` says.

### `security.internal` — the trusted `server.port` socket

| Field | Default | Effect |
|---|---|---|
| `allow_anonymous` | `true` | `true` → business APIs are key-free **on this port only**. `false` → every business API needs a caller key or a console session; `/` and `/health` stay anonymous so the container probe works |
| `expose_version` | `true` | Whether the anonymous `GET /` includes the exact build version |
| `expose_docs` | `true` | Whether `/docs`, `/openapi.json` and the vendored Swagger assets are served. The public zone never serves them whatever this says |

This is the port where Ollama model management works: `security.public.admin_endpoints` is never
applied here, so `api/pull` / `api/push` / `api/blobs/*` are forwarded unless `security.blocked_paths`
denies them. It is still not an open proxy — a path outside the closed forwardable set is a **404**
on this socket too (see [Path surface](#path-surface--the-closed-set-of-callable-paths)).

### `security.public` — the internet-facing socket

| Field | Default | Effect |
|---|---|---|
| `port` | `null` | The **only** switch that opens a public listener. `null` = none; a port ≠ `server.port` = two listeners in one process (shared key cache, rate-limit buckets, login lockout); a port == `server.port` = public-only single socket with **no anonymous HTTP path at all** (point the container probe at a TCP check). Validated to 1–65535 |
| `path_allowlist` | `DEFAULT_PUBLIC_PATHS` (`GET ^/v1/models$`, `POST ^/v1/chat/completions$`, `^/v1/messages$`, `^/api/generate$`, `^/api/chat$`) | The **complete** public surface: HTTP verb → list of `re.fullmatch` patterns. Anything unlisted is **404 before routing**, with or without a key. There is no global method gate, no global path allowlist and no `require_api_key` toggle — this mapping is the only positive rule, and every request that survives it still needs a caller key or console session cookie. Verb keys are upper-cased; a non-HTTP verb is a config error; an invalid regex is **dropped with a warning** (narrowing can only narrow). Prefix rules must be written `^/v1/.*$` because matching is full-match. `/v1/management/*` and `/admin*` are **404 here**, at code level, unless `admin_endpoints: allow` deliberately opens that plane (see below). Listing a path does not invent one: it must also be [forwardable](#path-surface--the-closed-set-of-callable-paths) or answered locally, otherwise the proxy returns its own **404** |
| `admin_endpoints` | `deny` | The **public listener's** management policy — it does nothing at all on `server.port`, so changing it there is a guaranteed no-op (pulling a model or opening the console on a trusted LAN is normal). It governs two things: **(1) Ollama's own model API** — `deny` (default) blocks the destructive calls (`api/delete` / `api/copy` / `api/create` / `api/push`), blob transfers (`api/blobs/*`) and `api/pull`; `readonly` allows `pull` only; `allow` forwards every admin call untouched, **`api/blobs/*` included**. **(2) the management plane** — `allow` is also the switch that makes `/admin*` and `/v1/management/*` *exist* on the public socket instead of being a hard 404 |

**`admin_endpoints: allow` opens the management plane as a prefix.** `bridge_denial()` decides the
management prefixes *before* it consults `path_allowlist`, so `allow` routes the console shell and
the whole `/v1/management/*` API to their real handlers — no allowlist entry is required, and none
could describe `/admin/assets/*` reliably. The plane is still never anonymous: the static shell and
`POST /v1/management/auth/login` are the only credential-free paths, every other management route
requires the session cookie, and a gateway API key is never an admin credential. Under `deny` and
`readonly` both prefixes stay **404** and listing them in `path_allowlist` changes nothing.

**For Ollama's own endpoints `admin_endpoints` never opens a path by itself.** On the public socket
`bridge_denial()` runs in
the middleware and **404**s anything absent from `path_allowlist` *before* the admin filter inside
`proxy.forward()` is reached. Exposing `pull` publicly therefore needs **both**
`admin_endpoints: readonly` (or `allow`) **and** a matching allowlist entry:

```yaml
security:
  public:
    admin_endpoints: readonly
    path_allowlist:
      # the map is the COMPLETE public surface - keep the defaults you still want
      GET:
        - ^/v1/models$
      POST:
        - ^/v1/chat/completions$
        - ^/api/pull$
```

Changing the mode while the path stays unlisted changes nothing: the **404** comes from the
allowlist, where the admin filter would have answered **403**. `api/show` and `api/version` are
never admin-blocked in any mode.

That combination is easy to get half-right, so startup reports it. With a public listener the log
prints either the endpoints that survive **both** switches (mode ∩ allowlist) or, when the mode is
`readonly` but nothing is listed, an explicit warning: *"security.public.admin_endpoints=readonly
has no effect: no security.public.path_allowlist rule admits an Ollama admin endpoint"*. (`allow`
gets a different line, because it also opens the management plane.) Check that line before
debugging a 404.

**Two decisions, not one.** `path_allowlist` and the API key are checked separately by
`app/bridge.py`, both from the request middleware and both before routing:

| Step | Function | Question | Answer |
|---|---|---|---|
| 1 | `bridge_denial()` | does this verb+path exist on this socket? | unlisted → **404**, even with a valid key |
| 2 | `public_auth_denial()` | does this caller have a credential? | none → **401** + `WWW-Authenticate: Bearer`, invalid key → **403** |

Step 2 is a property of the **zone**, so it cannot be waived by the config and does not depend on
whichever dependencies a route declares: listing a path never makes it anonymous, and a handler that
ever forgot `Depends(require_api_key)` is still authenticated from the internet. It runs after the
rate limiter (a throttled caller still gets **429**) and after the CORS pre-flight answer, and it
never applies to the internal listener — those anonymous paths (`/admin` static, `/`, `/health`,
everything under `security.internal.allow_anonymous`) are answered by routes that are not business
endpoints. There is no `require_api_key` toggle to turn any of this off.

The one request that legitimately arrives credential-free is the CORS pre-flight: step 2 never sees
an `OPTIONS`, because step 5 answers it first. It is still a *method+path* for step 1, so a browser
client on the public socket needs `OPTIONS:` listed beside the `GET:`/`POST:` entries it uses —
see [`security.cors_origins`](#security--hardening-shared-by-both-zones).

`api/blobs/*` used to stay blocked even under `allow`, because the prefix test sat outside the mode
list. That is fixed — `allow` now really forwards blobs (verified live: `POST /api/blobs/sha256:…`
reached Ollama). The management paths are in the forwardable set on purpose, so a refused one
answers **403** rather than a **404** claiming the endpoint does not exist; see
[Path surface](#path-surface--the-closed-set-of-callable-paths).

### `security` — hardening shared by both zones

| Field | Default | Effect |
|---|---|---|
| `blocked_paths` | `[]` | Operator deny-list, enforced **everywhere** (exact name or trailing-`*` prefix), in addition to the public admin blocklist. Checked in `proxy.py` through `SecurityConfig.is_path_blocked()` |
| `cors_origins` | `[]` | Exact origins allowed cross-site; empty sends no CORS headers at all. The middleware answers a preflight with a **bodyless `204`**, only for a listed origin. A preflight is still bound by the zone's path policy: the internal listener answers any path, while on the public socket `OPTIONS` is a method like any other and must be listed in `security.public.path_allowlist` — otherwise the preflight is a **404** and the browser never sends the real call |
| `max_body_bytes` | `33554432` | Request body cap: a `Content-Length` over it → **413** in the middleware, and a chunked body is counted while streaming (`read_limited_body`) before the proxy, messages, websearch and systemone paths forward it |
| `allow_query_api_key` | `false` | Whether `?api_key=…` is a credential transport. Off by default: a URL key lands in access logs. The key is masked in the request log either way |
| `allowed_client_ips` | `[]` | CIDR fence, **403** outside the list. It fences clients; it never selects a zone |
| `trusted_proxies` | `[]` | CIDRs whose `X-Forwarded-For` `client_ip()` may read — used for IP fencing, rate-limit buckets and audit rows, never for a zone. It takes the **rightmost untrusted hop**, so a forged leftmost value cannot pick an internal address. Behind nginx, overwrite the header (`proxy_set_header X-Forwarded-For $remote_addr`) rather than append |

### `security.rate_limit`

| Field | Default | Effect |
|---|---|---|
| `enabled` | `false` | In-memory token bucket, **429** with `Retry-After` when over budget. Startup warns when a public listener is open and this is off. Single process only — it is not a distributed limiter |
| `per_minute` | `60` | Sustained refill rate |
| `burst` | `20` | Bucket capacity = max immediate requests |
| `by` | `ip` | Bucket key: `ip` → `client_ip()` (or `unknown`), `key` → the caller's API key, falling back to the IP when a request carries none. Stale buckets are pruned past 5 000 entries |

## Path surface — the closed set of callable paths

Not a config node, but the frame every path-related setting above sits in. The catch-all used to
forward *anything* it could not match locally, which made the internal listener an open proxy for
the whole upstream API. `app/proxy.py` now carries a closed set (`FORWARDABLE_PATHS` +
`FORWARDABLE_PREFIXES`), and **both zones** answer **404** for a path outside it, with this detail:

```text
Endpoint '/<path>' is not part of this gateway's API. See /docs (internal zone) or docs/API.md for the callable paths.
```

| Path | Family | Note |
|---|---|---|
| `api/generate` | Ollama native | documented route, POST-only |
| `api/chat` | Ollama native | documented route, POST-only |
| `api/embed` | Ollama native | documented route, POST-only |
| `api/embeddings` | Ollama native | deprecated by Ollama, still answered by older instances; not in the schema |
| `api/show` | Ollama native | not in the schema, never admin-blocked |
| `api/version` | Ollama native | not in the schema, never admin-blocked |
| `v1/chat/completions` | OpenAI-compatible | documented route, POST-only |
| `v1/completions` | OpenAI-compatible | documented route, POST-only |
| `v1/embeddings` | OpenAI-compatible | documented route, POST-only |
| `api/pull` | Ollama model management | public zone: `admin_endpoints` **and** `path_allowlist` |
| `api/push` | Ollama model management | same |
| `api/create` | Ollama model management | same |
| `api/delete` | Ollama model management | same |
| `api/copy` | Ollama model management | same |
| `api/blobs/` (prefix) | Ollama model management | `api/blobs/<digest>`; forwarded under `admin_endpoints: allow` |

The management entries are in the set **on purpose**: a call refused by `admin_endpoints` then
answers **403** with `Endpoint '/<path>' is disabled on this public gateway.`, which is clearer than
a **404** claiming the endpoint does not exist. On the internal socket they are forwarded normally.

Paths the gateway answers **locally** never reach an upstream's wire, so they are not in the set:

| Answered locally | Served by |
|---|---|
| `/`, `/health`, `/probe` | gateway info, liveness and upstream probes |
| `/v1/models`, `/models`, `/api/tags`, `/api/ps` | aggregated across upstreams, not proxied to one |
| `/v1/rerank`, `/rerank`, `/api/rerank` | `rerank.py` scoring engine |
| `/v1/messages`, `/v1/message` | `messages.py` request mapping + envelope/SSE rebuild |
| `/v1/systemone` | `systemone.py`, its own `backend` |
| `/v1/tools/web-search` | `websearch.py`, its own provider upstream |
| `/v1/management/*`, `/admin/*` | management API + console (cookie-only; always **404** on public) |
| `/docs`, `/openapi.json` | internal zone, `security.internal.expose_docs` |

**Check order inside `proxy.forward()`**: dot-segment / NUL rejection (**403**) → forwardable-set
**404** → `admin_endpoints` (public only) + `blocked_paths` **403** → body-size cap **413** → model
routing **404**. A path refused at the second step never reaches routing, so `default_upstream`
cannot revive it, and `blocked_paths` can only ever produce a **403** for a real API path.

**POST-only routes.** `main.POST_ONLY_PATHS` is:

```text
/v1/rerank, /rerank, /api/rerank, /v1/systemone, /v1/tools/web-search,
/api/generate, /api/chat, /v1/chat/completions, /v1/completions,
/api/embed, /v1/embeddings, /v1/messages, /v1/message
```

Any other method on those paths is a gateway **405** with `Allow: POST`. The explicit list is needed
because Starlette prefers the catch-all's full match over a documented route's wrong-method partial
match — without it a `GET /api/chat` would be forwarded upstream instead of refused.

**Swagger visibility.** Six pass-throughs are registered as documented routes before the catch-all,
so they appear in `/openapi.json` and `/docs` with a request schema, examples, their response media
types and **404** / **405** entries:

| Route | Response media types |
|---|---|
| `POST /api/generate` | `application/json`, `application/x-ndjson` |
| `POST /api/chat` | `application/json`, `application/x-ndjson` |
| `POST /v1/chat/completions` | `application/json`, `text/event-stream` |
| `POST /v1/completions` | `application/json`, `text/event-stream` |
| `POST /api/embed` | `application/json` |
| `POST /v1/embeddings` | `application/json` |

Each handler is a one-liner into `ProxyService.forward()`, so routing, alias + parameter injection,
`num_ctx_overrides` clamping, streaming and request-log capture are unchanged — the registration
buys schema visibility and the **405**, nothing else. `api/embeddings`, `api/show` and `api/version`
stay out of the schema (the catch-all forwards them). `/docs` is internal-zone only and gated by
`security.internal.expose_docs`; the public zone never serves it. Per-endpoint contracts:
[API.md](API.md).

## Fields the console reads and routing does not

`upstreams[].description`, `upstreams[].vram_total_gb`, `aliases[].description`,
`rerank.endpoint`, the whole `api_models` node and `rerank.models` (as the selector fallback) exist
to render or label a view. They cannot change which backend a request reaches — with one exception
worth stating plainly: an `api_models` list *looks* like an authorization control and is not one.
Send a model that is not listed to `/v1/chat/completions` and it is still routed.

## Nodes that no longer exist

Leaving one of these in the file does nothing useful; each is either migrated with a warning or
ignored.

| Removed | Replaced by | What happens now |
|---|---|---|
| top-level `auth:` | `security.internal` / `security.public` | `auth.enabled` becomes the inverse of `internal.allow_anonymous` with a warning; `auth.api_keys`, `trusted_networks`, `allow_anonymous_health` and the IP whitelists are ignored with a warning |
| `security.allowed_http_methods` | the verb keys of `public.path_allowlist` | Migrated onto those verbs, then dropped |
| global `security.path_allowlist` | `security.public.path_allowlist` | Migrated (with a warning to verify the surface) only if the new node is absent |
| `security.expose_version` | `security.internal.expose_version` | Migrated |
| `security.admin_endpoints` | `security.public.admin_endpoints` | Migrated |
| `security.expose_health_details` | — | Ignored, listed as a no-op in the warning |
| `rerank.embedding_query_prefix` / `embedding_clamp`, `mode: embedding` | `logprobs` scoring | The field is unknown (silently dropped); the mode answers **400** explaining why |
| `api_keys:` in `config.yaml` | the SQLite `api_keys` table | Warned about and ignored — the console mints and revokes there |
| `GATEWAY_API_KEYS` / `GATEWAY_API_KEYS_FILE` / `.keys` beside the config | the SQLite `api_keys` table, written by the console | Not read at all; the launcher strips the variables and warns |
| `LLMHUB_PORT` / `LLMHUB_PUBLIC_PORT(S)` / `LLMHUB_HOST` / `LLMHUB_LOG_LEVEL` / `GATEWAY_AUTH_DISABLED` / `LLMHUB_PUBLIC_ADMIN` | `server.*` / `security.*` | Warned about; a public port and `allow_anonymous` are honoured **once** as a migration hint when the file leaves them unset — explicit config always wins. Remove them from `.env` |
| `LAYA_SERVER_API_KEY` | `SYSTEMONE_SERVER_API_KEY` | Only the name in `systemone.api_key_env` is read |

## Settings that stay outside `config.yaml`

| Variable | Role |
|---|---|
| `CONFIG_PATH` | Which file to load |
| `ADMIN_PASSWORD` | Console sign-in only; it is never a Bearer / `X-API-Key` credential |
| `ADMIN_SESSION_TTL` | Cookie lifetime (default 12 h, clamped 5 min…7 d, sliding renewal) |
| `REQUEST_LOG_DB` / `LLMHUB_DB` | Database file for `requests` / for `bench_results` + `api_keys` |
| `REQUEST_LOG_RETENTION_DAYS` | Row retention (`0` = keep forever, pruned hourly) |
| `*_API_KEY` / `*_API_TOKEN`, named by `systemone.api_key_env` / `websearch.api_key_env` | Provider credentials; the file may only name the variable, never hold the value |

## Checking whether a key actually does something

`Config` does not forbid extra attributes, so an unknown key parses, is dropped, and produces no
error — which is how a typo survives a deploy. To audit a file:

1. Ask the schema. Every key path in the file must be a declared field of the model that owns it
   (`Config` → `ServerConfig`, `SecurityConfig`, … in `app/config.py`). A path the model tree does
   not have is dead — a rename, a removed feature, or a misspelling.
2. Ask the code. A declared field is live when something outside `app/config.py` reads it — the
   field name appears in `proxy.py`, `management.py`, `main.py`, `bridge.py`, `auth.py` or one of
   the service modules, or through a `Config` helper (`resolve()`, `find_upstream_for_model()`,
   `default_upstream_obj()`, `websearch_upstream_obj()`, `is_path_blocked()`,
   `PublicSecurityConfig.allows()`). A field read by nothing at all is a node to delete.
3. Test the effect, not the parse. Change the value and watch what moves: `log_level: debug` shows
   the routing decision, `GET /v1/management/config` shows the masked projection the console sees,
   and a public-port change either changes a **404** or does not — in which case the listener was
   never bound, because ports are read at startup.

Two things this procedure is known to catch in a real deployment: a `default_upstream` naming an
upstream that does not exist (silently 404 fallback), and a `- ''` placeholder inside an
`api_models` list (ignored, and it switches a derived family into closed-set mode only when a real
name sits beside it).

---

Prev: [API.md](API.md) · Full manual: [USAGE.md](USAGE.md) · Deployment: [DEPLOY.md](DEPLOY.md)
