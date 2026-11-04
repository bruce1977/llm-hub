<script setup lang="ts">
import en from 'element-plus/es/locale/lang/en'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { computed } from 'vue'

import { i18n } from '@/i18n'

// Element Plus ships its own locale bundles (date pickers, table empty text, pagination).
// Re-derive it whenever the console language changes so both stay in sync.
const elementLocale = computed(() => (i18n.global.locale.value === 'en' ? en : zhCn))
</script>

<template>
  <el-config-provider :locale="elementLocale">
    <router-view />
  </el-config-provider>
</template>
