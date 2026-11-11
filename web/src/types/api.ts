// Response types for the read-only management API (/v1/management), as emitted by
// app/management.py. The shapes are taken from live payloads, so a field is optional only
// when the backend really can leave it out - a view must not paper over a contract change.

export type UpstreamType = 'ollama' | 'openai' | 'lmstudio' | 'opencode' | (string & {})
export type UpstreamStatus = 'online' | 'offline'
/** `/api/ps` classifies an Ollama resident model by size_vram/size; hosted providers
 *  (opencode) have no load state and report `cloud` instead. */
export type DeviceKind = 'gpu' | 'partial' | 'cpu' | 'cloud'
export type DistributionKey = 'upstream' | 'model' | 'status'
export type Bucket = 'hour' | 'day'

export interface ProbeResult {
  name: string
  type: UpstreamType
  base_url: string
  description: string
  status: UpstreamStatus
  reachable: boolean
  probe_ms: number | null
  loaded: number | null
  vram_used_gib: number
  vram_total_gib: number | null
  ps_supported: boolean
  error: string | null
}

export interface UpstreamDetail extends ProbeResult {
  /** Models this upstream declares in `config.yaml`, aliases applied. Absent on the 503
   *  "unreachable" answer, which stops before the model list is built. */
  models?: string[]
  /** The same instances' resident models as reported by /overview, per upstream. */
  running_models?: RunningModel[]
  unreachable: boolean
}

/** A model the console reports as available: an Ollama resident model (`/api/ps`), one
 *  declared by a hosted provider such as OpenCode (which has no load state - its device,
 *  memory and expiry are null and render as "-"), or a model derived from a root `aliases:`
 *  entry, which runs inside its target's instance and therefore reports no memory of its own. */
export interface RunningModel {
  model: string
  /** Rename target: the `model_aliases` short name when there is one, otherwise `model`. */
  display: string
  alias: string | null
  /** True for a model declared under root `aliases:` - it is callable on its own name. */
  derived: boolean
  /** Real upstream id a derived model runs on; only present when `derived` is true. */
  target?: string
  params?: Record<string, unknown>
  description?: string | null
  upstream: string
  device: DeviceKind | null
  device_ratio: number | null
  num_ctx: number | null
  size_gib: number | null
  vram_gib: number | null
  expires_at: string | null
}

export interface Kpi {
  upstreams_online: number
  upstreams_total: number
  running_models: number
  vram_used_gib: number
  vram_total_gib: number | null
  requests_today: number
  /** Tokens recorded today; rows without usage (streams) contribute nothing. */
  tokens_today: number
  p95_ms: number
  error_rate: number
}

export interface RequestLogState {
  enabled: boolean
  db: string
  retention_days: number
  dropped: number
}

export interface Overview {
  kpi: Kpi
  request_log: RequestLogState
  upstreams: ProbeResult[]
  running_models: RunningModel[]
  generated_at: string
  gateway_version: string
}

export interface UpstreamsResponse {
  upstreams: ProbeResult[]
}

/** One model an upstream declares in `config.yaml` (`upstreams[].models`), or one derived from
 *  a root `aliases:` entry. `display` is the `model_aliases` rename when there is one, otherwise
 *  the declared name; `model` is the name the caller sends, so a derived model carries its own
 *  alias name here and keeps the real id in `target`. The runtime fields (device, sizes,
 *  num_ctx) only exist while an Ollama instance holds the model in memory - a hosted provider
 *  reports them as null, and a derived model shows no memory because its instance is already
 *  counted under its own name. */
export interface LlmModel {
  model: string
  display: string
  alias: string | null
  derived: boolean
  target?: string
  params?: Record<string, unknown>
  description?: string | null
  upstream: string
  type: UpstreamType
  wildcard: boolean
  running: boolean
  device: DeviceKind | null
  device_ratio: number | null
  num_ctx: number | null
  size_gib: number | null
  vram_gib: number | null
}

export interface ModelAlias {
  alias: string
  /** `rename` came from `upstreams[].model_aliases`; `derived` is a model of its own. */
  kind: 'rename' | 'derived'
  target: string
  upstream: string | null
  params: Record<string, unknown>
  description: string | null
}

export interface ModelsLlm {
  models: LlmModel[]
  aliases: ModelAlias[]
  num_ctx_overrides: Record<string, number>
}

export interface RerankModel {
  model: string
  upstream: string
  type: UpstreamType
  base_url: string
  /** The upstream key, masked - never the value. */
  api_key: string | null
}

export interface ModelsRerank {
  enabled: boolean
  endpoint: string
  mode: string
  upstream_api: string
  on_error: string
  models: RerankModel[]
  scoring: {
    temperature: number
    top_logprobs: number
    max_tokens: number
    token_scan_depth: number
    keep_alive: string
    options: Record<string, unknown>
  }
  limits: {
    max_concurrency: number
    max_documents: number
    max_document_chars: number
  }
  request_example: Record<string, unknown>
  response_example: Record<string, unknown>
}

export interface ModelsSystemOne {
  enabled: boolean
  endpoint: string
  path: string
  backend: string
  base_url: string
  upstream: string | null
  linked_model: string | null
  api_key_env: string
  api_key: string | null
  timeout: number
  request_example: Record<string, unknown>
  stream_example: string
}

export interface ToolsWebSearch {
  enabled: boolean
  endpoint: string
  upstream: string | null
  upstream_type: UpstreamType | null
  path: string
  base_url: string
  api_key_env: string
  api_key: string | null
  timeout: number
  provider_configured: boolean
}

/** A row of the request-log table. `stream` is the SQLite integer flag (0 / 1);
 *  `tokens_in` / `tokens_out` are the usage split and `tokens` the reported total. */
export interface RequestRow {
  id: number
  ts: string
  ts_end: string | null
  method: string
  path: string
  query: string | null
  requested_model: string | null
  model: string | null
  upstream: string | null
  status: number
  duration_ms: number
  ttfb_ms: number | null
  stream: number
  tokens_in: number | null
  tokens_out: number | null
  tokens: number | null
}

/** The same row plus the bodies, where `stream` and the two truncation flags are booleans. */
export interface RequestDetail extends Omit<RequestRow, 'stream'> {
  req_size: number | null
  res_size: number | null
  client_ip: string | null
  error: string | null
  request_body: string | null
  response_body: string | null
  request_body_truncated: boolean
  response_body_truncated: boolean
  stream: boolean
}

export interface RequestStats {
  count: number
  p50_ms: number
  p95_ms: number
  p99_ms: number
  error_rate: number
}

export interface Pagination {
  page: number
  page_size: number
  total: number
  pages: number
}

export interface RequestsResponse {
  stats: RequestStats
  rows: RequestRow[]
  pagination: Pagination
  request_log: RequestLogState
}

export interface RequestsQuery {
  start?: string
  end?: string
  upstream?: string
  model?: string
  status?: string
  path?: string
  method?: string
  page?: number
  page_size?: number
}

export interface TimeseriesPoint {
  ts: string
  count: number
  errors: number
  p95: number
}

export interface Timeseries {
  bucket: Bucket
  start: string
  end: string
  points: TimeseriesPoint[]
}

export interface DistributionItem {
  key: string // real model id / upstream / status bucket, used as the filter value
  count: number
  display?: string // alias-priority label for the by-model pie; absent on other groupings
}

export interface Distribution {
  group_by: DistributionKey
  start: string
  end: string
  items: DistributionItem[]
}

export interface FailureSample {
  ts: string
  path: string
  model: string | null
  upstream: string | null
  status: number
  duration_ms: number
}

export interface Failures {
  total: number
  ok: number
  error_rate: number
  by_code: Record<string, number>
  by_upstream: Record<string, number>
  samples: FailureSample[]
}

export interface AuthPing {
  ok: boolean
  admin: boolean
}

export interface AuthResult {
  ok: boolean
}

/** Range query accepted by the three stats routes. */
export interface RangeQuery {
  start?: string
  end?: string
}

// --------------------------------------------------------------------------- //
// Benchmark (speed test) - /v1/management/bench*. Shapes mirror app/bench.py and
// app/bench_store.py exactly: a job polls to completion, each (upstream, model) keeps
// the latest 3 records in llmhub.db.
// --------------------------------------------------------------------------- //
export type BenchSource = 'server' | 'client' | 'client*' | (string & {})
export type BenchJobStatus = 'pending' | 'running' | 'done' | 'failed'

export interface BenchProgress {
  completed: number
  total: number
  warmup: boolean
}

export interface BenchStats {
  count: number
  mean: number
  std_dev: number
  min: number
  max: number
  cv_percent: number
}

export interface BenchRound {
  total_elapsed_s: number
  ttft_s: number | null
  prompt_tokens: number
  gen_tokens: number
  server_gen_ms: number | null
  server_prefill_ms: number | null
  server_load_ms: number | null
  decode_tok_s: number
  prefill_tok_s: number
  timing_source: BenchSource
  finish_reason: string | null
  reasoning_chars: number
  content_len: number
  thinking_leaked: boolean
  round: number
  warmup: boolean
}

export interface BenchConfig {
  server: string
  base_url: string
  host: string
  port: number
  model: string
  rounds: number
  warmup: number
  max_tokens: number
  temperature: number
  think: string
  think_suppressed: boolean
  prompt: string
}

export interface BenchResult {
  config: BenchConfig
  rounds: BenchRound[]
  timing_source: BenchSource
  steady: {
    decode: BenchStats
    prefill: BenchStats
    ttft: BenchStats & { mean_ms: number }
  }
}

export interface BenchJob {
  job_id: string
  upstream: string
  model: string
  status: BenchJobStatus
  progress: BenchProgress
  result: BenchResult | null
  error: string | null
  created_at: string
  finished_at: string | null
}

export interface BenchHistoryRecord {
  id: number
  upstream: string
  model: string
  created_at: string
  server: string | null
  host: string | null
  port: number | null
  rounds: number | null
  warmup: number | null
  max_tokens: number | null
  decode_tok_s: number | null
  decode_sd: number | null
  prefill_tok_s: number | null
  ttft_ms: number | null
  timing_source: BenchSource | null
  prompt_tokens: number | null
  gen_tokens: number | null
  payload: BenchResult | null
}

export interface BenchHistory {
  upstream: string
  model: string
  records: BenchHistoryRecord[]
}

// --------------------------------------------------------------------------- //
// Interactive API testers - /v1/management/api-test*. Shapes mirror app/api_test.py:
// `KINDS` is a closed allowlist of endpoint families, and each one gets the models that
// actually answer it, the credentials the call is signed with (masked) and, after a run,
// the envelope the gateway produced.
// --------------------------------------------------------------------------- //
export type ApiTestKind =
  | 'generate'
  | 'chat'
  | 'messages'
  | 'embed_ollama'
  | 'embed_openai'
  | 'rerank'
  | 'systemone'
  | 'websearch'

/** A model the selector offers: an `LlmModel` row plus the masked key of the upstream that
 *  would sign the call. `unknown` marks a name written in `api_models` that nothing serves -
 *  a misconfiguration to show, not hide, so its upstream may be null. */
export interface ApiTestModel extends Omit<LlmModel, 'upstream' | 'type'> {
  upstream: string | null
  type: UpstreamType | null
  api_key: string | null
  unknown: boolean
}

export interface ApiTestKindContext {
  kind: ApiTestKind
  method: string
  /** The public path the gateway serves, which for rerank is the configured endpoint. */
  endpoint: string
  family: string
  model_required: boolean
  /** A derived family may accept a name outside `models` (a laya enum, an opencode-port id).
   *  False once `api_models` explicitly listed the family's choices. */
  free_model: boolean
  enabled: boolean
  /** Which config block the list came from, shown so the user knows where to edit it. */
  source: string
  models: ApiTestModel[]
  default_model: string | null
  /** Masked key for the capability's own env variable, when that variable is set. */
  api_key: string | null
  api_key_env: string | null
}

export interface ApiTestContext {
  kinds: Partial<Record<ApiTestKind, ApiTestKindContext>>
  max_response_bytes: number
}

/** One executed tester request. A refusal is data, not an exception: the gateway's own
 *  status and detail land in `status` / `text` exactly as an external caller would see them. */
export interface ApiTestResult {
  kind: ApiTestKind
  method: string
  endpoint: string
  stream: boolean
  truncated: boolean
  error: string | null
  requested_model: string | null
  model: string | null
  upstream: string | null
  injected: Record<string, unknown> | null
  status: number
  ok: boolean
  duration_ms: number
  ttft_ms: number | null
  content_type: string | null
  /** Parsed JSON body, null for a streamed or non-JSON answer (see `text`). */
  body: unknown
  text: string
}

// --------------------------------------------------------------------------- //
// Config (Settings console): the whole config.yaml object, editable in place.
// The console only renders the four common segments (upstreams / rerank /
// systemone / websearch); the rest is preserved verbatim on save, so we keep the
// payload loose and let the backend validate the segments it owns.
// --------------------------------------------------------------------------- //
export interface ConfigPayload {
  upstreams: ConfigUpstream[]
  // The console only renders the four common segments; the rest is preserved verbatim,
  // so the editable blocks are kept loose (the backend validates them per segment).
  rerank: any
  systemone: any
  websearch: any
}

export interface ConfigUpstream {
  name: string
  base_url: string
  type?: string
  models?: string[]
  model_aliases?: Record<string, string>
  headers?: Record<string, string>
  // Native number inputs bind through v-model.number, which yields a string ("") while a
  // field is cleared; the console coerces these back to numbers before saving.
  timeout?: number | string | null
  priority?: number | string
  vram_total_gb?: number | string | null
  description?: string
}

/** Per-segment validation errors returned by PUT /config (HTTP 422). */
export interface ConfigSaveError {
  segments: Record<string, string>
}

// --------------------------------------------------------------------------- //
// API keys (GET/POST/DELETE /v1/management/api-keys). Only the hash lives in
// llmhub.db, so a listed row carries a mask rather than a secret; the plaintext
// appears in exactly one response - the one that created it.
// --------------------------------------------------------------------------- //
/** `file` marks a key imported from the environment on first start, `console` one minted here. */
export type ApiKeySource = 'console' | 'file'

export interface ApiKeyItem {
  id: number
  masked: string
  label: string
  source: ApiKeySource
  /** ISO UTC, second precision. */
  created_at: string
}

export interface ApiKeysResponse {
  items: ApiKeyItem[]
  total: number
}

export interface ApiKeyCreated {
  item: ApiKeyItem
  /** The only time this value is ever returned. */
  secret: string
}

export interface ApiKeyDeleted {
  deleted: number
}
