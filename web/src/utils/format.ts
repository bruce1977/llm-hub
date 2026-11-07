// Formatting helpers shared by the views. The management API emits naive UTC ISO strings
// (second precision), so every display path appends "Z" before parsing.

import { i18n } from '@/i18n'

export type BadgeClass = 'idle' | 'gpu' | 'part' | 'cpu' | 'cloud' | 'ok' | 'err' | 'warn'

export interface Badge {
  cls: BadgeClass
  text: string
}

export function parseUtc(value: string | null | undefined): Date | null {
  if (!value) return null
  const text = String(value).trim()
  const iso = text.includes('+') || text.endsWith('Z') ? text : `${text.replace(' ', 'T')}Z`
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date
}

export function fmtClock(value: string | null | undefined): string {
  const date = parseUtc(value)
  if (!date) return '—'
  return date.toLocaleTimeString('zh-CN', { hour12: false })
}

export function fmtDateTime(value: string | null | undefined): string {
  const date = parseUtc(value)
  if (!date) return '—'
  return `${date.toLocaleDateString('zh-CN')} ${date.toLocaleTimeString('zh-CN', { hour12: false })}`
}

export function fmtBucket(value: string | null | undefined, bucket: string | null | undefined): string {
  // Day buckets arrive as local calendar dates (the API already shifted them by the caller's
  // offset), so they are reformatted as text: parsing them as instants would convert a second
  // time and land on the previous day anywhere west of UTC.
  if (bucket === 'day') {
    const parts = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value ?? ''))
    return parts ? `${parts[2]}/${parts[3]}` : value || '—'
  }
  const date = parseUtc(value)
  if (!date) return value || '—'
  return date.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' })
}

export function fmtInt(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return Number(value).toLocaleString('en-US')
}

export function fmtMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${fmtInt(Math.round(Number(value)))} ms`
}

export function fmtPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return '—'
  return `${(Number(value) * 100).toFixed(digits)}%`
}

/** Token totals are only ever meaningful in thousands on the KPI tiles: 12345 -> "12.35k". */
export function fmtKiloTokens(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${(Number(value) / 1000).toFixed(2)}k`
}

export function fmtGib(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${Number(value).toFixed(1)} GiB`
}

/** "used / total" memory, or a single dash when the provider reports none at all. */
export function memoryText(vram: number | null | undefined, size: number | null | undefined): string {
  return vram === null || vram === undefined ? '—' : `${fmtGib(vram)} / ${fmtGib(size)}`
}

/** Upstream VRAM gauge. The unit belongs to the value itself: `vram_total_gib` is optional
 *  (not discoverable over the API), so without a configured total the tile used to show a
 *  bare number with no GiB anywhere on it. */
export function vramText(used: number | null | undefined, total: number | null | undefined): string {
  if (used === null || used === undefined) return '—'
  const value = Number(used)
  if (!Number.isFinite(value)) return '—'
  return total ? `${value.toFixed(1)} / ${Number(total).toFixed(1)} GiB` : `${value.toFixed(1)} GiB`
}

export function fmtBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(2)} MB`
}

// datetime-local <-> naive UTC ISO accepted by the API.
export function toInputValue(value: string | null | undefined): string {
  const date = parseUtc(value)
  if (!date) return ''
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

export function fromInputValue(value: string | null | undefined): string | null {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return date.toISOString().slice(0, 19)
}

export function hoursAgoInput(hours: number): string {
  const date = new Date(Date.now() - hours * 3600 * 1000)
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

export function nowInputValue(): string {
  const date = new Date()
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

// Default range helpers: a 7-day (calendar) window whose edges are aligned to
// 00:00 / 23:59 instead of "now minus N hours", so the dashboard opens on clean day boundaries.
export function daysAgoStartInput(days: number): string {
  const date = new Date()
  date.setHours(0, 0, 0, 0)
  date.setDate(date.getDate() - (days - 1))
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

export function todayEndInput(): string {
  const date = new Date()
  date.setHours(23, 59, 0, 0)
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

export function deviceBadge(device: string | null | undefined, _ratio?: number | null): Badge {
  if (device === 'cloud') return { cls: 'cloud', text: '● Cloud' }
  if (device === 'gpu') return { cls: 'gpu', text: '● GPU' }
  if (device === 'partial') return { cls: 'part', text: i18n.global.t('common.devicePartial') }
  if (device === 'cpu') return { cls: 'cpu', text: '● CPU' }
  return { cls: 'idle', text: '—' }
}

export function statusBadge(status: number | null | undefined): Badge {
  if (status === null || status === undefined) return { cls: 'idle', text: '—' }
  if (status >= 500) return { cls: 'err', text: String(status) } // 5xx -> red
  if (status >= 300) return { cls: 'warn', text: String(status) } // 3xx / 4xx -> yellow
  return { cls: 'ok', text: String(status) } // 2xx -> green
}
