import { createI18n } from 'vue-i18n'

import en from './messages/en'
import zhCN from './messages/zh-CN'
import zhTW from './messages/zh-TW'

/** Locales the console ships. `label` is the endonym, so it is never translated. */
export const SUPPORTED_LOCALES = [
  { code: 'zh-CN', label: '简体中文', short: '简' },
  { code: 'zh-TW', label: '繁體中文', short: '繁' },
  { code: 'en', label: 'English', short: 'EN' },
] as const

export type LocaleCode = (typeof SUPPORTED_LOCALES)[number]['code']

const STORAGE_KEY = 'llmhub-console-locale'
export const DEFAULT_LOCALE: LocaleCode = 'zh-CN'

const LOCALE_CODES: readonly string[] = SUPPORTED_LOCALES.map((l) => l.code)

function isLocale(value: unknown): value is LocaleCode {
  return LOCALE_CODES.includes(String(value))
}

/**
 * Browser tag -> console locale. A `zh` tag is ambiguous, so only the Traditional tags
 * (`zh-TW`, `zh-HK`, `zh-MO`, or an explicit `zh-Hant` script) land on 繁體中文;
 * `zh-CN`, bare `zh` and anything unknown keep the default.
 */
export function localeFromTag(tag: string): LocaleCode {
  const value = tag.toLowerCase().replace(/_/g, '-')
  if (value.startsWith('en')) return 'en'
  if (value.startsWith('zh-tw') || value.startsWith('zh-hk') || value.startsWith('zh-mo')) return 'zh-TW'
  if (value.startsWith('zh-hant')) return 'zh-TW'
  return DEFAULT_LOCALE
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
  return localeFromTag(nav)
}

export const i18n = createI18n({
  legacy: false,
  globalInjection: true,
  locale: detectLocale(),
  fallbackLocale: DEFAULT_LOCALE,
  messages: { 'zh-CN': zhCN, 'zh-TW': zhTW, en },
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
