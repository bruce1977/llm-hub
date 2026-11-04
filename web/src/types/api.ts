// Response types for the read-only management API (/v1/management), as emitted by
// app/management.py. The shapes are taken from live payloads, so a field is optional only
// when the backend really can leave it out - a view must not paper over a contract change.

export type UpstreamType = 'ollama' | 'openai' | 'lmstudio' | 'opencode' | (string & {})
export type UpstreamStatus = 'online' | 'offline'
export type DeviceKind = 'gpu' | 'partial' | 'cpu'
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
  /** Absent on the 503 "unreachable" answer, which stops before the /api/tags probe. */
  models?: string[]
  unreachable: boolean
  models_error?: string
}

export interface RunningModel {
  model: string
  upstream: string
  device: DeviceKind
  device_ratio: number
  num_ctx: number | null
  size_gib: number
  vram_gib: number
  expires_at: string | null
}

export interface Kpi {
  upstreams_online: number
  upstreams_total: number
  running_models: number
  vram_used_gib: number
  vram_total_gib: number | null
  requests_today: number
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

export interface LlmModel {
  model: string
  upstream: string
  type: UpstreamType
  alias: string | null
  num_ctx: number | null
  wildcard: boolean
  running: boolean
}

export interface ModelAlias {
  alias: string
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

/** A row of the request-log table. `stream` is the SQLite integer flag (0 / 1). */
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
  key: string
  count: number
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
