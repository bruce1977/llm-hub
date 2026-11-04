<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { errorDetail, statusOf } from '@/api/client'
import { gateway } from '@/stores/gateway'
import { session, signIn } from '@/stores/session'
import { theme, toggleTheme } from '@/stores/theme'

const route = useRoute()
const router = useRouter()

const form = reactive({ username: 'admin', password: '' })
const busy = ref(false)
const error = ref('')
const pwRef = ref<{ focus?: () => void } | null>(null)

async function submit() {
  if (busy.value) return
  if (!form.password) {
    error.value = '请输入管理台密码。'
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
      error.value = '用户名或密码错误，请重试。'
    } else {
      error.value = errorDetail(err) || '登录失败。'
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
      <span>{{ theme.mode === 'dark' ? '浅色' : '深色' }}</span>
    </button>

    <div v-if="!session.locked" class="login-card">
      <div class="login-brand">
        <div class="logo">L</div>
        <div>
          <div class="name">llm-hub</div>
          <div class="ver">{{ gateway.version ? `v${gateway.version} · ` : '' }}管理台</div>
        </div>
      </div>

      <div>
        <div class="login-title">管理员登录</div>
        <div class="sub" style="font-size: 12px; color: var(--text-dim)">
          只读管理台 <span class="tag" style="font-size: 10.5px; border: 1px solid var(--border); padding: 1px 7px; border-radius: 6px; color: var(--text-mute)">只读视图</span>
        </div>
      </div>

      <div v-if="error" class="login-error"><span>⚠</span> {{ error }}</div>

      <form class="login-fields" @submit.prevent="submit">
        <div class="field">
          <label for="username">用户名</label>
          <input id="username" v-model="form.username" class="inp" autocomplete="username" readonly />
        </div>
        <div class="field">
          <label for="password">管理员密码（ADMIN_PASSWORD）</label>
          <el-input
            id="password"
            ref="pwRef"
            v-model="form.password"
            type="password"
            show-password
            autocomplete="current-password"
            placeholder="请输入管理台密码"
            size="default"
          />
        </div>
        <button class="btn primary login-submit" type="submit" :disabled="busy">
          {{ busy ? '登录中…' : '登 录' }}
        </button>
      </form>

      <div class="login-note">
        管理台鉴权仅依赖环境变量 <code>ADMIN_PASSWORD</code>，不写入 <code>config.json</code>。<br />
        登录后凭 12h HttpOnly 会话 Cookie 访问 <code>/v1/management/*</code>；脚本调用也可改用网关 API Key
        （<code>Bearer</code> / <code>X-API-Key</code>）。管理密码本身不是请求凭据。
      </div>
    </div>

    <div v-else class="login-card">
      <div class="soon-box" style="border: none; padding: 20px 6px; gap: 12px">
        <div class="big">🔒</div>
        <div class="t">管理台未启用</div>
        <div class="d">
          当前未设置环境变量 <code>ADMIN_PASSWORD</code>，管理台处于锁定状态，任何访问均返回 503。
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
