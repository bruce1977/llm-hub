<script setup lang="ts">
import en from 'element-plus/es/locale/lang/en'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import zhTw from 'element-plus/es/locale/lang/zh-tw'
import { computed } from 'vue'

import { currentLocale } from '@/i18n'

// Element Plus ships its own locale bundles (date pickers, table empty text, pagination).
// Re-derive it whenever the console language changes so both stay in sync; the Chinese
// variants are separate bundles, so a 繁體 console must not inherit the 简体 wordings.
const elementLocales = { 'zh-CN': zhCn, 'zh-TW': zhTw, en } as const
const elementLocale = computed(() => elementLocales[currentLocale()])
</script>

<template>
  <el-config-provider :locale="elementLocale">
    <router-view />
  </el-config-provider>
</template>
