<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { errorDetail, statusOf } from '@/api/client'
import { api } from '@/api/management'
import RequestDrawer from '@/components/RequestDrawer.vue'
import TopBar from '@/components/TopBar.vue'
import type {
  Pagination,
  RequestLogState,
  RequestRow,
  RequestsQuery,
  RequestsResponse,
  RequestStats,
} from '@/types/api'
import { fmtClock, fmtInt, fmtMs, fmtPct, fromInputValue, hoursAgoInput, nowInputValue, statusBadge } from '@/utils/format'

const PAGE_SIZE = 15
const STATUS_OPTIONS = ['2xx', '3xx', '4xx', '5xx']

const filters = reactive({
  start: hoursAgoInput(24),
  end: nowInputValue(),
  upstream: '',
  model: '',
  status: '',
  path: '',
})

const result = ref<RequestsResponse | null>(null)
const upstreamOptions = ref<string[]>([])
const modelOptions = ref<string[]>([])
const lastUpdated = ref('')
const loadError = ref('')
const loading = ref(false)
const page = ref(1)
const drawerOpen = ref(false)
const detailId = ref<number | null>(null)

const stats = computed<RequestStats | null>(() => result.value?.stats ?? null)
const rows = computed<RequestRow[]>(() => result.value?.rows ?? [])
const pagination = computed<Pagination>(
  () => result.value?.pagination ?? { page: 1, pages: 0, total: 0, page_size: PAGE_SIZE },
)
const logState = computed<RequestLogState | null>(() => result.value?.request_log ?? null)

// A compact window of page numbers: first, last, and two neighbours around the current page.
const pageButtons = computed<(number | string)[]>(() => {
  const { page: current, pages } = pagination.value
  if (!pages) return []
  const wanted = new Set([1, pages, current - 1, current, current + 1])
  const list = [...wanted].filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b)
  const withGaps: (number | string)[] = []
  list.forEach((n, index) => {
    if (index && n - list[index - 1] > 1) withGaps.push('…')
    withGaps.push(n)
  })
  return withGaps
})

function queryParams(targetPage = page.value): RequestsQuery {
  return {
    start: fromInputValue(filters.start) || undefined,
    end: fromInputValue(filters.end) || undefined,
    upstream: filters.upstream || undefined,
    model: filters.model || undefined,
    status: filters.status || undefined,
    path: filters.path.trim() || undefined,
    page: targetPage,
    page_size: PAGE_SIZE,
  }
}

async function load(targetPage = page.value) {
  loading.value = true
  try {
    const { data } = await api.requests(queryParams(targetPage))
    result.value = data
    page.value = data.pagination.page
    lastUpdated.value = fmtClock(new Date().toISOString())
    loadError.value = ''
  } catch (error) {
    if (statusOf(error) !== 401) loadError.value = errorDetail(error)
  } finally {
    loading.value = false
  }
}

async function loadOptions() {
  try {
    const [overview, llm] = await Promise.all([api.overview(), api.modelsLlm()])
    upstreamOptions.value = (overview.data.upstreams ?? []).map((u) => u.name)
    const names = new Set<string>()
    ;(llm.data.models ?? []).forEach((m) => names.add(m.model))
    ;(llm.data.aliases ?? []).forEach((a) => names.add(a.alias))
    modelOptions.value = [...names].sort()
  } catch {
    // 选项拉取失败不影响明细表：筛选用输入框仍可用
  }
}

function apply() {
  load(1)
}

function reset() {
  Object.assign(filters, {
    start: hoursAgoInput(24),
    end: nowInputValue(),
    upstream: '',
    model: '',
    status: '',
    path: '',
  })
  load(1)
}

function goto(n: number | string) {
  if (typeof n !== 'number') return
  if (n >= 1 && n <= pagination.value.pages && n !== page.value) load(n)
}

function openDetail(row: RequestRow) {
  detailId.value = row.id
  drawerOpen.value = true
}

onMounted(() => {
  loadOptions()
  load(1)
})
</script>

<template>
  <TopBar
    title="Requests"
    sub="请求明细与统计 · 数据源 SQLite"
    refreshable
    :last-updated="lastUpdated"
    @refresh="load()"
  />

  <div v-if="loadError" class="warn-strip">⚠ {{ loadError }}</div>
  <div v-if="logState && !logState.enabled" class="warn-strip">
    ⚠ 请求日志已停用（<code>request_log.enabled=false</code>），新请求不再入库；以下仍是历史区间的数据。
  </div>

  <div class="filters">
    <div class="field">
      <label for="f-start">起始时间</label>
      <input id="f-start" v-model="filters.start" type="datetime-local" class="dt" />
    </div>
    <div class="field">
      <label for="f-end">结束时间</label>
      <input id="f-end" v-model="filters.end" type="datetime-local" class="dt" />
    </div>
    <div class="field">
      <label for="f-upstream">上游</label>
      <select id="f-upstream" v-model="filters.upstream" class="sel">
        <option value="">全部</option>
        <option v-for="name in upstreamOptions" :key="name" :value="name">{{ name }}</option>
      </select>
    </div>
    <div class="field">
      <label for="f-model">模型</label>
      <select id="f-model" v-model="filters.model" class="sel">
        <option value="">全部</option>
        <option v-for="name in modelOptions" :key="name" :value="name">{{ name }}</option>
      </select>
    </div>
    <div class="field">
      <label for="f-status">状态</label>
      <select id="f-status" v-model="filters.status" class="sel">
        <option value="">全部</option>
        <option v-for="s in STATUS_OPTIONS" :key="s" :value="s">{{ s }}</option>
      </select>
    </div>
    <div class="field">
      <label for="f-path">路径 / 关键字</label>
      <input id="f-path" v-model="filters.path" class="inp" placeholder="/v1/chat/completions" @keyup.enter="apply" />
    </div>
    <button class="btn primary" :disabled="loading" @click="apply">应用筛选</button>
    <button class="btn" @click="reset">重置</button>
  </div>

  <div class="minikpis">
    <div class="minikpi"><div class="l">区间请求</div><div class="v">{{ fmtInt(stats?.count) }}</div></div>
    <div class="minikpi"><div class="l">P50</div><div class="v">{{ fmtMs(stats?.p50_ms) }}</div></div>
    <div class="minikpi"><div class="l">P95</div><div class="v">{{ fmtMs(stats?.p95_ms) }}</div></div>
    <div class="minikpi"><div class="l">P99</div><div class="v">{{ fmtMs(stats?.p99_ms) }}</div></div>
    <div class="minikpi">
      <div class="l">错误率</div>
      <div class="v" :class="{ bad: (stats?.error_rate ?? 0) > 0 }">{{ fmtPct(stats?.error_rate) }}</div>
    </div>
  </div>

  <div>
    <div class="view-title">请求明细 <span class="tag">SQLite · 每页 {{ PAGE_SIZE }}</span></div>
    <div class="card table-card">
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>方法</th>
            <th>路径 / 模型</th>
            <th>上游</th>
            <th>状态</th>
            <th>耗时</th>
            <th>流式</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.id">
            <td class="mono">{{ fmtClock(row.ts) }}</td>
            <td>{{ row.method }}</td>
            <td class="path-cell">
              {{ row.path }}
              <div class="mono sub-line">{{ row.model || row.requested_model || '—' }}</div>
            </td>
            <td>{{ row.upstream || '—' }}</td>
            <td><span class="badge" :class="statusBadge(row.status).cls">{{ statusBadge(row.status).text }}</span></td>
            <td class="mono">{{ fmtMs(row.duration_ms) }}</td>
            <td>{{ row.stream ? '是' : '否' }}</td>
            <td><span class="link" @click="openDetail(row)">详情</span></td>
          </tr>
          <tr v-if="!rows.length && !loading">
            <td colspan="8" class="empty">该区间内没有请求记录</td>
          </tr>
          <tr v-if="!rows.length && loading">
            <td colspan="8" class="empty">加载中…</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="pager">
    <span>共 {{ fmtInt(pagination.total) }} 条 · 每页 {{ pagination.page_size }} 条</span>
    <div class="pg-btns">
      <button :disabled="pagination.page <= 1" @click="goto(pagination.page - 1)">上一页</button>
      <template v-for="(n, index) in pageButtons" :key="`${n}-${index}`">
        <span v-if="n === '…'" class="muted">…</span>
        <button v-else :class="{ cur: n === pagination.page }" @click="goto(n)">{{ n }}</button>
      </template>
      <button :disabled="pagination.page >= pagination.pages" @click="goto(pagination.page + 1)">下一页</button>
    </div>
  </div>

  <RequestDrawer v-model="drawerOpen" :record-id="detailId" />

  <div class="foot">llm-hub 管理台 · Requests（只读视图）</div>
</template>

<style scoped>
.table-card {
  padding: 4px 6px;
}

.path-cell {
  max-width: 320px;
  overflow-wrap: anywhere;
}

.sub-line {
  color: var(--text-dim);
  font-size: 11.5px;
}

.warn-strip code {
  color: var(--text-dim);
}
</style>
