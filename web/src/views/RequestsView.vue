<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

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

const { t } = useI18n()

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
// Installed models (and aliases) with the upstreams that can serve them; the 模型 filter
// is derived from it, so choosing an upstream narrows the list to what that instance offers.
interface ModelChoice {
  name: string
  upstreams: string[]
}
const modelChoices = ref<ModelChoice[]>([])
const modelOptions = computed<string[]>(() => {
  const names = new Set<string>()
  for (const choice of modelChoices.value) {
    if (filters.upstream && !choice.upstreams.includes(filters.upstream)) continue
    names.add(choice.name)
  }
  return [...names].sort()
})
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
    const models = llm.data.models ?? []
    const choices: ModelChoice[] = models.map((m) => ({ name: m.model, upstreams: [m.upstream] }))
    // An alias without its own upstream resolves wherever its target lives, so it stays
    // offered under exactly those upstreams.
    for (const alias of llm.data.aliases ?? []) {
      const home = alias.upstream
        ? [alias.upstream]
        : models.filter((m) => m.model === alias.target).map((m) => m.upstream)
      choices.push({ name: alias.alias, upstreams: home })
    }
    modelChoices.value = choices
  } catch {
    // 选项拉取失败不影响明细表：筛选用输入框仍可用
  }
}

// Picking an upstream that cannot serve the current model would silently filter to nothing.
watch(
  () => filters.upstream,
  () => {
    if (filters.model && !modelOptions.value.includes(filters.model)) filters.model = ''
  },
)

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
    :title="t('nav.requests')"
    :sub="t('requests.sub')"
    refreshable
    :loading="loading"
    :last-updated="lastUpdated"
    @refresh="load()"
  />

  <div v-if="loadError" class="warn-strip">⚠ {{ loadError }}</div>
  <div v-if="logState && !logState.enabled" class="warn-strip">
    ⚠
    <i18n-t keypath="requests.logDisabled" scope="global">
      <template #code><code>request_log.enabled=false</code></template>
    </i18n-t>
  </div>

  <div class="filters">
    <div class="field">
      <label for="f-start">{{ t('requests.filters.start') }}</label>
      <input id="f-start" v-model="filters.start" type="datetime-local" class="dt" />
    </div>
    <div class="field">
      <label for="f-end">{{ t('requests.filters.end') }}</label>
      <input id="f-end" v-model="filters.end" type="datetime-local" class="dt" />
    </div>
    <div class="field">
      <label for="f-upstream">{{ t('requests.filters.upstream') }}</label>
      <select id="f-upstream" v-model="filters.upstream" class="sel">
        <option value="">{{ t('requests.filters.all') }}</option>
        <option v-for="name in upstreamOptions" :key="name" :value="name">{{ name }}</option>
      </select>
    </div>
    <div class="field">
      <label for="f-model">{{
        filters.upstream
          ? t('requests.filters.modelWithUpstream', { upstream: filters.upstream })
          : t('requests.filters.model')
      }}</label>
      <select id="f-model" v-model="filters.model" class="sel">
        <option value="">{{ t('requests.filters.all') }}</option>
        <option v-for="name in modelOptions" :key="name" :value="name">{{ name }}</option>
      </select>
    </div>
    <div class="field">
      <label for="f-status">{{ t('requests.filters.status') }}</label>
      <select id="f-status" v-model="filters.status" class="sel">
        <option value="">{{ t('requests.filters.all') }}</option>
        <option v-for="s in STATUS_OPTIONS" :key="s" :value="s">{{ s }}</option>
      </select>
    </div>
    <div class="field">
      <label for="f-path">{{ t('requests.filters.path') }}</label>
      <input id="f-path" v-model="filters.path" class="inp" placeholder="/v1/chat/completions" @keyup.enter="apply" />
    </div>
    <button class="btn primary" :disabled="loading" @click="apply">{{ t('requests.filters.apply') }}</button>
    <button class="btn" @click="reset">{{ t('common.reset') }}</button>
  </div>

  <div class="minikpis">
    <div class="minikpi"><div class="l">{{ t('requests.stats.count') }}</div><div class="v">{{ fmtInt(stats?.count) }}</div></div>
    <div class="minikpi"><div class="l">P50</div><div class="v">{{ fmtMs(stats?.p50_ms) }}</div></div>
    <div class="minikpi"><div class="l">P95</div><div class="v">{{ fmtMs(stats?.p95_ms) }}</div></div>
    <div class="minikpi"><div class="l">P99</div><div class="v">{{ fmtMs(stats?.p99_ms) }}</div></div>
    <div class="minikpi">
      <div class="l">{{ t('requests.stats.errorRate') }}</div>
      <div class="v" :class="{ bad: (stats?.error_rate ?? 0) > 0 }">{{ fmtPct(stats?.error_rate) }}</div>
    </div>
  </div>

  <div>
    <div class="view-title">
      {{ t('requests.table.title') }} <span class="tag">{{ t('requests.table.tag', { n: PAGE_SIZE }) }}</span>
    </div>
    <div class="card table-card">
      <table>
        <thead>
          <tr>
            <th>{{ t('requests.table.time') }}</th>
            <th>{{ t('requests.table.method') }}</th>
            <th>{{ t('requests.table.pathModel') }}</th>
            <th>{{ t('requests.table.upstream') }}</th>
            <th>{{ t('requests.table.status') }}</th>
            <th>{{ t('requests.table.duration') }}</th>
            <th>{{ t('requests.table.stream') }}</th>
            <th>{{ t('requests.table.actions') }}</th>
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
            <td>{{ row.stream ? t('common.yes') : t('common.no') }}</td>
            <td><span class="link" @click="openDetail(row)">{{ t('common.detail') }}</span></td>
          </tr>
          <tr v-if="!rows.length && !loading">
            <td colspan="8" class="empty">{{ t('requests.table.empty') }}</td>
          </tr>
          <tr v-if="!rows.length && loading">
            <td colspan="8" class="empty">{{ t('common.loading') }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="pager">
    <span>{{ t('common.total', { n: fmtInt(pagination.total) }) }} · {{ t('requests.pager.perPage', { n: pagination.page_size }) }}</span>
    <div class="pg-btns">
      <button :disabled="pagination.page <= 1" @click="goto(pagination.page - 1)">{{ t('requests.pager.prev') }}</button>
      <template v-for="(n, index) in pageButtons" :key="`${n}-${index}`">
        <span v-if="n === '…'" class="muted">…</span>
        <button v-else :class="{ cur: n === pagination.page }" @click="goto(n)">{{ n }}</button>
      </template>
      <button :disabled="pagination.page >= pagination.pages" @click="goto(pagination.page + 1)">{{ t('requests.pager.next') }}</button>
    </div>
  </div>

  <RequestDrawer v-model="drawerOpen" :record-id="detailId" />

  <div class="foot">{{ t('common.footReadOnly', { page: t('nav.requests') }) }}</div>
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
