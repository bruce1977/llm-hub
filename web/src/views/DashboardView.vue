<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

import { errorDetail, statusOf } from '@/api/client'
import { api } from '@/api/management'
import EChart from '@/components/EChart.vue'
import TopBar from '@/components/TopBar.vue'
import { useChartTheme } from '@/composables/chartTheme'
import type { ChartOption } from '@/plugins/echarts'
import { gateway } from '@/stores/gateway'
import type {
  Bucket,
  DistributionItem,
  Kpi,
  Overview,
  ProbeResult,
  RangeQuery,
  RequestLogState,
  RunningModel,
  TimeseriesPoint,
} from '@/types/api'
import {
  deviceBadge,
  fmtBucket,
  fmtClock,
  fmtInt,
  fmtMs,
  fmtPct,
  fromInputValue,
  hoursAgoInput,
  nowInputValue,
} from '@/utils/format'

const POLL_MS = 5000

const chartTheme = useChartTheme()
const overview = ref<Overview | null>(null)
const points = ref<TimeseriesPoint[]>([])
const pies = ref<{
  upstream: DistributionItem[]
  model: DistributionItem[]
  status: DistributionItem[]
}>({ upstream: [], model: [], status: [] })
const bucket = ref<Bucket>('hour')
const range = reactive({ start: hoursAgoInput(24), end: nowInputValue() })
const lastUpdated = ref('')
const loadError = ref('')
let timer: ReturnType<typeof setInterval> | null = null
let busy = false

const kpi = computed<Partial<Kpi>>(() => overview.value?.kpi ?? {})
const upstreams = computed<ProbeResult[]>(() => overview.value?.upstreams ?? [])
const running = computed<RunningModel[]>(() => overview.value?.running_models ?? [])
const logState = computed<RequestLogState | null>(() => overview.value?.request_log ?? null)
const onlineNames = computed(() =>
  upstreams.value
    .filter((u) => u.reachable)
    .map((u) => u.name)
    .join(' · '),
)
const runningUpstreams = computed(() => new Set(running.value.map((m) => m.upstream)).size)
const vramPct = computed(() => {
  const used = kpi.value.vram_used_gib
  const total = kpi.value.vram_total_gib
  if (!used || !total) return 0
  return Math.min(100, Math.round((used / total) * 100))
})

async function loadOverview() {
  const { data } = await api.overview()
  overview.value = data
  gateway.version = data.gateway_version ?? gateway.version
  lastUpdated.value = fmtClock(data.generated_at)
}

async function loadStats() {
  const start = fromInputValue(range.start)
  const end = fromInputValue(range.end)
  const spanHours = start && end ? (new Date(end).getTime() - new Date(start).getTime()) / 3600000 : 24
  bucket.value = spanHours > 48 ? 'day' : 'hour'
  const params: RangeQuery = start && end ? { start, end } : {}
  const [ts, up, mo, st] = await Promise.all([
    api.timeseries({ ...params, bucket: bucket.value }),
    api.distribution({ ...params, group_by: 'upstream' }),
    api.distribution({ ...params, group_by: 'model' }),
    api.distribution({ ...params, group_by: 'status' }),
  ])
  points.value = ts.data.points ?? []
  pies.value = {
    upstream: up.data.items ?? [],
    model: mo.data.items ?? [],
    status: st.data.items ?? [],
  }
}

async function refresh() {
  if (busy) return
  busy = true
  try {
    await Promise.all([loadOverview(), loadStats()])
    loadError.value = ''
  } catch (error) {
    // A 401 is handled by the axios interceptor (back to the gate); everything else is
    // shown inline so one dead probe cannot blank the page.
    if (statusOf(error) !== 401) loadError.value = errorDetail(error)
  } finally {
    busy = false
  }
}

const trendOption = computed<ChartOption>(() => {
  const t = chartTheme.value
  const rows = points.value
  return {
    tooltip: {
      trigger: 'axis',
      backgroundColor: t.surface,
      borderColor: t.border,
      textStyle: { color: t.text, fontSize: 11 },
      axisPointer: { type: 'shadow' },
    },
    legend: { data: ['请求数', 'P95', '错误'], right: 0, top: 0, textStyle: { color: t.dim, fontSize: 11 }, itemWidth: 12, itemHeight: 8 },
    grid: { left: 44, right: 48, top: 32, bottom: 26 },
    xAxis: {
      type: 'category',
      data: rows.map((p) => fmtBucket(p.ts, bucket.value)),
      axisLine: { lineStyle: { color: t.grid } },
      axisTick: { show: false },
      axisLabel: { color: t.mute, fontSize: 10, interval: 'auto', hideOverlap: true },
    },
    yAxis: [
      {
        type: 'value',
        name: '请求数',
        nameTextStyle: { color: t.mute, fontSize: 10 },
        splitLine: { lineStyle: { color: t.grid } },
        axisLabel: { color: t.mute, fontSize: 10 },
      },
      {
        type: 'value',
        name: 'P95 ms',
        nameTextStyle: { color: t.mute, fontSize: 10 },
        splitLine: { show: false },
        axisLabel: { color: t.mute, fontSize: 10 },
      },
    ],
    series: [
      {
        name: '请求数',
        type: 'bar',
        data: rows.map((p) => p.count),
        barMaxWidth: 18,
        itemStyle: { color: t.accent, borderRadius: [3, 3, 0, 0] },
      },
      {
        name: '错误',
        type: 'bar',
        stack: 'err',
        data: rows.map((p) => p.errors),
        barMaxWidth: 18,
        barGap: '-100%',
        itemStyle: { color: t.red, borderRadius: [3, 3, 0, 0] },
      },
      {
        name: 'P95',
        type: 'line',
        yAxisIndex: 1,
        data: rows.map((p) => p.p95),
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        lineStyle: { color: t.amber, width: 2 },
        itemStyle: { color: t.amber },
      },
    ],
  }
})

function pieOption(items: DistributionItem[], colorFor?: (key: string) => string): ChartOption {
  const t = chartTheme.value
  return {
    color: t.palette,
    tooltip: {
      trigger: 'item',
      backgroundColor: t.surface,
      borderColor: t.border,
      textStyle: { color: t.text, fontSize: 11 },
      formatter: '{b}: {c} ({d}%)',
    },
    legend: {
      type: 'scroll',
      orient: 'vertical',
      right: 0,
      top: 'middle',
      itemWidth: 9,
      itemHeight: 9,
      textStyle: { color: t.dim, fontSize: 10.5 },
    },
    series: [
      {
        type: 'pie',
        radius: ['0%', '68%'],
        center: ['36%', '50%'],
        avoidLabelOverlap: true,
        label: { show: false },
        emphasis: { label: { show: false }, scaleSize: 4 },
        labelLine: { show: false },
        itemStyle: { borderColor: t.surface, borderWidth: 1 },
        data: items.map((item) => ({
          name: item.key,
          value: item.count,
          itemStyle: colorFor ? { color: colorFor(item.key) } : undefined,
        })),
      },
    ],
  }
}

const upstreamPie = computed(() => pieOption(pies.value.upstream))
const modelPie = computed(() => pieOption(pies.value.model))
const statusPie = computed(() =>
  pieOption(pies.value.status, (key) => {
    const t = chartTheme.value
    return key === 'ok' ? t.green : t.red
  }),
)

onMounted(() => {
  refresh()
  timer = setInterval(() => refresh(), POLL_MS)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  timer = null
})
</script>

<template>
  <TopBar
    title="Dashboard"
    sub="聚合视图 · 数据每 5s 自动刷新"
    refreshable
    :last-updated="lastUpdated"
    @refresh="refresh()"
  >
    <template #actions>
      <div class="range-pick">
        <input v-model="range.start" type="datetime-local" class="dt" />
        <span class="arrow">→</span>
        <input v-model="range.end" type="datetime-local" class="dt" />
        <button class="apply" @click="refresh()">应用</button>
      </div>
    </template>
  </TopBar>

  <div v-if="loadError" class="warn-strip">⚠ {{ loadError }}</div>
  <div v-if="logState && logState.enabled === false" class="warn-strip">
    ⚠ 请求明细已停用（<code>request_log.enabled = false</code>），统计与 Requests 页不会有新数据。
  </div>

  <div class="kpis">
    <div class="card kpi">
      <div class="label"><span class="dot" :class="{ bad: kpi.upstreams_online === 0 }"></span> 上游在线</div>
      <div class="val">{{ fmtInt(kpi.upstreams_online) }}<small> / {{ fmtInt(kpi.upstreams_total) }}</small></div>
      <div class="meta">{{ onlineNames || '无在线上游' }}</div>
    </div>
    <div class="card kpi">
      <div class="label">运行中模型</div>
      <div class="val">{{ fmtInt(kpi.running_models) }}</div>
      <div class="meta">跨 {{ runningUpstreams }} 个上游实例</div>
    </div>
    <div class="card kpi">
      <div class="label">显存占用</div>
      <div class="val">
        {{ kpi.vram_used_gib === undefined ? '—' : Number(kpi.vram_used_gib).toFixed(1)
        }}<small v-if="kpi.vram_total_gib"> / {{ Number(kpi.vram_total_gib).toFixed(1) }} GiB</small>
      </div>
      <div class="bar"><i :style="{ width: `${vramPct}%` }"></i></div>
    </div>
    <div class="card kpi">
      <div class="label">今日请求</div>
      <div class="val">{{ fmtInt(kpi.requests_today) }}</div>
      <div class="meta">P95 {{ fmtMs(kpi.p95_ms) }} · 错误 {{ fmtPct(kpi.error_rate) }}</div>
    </div>
  </div>

  <div>
    <div class="section-title">上游实例 <span class="tag">live</span></div>
    <div class="card tight">
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>类型</th>
            <th>状态</th>
            <th>已加载模型</th>
            <th>显存占用</th>
            <th>探测延迟</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in upstreams" :key="u.name">
            <td class="mono">{{ u.name }}</td>
            <td><span class="chip">{{ u.type }}</span></td>
            <td>
              <span class="badge" :class="u.reachable ? 'ok' : 'err'">
                ● {{ u.reachable ? '在线' : '离线' }}
              </span>
            </td>
            <td>{{ fmtInt(u.loaded) }}</td>
            <td class="mono">
              {{ u.vram_used_gib === null || u.vram_used_gib === undefined ? '—' : Number(u.vram_used_gib).toFixed(1) }}
              <template v-if="u.vram_total_gib"> / {{ Number(u.vram_total_gib).toFixed(1) }} GiB</template>
            </td>
            <td class="mono">{{ u.reachable ? `${u.probe_ms} ms` : '超时' }}</td>
          </tr>
          <tr v-if="!upstreams.length">
            <td colspan="6" class="empty">加载中…</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div>
    <div class="section-title">运行中模型 <span class="tag">device</span></div>
    <div class="card tight">
      <table>
        <thead>
          <tr>
            <th>模型</th>
            <th>上游</th>
            <th>设备</th>
            <th>上下文</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in running" :key="`${m.upstream}/${m.model}`">
            <td class="mono">{{ m.model }}</td>
            <td>{{ m.upstream }}</td>
            <td><span class="badge" :class="deviceBadge(m.device, m.device_ratio).cls">{{ deviceBadge(m.device, m.device_ratio).text }}</span></td>
            <td class="mono">{{ fmtInt(m.num_ctx) }}</td>
          </tr>
          <tr v-if="!running.length">
            <td colspan="4" class="empty">当前没有驻留的模型</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div>
    <div class="section-title">
      <span>{{ bucket === 'day' ? '按天请求趋势' : '24h 请求趋势' }}</span>
      <span class="tag">req · P95</span>
    </div>
    <div class="echart-card">
      <EChart :option="trendOption" :empty="!points.length" height="240px" />
    </div>

    <div class="pie-grid">
      <div class="echart-card">
        <div class="card-h">各 Upstream 请求分布 <span class="tag">by upstream</span></div>
        <EChart :option="upstreamPie" :empty="!pies.upstream.length" />
      </div>
      <div class="echart-card">
        <div class="card-h">各 Model 请求分布 <span class="tag">by model</span></div>
        <EChart :option="modelPie" :empty="!pies.model.length" />
      </div>
      <div class="echart-card">
        <div class="card-h">成功 / 失败分布 <span class="tag">by status</span></div>
        <EChart :option="statusPie" :empty="!pies.status.length" />
      </div>
    </div>
  </div>

  <div class="foot">llm-hub 管理台 · Dashboard（只读视图）</div>
</template>

<style scoped>
.range-pick {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.dt {
  background: var(--surface-2);
  color: var(--text);
  border: 1px solid var(--border-strong);
  border-radius: 7px;
  padding: 5px 8px;
  font-size: 11.5px;
  font-family: inherit;
}

.range-pick .arrow {
  color: var(--text-mute);
}

.apply {
  border: 1px solid var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
  border-radius: 7px;
  padding: 5px 13px;
  font-size: 11.5px;
  cursor: pointer;
  font-weight: 600;
}

.apply:hover {
  background: var(--accent);
  color: #fff;
}

.warn-strip code {
  color: inherit;
}
</style>
