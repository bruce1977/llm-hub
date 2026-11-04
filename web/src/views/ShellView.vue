<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { gateway } from '@/stores/gateway'
import { session, signOut } from '@/stores/session'

const router = useRouter()
const collapsed = ref(false)

async function logout() {
  await signOut()
  router.replace({ name: 'login' })
}
</script>

<template>
  <div class="app" :class="{ collapsed }">
    <button class="restore-btn" title="显示菜单" @click="collapsed = false">☰</button>

    <aside class="sidebar">
      <div class="brand">
        <div class="logo">L</div>
        <div>
          <div class="name">llm-hub</div>
          <div class="ver">{{ gateway.version ? `v${gateway.version} · ` : '' }}管理台</div>
        </div>
        <button class="collapse-btn" title="隐藏菜单" @click="collapsed = true">«</button>
      </div>

      <RouterLink class="nav-item" :to="{ name: 'dashboard' }"><span class="ico">▦</span> Dashboard</RouterLink>
      <RouterLink class="nav-item" :to="{ name: 'upstreams' }"><span class="ico">⛁</span> Upstreams</RouterLink>

      <div class="nav-label">Models</div>
      <RouterLink class="nav-item nav-sub" :to="{ name: 'models-llm' }"><span class="ico">✦</span> LLM</RouterLink>
      <RouterLink class="nav-item nav-sub" :to="{ name: 'models-rerank' }"
        ><span class="ico">⚡</span> Rerank</RouterLink
      >
      <RouterLink class="nav-item nav-sub" :to="{ name: 'models-systemone' }"
        ><span class="ico">◈</span> System one</RouterLink
      >

      <div class="nav-label">Tools</div>
      <RouterLink class="nav-item nav-sub" :to="{ name: 'tools-websearch' }"
        ><span class="ico">🔍</span> Web Search</RouterLink
      >

      <RouterLink class="nav-item" :to="{ name: 'requests' }"><span class="ico">☰</span> Requests</RouterLink>
      <RouterLink class="nav-item" :to="{ name: 'settings' }"><span class="ico">⚙</span> Settings</RouterLink>

      <div class="spacer"></div>
      <div class="user">
        <div class="av">A</div>
        <div>
          <div class="un">{{ session.user }}</div>
          <div class="rl">Administrator</div>
        </div>
        <span class="link" style="margin-left: auto; font-size: 11.5px" @click="logout">退出</span>
      </div>
    </aside>

    <main class="content">
      <RouterView />
    </main>
  </div>
</template>
