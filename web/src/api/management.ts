import type { AxiosResponse } from 'axios'

import { http } from './client'
import type {
  AuthPing,
  AuthResult,
  BenchHistory,
  BenchJob,
  Bucket,
  Distribution,
  DistributionKey,
  Failures,
  ModelsLlm,
  ModelsRerank,
  ModelsSystemOne,
  Overview,
  RangeQuery,
  RequestDetail,
  RequestsQuery,
  RequestsResponse,
  Timeseries,
  ToolsWebSearch,
  UpstreamDetail,
  UpstreamsResponse,
} from '@/types/api'

// One wrapper per management endpoint. Every route is a read-only GET except the three
// auth routes; see docs/API.md "管理 API（Web 管理台）". The generics are what gives every
// view a typed `data` - a renamed backend field becomes a compile error here first.
export const api = {
  login: (username: string, password: string): Promise<AxiosResponse<AuthResult>> =>
    http.post<AuthResult>('/auth/login', { username, password }),
  logout: (): Promise<AxiosResponse<AuthResult>> => http.post<AuthResult>('/auth/logout'),
  ping: (): Promise<AxiosResponse<AuthPing>> => http.get<AuthPing>('/auth/ping'),

  overview: () => http.get<Overview>('/overview'),
  upstreams: () => http.get<UpstreamsResponse>('/upstreams'),
  upstream: (name: string) => http.get<UpstreamDetail>(`/upstreams/${encodeURIComponent(name)}`),

  modelsLlm: () => http.get<ModelsLlm>('/models/llm'),
  modelsRerank: () => http.get<ModelsRerank>('/models/rerank'),
  modelsSystemOne: () => http.get<ModelsSystemOne>('/models/systemone'),
  toolsWebSearch: () => http.get<ToolsWebSearch>('/tools/websearch'),

  requests: (params: RequestsQuery) => http.get<RequestsResponse>('/requests', { params }),
  requestDetail: (id: number) => http.get<RequestDetail>(`/requests/${id}`),

  timeseries: (params: RangeQuery & { bucket?: Bucket }) =>
    http.get<Timeseries>('/stats/timeseries', { params }),
  distribution: (params: RangeQuery & { group_by: DistributionKey }) =>
    http.get<Distribution>('/stats/distribution', { params }),
  failures: (params: RangeQuery) => http.get<Failures>('/stats/failures', { params }),

  // Benchmark (speed test). benchStart kicks off an async job and returns a job_id;
  // benchStatus polls until status is "done" or "failed"; benchHistory reads the latest
  // 3 persisted records for a (upstream, model) from llmhub.db.
  benchStart: (body: {
    upstream: string
    model: string
    rounds?: number | null
    max_tokens?: number | null
    think?: string | null
  }): Promise<AxiosResponse<{ job_id: string; status: string }>> =>
    http.post<{ job_id: string; status: string }>('/bench', body),
  benchStatus: (jobId: string): Promise<AxiosResponse<BenchJob>> =>
    http.get<BenchJob>(`/bench/${encodeURIComponent(jobId)}`),
  benchHistory: (upstream: string, model: string): Promise<AxiosResponse<BenchHistory>> =>
    http.get<BenchHistory>('/bench/history', { params: { upstream, model } }),
}
