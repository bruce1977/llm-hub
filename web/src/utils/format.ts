// Formatting helpers shared by the views. The management API emits naive UTC ISO strings
// (second precision), so every display path appends "Z" before parsing.

export type BadgeClass = 'idle' | 'gpu' | 'part' | 'cpu' | 'ok' | 'err'

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
  const date = parseUtc(value)
  if (!date) return value || '—'
  if (bucket === 'day') return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
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

export function fmtGib(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${Number(value).toFixed(1)} GiB`
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
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
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
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function nowInputValue(): string {
  const date = new Date()
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function deviceBadge(device: string | null | undefined, ratio: number | null | undefined): Badge {
  if (!device) return { cls: 'idle', text: '—' }
  const pct = ratio === null || ratio === undefined ? null : Math.round(ratio * 100)
  if (device === 'gpu') return { cls: 'gpu', text: pct === null ? '● GPU' : `● GPU ${pct}%` }
  if (device === 'partial') return { cls: 'part', text: pct === null ? '● 部分' : `● 部分 ${pct}%` }
  return { cls: 'cpu', text: '● CPU' }
}

export function statusBadge(status: number | null | undefined): Badge {
  if (status === null || status === undefined) return { cls: 'idle', text: '—' }
  return status < 400 ? { cls: 'ok', text: String(status) } : { cls: 'err', text: String(status) }
}
