<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'

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
  daysAgoStartInput,
  deviceBadge,
  fmtBucket,
  fmtClock,
  fmtInt,
  fmtKiloTokens,
  fmtMs,
  fmtPct,
  fromInputValue,
  todayEndInput,
} from '@/utils/format'

const POLL_MS = 60000

const { t } = useI18n()
const chartTheme = useChartTheme()
const overview = ref<Overview | null>(null)
const points = ref<TimeseriesPoint[]>([])
const pies = ref<{
  upstream: DistributionItem[]
  model: DistributionItem[]
  status: DistributionItem[]
}>({ upstream: [], model: [], status: [] })
const bucket = ref<Bucket>('hour')
const range = reactive({ start: daysAgoStartInput(7), end: todayEndInput() })
const lastUpdated = ref('')
const loadError = ref('')
// `refreshing` drives the header ring and the chart veil; `loaded` separates "never had
// data" (skeletons) from "showing the previous frame while the next one arrives".
const refreshing = ref(false)
const loaded = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

const kpi = computed<Partial<Kpi>>(() => overview.value?.kpi ?? {})
const upstreams = computed<ProbeResult[]>(() => overview.value?.upstreams ?? [])
const running = computed<RunningModel[]>(() => overview.value?.running_models ?? [])

function runningTitle(m: RunningModel): string | undefined {
  if (m.derived) return t('common.derivedFrom', { target: m.target ?? '' })
  return m.alias ? t('common.realModel', { model: m.model }) : undefined
}
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
  if (refreshing.value) return
  refreshing.value = true
  try {
    await Promise.all([loadOverview(), loadStats()])
    loaded.value = true
    loadError.value = ''
  } catch (error) {
    // A 401 is handled by the axios interceptor (back to the gate); everything else is
    // shown inline so one dead probe cannot blank the page.
    if (statusOf(error) !== 401) loadError.value = errorDetail(error)
  } finally {
    refreshing.value = false
  }
}

const trendOption = computed<ChartOption>(() => {
  const ct = chartTheme.value
  const rows = points.value
  return {
    tooltip: {
      trigger: 'axis',
      backgroundColor: ct.surface,
      borderColor: ct.border,
      textStyle: { color: ct.text, fontSize: 11 },
      axisPointer: { type: 'shadow' },
    },
    legend: {
      data: [t('dashboard.chart.requests'), 'P95', t('dashboard.chart.errors')],
      right: 0,
      top: 0,
      textStyle: { color: ct.dim, fontSize: 11 },
      itemWidth: 12,
      itemHeight: 8,
    },
    grid: { left: 44, right: 48, top: 32, bottom: 26 },
    xAxis: {
      type: 'category',
      data: rows.map((p) => fmtBucket(p.ts, bucket.value)),
      axisLine: { lineStyle: { color: ct.grid } },
      axisTick: { show: false },
      axisLabel: { color: ct.mute, fontSize: 10, interval: 'auto', hideOverlap: true },
    },
    yAxis: [
      {
        type: 'value',
        name: t('dashboard.chart.requests'),
        nameTextStyle: { color: ct.mute, fontSize: 10 },
        splitLine: { lineStyle: { color: ct.grid } },
        axisLabel: { color: ct.mute, fontSize: 10 },
      },
      {
        type: 'value',
        name: 'P95 ms',
        nameTextStyle: { color: ct.mute, fontSize: 10 },
        splitLine: { show: false },
        axisLabel: { color: ct.mute, fontSize: 10 },
      },
    ],
    series: [
      {
        name: t('dashboard.chart.requests'),
        type: 'bar',
        data: rows.map((p) => p.count),
        barMaxWidth: 18,
        itemStyle: { color: ct.accent, borderRadius: [3, 3, 0, 0] },
      },
      {
        name: t('dashboard.chart.errors'),
        type: 'bar',
        stack: 'err',
        data: rows.map((p) => p.errors),
        barMaxWidth: 18,
        barGap: '-100%',
        itemStyle: { color: ct.red, borderRadius: [3, 3, 0, 0] },
      },
      {
        name: 'P95',
        type: 'line',
        yAxisIndex: 1,
        data: rows.map((p) => p.p95),
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        lineStyle: { color: ct.amber, width: 2 },
        itemStyle: { color: ct.amber },
      },
    ],
  }
})

function pieOption(items: DistributionItem[], colorFor?: (key: string) => string): ChartOption {
  const ct = chartTheme.value
  const counts = new Map(items.map((item) => [item.key, item.count]))
  // Long model ids would paint over the disc, so the pie carries no labels at all: name +
  // count live in the one-per-line legend list under the chart.
  const legendLabel = (name: string): string => {
    const short = name.length > 16 ? `${name.slice(0, 15)}…` : name
    return `${short}  ${fmtInt(counts.get(name) ?? 0)}`
  }
  return {
    color: ct.palette,
    tooltip: {
      trigger: 'item',
      backgroundColor: ct.surface,
      borderColor: ct.border,
      textStyle: { color: ct.text, fontSize: 11 },
      formatter: '{b}: {c} ({d}%)',
    },
    legend: {
      // One entry per line under the disc: a horizontal row fits only two names per card and
      // silently pages them away.
      type: 'scroll',
      orient: 'vertical',
      left: 'center',
      top: '57%',
      itemWidth: 8,
      itemHeight: 8,
      itemGap: 3,
      textStyle: { color: ct.dim, fontSize: 9.5, lineHeight: 10.5 },
      formatter: legendLabel,
    },
    series: [
      {
        type: 'pie',
        // A solid disc (radius is measured against min(width, height) / 2), pushed up so the
        // legend rows below it never touch the slices.
        radius: '46%',
        center: ['50%', '31%'],
        avoidLabelOverlap: true,
        label: { show: false },
        emphasis: { label: { show: false }, scaleSize: 4 },
        labelLine: { show: false },
        itemStyle: { borderColor: ct.surface, borderWidth: 2 },
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
    const ct = chartTheme.value
    return key === 'ok' ? ct.green : ct.red
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
    :title="t('nav.dashboard')"
    :sub="t('dashboard.sub')"
    refreshable
    :loading="refreshing"
    :last-updated="lastUpdated"
    @refresh="refresh()"
  >
    <template #actions>
      <div class="range-pick">
        <el-date-picker
          v-model="range.start"
          type="datetime"
          format="YYYY-MM-DD HH:mm:ss"
          value-format="YYYY-MM-DDTHH:mm:ss"
          :clearable="false"
          size="small"
        />
        <span class="arrow">→</span>
        <el-date-picker
          v-model="range.end"
          type="datetime"
          format="YYYY-MM-DD HH:mm:ss"
          value-format="YYYY-MM-DDTHH:mm:ss"
          :clearable="false"
          size="small"
        />
        <button class="apply" @click="refresh()">{{ t('common.apply') }}</button>
      </div>
    </template>
  </TopBar>

  <div v-if="loadError" class="warn-strip">⚠ {{ loadError }}</div>
  <div v-if="logState && logState.enabled === false" class="warn-strip">
    ⚠
    <i18n-t keypath="dashboard.logDisabled" scope="global">
      <template #code><code>request_log.enabled = false</code></template>
    </i18n-t>
  </div>

  <div class="kpis" :class="{ refreshing: refreshing && loaded }">
    <div class="card kpi">
      <div class="label"><span class="dot" :class="{ bad: kpi.upstreams_online === 0 }"></span> {{ t('dashboard.kpi.upstreamsOnline') }}</div>
      <div v-if="overview" class="val">
        {{ fmtInt(kpi.upstreams_online) }}<small> / {{ fmtInt(kpi.upstreams_total) }}</small>
      </div>
      <div v-else class="val"><span class="skeleton"></span></div>
      <div class="meta">{{ onlineNames || t('dashboard.kpi.noOnlineUpstreams') }}</div>
    </div>
    <div class="card kpi">
      <div class="label">{{ t('dashboard.kpi.runningModels') }}</div>
      <div v-if="overview" class="val">{{ fmtInt(kpi.running_models) }}</div>
      <div v-else class="val"><span class="skeleton"></span></div>
      <div class="meta">{{ t('dashboard.kpi.acrossUpstreams', { n: runningUpstreams }) }}</div>
    </div>
    <div class="card kpi">
      <div class="label">{{ t('dashboard.kpi.vram') }}</div>
      <div v-if="overview" class="val">
        {{ kpi.vram_used_gib === undefined ? '—' : Number(kpi.vram_used_gib).toFixed(1)
        }}<small v-if="kpi.vram_total_gib"> / {{ Number(kpi.vram_total_gib).toFixed(1) }} GiB</small>
      </div>
      <div v-else class="val"><span class="skeleton"></span></div>
      <div class="bar"><i :style="{ width: `${vramPct}%` }"></i></div>
    </div>
    <div class="card kpi">
      <div class="label">{{ t('dashboard.kpi.requestsTokens') }}</div>
      <div v-if="overview" class="val pair">
        <span>{{ fmtInt(kpi.requests_today) }}<small>{{ t('dashboard.kpi.requestsUnit') }}</small></span>
        <span>{{ fmtKiloTokens(kpi.tokens_today) }}<small>{{ t('dashboard.kpi.tokensUnit') }}</small></span>
      </div>
      <div v-else class="val"><span class="skeleton"></span></div>
      <div class="meta">{{ t('dashboard.kpi.p95Errors', { p95: fmtMs(kpi.p95_ms), rate: fmtPct(kpi.error_rate) }) }}</div>
    </div>
  </div>

  <div>
    <div class="section-title">{{ t('dashboard.upstreams.title') }} <span class="tag">live</span></div>
    <div class="card tight">
      <table>
        <thead>
          <tr>
            <th>{{ t('dashboard.upstreams.name') }}</th>
            <th>{{ t('dashboard.upstreams.type') }}</th>
            <th>{{ t('dashboard.upstreams.status') }}</th>
            <th>{{ t('dashboard.upstreams.loaded') }}</th>
            <th>{{ t('dashboard.upstreams.vram') }}</th>
            <th>{{ t('dashboard.upstreams.probe') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in upstreams" :key="u.name">
            <td class="mono">{{ u.name }}</td>
            <td><span class="chip">{{ u.type }}</span></td>
            <td>
              <span class="badge" :class="u.reachable ? 'ok' : 'err'">
                ● {{ u.reachable ? t('common.online') : t('common.offline') }}
              </span>
            </td>
            <td>{{ fmtInt(u.loaded) }}</td>
            <td class="mono">
              {{ u.vram_used_gib === null || u.vram_used_gib === undefined ? '—' : Number(u.vram_used_gib).toFixed(1) }}
              <template v-if="u.vram_total_gib"> / {{ Number(u.vram_total_gib).toFixed(1) }} GiB</template>
            </td>
            <td class="mono">{{ u.reachable ? `${u.probe_ms} ms` : t('common.timeout') }}</td>
          </tr>
          <tr v-if="!upstreams.length">
            <td colspan="6" class="empty">{{ t('common.loading') }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div>
    <div class="section-title">{{ t('dashboard.running.title') }} <span class="tag">device</span></div>
    <div class="card tight">
      <table>
        <thead>
          <tr>
            <th>{{ t('dashboard.running.model') }}</th>
            <th>{{ t('dashboard.running.upstream') }}</th>
            <th>{{ t('dashboard.running.device') }}</th>
            <th>{{ t('dashboard.running.context') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in running" :key="`${m.upstream}/${m.model}`">
            <td class="mono" :title="runningTitle(m)">
              {{ m.display }}
              <span v-if="m.alias" class="chip s2" :title="t('common.aliasTip')">alias</span>
              <span v-if="m.derived" class="chip s2" :title="t('common.derivedTip')">derived</span>
            </td>
            <td>{{ m.upstream }}</td>
            <td><span class="badge" :class="deviceBadge(m.device, m.device_ratio).cls">{{ deviceBadge(m.device, m.device_ratio).text }}</span></td>
            <td class="mono">{{ fmtInt(m.num_ctx) }}</td>
          </tr>
          <tr v-if="!running.length">
            <td colspan="4" class="empty">{{ t('dashboard.running.empty') }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div>
    <div class="section-title">
      <span>{{ bucket === 'day' ? t('dashboard.trend.day') : t('dashboard.trend.hour') }}</span>
      <span class="tag">req · P95</span>
    </div>
    <div class="echart-card">
      <EChart
        :option="trendOption"
        :empty="!points.length"
        :empty-text="loaded ? t('common.empty') : t('common.loading')"
        :loading="refreshing"
        height="240px"
      />
    </div>

    <div class="pie-grid">
      <div class="echart-card">
        <div class="card-h">{{ t('dashboard.chart.upstreamDist') }} <span class="tag">by upstream</span></div>
        <EChart
          :option="upstreamPie"
          :empty="!pies.upstream.length"
          :empty-text="loaded ? t('common.empty') : t('common.loading')"
          :loading="refreshing"
          height="300px"
        />
      </div>
      <div class="echart-card">
        <div class="card-h">{{ t('dashboard.chart.modelDist') }} <span class="tag">by model</span></div>
        <EChart
          :option="modelPie"
          :empty="!pies.model.length"
          :empty-text="loaded ? t('common.empty') : t('common.loading')"
          :loading="refreshing"
          height="300px"
        />
      </div>
      <div class="echart-card">
        <div class="card-h">{{ t('dashboard.chart.statusDist') }} <span class="tag">by status</span></div>
        <EChart
          :option="statusPie"
          :empty="!pies.status.length"
          :empty-text="loaded ? t('common.empty') : t('common.loading')"
          :loading="refreshing"
          height="300px"
        />
      </div>
    </div>
  </div>

  <div class="foot">{{ t('common.footReadOnly', { page: t('nav.dashboard') }) }}</div>
</template>

<style scoped>
/* The 今日请求 tile carries two numbers, so it trades the single 26px value for a pair. */
.kpi .val.pair {
  display: flex;
  align-items: baseline;
  gap: 14px;
  font-size: 21px;
}

.kpi .val.pair small {
  font-size: 11px;
  margin-left: 3px;
}

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
