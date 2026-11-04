<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { SUPPORTED_LOCALES, setLocale, type LocaleCode } from '@/i18n'
import { gateway } from '@/stores/gateway'
import { session, signOut } from '@/stores/session'
import { theme, toggleTheme } from '@/stores/theme'

const { t, locale } = useI18n()
const router = useRouter()
const collapsed = ref(false)
const menuOpen = ref(false)
const langOpen = ref(false)
const dock = ref<HTMLElement | null>(null)
let langCloseTimer: number | null = null

function clearLangTimer() {
  if (langCloseTimer !== null) {
    window.clearTimeout(langCloseTimer)
    langCloseTimer = null
  }
}

function closeMenu() {
  menuOpen.value = false
  langOpen.value = false
  clearLangTimer()
}

function openLang() {
  clearLangTimer()
  langOpen.value = true
}

// A short delay lets the pointer cross the 6px gap between the row and the flyout.
function scheduleLangClose() {
  clearLangTimer()
  langCloseTimer = window.setTimeout(() => {
    langOpen.value = false
    langCloseTimer = null
  }, 150)
}

function toggleMenu() {
  menuOpen.value = !menuOpen.value
  if (!menuOpen.value) langOpen.value = false
}

function onPointerDown(event: PointerEvent) {
  if (!menuOpen.value || !dock.value) return
  if (!dock.value.contains(event.target as Node)) closeMenu()
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') closeMenu()
}

async function logout() {
  closeMenu()
  await signOut()
  router.replace({ name: 'login' })
}

/** Switch the console language; the choice is persisted by the i18n module. */
function chooseLocale(code: LocaleCode) {
  setLocale(code)
  langOpen.value = false
}

function openSettings() {
  closeMenu()
  router.push({ name: 'settings' })
}

function openAbout() {
  closeMenu()
  router.push({ name: 'about' })
}

onMounted(() => {
  document.addEventListener('pointerdown', onPointerDown)
  document.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onPointerDown)
  document.removeEventListener('keydown', onKeydown)
  clearLangTimer()
})
</script>

<template>
  <div class="app" :class="{ collapsed }">
    <button class="restore-btn" :title="t('shell.showMenu')" @click="collapsed = false">☰</button>

    <aside class="sidebar">
      <div class="brand">
        <div class="logo">L</div>
        <div>
          <div class="name">llm-hub</div>
          <div class="ver">{{ gateway.version ? `v${gateway.version} · ` : '' }}{{ t('shell.console') }}</div>
        </div>
        <button class="collapse-btn" :title="t('shell.hideMenu')" @click="collapsed = true">«</button>
      </div>

      <RouterLink class="nav-item" :to="{ name: 'dashboard' }"><span class="ico">▦</span> {{ t('nav.dashboard') }}</RouterLink>
      <RouterLink class="nav-item" :to="{ name: 'upstreams' }"><span class="ico">⛁</span> {{ t('nav.upstreams') }}</RouterLink>
      <RouterLink class="nav-item" :to="{ name: 'requests' }"><span class="ico">☰</span> {{ t('nav.requests') }}</RouterLink>

      <div class="nav-label">{{ t('shell.models') }}</div>
      <RouterLink class="nav-item nav-sub" :to="{ name: 'models-llm' }"><span class="ico">✦</span> {{ t('nav.llm') }}</RouterLink>
      <RouterLink class="nav-item nav-sub" :to="{ name: 'models-embed' }"
        ><span class="ico">◍</span> {{ t('nav.embed') }}</RouterLink
      >
      <RouterLink class="nav-item nav-sub" :to="{ name: 'models-rerank' }"
        ><span class="ico">⇅</span> {{ t('nav.rerank') }}</RouterLink
      >
      <RouterLink class="nav-item nav-sub" :to="{ name: 'models-systemone' }"
        ><span class="ico">◈</span> {{ t('nav.systemone') }}</RouterLink
      >

      <div class="nav-label">{{ t('shell.tools') }}</div>
      <RouterLink class="nav-item nav-sub" :to="{ name: 'tools-websearch' }"
        ><span class="ico">◎</span> {{ t('nav.webSearch') }}</RouterLink
      >
    </aside>

    <main class="content">
      <RouterView />
    </main>

    <!-- Account chip: fixed to the viewport corner so a long page never hides it. -->
    <div ref="dock" class="user-dock">
      <transition name="um">
        <div v-if="menuOpen" class="user-menu" :aria-label="t('shell.accountMenu')" role="menu">
          <div class="um-head">
            <span class="av">A</span>
            <div>
              <div class="un">{{ session.user }}</div>
              <div class="rl">{{ t('shell.role') }}</div>
            </div>
          </div>

          <div class="um-sub-wrap" @pointerenter="openLang" @pointerleave="scheduleLangClose">
            <button type="button" class="um-item" :aria-expanded="langOpen" @click="openLang">
              <span class="um-ico">文</span>
              <span class="um-t">{{ t('shell.language') }}</span>
              <span class="um-val">{{ SUPPORTED_LOCALES.find((l) => l.code === locale)?.label }}</span>
              <span class="um-chev" :class="{ open: langOpen }">▸</span>
            </button>
            <transition name="fly">
              <div v-if="langOpen" class="um-flyout" role="menu" :aria-label="t('shell.languageAria')">
                <button
                  v-for="item in SUPPORTED_LOCALES"
                  :key="item.code"
                  type="button"
                  class="um-item"
                  :class="{ on: locale === item.code }"
                  role="menuitemradio"
                  :aria-checked="locale === item.code"
                  @click="chooseLocale(item.code)"
                >
                  <span class="um-ico">{{ locale === item.code ? '✓' : '·' }}</span>
                  <span class="um-t">{{ item.label }}</span>
                </button>
              </div>
            </transition>
          </div>

          <button type="button" class="um-item" @click="toggleTheme">
            <span class="um-ico">{{ theme.mode === 'dark' ? '☾' : '☀' }}</span>
            <span class="um-t">{{ theme.mode === 'dark' ? t('shell.darkMode') : t('shell.lightMode') }}</span>
            <span class="um-switch" :class="{ on: theme.mode === 'dark' }"><i></i></span>
          </button>

          <button type="button" class="um-item" @click="openSettings">
            <span class="um-ico">⚙</span>
            <span class="um-t">{{ t('shell.settings') }}</span>
            <span class="um-val">{{ t('nav.settings') }}</span>
          </button>

          <button type="button" class="um-item" @click="openAbout">
            <span class="um-ico">?</span>
            <span class="um-t">{{ t('shell.about') }}</span>
            <span class="um-val">{{ t('nav.about') }}</span>
          </button>

          <div class="um-sep"></div>

          <button type="button" class="um-item danger" @click="logout">
            <span class="um-ico">⇥</span>
            <span class="um-t">{{ t('shell.signOut') }}</span>
          </button>
        </div>
      </transition>

      <button
        type="button"
        class="user-card"
        :class="{ open: menuOpen }"
        :aria-expanded="menuOpen"
        :title="t('shell.accountMenu')"
        @click="toggleMenu"
      >
        <span class="av">A</span>
        <span class="uc-text">
          <span class="un">{{ session.user }}</span>
          <span class="rl">{{ t('shell.role') }}</span>
        </span>
        <span class="uc-chev" :class="{ open: menuOpen }">▴</span>
      </button>
    </div>
  </div>
</template>
