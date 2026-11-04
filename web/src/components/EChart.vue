<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import echarts from '@/plugins/echarts'
import type { ChartInstance, ChartOption } from '@/plugins/echarts'

const props = withDefaults(
  defineProps<{
    option: ChartOption
    height?: string
    empty?: boolean
  }>(),
  { height: '220px', empty: false },
)

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
  <div class="echart-box" :style="{ height }">
    <div v-show="!empty" ref="el" style="width: 100%; height: 100%"></div>
    <div v-if="empty" class="empty" style="height: 100%; display: flex; align-items: center; justify-content: center">
      暂无数据
    </div>
  </div>
</template>
