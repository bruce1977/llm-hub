<script setup lang="ts">
import type { ConfigRow } from '@/types/ui'

withDefaults(
  defineProps<{
    title: string
    tag?: string
    rows?: ConfigRow[]
  }>(),
  { tag: '', rows: () => [] },
)
</script>

<template>
  <div class="card">
    <div class="card-h">
      {{ title }}
      <span v-if="tag" class="tag">{{ tag }}</span>
    </div>
    <div class="kv">
      <template v-for="row in rows" :key="row.label">
        <div class="k">{{ row.label }}</div>
        <div class="v" :class="{ mono: row.mono }">
          <span v-if="row.badge" class="badge" :class="row.badge.cls">{{ row.badge.text }}</span>
          <template v-else>{{ row.value ?? '—' }}</template>
          <span v-if="row.note" class="chip note">{{ row.note }}</span>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.kv {
  grid-template-columns: 148px 1fr;
}

.note {
  margin-left: 6px;
}
</style>
