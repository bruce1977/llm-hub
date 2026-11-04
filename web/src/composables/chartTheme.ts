import { computed } from 'vue'
import type { ComputedRef } from 'vue'

import { theme } from '@/stores/theme'

export interface ChartTheme {
  text: string
  dim: string
  mute: string
  accent: string
  green: string
  amber: string
  red: string
  violet: string
  grid: string
  border: string
  surface: string
  palette: string[]
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

// Charts read their colors from the same CSS variables the page uses, so switching the
// theme re-computes every option and the palette can never drift from the UI.
export function useChartTheme(): ComputedRef<ChartTheme> {
  return computed(() => {
    void theme.mode
    return {
      text: cssVar('--text') || '#e6e8ec',
      dim: cssVar('--text-dim') || '#8b95a5',
      mute: cssVar('--text-mute') || '#5d6675',
      accent: cssVar('--accent') || '#4f8cff',
      green: cssVar('--green') || '#3fb950',
      amber: cssVar('--amber') || '#d29922',
      red: cssVar('--red') || '#f85149',
      violet: cssVar('--violet') || '#a371f7',
      grid: cssVar('--grid') || 'rgba(255,255,255,.06)',
      border: cssVar('--border-strong') || 'rgba(255,255,255,.14)',
      surface: cssVar('--surface-2') || '#1e2430',
      palette: [
        cssVar('--accent') || '#4f8cff',
        cssVar('--green') || '#3fb950',
        cssVar('--violet') || '#a371f7',
        cssVar('--amber') || '#d29922',
        cssVar('--red') || '#f85149',
        '#39c5cf',
        '#f778ba',
        '#8b949e',
      ],
    }
  })
}
