# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed (every tester picker now honours `api_models`)

- **A non-empty `api_models.<family>` list is now a closed set.** `websearch` used to be replaced
  by the search provider's declared models even when the config spelled out the choices, blank
  entries (`- ''`) could become empty `<option>`s, and `systemone` / `websearch` always offered
  「Custom model…」. The provider filter, the free-text entry and any extra narrowing now apply
  only to **derived** families (an empty list), so the config's names are exactly what every
  picker offers. Blank placeholders are dropped; a list of only placeholders still derives.
- **No hardcoded model names left on the console.** `Models · SystemOne`'s "Available models"
  table (`tev1:0.8b` / `tev1:latest` / `nimble:latest`), the `qwen3:8b` in the three
  `Models · LLM` request/response samples and `opencode/big-pickle` in the Web Search response
  sample now read the same `/api-test/models` payload as their testers. The SystemOne table
  gained a source tag, an empty state and an upstream tooltip.
- Docs: USAGE / USAGE_CN, API / API_CN, TESTCASES, AGENTS and the web-management design doc
  describe the closed-set rule; `test_management.py` grows to 407 checks.

### Fixed (one error definition, a config-driven Embedding model list, and VRAM units)

- **`stats/distribution?group_by=status` now counts only HTTP >= 500 as `error`.** It used to split
  at 400, so the status pie disagreed with the KPI error rate, the timeseries error bars and
  `/stats/failures` on the same window (3 vs 0 on the real database). `request_log._DISTRIBUTION_KEYS`
  now shares the `status >= 500` threshold; 4xx replies such as a blocked `/api/pull` stay in
  `ok`. The pie's two counters are the only changed values, and API/API_CN, AGENTS, TESTCASES,
  the web-management design doc and the management test suite were updated to match.
- **Models · Embedding no longer hardcodes `bge-m3:latest`.** The available-model table, its tab
  count, the config-source tag and all four request/response examples now come from
  `/v1/management/api-test/models` (`kinds.embed_ollama` + `kinds.embed_openai`), i.e. from
  `api_models.embed` with the declared `upstreams[].models` pool as fallback — exactly the pool
  the page's own two testers use. A model served by both families shows both endpoints; an empty
  pool shows a configuration hint; with nothing configured the examples use a `<model>` placeholder.
  New `modelsEmbed.available.empty` / `sourceNote` keys in all three languages.
- **Console VRAM values always carry their unit.** The Dashboard KPI and its upstream table showed
  a bare number whenever `vram_total_gib` was not configured. `format.ts` gained
  `vramText(used, total)`, shared by DashboardView and UpstreamsView; `DeviceKind` gained the
  `cloud` value hosted-provider rows already returned.

### Added (caller API keys stored as hashes in SQLite, with a console page to mint and revoke)

- **API keys moved out of the file and into the database.** `app/key_store.py` keeps one row per key in
  the `api_keys` table of `llmhub.db` and stores only `sha256(<plaintext>)` (lowercase hex) plus a
  display mask — **the database never holds a secret**, so a copied `.db` cannot be replayed. A slow
  KDF (bcrypt/argon2) would only buy milliseconds of offline-cracking time against a 238-bit random
  string while costing every single request, so a plain SHA-256 comparison against an in-memory
  `frozenset` is what the gateway uses. Verification happens entirely in memory: `auth.py` asks
  `get_store().verify(key)` and the hot path never opens sqlite
- **New key format**: `sk-hub-` + 40 base62 characters — 47 characters in total, generated with
  `secrets.choice`. The mask keeps the prefix plus the first 4 and last 4 characters
  (`sk-hub-7Kp2…Q9df`); a legacy key that does not match the format masks head 8 / tail 4, and
  anything 12 characters or shorter is shown as `••••••` with nothing of it visible
- **`GATEWAY_API_KEYS` / the `.keys` file now only seed an empty table.** On the very first start, when
  the table has no row, the environment keys are imported (`source: "file"`, no label) and logged as
  seeded. Afterwards the file is not read for authentication: editing it changes nothing, and a key
  revoked in the console stays revoked across restarts. Keys in the environment that no row serves
  are reported once at startup as masks
- **Three new management routes** — `GET /v1/management/api-keys` (list: `id` / `masked` / `label` /
  `source` / `created_at`, newest first), `POST` (body `{label}`, label required → **201** with
  `{item, secret}`; blank → **400**, over 100 characters → **422**) and `DELETE /api-keys/{id}`
  (**200**, unknown or already-revoked id → **404**). A minted key authenticates gateway calls
  immediately, and revoking one takes effect immediately too. Unlike every other management route,
  these three accept the console **session cookie only** (`require_admin_session`): a gateway API key is
  what any client already holds, so accepting one here would let a caller issue itself more access
- **Console page ⚿ API Key 管理** (`/admin/api-keys`, entered from the account menu, no sidebar row):
  the list shows mask, description and creation time (`source` stays in the API payload but gets no
  column); *New key* opens a modal
  that asks for a description and then displays the plaintext **exactly once**, with a copy button that
  falls back to `execCommand('copy')` and then to selecting the field when the clipboard is refused.
  Closing that modal loses the plaintext for good. Revoking is deliberately two-step (the row's button
  becomes *Confirm revoke* / *Cancel*), and a fourth card states the format, what is stored, how it is
  checked and where the keys came from

### Added (Anthropic Messages API: `POST /v1/messages`, plus the `Models · LLM → API 用法` doc and tester)

- **New caller endpoint `POST /v1/messages`** (Anthropic Messages compatible), with the singular alias
  `POST /v1/message`. The gateway translates the request into the OpenAI chat shape and forwards it to
  the upstream — Ollama `/api/chat` or `/v1/chat/completions` — then wraps the answer back in the
  Anthropic envelope (`id` / `type: message` / `role` / `content: [{type:text,text}]` /
  `stop_reason` / `usage:{input_tokens,output_tokens}`). Accepts `Authorization: Bearer` or `x-api-key`
- **Streaming follows the Anthropic SSE order** (`message_start` → `content_block_start` →
  `content_block_delta*` → `content_block_stop` → `message_delta` → `message_stop`); an upstream error is
  surfaced as an `event: error` rather than a dropped connection. `usage` is rebuilt from the upstream's
  native token counts (`prompt_eval_count`/`eval_count` for Ollama, `prompt_tokens`/`completion_tokens`
  for OpenAI-compatible)
- **`model` defaults to `auto`** (the default upstream's first declared model). An unset `default_upstream`
  now means *no* default: an unresolvable model returns `404` instead of silently routing to the first
  upstream — `config.default_upstream_obj()` was changed to return `None` when no default is configured
- **Console: `Models · LLM → API 用法`** gained a third entry — Messages (Anthropic compatible) — with a
  parameter table, request/response examples, and an inline **Test** window that runs server-side through
  the same `MessagesService` the gateway uses (alias resolution, parameter injection and the request log
  behave exactly like an external call). The API tab counter now reads 3
- Docs: `docs/API.md` has a new Messages section; `docs/USAGE.md` / `docs/USAGE_CN.md` note the third
  generation endpoint

### Changed (docs: API and DEPLOY are bilingual, one changelog)

- **`docs/API.md` and `docs/DEPLOY.md` now exist in both languages.** The unsuffixed file is English and
  the Chinese text moved to `docs/API_CN.md` / `docs/DEPLOY_CN.md` (`git mv`, so history follows), matching
  the `USAGE.md` / `USAGE_CN.md` convention that was already in place. Internal links were relocalised:
  `USAGE.md` points at English anchors (`#web-search-opencode-gateway-proxy`,
  `#messages-anthropic-compatible`), `USAGE_CN.md` at `API_CN.md`, and the README doc map now lists
  `API_CN.md` / `DEPLOY_CN.md` on the Chinese side
- **`CHANGELOG_CN.md` removed** — the changelog is English only from here on. The README language strip and
  the directory tree in AGENTS.md point at the single file
- **`DEPLOY` grew a local-run path**: the Windows launcher that used to hide under the build-script section
  is now a top-level "Running locally (no Docker)" section with the manual `venv` + `uvicorn` steps, and the
  console-bundle section is a sibling of it rather than a subsection of the scripts
- **AGENTS.md gains a Documentation section** with the rules this cleanup settled on: one language pair per
  doc with the English file unsuffixed, changelog English only, a three-row README doc map showing file
  names without paths, descriptions that match the target's real headings, README images in `docs/images/`,
  identical heading structure and anchors across a pair, code copied rather than translated, and cross-links
  updated in the same pass. "Adding New Endpoint" now spells out the two-language documentation step and the
  console-tester wiring

### Changed (request statistics count API calls only)

- The Requests list, its statistics bar and the Dashboard timeseries/distribution charts now scope
  every query with `path LIKE '/v1/%' OR path LIKE '/api/%'` (`request_log.API_PATH_SCOPE`). Rows for
  other paths (the health check, monitoring probes) are still captured and still open by `id` —
  `GET /v1/management/requests/{id}` is deliberately unscoped — they simply no longer dilute request
  counts, P50/P95/P99 and the error rate, which used to be dominated by a HEAD or a `/health` poll
  every second

### Added (console: Traditional Chinese)

- A third locale, `web/src/i18n/messages/zh-TW.ts`. All three message files share one schema
  (`MessageSchema = typeof zhCN`), so a missing key in 繁體中文 or English is a `vue-tsc` error rather
  than a silent fallback, and `npm run build` fails before a half-translated bundle ships
- The language submenu (account menu → 语言 / Language) now lists **简体中文 / 繁體中文 / English** —
  the `soon` placeholder is gone — from one data-driven `SUPPORTED_LOCALES` list whose labels stay in
  their own language. The current locale gets a `✓`, the choice persists in `localStorage`, `<html lang>`
  follows it and Element Plus' own widgets (date panel, pagination) switch with it, because the
  `zh-cn` / `zh-tw` / `en` locale bundles are wired through `el-config-provider`
- A browser `zh-TW` / `zh-HK` / `zh-MO` / `zh-Hant` tag now opens the console in Traditional Chinese
  (`localeFromTag()`); a bare `zh` keeps Simplified, as before

### Fixed (dashboard: "today" is the user's day, not the server's UTC day)

- The *today* KPI tile counted a UTC calendar day while every other screen spoke in local time. At
  UTC+8 that window starts at 08:00 local, so a request made between 00:00 and 08:00 was silently
  dropped from the count, the token total, the P95 and the error rate — measured on a fixture of three
  rows, the tile showed 1 where the local day holds 2. `GET /v1/management/overview` now takes
  `today_start` / `today_end`, and the console fills them with the browser's local midnight converted
  to UTC; a caller that sends nothing keeps the previous UTC-midnight fallback, so scripts and tests
  are unaffected. The About page sends the same window, so both screens agree
- Day bars on the request chart were UTC days too, for the same reason: the 00:00-08:00 slice of local
  time landed on the previous bar. `GET /v1/management/stats/timeseries` takes `tz_offset` (minutes to
  add to UTC, clamped to ±840) and shifts only the `day` grouping; hour buckets stay UTC because the
  console renders them as instants. The bar label is now a plain local date and is no longer parsed
  back as one, which also fixes the previous-day label in timezones west of UTC

### Fixed (console: checkbox accent colour and one control height)

- Native checkboxes inherited the browser's own accent — a purple that belongs to nothing else in this
  console. Every checked box in the API testers and on the Settings page now takes
  `accent-color: var(--accent)`, so it matches the buttons, links and focus rings beside it
- Controls sharing a row are one box high. The datetime range pickers on Dashboard and Requests render
  at Element Plus' default 32px while the text inputs, native selects and filter buttons did not, so
  the Requests filter row visibly stepped. `.sel`, `input.inp` and `.filters .btn` are pinned to 32px
  (`input.inp`, not `.inp`, because that class is also on Settings' `<textarea>`s, which must keep the
  height their `rows` give them)

### Changed (console: alias table layout, and Upstreams rows stay open together)

- **The 别名与派生模型 table on Models · LLM lost its 说明 column**: the description is now the native
  tooltip on the **name** and **target model** cells, and the five remaining columns are pinned to
  24% / 12% / 24% / 14% / 26% with `table-layout: fixed` + `colgroup`, so a long name clips to an
  ellipsis instead of pushing the whole table apart
- **`Display name` and `Derived model` share one chip style** (`.chip.s2`); the text is the only
  difference. Previously one was grey and the other violet, which read as two levels of importance
- **Expanding an Upstreams row no longer collapses the others.** `expanded` and `detailBusy` became
  arrays of upstream names, so several instances stay open at once, concurrent probes each keep their
  own "probing" line, and clicking a row again closes only that row. The once-a-minute refresh now
  re-probes every open row (`Promise.all`) instead of only the last one clicked

### Added (per-endpoint API testers on every `API 用法` tab, plus `api_models`)

- **Every documented endpoint can now be called from the console**: the `API 用法` tab of Models · LLM,
  Models · Embed, Models · Rerank, Models · System One and Tools · Web Search puts a **`Test` button**
  at the right end of each endpoint's *Request parameters* header, and it opens the tester in a popup
  over the page rather than inline below the block — 7 kinds in total (`generate`, `chat`,
  `embed_ollama`, `embed_openai`, `rerank`, `systemone`, `websearch`). Pick a model, adjust the
  parameters, run it and read back status, timing, the routed upstream, injected alias parameters,
  the assembled answer and the raw body. The window mounts on first open and is only hidden
  afterwards, so closing and reopening keeps the form and the last answer
- **Execution is server-side** (`app/api_test.py`): the console's session cookie authenticates
  `require_admin` but is not a caller API key, so the tester rebuilds a synthetic ASGI `Request` and
  hands it to the gateway's own services. Alias + parameter injection, `num_ctx_overrides` clamping,
  the blocked-path list and request-log capture all behave exactly as they do for a real client, and
  the `KINDS` table is a closed allowlist — no arbitrary URL is reachable through it
- **New `api_models` config block** (`api_models.generate` / `embed` / `rerank` / `systemone` /
  `websearch`) narrows each family's model selector: an empty list derives it (`rerank.models`,
  `systemone.model`, otherwise the callable pool), a list may name a real id, an alias or a derived
  model, and any name **explicitly claimed by another family** (`embed` / `rerank` / `systemone`
  or `rerank.models`) disappears from generate/chat — compared with a trailing `:latest` ignored,
  since Ollama treats `tev1` and `tev1:latest` as one model. A fallback such as `systemone.model` is
  deliberately not a claim, so a chat model may also answer decision requests, and a `websearch`
  claim is not one either: that endpoint is a chat pass-through, so a hosted provider's routing
  model (`opencode/auto`) stays selectable for chat.
  `websearch` and `systemone` additionally allow free text, because `model` is optional there and
  the provider resolves it. The lists narrow what the console offers only — routing is untouched
- **Ollama-native testers list Ollama models only**: `POST /api/generate` and `POST /api/embed` are
  Ollama's own API, so a derived selector for those two kinds keeps `type: "ollama"` rows only — an
  `lmstudio` or `opencode` model would be a guaranteed upstream 404 there. The same models stay
  selectable on `/v1/chat/completions` and `/v1/embeddings`, which those upstreams do implement, and
  an explicit `api_models` list is still served as written
- `GET /v1/management/api-test/models` returns per kind the endpoint, eligible models (alias shown as
  `display` when configured, else the declared name), the preselected default, each parameter's
  default, whether free text is allowed, the feature's `enabled` flag and — when that family signs
  with an extra key — `api_key_env` plus the fixed `••••••` mask. `POST /v1/management/api-test`
  answers with the envelope (`status`, `ok`, `duration_ms`, `ttft_ms`, `stream`, `content_type`,
  `body`/`text`, `truncated`, plus the in-force `requested_model` / `model` / `upstream` / `injected`).
  Responses are capped at 128 KB; the five views share one probe through `useApiTestContext()`, so
  opening a tester never re-probes every upstream
- The form **opens with its initial values already filled in** — it shows what the endpoint would
  have used anyway, and a select with no default displays its `not set` option instead of rendering
  nothing. A documented boolean is a **checkbox** that always sends `true` / `false` (unchecked
  means false, never "omit"); a boolean without a default keeps the three-state select so a field
  like System One's `stream` can still be left out. `Restore defaults` writes the values back (it
  used to be `Fill defaults`), `Clear` blanks every field and the model, and any remaining empty
  field is omitted from the body rather than sent empty. The two LLM windows start with **`stream`
  off** so one Run returns a single JSON; the generate window adds a **`think`** checkbox (off by
  default, Ollama's native field) and the chat window exposes the OpenAI-compatible
  **`reasoning_effort`**. The model picker lists **names/aliases only** — the alias when configured,
  else the declared id — with the upstream and runtime state moved into the hover tooltip, and it
  **can be cleared at any time** (the `×` beside the field, or the select's empty option): the body
  then carries no `model`, a family that needs one answers the tester's own "pick a model first"
  guard, and System One / Web Search let the endpoint decide
- System One / Web Search pick the model from a **real dropdown**, preset to `default_model`
  (Web Search stays empty) and ending in a 「Custom model…」 entry that swaps in a free-text box
  (focused, with the same suggestions); re-picking a listed name closes it again. The old text box
  pre-filled the default, and Chromium filters a `<datalist>` by the text already in the box - that
  is why System One showed `tev1` and never `nimble`. Chips for chunked answers and TTFT are driven
  by the observed `content_type`, so a single JSON response never claims to have streamed. Typing
  into a numeric parameter no longer crashes the window: `v-model` on `type="number"` yields a
  number, so every read goes through `String(...)` before trimming
- **`HEAD` requests are forwarded but never recorded** in the request log (no body, no answer, and
  monitors send them continuously); the decision lives in one place,
  `logging_on = self._logging_on(cfg) and method != "HEAD"`. With the shipped
  `allowed_http_methods: [GET, POST]` a HEAD answers `405` before it ever reaches the proxy
- The System One config projection's `request_example` now uses an **object** `criteria` for `choice`
  questions: Ollama 0.40+ rejects an array with `400 "criteria" must be a non-empty object`, while
  laya-server accepts either shape, so one example runs on both backends
- Docs: `docs/API.md` (two route rows + the **接口测试（api-test）** section), `docs/USAGE.md` /
  `USAGE_CN.md` (the `api_models` reference, the **API tester** feature row and the console prose),
  `docs/TESTCASES.md`, `docs/web-management/*` and `AGENTS.md`; `test_management.py` grew to 297 checks

### Added (a model declared under `aliases:` is listed as a model of its own)

- **Two different concepts, now two different rows**: an entry under `upstreams[].model_aliases` only
  renames an existing model, while an entry under the root `aliases:` node is a model of its own — it
  carries its own pinned `params` (e.g. `think: false`) and its target stays callable with its own
  defaults. The console was conflating them: a root alias renamed its target's row and never appeared
  as a model, so `qwen3.5:4b-nothink` was invisible
- `Config` now records which alias names `_expand_upstream_aliases` generated
  (`is_renamed_model()`, `derived_aliases()`); routing and `/v1/models` are untouched
- `/v1/management/overview`, `/upstreams/{name}` and `/models/llm` emit derived rows with
  `derived: true` plus `target` / `params` / `description`: the row sits right after its target,
  inherits its device and `num_ctx` from the `/api/ps` entry and reports `size_gib` / `vram_gib` as
  `null`, so VRAM and the KPI `running_models` count the instance once. `_alias_by_target()` no longer
  renames a target through a root alias. `aliases[]` gained `kind: "rename" | "derived"`
- Console: a `derived` chip on every model row (Dashboard, Upstreams expander, Models · LLM,
  Web Search 可用模型), the 别名映射 table became **别名与派生模型** (name / kind / target / upstream /
  pinned params / description), and the speed test + hover history of a derived row address its
  **target** model with the alias's `params.think`, because the upstream has never heard of the alias
- Docs: `docs/USAGE.md` / `USAGE_CN.md` (`aliases` and both `model_aliases` mentions), `docs/API.md`,
  `docs/TESTCASES.md`, `docs/web-management/*` and `AGENTS.md` all state the rename-vs-new-model rule;
  `test_management.py` grew to 219 checks

### Changed (docs: README slimmed to a landing page, new USAGE manual and screenshots)

- **README / README_CN rewritten as a landing page**: one-sentence pitch, a client/gateway/backend
  diagram, six capability rows, six real screenshots, a three-step quick start, a minimal `curl`
  tour and a document index — down from ~1000 lines to under 160, so the first screen answers
  "what is this for?"
- **New `docs/USAGE.md` / `docs/USAGE_CN.md`**: the entire former README body (feature table,
  directory structure, all three deployment paths, configuration reference, LM Studio integration,
  console internals, full endpoint and error tables, second Ollama instance, FAQ, public-exposure
  checklist, local development) moved over with a table of contents and a link back to the README.
  The manuals live under `docs/` so the repository root keeps only the landing page, the changelog
  and `AGENTS.md`; every reference (README, `AGENTS.md`, this file) and the manuals' internal
  relative links (`../README.md`, `API.md`) were updated accordingly
- **New `docs/screenshots/{en,zh}/`**: 10 live captures per language — 20 files (login gate,
  dashboard, upstreams, models LLM / Rerank / System One, requests and detail, web search, settings)
  rendered from the built `app/web` bundle against a real gateway — no mock-ups
- **New `docs/web-management/capture-live.js`**: Playwright capture script (`LLMHUB_BASE` +
  `ADMIN_PASSWORD`, `deviceScaleFactor: 2`, JPEG q88) that walks both `llmhub-console-locale`
  values and can be re-run at any time
- `AGENTS.md` directory tree and document ownership updated (README = landing page,
  `docs/USAGE.md` = manual, `docs/screenshots` = gallery)

### Changed (console: sidebar and page titles are now translated)

- New `nav` namespace shared by the sidebar and the page titles (`dashboard` / `upstreams` /
  `requests` / `llm` / `embed` / `rerank` / `systemone` / `webSearch` / `modelsLlm` … / `settings` /
  `about`): the eight menu entries, nine `TopBar` titles, the "read-only" footer and the account
  menu's Settings / About entries all go through `t('nav.*')`
- The account chip role (`Administrator`) now uses `t('shell.role')`; the Chinese sidebar reads
  总览 / 上游 / 请求 / 模型 / 工具 / 联网检索

### Fixed (console: model table column widths)

- The Models · LLM table now uses `table-layout: fixed` plus `overflow: hidden; text-overflow:
  ellipsis`, and the `显存 / 占用 (GiB)` column grew from 124px to 152px. A 16 GiB-class pair
  (`16.1 GiB / 16.1 GiB`) used to overflow its cell under the default auto layout and was covered
  by the running-state badge

### Changed (Dockerfile and build context)

- `Dockerfile`: `ARG VERSION` with `org.opencontainers.image.*` labels (title, description,
  version), fed by `scripts/docker-build.bat` through `--build-arg VERSION=%VERSION%` from
  `__version__` in `app/main.py`; the health check tightened from 1h to 30s (5s timeout); comments
  now state that `/data` holds both `config.yaml` and `llmhub.db` (request log + speed-test history)
  and why the container stays root (bind-mount ownership)
- `.dockerignore` extended: `web/` (230 MB of `node_modules`), `data/` (config and SQLite, mounted
  as a volume instead), `docs/`, `tests/`, `scripts/`, `*.md` and the tooling caches — the build
  context drops from hundreds of MB to a few MB and local config can no longer leak into it
- No functional Dockerfile change was needed: `bench.py` / `bench_store.py` are covered by
  `COPY app/*.py`, and the dependency set is still the five packages in `requirements.txt`
  (SQLite comes from the standard library)

### Changed (auth: implicit `.keys`, trusted-network startup)

- **`.keys` is now read without any environment variable**: when `GATEWAY_API_KEYS_FILE` is unset, `config.collect_env_api_keys()` falls back to `config.default_keys_file()` — `.keys` in the same directory as `CONFIG_PATH` (`/config/.keys` when the config lives in `/config`, `/data/.keys` in the default image layout). An explicit `GATEWAY_API_KEYS_FILE` still wins, and `GATEWAY_API_KEYS` keeps merging in
- **Trusted-network startup**: the "no API key → refuse to start" self-check now only fires when `auth.allow_access_ip_whitelist` is empty. With a non-empty whitelist the gateway starts keyless and logs a warning, because those addresses already bypass the key check — so an IP-whitelist-only deployment (docs + LAN clients, no keys) is possible
- **Misspelling guard**: `allow_access_ip_whilelist` / `allow_ip_whitelist` are normalised to `allow_access_ip_whitelist` with a warning. Pydantic ignores unknown fields by default, so the typo used to leave the whitelist silently empty (`/docs` 404, no key bypass)
- `auth.api_keys` stays removed from `config.yaml` (already the case): keys are runtime-only, and a leftover block is dropped with a warning
- `tests/smoke_test.py` adds two checks (implicit key-file location, typo normalisation) — **135** cases
- `README` / `README_CN` / `docs/DEPLOY.md` / `.env.example` / `AGENTS.md` updated

### Added (console: zh-CN / en internationalization)

- The console is now bilingual: **简体中文 (default)** and **English**, switchable from the account menu (语言 / Language) and persisted in `localStorage` (`llmhub-console-locale`). First visit guesses from `navigator.language` (`en*` → English, everything else → Chinese), and the switch also updates `<html lang>` and Element Plus's own locale via `el-config-provider`, so date pickers / pagination / empty states follow along
- Implementation is `vue-i18n@11` in Composition mode (`legacy: false`). Messages live in `web/src/i18n/messages/zh-CN.ts` + `en.ts`, namespaced per view (`dashboard`, `requests`, `modelsLlm`, …). `en.ts` is annotated `const en: MessageSchema = …` against `export type MessageSchema = typeof zhCN`, so a missing or stray key fails `vue-tsc` instead of silently falling back to Chinese. 447 keys, variables go through named interpolation (`{n}`), and inline markup in prose uses `<i18n-t>` slots so `<code>`/`<b>` survive translation
- Every string the user can see was migrated (views, TopBar, request drawer, API docs, empty states, tooltips, `aria-label`s, Element Plus table/pagination text); `utils/format.ts` and `api/client.ts` call `i18n.global.t()` because they are plain modules. Product names, endpoints, field names, units and code samples stay untranslated

### Changed (console: request-detail body panes)

- The 请求体 / 响应体 panes in the request drawer now **soft-wrap, keep a fixed 320px height and scroll vertically**, instead of running off to the right. `JsonPane` gained `height` and `wrap` props (`.code2.wrap` → `white-space: pre-wrap`, `overflow-x: hidden`, `scrollbar-gutter: stable`), and a body that parses as JSON is pretty-printed with 2-space indentation first — the stored single-line body was unreadable even when it did fit
- A body truncated at 8 KB or a non-JSON payload still renders verbatim; `ApiDoc` examples keep the old `max-height` + no-wrap behaviour

### Added (console: per-model speed test)

- **测速** on Models · LLM: every model row in 上游模型映射 now carries a `测速` button. Clicking it starts `POST /v1/management/bench`, opens a modal and polls `/bench/{job_id}` once a second, showing `测速中 · 第 x/y 轮` with a progress bar, then three stat cards (Decode / Prefill / TTFT: mean, ±std dev, min/max, CV) plus the per-round table (round / kind / decode / prefill / ttft / generated tokens). Failures surface the backend `error` verbatim and offer a re-run; only one job runs at a time and closing the modal stops polling
- Hovering any model row (250 ms debounce) pops a card with the **latest 3** stored results for that `upstream` + `model` (time, timing-source badge, Decode / Prefill / TTFT); results are cached per row for the session. `暂无测速记录` when nothing is stored yet
- New `app/bench.py` — a Python port of `bench_llm.mjs` (no Node sidecar): 1 warm-up + N streaming rounds, Decode/Prefill taken from server-side timings (`eval_duration`, `predicted_ms`, `prompt_eval_duration`) and falling back to the wall clock, with the choice recorded in `timing_source` (`server` / `client` / `client*`); thinking models get `think: false` automatically; a preflight resolves the model id (exact → normalised → substring) before measuring; its `httpx.AsyncClient` uses `trust_env=False` like the gateway's own client, otherwise a system-wide `HTTP_PROXY` hijacks localhost upstream calls and the test dies with `502`
- Benchmarks run on a **worker thread with its own event loop** (`asyncio.run`) rather than a task on the request's loop: a task created inside `POST /bench` is cancelled as soon as that request scope ends, which left jobs stuck in `running` forever. The result row is queued **before** the job flips to `done`, and `/bench/history` awaits `BenchStore.flush()` first, so polling `done` and reading the history can never observe an empty table; `BenchStore.record()` hands rows to the app loop with `call_soon_threadsafe`
- New `app/bench_store.py` — `bench_results` table in `llmhub.db` (same WAL queue writer as `requests`), keeping only the **latest 3** rows per `(upstream, model)`; the full per-round payload is stored as JSON
- `GET /v1/management/bench/history` is registered **before** `GET /v1/management/bench/{job_id}`: with the opposite order FastAPI matched the literal `history` segment as a job id and every hover returned 404 `任务不存在或已过期`
- Models · LLM table column sizing: the model name absorbs the leftover width while the rest get explicit hints (upstream 96 / type 88 / num_ctx 78 / device 104 / memory 124 / state 96 / actions 84 px) and `nowrap`, so `● 运行中` no longer wraps onto two lines; `.badge` is `nowrap` globally
- `docs/API.md` documents the three routes, the timing methodology and the `bench_results` schema; the console spec (§2.3) and `app/web` are updated

### Added (console: About page)

- New **关于 / About** view (`/admin/about`, no sidebar row - it sits next to 设置 in the account menu): project introduction, a two-column 主要功能 grid (multi-upstream routing, aliases + parameter injection, embedding / rerank, System One, web search, security & quotas, request log & stats, the read-only console, hot reload and streaming pass-through), version info and links
- The version block is live rather than baked in: `gateway_version` comes from `/v1/management/overview` (and fills the sidebar version when the dashboard was never opened), next to online upstreams, running models, today's requests / tokens, the request-log state and `generated_at`. A hollow ring breathes while the probe is in flight and fills once the value lands; `↻` re-probes
- Links to the repository (https://github.com/bruce1977/llm-hub), the Docker image and `/docs`, plus the WeChat 公众号 QR served same-origin from `web/public/wechat-qr.jpg` → `/admin/wechat-qr.jpg` (the src is built from `import.meta.env.BASE_URL`, and a missing asset shows where to put the file instead of a broken image)
- `docs/web-management/web-management-implementation.md` gained §2.10, `AGENTS.md` documents the page and the menu entry, and `dashboard-mockup.html` mirrors both; `app/web` rebuilt

### Added (console: module introductions)

- Models · Embed, Models · Rerank, Models · System One and Tools · Web Search each open their 模型清单 tab with a **功能介绍** card as the first card: ~200 characters on what the module is for (semantic search / clustering / dedupe / RAG, precision ranking after recall, low-latency decisions, fresh external knowledge) and how the gateway serves it. The four share `.feature-intro` (`web/src/styles/theme.css`), so the longer prose stays readable next to the denser `api-note`
- `docs/web-management/web-management-implementation.md` (§2.3b / 2.4 / 2.5 / 2.9) and `AGENTS.md` record the section; `dashboard-mockup.html` mirrors the card on all four pages; `app/web` rebuilt

### Changed (console: Web Search tabs and available models)

- Tools · Web Search now mirrors Models · Rerank: two tabs, **模型清单** (config card + available models) and **API 用法** (the `ApiDoc` block). Switching tabs sends no request; one `load()` fetches `/tools/websearch` and `/models/llm` together
- The new **可用模型** card lists the models of the provider upstream (`websearch.upstream`): rows are the `/models/llm` rows for that upstream, so an alias is shown as `display`, an expanded `opencode/*` wildcard keeps its `wildcard` chip, and a reachable hosted provider reads `● Cloud` / `● 在线`. The card explains that the search capability comes from a backend **opencode-port** deployment ([github.com/bruce1977/opencode-port](https://github.com/bruce1977/opencode-port)) - one `type: "opencode"` upstream pointed at it (default `127.0.0.1:10000`) plus `websearch.upstream` pointing at that upstream - and that the request `model` may be omitted
- The `model` parameter is documented as an opencode-port model name (not an llm-hub-configured one) and optional: omitted, opencode-port uses its own default model. `docs/API.md`, the console spec, AGENTS.md and `dashboard-mockup.html` carry the same wording
- `app/web` rebuilt

### Changed (console: sidebar polish, fixed account menu)

- The account chip now lives **fixed at the bottom-left** of the panel (`.user-dock`, `position: fixed`, aligned to the centred 1180px shell) instead of scrolling with the sidebar, so a long page (Requests) can no longer hide it; `.app` keeps `min-height: max(640px, calc(100vh - 48px))`, so on a short page the chip stays inside the card rather than floating below it. The avatar is a gradient circle with a double ring so the initial reads clearly
- Clicking the chip opens an account menu with: **语言 / Language** (a right-side flyout submenu - it does not expand the current menu - with 中文 selectable and English a `soon` placeholder until the UI translation lands), a **dark-mode switch** (the only theme toggle now; the sidebar and floating collapsed-state buttons are gone), **设置** (`/admin/settings`, whose sidebar row was removed) and **退出登录**. Outside `pointerdown` or `Esc` closes the menu; the flyout waits 150ms after the pointer leaves so the gap between the two can be crossed
- Rerank is `⇅` and Web Search is `◎`, matching the rest of the sidebar glyphs; the nav is Dashboard / Upstreams / Requests / Models (LLM · Embed · Rerank · System one) / Tools (Web Search)
- `docs/web-management/dashboard-mockup.html` mirrors the new dock, menu, flyout, nav order and icons, and gained a `#view-models-embed` section so the Embed nav link resolves; `app/web` rebuilt

### Changed (`auth` config: keys are environment-only, new IP whitelist)

- **`auth.api_keys` is gone**: keys are no longer written to `config.yaml` and come only from `GATEWAY_API_KEYS` and `GATEWAY_API_KEYS_FILE` (e.g. `data/.keys`), merged and de-duplicated. After loading, `ConfigManager` stores them in a module-level runtime store; `cfg.auth.api_keys` remains a read-only view, so `auth.py`, `admin_auth.py`, `main.py` and the launcher needed no call-site changes. A leftover `api_keys:` block is dropped by a `model_validator` with an "ignored" warning, and `merge_env_api_keys()` is deleted
- **New `auth.allow_access_ip_whitelist`**: a list of regexes matched with `re.fullmatch` against the ASGI client address (the **TCP peer** on a direct connection, `request.client.host`). Matching sources may open `/docs` and `/openapi.json` and call the API without an API key. **The gateway code never reads `X-Forwarded-For`** - a caller can forge it - but uvicorn's `ProxyHeadersMiddleware` rewrites `request.client` from forwarded headers when the peer is a trusted proxy (`--forwarded-allow-ips`, default `127.0.0.1`), so behind a local reverse proxy the real source is what matches (verified live: a local request carrying `X-Forwarded-For: 8.8.8.8` turns `/docs` into 404). A malformed pattern only logs a warning and never opens anything (fail closed)
- **`security.allow_docs` removed**: `/docs` visibility now follows `auth.allow_access_ip_whitelist`, and a non-matching address gets **404** (the same status the old "off" switch returned). `auth.py` gained `peer_ip()` / `ip_pattern_allowed()` / `docs_allowed()`, and the middleware's docs gate calls `docs_allowed()`
- Tests: `smoke_test.py` adds `[16b]` (empty whitelist → `/docs` 404 and API 401; `^testclient$` → `/docs`, `/openapi.json` and keyless `/v1/models` all 200; a forged `X-Forwarded-For` unlocks nothing; a malformed regex fails closed) and asserts the template has no `api_keys` while `SecurityConfig` no longer has `allow_docs` - **133** cases; `test_gateway_new.py`'s docs gate now toggles `auth.allow_access_ip_whitelist`
- Docs swept: README / README_CN (`auth` section, security table, public checklist, `/docs` note), `docs/API.md`, `docs/TESTCASES.md`, `docs/web-management/web-management-implementation.md`, `docs/workflow.md`, AGENTS.md and `data/config.example.yaml` / `data/config.example.json` / `.env.example`

### Added (config-driven model inventory, aliases and today's token total)

- The console's model **names** now come from `config.yaml` (`upstreams[].models`) instead of from whatever an instance happens to report. An installed-but-undeclared model is not routable through the gateway, so it does not belong in the table either; `GET /v1/management/models/llm` returns exactly one row per declared name per upstream
- A prefix wildcard is the one declaration that does query the upstream: `qwen3:*` or `opencode/*` is expanded, with `main.fetch_upstream_models()` - the same 60s-cached call the gateway routes with, imported inside the handler because `main` already imports `management` at module scope - into the concrete models it matches, and those rows carry `wildcard: true`. Discovery is only called for the upstreams that actually declare a pattern, and a pattern that matches nothing (including an offline upstream) stays visible as the pattern itself with `running: false`, rather than silently disappearing or claiming a string is online
- Every model row carries `model` (the real upstream id), `display` (the configured alias when there is one, otherwise the declared name) and `alias` (`null` when unaliased), from `_alias_by_target()` - the inverse of `Config.aliases` keyed by `(upstream, real id)`. The Dashboard's 运行中模型 table, the Upstreams expander and Models · LLM all show the alias with an `alias` chip and reveal the real id on hover, because that is the name callers actually send
- An `opencode` (hosted) upstream has no `/api/ps`: when its health probe answers, **every** model it declares counts as available - with `device: "cloud"` and `device_ratio`, `size_gib` and `vram_gib` left `null`, which the console renders as `Cloud` and `-` - and those rows join `GET /v1/management/overview`'s `running_models` and its KPI. `_hosted_models()` builds them for both `/overview` and `upstreams/{name}`, so the two views cannot disagree, and an offline provider contributes nothing
- `GET /v1/management/upstreams/{name}` returns the declared names (aliases applied, wildcards expanded) plus `running_models` (the `/api/ps` view for Ollama, the hosted list otherwise). The earlier `models_endpoint` echo is gone - the list is no longer sourced from the instance, so naming a discovery endpoint would be misleading
- `GET /v1/management/overview` KPIs gain **`tokens_today`**: today's (UTC) summed usage, preferring `tokens` and falling back to `tokens_in + tokens_out`, from a new aggregate in `request_log._today_summary_sync`. A row whose provider reported nothing contributes 0
- Tests: `tests/test_management.py` asserts the config-only row set, `display` / `alias` per row (an aliased model is shown as its alias, an unaliased one keeps its id), wildcard expansion replacing the pattern row with the models it matches (and each expanded model carrying its own runtime), an unresolvable pattern kept as a non-running row, hosted rows (probe online ⇒ all declared models running with `device: "cloud"` and memory `null`, added to the Dashboard list and the KPI; offline ⇒ no rows), the `/api/ps` device and memory columns, a dead upstream adding no runtime rows, `running_models` and `tokens_today`, plus that the built shell declares `/admin/favicon.svg` and the static route serves it as `image/svg+xml` instead of falling back to the SPA shell - **198** cases

### Changed (console: navigation, refresh feedback, charts, filters)

- The runtime configuration file is **`config.yaml`** (`data/config.yaml`, template `data/config.example.yaml`), parsed by PyYAML - a new `requirements.txt` entry. YAML 1.2 is a JSON superset, so an existing `config.json` body still loads: `ConfigManager._active_path()` falls back to a sibling `config.json` with a warning, `CONFIG_PATH` keeps honouring an explicit path whatever its extension, and `scripts/start-local.ps1` mirrors the same fallback (and now preflights `yaml`). Docs, examples, `.env.example`, the Dockerfile and the local launcher are swept to the new name
- Sidebar highlighting keys off `.nav-item.router-link-exact-active` instead of `router-link-active`. The Dashboard link lives on `/`, which is an ancestor of every route, so the old selector left it lit on every page (reproduced in the browser: on `/admin/upstreams` the Dashboard item still carried the active class)
- Dashboard and Upstreams now poll **once a minute** (`POLL_MS = 60000`), not every 5 s / 10 s. Each tick probes every upstream with a 5 s budget, so the old cadence could keep the gateway busy back-to-back
- Refreshing is now visible. `TopBar` takes a `loading` prop and, while it is set, spins and disables the `↻`, runs an indeterminate 2px progress line along the bar's bottom edge, swaps the sub-caption to `刷新中…` with a small spinner and greys out the "last updated" text; the Dashboard additionally dims the KPI tiles, veils all four charts and shows skeletons until the first payload lands. Each view owns one `refreshing` ref that re-enters-guarded and clears in `finally`; `TopBar`'s own 600ms fake spin is gone, because it used to announce completion while the upstream probes were still running. Rerank / System One / Web Search / Requests got the same wiring
- The "today" tile is now `今日请求 / Token 消耗` - request count beside the token total formatted by the new `fmtKiloTokens()` (`k`, two decimals, e.g. `0.04k` for 40)
- The three distribution charts are **solid pies** (a donut was tried and reverted on request): no labels on the disc at all (long model ids overlapped it), and name + count in a one-entry-per-line scrollable **legend below** the chart at 9.5px / 10.5px line-height with 16-character truncation, the disc pushed up so the rows never touch the slices
- Models · LLM table is 模型名 / 上游 / 类型 / num_ctx / 设备 / 显存 · 占用 (GiB) / 运行态 - the 别名 column is dropped and a `wildcard` chip marks the rows an expanded pattern produced; the 别名映射 table keeps only 别名 / 目标模型 / 上游
- Models · LLM is split into two tabs: **模型清单** (those three tables) and **API 用法**. The second reuses the Rerank page's `ApiDoc` layout to document the two generation endpoints the gateway supports today - `POST /api/generate` (its parameter table mirrors the `openapi_extra` schema in `app/main.py`: model / prompt / system / template / raw / context / images / format / stream / keep_alive / logprobs / top_logprobs / options) and `POST /v1/chat/completions` (model / messages / temperature / max_tokens / stream / tools), each with a request and a response example. Both state that they **stream by default** (omitting `stream` gives NDJSON and SSE respectively), that `model` is resolved through the gateway's aliases first, that `options.num_ctx` is still clamped by `num_ctx_overrides`, and that any non-POST method is a 405. Switching tabs sends no request; `↻` keeps refreshing only `/v1/management/models/llm`
- Device badges are consistent now: `.badge.cpu` lost its 1px border and bold weight so it matches `gpu` / `part` - the old style made the CPU row 2px taller than every other row. `dashboard-mockup.html` was updated to match. Badges now show the device type only (**GPU / 部分 / CPU / Cloud**) and never a percentage
- Hosted (`opencode`) models now report **Cloud**: the backend changed their `device` from `null` to `"cloud"` (both `_hosted_models()` and `_llm_row()`), and the console gained a violet `.badge.cloud`, so the Dashboard, Upstreams and Models · LLM all render `● Cloud` while memory stays `-`
- The sidebar moved **Requests** directly below **Upstreams** and added **Models · Embed** (above Rerank), which reuses the Rerank layout (info card + endpoint comparison + `ApiDoc`) to document `POST /api/embed` (Ollama native) and `POST /v1/embeddings` (OpenAI-compatible). The page fetches nothing - the gateway has no `embed` config block and both endpoints are pass-through
- Upstreams row-expander reads `running_models` from its own detail call instead of regrouping `/overview`, re-probes on every expand, tags the left table `/api/ps` or `在线（探测有效）` depending on the upstream type, and labels the right table `config.yaml · upstreams[].models`; an upstream that declares nothing says so instead of showing an empty grid
- The Requests **模型** filter follows **上游**: options are the selected upstream's declared names (with wildcards expanded), plus aliases, which inherit every upstream that serves their target (`ModelChoice.upstreams: string[]`); the label reads `模型 · <upstream>` and a model that the new upstream cannot serve is cleared rather than silently filtering to nothing
- New `web/public/favicon.svg` (blue→violet gradient tile with a hub mark) with `<link rel="icon">` in `web/index.html`; Vite copies `public/` verbatim into `app/web` and rewrites the href to `/admin/favicon.svg`, which the existing `/admin/{asset_path}` static route serves as `image/svg+xml` - no new route, no CDN, CSP-compatible
- `app/web` rebuilt and every view re-verified against a live gateway (real Ollama + OpenCode upstreams): the alias-resolved running-model list (`qwen3.6:35b` and `opencode/auto` shown by alias), 13 LLM rows of which 8 count as running (4 of them hosted, showing `Cloud` with `-` memory), `opencode/*` expanded to the provider's real ids, the pie legends, nav exact-active on `/admin/upstreams`, the runtime device/memory columns and the upstream-scoped model dropdown. All four mock suites stay green (133 / 63 / 198 / 15)
- Docs: `docs/API.md` endpoint table, `docs/TESTCASES.md` 测试 35 / 测试 36 (payload examples and assertions), `docs/web-management/web-management-implementation.md` (§2.1-2.3, §2.6, §3.2-3.3, §5), AGENTS.md (management API, web console, directory tree, test counts) and README / README_CN feature rows, endpoint tables and console paragraphs - plus the config-filename and YAML wording in README / README_CN, `docs/DEPLOY.md` and `docs/API.md`
- Note: the console briefly went the other way (union of live discovery with the config, `installed: false` for a declared-but-missing model, and `models_endpoint` naming the discovery call). That experiment is reverted: the displayed set is the configuration, and the API is consulted only to resolve a pattern the configuration itself says is open-ended

### Added (`tokens_in` / `tokens_out` in the request log)

- The `requests` table records token usage **per direction**: `tokens_in` holds the prompt/input count, `tokens_out` the generated/output count, and `tokens` keeps the total. Both response formats are covered - `usage.prompt_tokens` / `usage.completion_tokens` on OpenAI-compatible bodies and `prompt_eval_count` / `eval_count` on Ollama-native ones
- The total still prefers the provider's own `usage.total_tokens`, because it can legitimately exceed input + output when the backend counts reasoning tokens
- `request_log.extract_tokens()` became `extract_token_usage()`, returning the `(input, output, total)` triple. A response that never reports usage stores `NULL` in all three columns, so "not reported" stays distinguishable from a real `0`; numeric strings from sloppy providers are coerced, booleans are not counted
- Old databases upgrade in place as before, but `_ADDED_COLUMNS` now pairs every late-added column with its **declared type**, so `tokens_in` / `tokens_out` arrive as `INTEGER` instead of the `TEXT` the first three migrated columns used
- `GET /v1/management/requests` rows and `GET /v1/management/requests/{id}` carry both counts, and the console's request drawer shows `Token 输入 / 输出` with the total beside it (`web/src/types/api.ts` gained the fields, so the split is compile-checked)
- Streaming responses report no usage to the gateway today, so their rows leave all three token columns empty - unchanged by this commit, and the reason the split is only captured on the non-streaming path
- Tests: `tests/test_management.py` asserts the Ollama split (5 + 7 = 12), the OpenAI split (4 + 8 = 12), `NULL` for a response without usage, the split on the list rows and the `INTEGER` declaration on an upgraded legacy database - **166** cases
- Docs: the column tables and examples in `docs/API.md`, `docs/TESTCASES.md` 测试 37 / 测试 39, `docs/web-management/web-management-implementation.md`, AGENTS.md and README / README_CN

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

- New `scripts/start-local.ps1` (+ `scripts/start-local.bat`, a cmd wrapper that calls it with `-ExecutionPolicy Bypass`): locates python (`python`, else the `py -3` launcher, overridable with `-Python`), reads `.env`, finds `config.yaml` and **runs the app's own rules before launching**, so the checks cannot drift - `config.yaml` is parsed by the real Pydantic models and the keys are screened by `auth.find_weak_keys`
- Preflight covers the usual local failures: missing dependency (with the `-Install` hint), a config the validator rejects, `auth.enabled` with no key, weak/placeholder keys (fatal only when `security.fail_on_weak_keys` says the boot would fail, otherwise a warning), `ADMIN_PASSWORD` unset (console and `/v1/management` would answer 503), `app/web/index.html` missing (a rebuilt console is needed), and a port already in use - named with the owning PID and process name
- It starts uvicorn the way the flat modules need: `--app-dir app` with the working directory set to `app/`, exactly the image's `WORKDIR /app`, so the relative paths written in `.env` (`../data/config.yaml`, `../data/.keys`) resolve identically on the host and in the container. The old documented form `python -m uvicorn app.main:app` is **removed** from AGENTS.md - it cannot work, the modules import each other by bare name (`ModuleNotFoundError: No module named 'admin_auth'`)
- Config resolution is `-Config` (relative to the caller's directory) > `CONFIG_PATH` from `.env` (relative to `app/`, used only when it exists) > `<repo>\data\config.yaml`, which is created from `data\config.example.yaml` with a warning when missing. Values already exported in the session win over `.env`, an unreadable `GATEWAY_API_KEYS_FILE` is dropped with a warning when inline `GATEWAY_API_KEYS` are available, and `--reload` is silently downgraded when `watchfiles` is not installed
- Flags: `-Port` / `-Bind` / `-LogLevel` (default to `server.*` from the config), `-EnvFile`, `-Python`, `-NoReload`, `-Install`, `-CheckOnly` (run the checks, start nothing). Only secret **names** are ever printed
- Docs: README / README_CN gain a "Launcher / 启动脚本" section with the flag table; `docs/DEPLOY.md` documents the script next to the build/publish scripts; AGENTS.md lists both files in the `scripts/` tree, replaces the broken Local Development command and adds a Common Issues entry

### Added (Web console frontend)

- New `web/` app: Vue 3 + Vite + Element Plus + axios + ECharts single-page console. Sources live in `web/src`, and `npm run build` writes the bundle into **`app/web`**, which is committed and served by FastAPI at **`/admin`** - the bundle sits next to the modules, so the local `app/web` and the image's `/app/web` are the same resolved path (`Dockerfile` only does `COPY app/web /app/web`, so the image needs no node toolchain; develop with `cd web && npm install && npm run dev`, ship with `npm run build`)
- Eight views: Dashboard (KPIs, 24h/7d trend, upstream/model/status pies), Upstreams (expandable rows, VRAM gauge, GPU/partial/CPU device badges, offline-upstream notice), Models·LLM (upstream model mapping, aliases and `num_ctx_overrides` as a read-only projection of `config.yaml`), Models·Rerank, Models·System One, Tools·Web Search (parameter reference plus request/response examples), Requests (time/upstream/model/status/path filters, pagination and a detail drawer with the truncated request/response bodies) and Settings (placeholder). Every chart is ECharts and reads its colors from CSS custom properties, so switching theme recolors the charts too
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
- New `config.yaml` block `request_log` with the write switch `enabled` (default `true`). `ProxyService._logging_on` evaluates it **per request**, so saving the file starts or stops recording without a restart - the config mtime is polled at most once per second, so the switch lands within ~1s (verified against a live Ollama: zero new rows while off, exactly one row after re-enabling); while off, bodies are not captured and no row is queued. `/v1/management/overview` and `/v1/management/requests` report the state as `request_log: {enabled, db, retention_days, dropped}` so the console can say "logging disabled" instead of showing an empty list
- `proxy.py` instruments every outcome - blocked (403), oversized (413), unrouted (404), streaming, non-streaming and connect/read/HTTP failures (502/504) - recording method, path, resolved model, upstream, status, duration, TTFB, stream flag, token usage (`usage.total_tokens` or Ollama's `prompt_eval_count + eval_count`), sizes, client IP and truncated bodies
- Bodies are stored truncated to 8 KB with `authorization` / `api_key` / `token` / `password` fields replaced by `***`; the request-detail route returns them for the console's drawer
- `GET /v1/management/requests` supports `start` / `end` / `upstream` / `model` / `status` (2xx|3xx|4xx|5xx) / `path` / `page` / `page_size` and returns `stats` (count, P50/P95/P99, error rate), `rows` and `pagination`; `stats/timeseries` buckets by hour or day with per-bucket count/errors/P95, `stats/distribution?group_by=upstream|model|status` returns the grouped counts that drive the three Dashboard pies (`status` collapses to `ok` below 400 and `error` at 400+, rows that never reached an upstream bucket as `-`, so the items always sum to the log total; any other `group_by` answers **400**), and `stats/failures` breaks failures down by status code and upstream with recent samples
- `UpstreamConfig.vram_total_gb`: optional host VRAM total for the overview gauge (not discoverable over the API)
- `/v1/management` is registered before the catch-all and exempted from `security.path_allowlist` (like `/admin`); legacy `/admin/login|logout|ping` now delegate to the same implementation
- Tests: new `tests/test_management.py` (158 cases, including the 11 console-serving cases above) with a local mock Ollama - auth (session cookie **and** gateway API key → 200; the admin password as `Bearer` / `X-API-Key` → 401; unknown key → 401; key in the query string → 401; wrong username → 401; username trimmed and lowercased; 503 when unset), probing and device classification, masked projections including `tools/websearch` and the rerank `upstream_api` / `on_error`, streaming/non-stream/502 capture, the new columns (arrival/finish timestamps, alias `requested_model` vs resolved `model`, `query`), filters, pagination, timeseries, distribution (bucket conventions, ordering, totals and the `group_by` guard), the `request_log.enabled` off/on behaviour and the legacy-database `ALTER TABLE` upgrade
- Docs/config: `docs/API.md` gains a "管理 API（Web 管理台）" section with the `requests` column list, the `request_log.enabled` switch and worked `stats/distribution` / `tools/websearch` responses; README / README_CN add the feature row, directory entries, a `request_log` config block, `ADMIN_PASSWORD` / `REQUEST_LOG_*` variables and `vram_total_gb`; `docs/TESTCASES.md` adds 测试 34-40; `.env.example`, `data/config.example.yaml` (now shipping `request_log`), `.gitignore` (`data/*.db*`), `docs/DEPLOY.md` and AGENTS.md updated

### Added (Web Search)

- New endpoint `POST /v1/tools/web-search`: keyword web search served by an OpenCode API Gateway. The request body and the provider response are passed through unchanged (`query`, `results[]`, concatenated `text`, `sources`, plus `answer` when `synthesize: true`)
- `POST /v1/tools/web-search` now logs a diagnostic line when the provider is unreachable: the full exception chain plus a per-address TCP probe (`dns(host) -> ip:port ConnectionRefusedError errno=111`, `TimeoutError`, `gaierror`), since httpx collapses all of them into `All connection attempts failed`
- New `websearch` config block: `enabled`, `upstream`, `path` (default `v1/web-search`), `api_key_env`, `timeout`. `upstream` selects the provider by name; left empty it picks the first `type: "opencode"` upstream, and an unknown name returns 503 instead of silently falling back
- New upstream type `opencode`: OpenAI-compatible model discovery (`/v1/models`, `data[].id`), liveness via `/health`. Registering the provider as a normal upstream means its models also join the aggregated `/v1/models` and chat can be routed to it
- Optional provider signing through `websearch.api_key_env` (empty by default = unsigned, which is what the current gateway needs); `OPENCODE_GATEWAY_API_KEY` documented in `.env.example` and `docs/DEPLOY.md`
- The endpoint appears in `/docs` with a real request/response schema (`query` required, `top_k`, `recency`, `synthesize`, ...), is POST-only (405 otherwise) and requires the gateway API key (401/403)
- `data/config.yaml` and `data/config.example.yaml` ship an `opencode` upstream (declaring `auto` plus the `opencode/*` wildcard) and a `websearch` block
- API.md: new "Web Search（OpenCode 网关代理）" section; README / README_CN: feature row, `websearch` config reference, curl example, `opencode` upstream type and the slash-wildcard caveat (`opencode/*`, not `opencode:*`)
- Tests: `smoke_test.py` +13 (104 total) for the `opencode` type and `websearch` config resolution; `test_gateway_new.py` +22 (55 total) for the endpoint's auth, forwarding, pass-through, signing, status mapping and OpenAPI schema; `test_api.py` +8 (28 total) E2E cases; docs/TESTCASES.md adds 测试 31-33 with measured responses

### Added (Build & publish scripts)

- `scripts/docker-build.bat`: builds `bruce1977/llm-hub:latest` plus `:<version>`, reading the version from `__version__` in `app/main.py` (overridable: `docker-build.bat [image] [version]`); aborts with exit code 1 when Docker is unreachable
- `scripts/docker-publish.bat`: runs the build first and only then pushes `latest` and `:<version>` to Docker Hub, stopping with a `docker login` hint if a push fails. Neither script touches git - commit and tag by hand before publishing
- `docs/DEPLOY.md`: new "一键构建 / 发布脚本（Windows）" section documenting both scripts

### Changed (Repository layout)

- `API.md`, `DEPLOY.md`, `workflow.md`, `Ranker.md` and `tests/TESTCASES.md` moved into `docs/` (history preserved); `README*`, `CHANGELOG*` and `AGENTS.md` stay at the repository root, and cross-links were updated accordingly
- `.example.env` renamed to `.env.example`, the conventional template name; every copy instruction in README / README_CN / `docs/DEPLOY.md` / `docs/workflow.md` / `docs/TESTCASES.md` / AGENTS.md follows the new name
- Removed two orphaned root files: `opencode.jsonc` (its `.opencode/skills` directory was deleted in 9a8bfea, so it pointed at nothing) and `Modelfile.reranker` (the authoritative reranker Modelfile lives outside the repo and is documented in `docs/Ranker.md`); the cross-reference in `data/config.example.yaml` now points at that document

### Changed (System One backend)

- **Breaking (deployment):** the environment variable naming the System One backend key is renamed `LAYA_SERVER_API_KEY` → `SYSTEMONE_SERVER_API_KEY`, because the endpoint is no longer laya-server only. Rename it in `.env` / the container environment; `systemone.api_key_env` may still be set to any other variable name
- Ollama 0.40+ serves `POST /v1/systemone` natively, so `systemone.backend` can now point at any Ollama port (e.g. `http://llm.home:11435`) - no gateway code change; forwarding stays pure pass-through
- `systemone.model` is confirmed to travel to the backend on the wire. It is injected only when it is a concrete value and the caller supplied no `model`; `"local"` remains the "do not inject" sentinel. The code default stays `auto` (valid for laya-server); `data/config.yaml` and `data/config.example.yaml` now ship `model: "tev1"` against an Ollama backend, since Ollama answers **404** for `auto` and **400** when the model is missing
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
