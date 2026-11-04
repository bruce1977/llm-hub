<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import { errorDetail, statusOf } from '@/api/client'
import { gateway } from '@/stores/gateway'
import { session, signIn } from '@/stores/session'
import { theme, toggleTheme } from '@/stores/theme'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const form = reactive({ username: 'admin', password: '' })
const busy = ref(false)
const error = ref('')
const pwRef = ref<{ focus?: () => void } | null>(null)

async function submit() {
  if (busy.value) return
  if (!form.password) {
    error.value = t('login.errorEmpty')
    pwRef.value?.focus?.()
    return
  }
  busy.value = true
  error.value = ''
  try {
    await signIn(form.username, form.password)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : null
    router.replace(redirect || { name: 'dashboard' })
  } catch (err) {
    const status = statusOf(err)
    if (status === 503) {
      error.value = ''
      session.locked = true
    } else if (status === 401) {
      // The backend answers in English; the console is Chinese-only.
      error.value = t('login.errorUnauthorized')
    } else {
      error.value = errorDetail(err) || t('login.errorFailed')
    }
    form.password = ''
    pwRef.value?.focus?.()
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <button class="toggle theme-fab" @click="toggleTheme">
      <span>{{ theme.mode === 'dark' ? '☀' : '☾' }}</span>
      <span>{{ theme.mode === 'dark' ? t('login.light') : t('login.dark') }}</span>
    </button>

    <div v-if="!session.locked" class="login-card">
      <div class="login-brand">
        <div class="logo">L</div>
        <div>
          <div class="name">llm-hub</div>
          <div class="ver">{{ gateway.version ? `v${gateway.version} · ` : '' }}{{ t('shell.console') }}</div>
        </div>
      </div>

      <div>
        <div class="login-title">{{ t('login.title') }}</div>
        <div class="sub" style="font-size: 12px; color: var(--text-dim)">
          {{ t('login.readonly') }} <span class="tag" style="font-size: 10.5px; border: 1px solid var(--border); padding: 1px 7px; border-radius: 6px; color: var(--text-mute)">{{ t('login.readonlyTag') }}</span>
        </div>
      </div>

      <div v-if="error" class="login-error"><span>⚠</span> {{ error }}</div>

      <form class="login-fields" @submit.prevent="submit">
        <div class="field">
          <label for="username">{{ t('login.username') }}</label>
          <input id="username" v-model="form.username" class="inp" autocomplete="username" readonly />
        </div>
        <div class="field">
          <label for="password">{{ t('login.passwordLabel') }}</label>
          <el-input
            id="password"
            ref="pwRef"
            v-model="form.password"
            type="password"
            show-password
            autocomplete="current-password"
            :placeholder="t('login.passwordPlaceholder')"
            size="default"
          />
        </div>
        <button class="btn primary login-submit" type="submit" :disabled="busy">
          {{ busy ? t('login.submitting') : t('login.submit') }}
        </button>
      </form>

      <div class="login-note">
        <i18n-t keypath="login.noteLine1" scope="global">
          <template #env><code>ADMIN_PASSWORD</code></template>
          <template #config><code>config.yaml</code></template>
        </i18n-t>
        <br />
        <i18n-t keypath="login.noteLine2" scope="global">
          <template #api><code>/v1/management/*</code></template>
          <template #bearer><code>Bearer</code></template>
          <template #apiKey><code>X-API-Key</code></template>
        </i18n-t>
      </div>
    </div>

    <div v-else class="login-card">
      <div class="soon-box" style="border: none; padding: 20px 6px; gap: 12px">
        <div class="big">🔒</div>
        <div class="t">{{ t('login.lockedTitle') }}</div>
        <div class="d">
          <i18n-t keypath="login.lockedBody" scope="global">
            <template #env><code>ADMIN_PASSWORD</code></template>
          </i18n-t>
        </div>
        <div class="code" style="width: 100%; text-align: center; max-height: none">HTTP 503 · Service Unavailable</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-card .inp {
  width: 100%;
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
  font-family: inherit;
}

.login-card code {
  color: var(--text-dim);
}
</style>
