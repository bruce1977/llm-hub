import axios from 'axios'
import type { AxiosError, AxiosResponse } from 'axios'

/** What the gateway puts in a failed response body (`HTTPException` uses `detail`). */
export interface ErrorBody {
  detail?: unknown
}

// The console talks only to the management API. The session cookie is HttpOnly and
// same-origin, so `withCredentials` is what carries the login through.
export const http = axios.create({
  baseURL: '/v1/management',
  timeout: 20000,
  withCredentials: true,
})

export type UnauthorizedHandler = (status: number) => void

let onUnauthorized: UnauthorizedHandler | null = null

// Registered by main.ts once the router exists; importing the router here would be a
// cycle (views -> api -> router -> views).
export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  onUnauthorized = handler
}

function isAuthRoute(url: unknown): boolean {
  return typeof url === 'string' && url.startsWith('/auth/')
}

/** HTTP status of a failed call, or `null` when there was no response at all. */
export function statusOf(error: unknown): number | null {
  const response = (error as AxiosError<ErrorBody> | null)?.response
  return typeof response?.status === 'number' ? response.status : null
}

/** Response body of a failed call, or `null` when the request died before answering. */
export function errorResponseBody<T = unknown>(error: unknown): T | null {
  const data = (error as AxiosError<T> | null)?.response?.data
  return data ?? null
}

http.interceptors.response.use(
  (response: AxiosResponse): AxiosResponse => response,
  (error: AxiosError<ErrorBody>) => {
    // 401 is the only authentication failure: the session cookie is missing, expired or
    // tampered with, so the gate is the right destination. A 503 is *not* an auth failure
    // (ADMIN_PASSWORD unset, or an unreachable upstream behind upstreams/{name}) and is
    // left for the caller to explain.
    if (error.response?.status === 401 && !isAuthRoute(error.config?.url) && onUnauthorized) {
      onUnauthorized(401)
    }
    return Promise.reject(error)
  },
)

export function errorDetail(error: unknown): string {
  const data = (error as AxiosError<ErrorBody> | null)?.response?.data
  if (data && typeof data === 'object' && data.detail) {
    return typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
  }
  if ((error as AxiosError | null)?.code === 'ECONNABORTED') return '请求超时，请稍后重试。'
  return (error as Error | null)?.message || '请求失败。'
}
