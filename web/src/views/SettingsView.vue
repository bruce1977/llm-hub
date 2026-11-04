<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import TopBar from '@/components/TopBar.vue'

const { t } = useI18n()

const upcoming = computed(() => [
  t('settings.upcoming.apiKeys'),
  t('settings.upcoming.rerank'),
  t('settings.upcoming.security'),
  t('settings.upcoming.playground'),
  t('settings.upcoming.upstreams'),
])
</script>

<template>
  <TopBar :title="t('nav.settings')" :sub="t('settings.sub')" />

  <div class="soon-box">
    <div class="big">🚧</div>
    <div class="t">{{ t('settings.comingSoon') }}</div>
    <div class="d">{{ t('settings.desc') }}</div>
    <div class="soon-list">
      <span v-for="item in upcoming" :key="item" class="chip s2">{{ item }}</span>
    </div>
  </div>

  <div class="foot">{{ t('common.footReadOnly', { page: t('nav.settings') }) }}</div>
</template>
