import { reactive } from 'vue'

const STORAGE_KEY = 'llmhub-console-theme'

export type ThemeMode = 'light' | 'dark'

function read(): ThemeMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'light' || saved === 'dark') return saved
  } catch {
    // Private browsing / disabled storage: fall through to the default.
  }
  return 'dark'
}

export const theme = reactive<{ mode: ThemeMode }>({ mode: read() })

export function applyTheme(): void {
  const root = document.documentElement
  root.dataset.theme = theme.mode
  // Element Plus ships a separate dark palette keyed off this class.
  root.classList.toggle('dark', theme.mode === 'dark')
}

export function toggleTheme(): void {
  theme.mode = theme.mode === 'dark' ? 'light' : 'dark'
  try {
    localStorage.setItem(STORAGE_KEY, theme.mode)
  } catch {
    // Ignore: the theme simply will not persist.
  }
  applyTheme()
}

applyTheme()
