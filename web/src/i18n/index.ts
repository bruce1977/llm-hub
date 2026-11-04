import { createI18n } from 'vue-i18n'

import en from './messages/en'
import zhCN from './messages/zh-CN'

/** Locales the console ships. `label` is the endonym, so it is never translated. */
export const SUPPORTED_LOCALES = [
  { code: 'zh-CN', label: '中文', short: '中' },
  { code: 'en', label: 'English', short: 'EN' },
] as const

export type LocaleCode = (typeof SUPPORTED_LOCALES)[number]['code']

const STORAGE_KEY = 'llmhub-console-locale'
export const DEFAULT_LOCALE: LocaleCode = 'zh-CN'

function isLocale(value: unknown): value is LocaleCode {
  return value === 'zh-CN' || value === 'en'
}

/** Saved choice first, then the browser language, then the default (zh-CN). */
export function detectLocale(): LocaleCode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (isLocale(saved)) return saved
  } catch {
    // Private browsing / disabled storage: fall through to the browser hint.
  }
  const nav = typeof navigator === 'undefined' ? '' : navigator.language || ''
  return nav.toLowerCase().startsWith('en') ? 'en' : DEFAULT_LOCALE
}

export const i18n = createI18n({
  legacy: false,
  globalInjection: true,
  locale: detectLocale(),
  fallbackLocale: DEFAULT_LOCALE,
  messages: { 'zh-CN': zhCN, en },
})

export function currentLocale(): LocaleCode {
  return isLocale(i18n.global.locale.value) ? i18n.global.locale.value : DEFAULT_LOCALE
}

/** Switch the console language and remember it for the next visit. */
export function setLocale(code: LocaleCode): void {
  i18n.global.locale.value = code
  try {
    localStorage.setItem(STORAGE_KEY, code)
  } catch {
    // Ignore: the choice simply will not persist.
  }
  document.documentElement.lang = code
}

// Reflect the detected language on <html lang> for screen readers and browser hints.
document.documentElement.lang = currentLocale()

export default i18n
