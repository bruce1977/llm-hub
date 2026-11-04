<script setup lang="ts">
import { ref } from 'vue'

import { theme, toggleTheme } from '@/stores/theme'

withDefaults(
  defineProps<{
    title: string
    sub?: string
    lastUpdated?: string
    refreshable?: boolean
  }>(),
  { sub: '', lastUpdated: '', refreshable: false },
)

const emit = defineEmits<{ refresh: [] }>()
const spinning = ref(false)

function refresh() {
  spinning.value = true
  setTimeout(() => (spinning.value = false), 600)
  emit('refresh')
}
</script>

<template>
  <div class="topbar">
    <div>
      <h1>{{ title }}</h1>
      <div class="sub">
        {{ sub }}
        <button v-if="refreshable" class="icon-btn" :class="{ spin: spinning }" title="立即刷新" @click="refresh">
          ↻
        </button>
      </div>
    </div>
    <div class="actions">
      <slot name="actions" />
      <span v-if="lastUpdated" class="refresh">最后更新 {{ lastUpdated }}</span>
      <button class="toggle" @click="toggleTheme">
        <span>{{ theme.mode === 'dark' ? '☀' : '☾' }}</span>
        <span>{{ theme.mode === 'dark' ? '浅色' : '深色' }}</span>
      </button>
    </div>
  </div>
</template>
