<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import echarts from '@/plugins/echarts'
import type { ChartInstance, ChartOption } from '@/plugins/echarts'

const props = withDefaults(
  defineProps<{
    option: ChartOption
    height?: string
    empty?: boolean
    emptyText?: string
    /** Dim the previous frame and float a ring over it while the new option loads. */
    loading?: boolean
  }>(),
  { height: '220px', empty: false, emptyText: '', loading: false },
)

const { t } = useI18n()

const el = ref<HTMLDivElement | null>(null)
let chart: ChartInstance | null = null
let observer: ResizeObserver | null = null

onMounted(() => {
  const node = el.value
  if (!node) return
  chart = echarts.init(node, null, { renderer: 'canvas' })
  chart.setOption(props.option)
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(node)
})

// notMerge: a pie with fewer slices than before must not keep the stale ones.
watch(
  () => props.option,
  (option) => {
    chart?.setOption(option, true)
  },
  { deep: true },
)

onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="echart-box" :class="{ busy: loading }" :style="{ height }">
    <div v-show="!empty" ref="el" class="chart-canvas" style="width: 100%; height: 100%"></div>
    <div v-if="empty" class="empty" style="height: 100%; display: flex; align-items: center; justify-content: center">
      {{ emptyText || t('common.empty') }}
    </div>
    <div v-if="loading" class="chart-veil"><span class="spinner"></span></div>
  </div>
</template>
