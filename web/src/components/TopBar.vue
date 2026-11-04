<script setup lang="ts">
import { useI18n } from 'vue-i18n'

withDefaults(
  defineProps<{
    title: string
    sub?: string
    lastUpdated?: string
    refreshable?: boolean
    /** The view's own in-flight flag: the header then shows the live refresh state. */
    loading?: boolean
  }>(),
  { sub: '', lastUpdated: '', refreshable: false, loading: false },
)

const emit = defineEmits<{ refresh: [] }>()

const { t } = useI18n()
</script>

<template>
  <div class="topbar">
    <div>
      <h1>{{ title }}</h1>
      <div class="sub">
        {{ sub }}
        <!-- Refresh meta sits next to the refresh button so refresh-related controls stay grouped. -->
        <span v-if="lastUpdated || loading" class="refresh" :class="{ busy: loading }">
          {{ loading ? t('topbar.refreshing') : t('topbar.lastUpdated', { time: lastUpdated }) }}
        </span>
        <button
          v-if="refreshable"
          class="icon-btn"
          :class="{ spin: loading }"
          :disabled="loading"
          :title="loading ? t('topbar.refreshingTitle') : t('topbar.refreshNow')"
          @click="emit('refresh')"
        >
          ↻
        </button>
        <span v-if="loading" class="spinner" aria-hidden="true"></span>
      </div>
    </div>
    <div class="actions">
      <slot name="actions" />
    </div>
    <div v-if="loading" class="progress"><i></i></div>
  </div>
</template>
