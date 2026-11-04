<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { errorDetail, statusOf } from '@/api/client'
import { api } from '@/api/management'
import ApiDoc from '@/components/ApiDoc.vue'
import TopBar from '@/components/TopBar.vue'
import type { BenchHistoryRecord, BenchJob, LlmModel, ModelsLlm } from '@/types/api'
import type { ApiParam, ApiRequestSpec, ApiResponseSpec } from '@/types/ui'
import { deviceBadge, fmtClock, fmtInt, memoryText } from '@/utils/format'

type Tab = 'models' | 'api'

const { t } = useI18n()

const data = ref<ModelsLlm>({ models: [], aliases: [], num_ctx_overrides: {} })
const lastUpdated = ref('')
const loadError = ref('')
const keyword = ref('')
const refreshing = ref(false)
// This projection probes every upstream, so it can take seconds when one of them is down;
// the tables must not read "no models" while the first response is still in flight.
const loaded = ref(false)
const tab = ref<Tab>('models')

const models = computed<LlmModel[]>(() => {
  const needle = keyword.value.trim().toLowerCase()
  if (!needle) return data.value.models
  return data.value.models.filter(
    (m) =>
      m.model.toLowerCase().includes(needle) ||
      m.display.toLowerCase().includes(needle) ||
      m.upstream.toLowerCase().includes(needle),
  )
})

/** A hosted provider has no resident-model API: the probe answering is the only state. */
function stateText(m: LlmModel): string {
  if (m.type === 'opencode') return m.running ? t('common.online') : t('common.offline')
  return m.running ? t('common.running') : t('common.notLoaded')
}

function sourceText(source: string | null | undefined): string {
  if (source === 'server') return t('modelsLlm.source.server')
  if (source === 'client*') return t('modelsLlm.source.clientFallback')
  if (source === 'client') return t('modelsLlm.source.client')
  return source || '—'
}

function sourceClass(source: string | null | undefined): string {
  if (source === 'server') return 'ok'
  if (source === 'client*') return 'err'
  return 'idle'
}

// --------------------------------------------------------------------------- //
// Speed-test modal: kicks off an async bench job and polls until it finishes.
// --------------------------------------------------------------------------- //
const bench = ref<{
  open: boolean
  upstream: string
  model: string
  job: BenchJob | null
  error: string
}>({ open: false, upstream: '', model: '', job: null, error: '' })
let benchTimer: ReturnType<typeof setInterval> | null = null

function clearBenchTimer() {
  if (benchTimer !== null) {
    clearInterval(benchTimer)
    benchTimer = null
  }
}

async function pollBench() {
  if (!bench.value.job) return
  try {
    const res = await api.benchStatus(bench.value.job.job_id)
    bench.value.job = res.data
    if (res.data.status === 'done' || res.data.status === 'failed') {
      clearBenchTimer()
    }
  } catch (error) {
    clearBenchTimer()
    bench.value.error = errorDetail(error)
  }
}

async function runBench(upstream: string, model: string) {
  clearBenchTimer()
  bench.value = { open: true, upstream, model, job: null, error: '' }
  try {
    const res = await api.benchStart({ upstream, model })
    bench.value.job = {
      job_id: res.data.job_id,
      upstream,
      model,
      status: 'pending',
      progress: { completed: 0, total: 0, warmup: false },
      result: null,
      error: null,
      created_at: '',
      finished_at: null,
    }
    await pollBench()
    benchTimer = setInterval(pollBench, 1000)
  } catch (error) {
    if (statusOf(error) === 401) return
    bench.value.error = errorDetail(error)
  }
}

function startBench(m: LlmModel) {
  return runBench(m.upstream, m.model)
}

function closeBench() {
  clearBenchTimer()
  bench.value.open = false
}

// --------------------------------------------------------------------------- //
// Hover popover: fetch the latest 3 speed records for a (upstream, model) and float
// them next to the row. Lazily loaded, debounced, and cached per (upstream, model).
// --------------------------------------------------------------------------- //
const tip = ref<{
  show: boolean
  x: number
  y: number
  upstream: string
  model: string
  records: BenchHistoryRecord[]
  loading: boolean
  error: string
  cache: Record<string, BenchHistoryRecord[]>
}>({
  show: false,
  x: 0,
  y: 0,
  upstream: '',
  model: '',
  records: [],
  loading: false,
  error: '',
  cache: {},
})
let tipDebounce: ReturnType<typeof setTimeout> | null = null

function onRowEnter(m: LlmModel, ev: MouseEvent) {
  if (tipDebounce !== null) clearTimeout(tipDebounce)
  tipDebounce = setTimeout(() => showTip(m, ev), 250)
}

function showTip(m: LlmModel, ev: MouseEvent) {
  const key = `${m.upstream}/${m.model}`
  tip.value.upstream = m.upstream
  tip.value.model = m.model
  tip.value.x = ev.clientX + 14
  tip.value.y = ev.clientY + 14
  tip.value.show = true
  const cached = tip.value.cache[key]
  if (cached) {
    tip.value.records = cached
    return
  }
  tip.value.loading = true
  tip.value.error = ''
  tip.value.records = []
  api
    .benchHistory(m.upstream, m.model)
    .then((res) => {
      const recs = res.data.records || []
      tip.value.cache[key] = recs
      if (tip.value.upstream === m.upstream && tip.value.model === m.model) {
        tip.value.records = recs
        tip.value.loading = false
      }
    })
    .catch((error) => {
      if (tip.value.upstream === m.upstream && tip.value.model === m.model) {
        tip.value.loading = false
        tip.value.error = statusOf(error) === 401 ? '' : errorDetail(error)
      }
    })
}

function onRowLeave() {
  if (tipDebounce !== null) clearTimeout(tipDebounce)
  tip.value.show = false
}

onBeforeUnmount(() => {
  clearBenchTimer()
  if (tipDebounce !== null) clearTimeout(tipDebounce)
})

const authHeaders: string[][] = [
  ['Authorization', 'Bearer <API_KEY>'],
  ['Content-Type', 'application/json'],
]

const generateParams = computed<ApiParam[]>(() => [
  { name: 'model', type: 'string', required: true, desc: t('modelsLlm.generateParams.model') },
  { name: 'prompt', type: 'string', required: true, desc: t('modelsLlm.generateParams.prompt') },
  { name: 'system', type: 'string', required: false, desc: t('modelsLlm.generateParams.system') },
  { name: 'template', type: 'string', required: false, desc: t('modelsLlm.generateParams.template') },
  { name: 'raw', type: 'boolean', required: false, desc: t('modelsLlm.generateParams.raw') },
  { name: 'context', type: 'integer[]', required: false, desc: t('modelsLlm.generateParams.context') },
  { name: 'images', type: 'string[]', required: false, desc: t('modelsLlm.generateParams.images') },
  { name: 'format', type: '"json" | object', required: false, desc: t('modelsLlm.generateParams.format') },
  { name: 'stream', type: 'boolean', required: false, desc: t('modelsLlm.generateParams.stream') },
  { name: 'keep_alive', type: 'string | number', required: false, desc: t('modelsLlm.generateParams.keepAlive') },
  { name: 'logprobs', type: 'boolean', required: false, desc: t('modelsLlm.generateParams.logprobs') },
  { name: 'top_logprobs', type: 'integer', required: false, desc: t('modelsLlm.generateParams.topLogprobs') },
  { name: 'options', type: 'object', required: false, desc: t('modelsLlm.generateParams.options') },
])

const generateRequest = computed<ApiRequestSpec>(() => ({
  method: 'POST',
  path: '/api/generate',
  headers: authHeaders,
  body: {
    model: 'qwen3:8b',
    prompt: '写一首关于编程的诗：',
    raw: true,
    stream: false,
    keep_alive: '3m',
    options: { temperature: 0, num_predict: 256 },
  },
}))

const generateResponse = computed<ApiResponseSpec>(() => ({
  status: '200',
  contentType: 'application/x-ndjson',
  note: t('modelsLlm.responses.generateNote'),
  body:
    '{"model":"qwen3:8b","response":"代码","done":false}\n' +
    '{"model":"qwen3:8b","response":"如诗行","done":true,"done_reason":"stop",' +
    '"prompt_eval_count":12,"eval_count":50}',
}))

const chatParams = computed<ApiParam[]>(() => [
  { name: 'model', type: 'string', required: true, desc: t('modelsLlm.chatParams.model') },
  { name: 'messages', type: 'array', required: true, desc: t('modelsLlm.chatParams.messages') },
  { name: 'temperature', type: 'number', required: false, desc: t('modelsLlm.chatParams.temperature') },
  { name: 'max_tokens', type: 'integer', required: false, desc: t('modelsLlm.chatParams.maxTokens') },
  { name: 'stream', type: 'boolean', required: false, desc: t('modelsLlm.chatParams.stream') },
  { name: 'tools', type: 'array', required: false, desc: t('modelsLlm.chatParams.tools') },
])

const chatRequest = computed<ApiRequestSpec>(() => ({
  method: 'POST',
  path: '/v1/chat/completions',
  headers: authHeaders,
  body: {
    model: 'qwen3:8b',
    messages: [
      { role: 'system', content: '你是一个有帮助的助手' },
      { role: 'user', content: '什么是机器学习？' },
    ],
    temperature: 0.7,
    max_tokens: 500,
    stream: false,
  },
}))

const chatResponse = computed<ApiResponseSpec>(() => ({
  status: '200',
  contentType: 'application/json',
  note: t('modelsLlm.responses.chatNote'),
  body: {
    id: 'chatcmpl-abc123',
    object: 'chat.completion',
    created: 1694000000,
    model: 'qwen3:8b',
    choices: [
      {
        index: 0,
        message: { role: 'assistant', content: '机器学习是人工智能的一个分支...' },
        finish_reason: 'stop',
      },
    ],
    usage: { prompt_tokens: 25, completion_tokens: 100, total_tokens: 125 },
  },
}))

async function load() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    const response = await api.modelsLlm()
    data.value = response.data
    loaded.value = true
    lastUpdated.value = fmtClock(new Date().toISOString())
    loadError.value = ''
  } catch (error) {
    if (statusOf(error) !== 401) loadError.value = errorDetail(error)
  } finally {
    refreshing.value = false
  }
}

onMounted(load)
</script>

<template>
  <TopBar
    :title="t('nav.modelsLlm')"
    :sub="t('modelsLlm.sub')"
    refreshable
    :loading="refreshing"
    :last-updated="lastUpdated"
    @refresh="load"
  />

  <div v-if="loadError" class="warn-strip">⚠ {{ loadError }}</div>

  <div class="tabs" role="tablist">
    <button
      class="tab"
      :class="{ on: tab === 'models' }"
      role="tab"
      :aria-selected="tab === 'models'"
      @click="tab = 'models'"
    >
      {{ t('modelsLlm.tabs.models') }}
      <span class="count">{{ loaded ? data.models.length : '—' }}</span>
    </button>
    <button
      class="tab"
      :class="{ on: tab === 'api' }"
      role="tab"
      :aria-selected="tab === 'api'"
      @click="tab = 'api'"
    >
      {{ t('modelsLlm.tabs.api') }}
      <span class="count">2</span>
    </button>
  </div>

  <template v-if="tab === 'models'">
    <div class="filters">
      <div class="field">
        <label for="kw">{{ t('modelsLlm.filter.label') }}</label>
        <input id="kw" v-model="keyword" class="inp" :placeholder="t('modelsLlm.filter.placeholder')" />
      </div>
      <div class="field" style="margin-left: auto; align-items: flex-end">
        <label>&nbsp;</label>
        <span class="chip">{{
          loaded ? t('modelsLlm.count', { shown: models.length, total: data.models.length }) : t('common.loading')
        }}</span>
      </div>
    </div>

    <div>
      <div class="view-title">
        {{ t('modelsLlm.mapping.title') }} <span class="tag">config.yaml · upstreams[].models</span>
        <span class="tag s2">{{ t('modelsLlm.mapping.hint') }}</span>
      </div>
      <div class="card tight">
        <table class="tbl-models">
          <colgroup>
            <col />
            <col class="c-upstream" />
            <col class="c-type" />
            <col class="c-ctx" />
            <col class="c-device" />
            <col class="c-mem" />
            <col class="c-state" />
            <col class="c-op" />
          </colgroup>
          <thead>
            <tr>
              <th>{{ t('modelsLlm.columns.model') }}</th>
              <th>{{ t('modelsLlm.columns.upstream') }}</th>
              <th>{{ t('modelsLlm.columns.type') }}</th>
              <th>num_ctx</th>
              <th>{{ t('modelsLlm.columns.device') }}</th>
              <th>{{ t('modelsLlm.columns.memory') }}</th>
              <th>{{ t('modelsLlm.columns.state') }}</th>
              <th>{{ t('modelsLlm.columns.actions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="m in models"
              :key="`${m.upstream}/${m.model}`"
              @mouseenter="onRowEnter(m, $event)"
              @mouseleave="onRowLeave"
            >
              <td class="mono" :title="m.alias ? t('common.realModel', { model: m.model }) : undefined">
                {{ m.display }}
                <span v-if="m.wildcard" class="chip s2" :title="t('common.wildcardTip')">wildcard</span>
                <span v-if="m.alias" class="chip s2" :title="t('common.aliasTip')">alias</span>
              </td>
              <td>{{ m.upstream }}</td>
              <td><span class="chip">{{ m.type }}</span></td>
              <td class="mono">{{ fmtInt(m.num_ctx) }}</td>
              <td><span class="badge" :class="deviceBadge(m.device, m.device_ratio).cls">{{ deviceBadge(m.device, m.device_ratio).text }}</span></td>
              <td class="mono">{{ memoryText(m.vram_gib, m.size_gib) }}</td>
              <td>
                <span class="badge" :class="m.running ? 'ok' : 'idle'">● {{ stateText(m) }}</span>
              </td>
              <td>
                <button class="bench-btn" :disabled="bench.open" @click="startBench(m)">{{ t('modelsLlm.bench') }}</button>
              </td>
            </tr>
            <tr v-if="!models.length">
              <td colspan="8" class="empty">{{ loaded ? t('modelsLlm.emptyModels') : t('common.loading') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div>
      <div class="view-title">{{ t('modelsLlm.aliases.title') }} <span class="tag">aliases</span></div>
      <div class="card tight">
        <table>
          <thead>
            <tr>
              <th>{{ t('modelsLlm.aliases.alias') }}</th>
              <th>{{ t('modelsLlm.aliases.target') }}</th>
              <th>{{ t('modelsLlm.aliases.upstream') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in data.aliases" :key="a.alias">
              <td class="mono">{{ a.alias }}</td>
              <td class="mono">{{ a.target }}</td>
              <td>{{ a.upstream || '—' }}</td>
            </tr>
            <tr v-if="!data.aliases.length">
              <td colspan="3" class="empty">{{ loaded ? t('modelsLlm.aliases.empty') : t('common.loading') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div>
      <div class="view-title">{{ t('modelsLlm.overrides.title') }} <span class="tag">num_ctx_overrides</span></div>
      <div class="card tight">
        <table>
          <thead>
            <tr>
              <th>{{ t('modelsLlm.overrides.model') }}</th>
              <th>num_ctx</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(value, model) in data.num_ctx_overrides" :key="model">
              <td class="mono">{{ model }}</td>
              <td class="mono">{{ fmtInt(value) }}</td>
            </tr>
            <tr v-if="!Object.keys(data.num_ctx_overrides).length">
              <td colspan="2" class="empty">{{ loaded ? t('modelsLlm.overrides.empty') : t('common.loading') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </template>

  <template v-else>
    <div class="api-lead">
      <i18n-t keypath="modelsLlm.apiLead.body" scope="global">
        <template #model><code>model</code></template>
        <template #overrides><code>num_ctx_overrides</code></template>
        <template #strong
          ><b
            ><i18n-t keypath="modelsLlm.apiLead.streamNote" scope="global"
              ><template #stream><code>stream</code></template></i18n-t
            ></b
          ></template
        >
        <template #method><span class="mono">POST</span></template>
        <template #code><b>405</b></template>
      </i18n-t>
    </div>

    <ApiDoc
      :title="t('modelsLlm.docs.generateTitle')"
      tag="/api/generate"
      :params="generateParams"
      :request="generateRequest"
      :response="generateResponse"
      :note="t('modelsLlm.docs.generateNote')"
    />

    <ApiDoc
      :title="t('modelsLlm.docs.chatTitle')"
      tag="/v1/chat/completions"
      :params="chatParams"
      :request="chatRequest"
      :response="chatResponse"
      :note="t('modelsLlm.docs.chatNote')"
    />
  </template>

  <div class="foot">{{ t('common.footReadOnly', { page: t('nav.modelsLlm') }) }}</div>

  <!-- Speed-test modal -->
  <div v-if="bench.open" class="modal-overlay" @click.self="closeBench">
    <div class="modal" role="dialog" aria-modal="true">
      <div class="modal-head">
        <div>
          <div class="modal-title">{{ t('modelsLlm.benchModal.title') }}</div>
          <div class="modal-sub mono">{{ bench.model }} · {{ bench.upstream }}</div>
        </div>
        <button class="icon-btn" :aria-label="t('modelsLlm.benchModal.close')" @click="closeBench">✕</button>
      </div>

      <div class="modal-body">
        <div v-if="bench.error" class="bench-error">⚠ {{ bench.error }}</div>

        <template v-else-if="!bench.job || bench.job.status === 'pending' || bench.job.status === 'running'">
          <div class="bench-progress">
            <span class="spinner"></span>
            <div>
              <div class="bp-line">{{ t('modelsLlm.benchModal.running') }}</div>
              <div class="bp-sub" v-if="bench.job && bench.job.progress.total">
                {{ t('modelsLlm.benchModal.progress', { done: bench.job.progress.completed, total: bench.job.progress.total }) }}
                <span v-if="bench.job.progress.warmup">{{ t('modelsLlm.benchModal.warmupNote') }}</span>
              </div>
              <div class="bp-sub" v-else>{{ t('modelsLlm.benchModal.connecting') }}</div>
            </div>
          </div>
        </template>

        <template v-else-if="bench.job.status === 'failed'">
          <div class="bench-error">
            ⚠ {{ t('modelsLlm.benchModal.failed', { error: bench.job.error || t('modelsLlm.benchModal.unknownError') }) }}
          </div>
          <button class="bench-btn wide" @click="runBench(bench.upstream, bench.model)">{{ t('modelsLlm.benchModal.rerun') }}</button>
        </template>

        <template v-else-if="bench.job.result">
          <div class="badge" :class="sourceClass(bench.job.result.timing_source)">
            {{ sourceText(bench.job.result.timing_source) }}
          </div>

          <div class="stat-grid">
            <div class="stat-card">
              <div class="stat-label">{{ t('modelsLlm.benchModal.decode') }}</div>
              <div class="stat-value">{{ fmtInt(Math.round(bench.job.result.steady.decode.mean)) }}</div>
              <div class="stat-unit">tok/s</div>
              <div class="stat-meta">
                {{
                  t('modelsLlm.benchModal.statMeta', {
                    std: fmtInt(Math.round(bench.job.result.steady.decode.std_dev)),
                    min: fmtInt(Math.round(bench.job.result.steady.decode.min)),
                    max: fmtInt(Math.round(bench.job.result.steady.decode.max)),
                  })
                }}
              </div>
            </div>
            <div class="stat-card">
              <div class="stat-label">{{ t('modelsLlm.benchModal.prefill') }}</div>
              <div class="stat-value">{{ fmtInt(Math.round(bench.job.result.steady.prefill.mean)) }}</div>
              <div class="stat-unit">tok/s</div>
              <div class="stat-meta">
                {{
                  t('modelsLlm.benchModal.statMeta', {
                    std: fmtInt(Math.round(bench.job.result.steady.prefill.std_dev)),
                    min: fmtInt(Math.round(bench.job.result.steady.prefill.min)),
                    max: fmtInt(Math.round(bench.job.result.steady.prefill.max)),
                  })
                }}
              </div>
            </div>
            <div class="stat-card">
              <div class="stat-label">{{ t('modelsLlm.benchModal.ttft') }}</div>
              <div class="stat-value">{{ fmtInt(Math.round(bench.job.result.steady.ttft.mean_ms)) }}</div>
              <div class="stat-unit">ms</div>
              <div class="stat-meta">
                {{
                  t('modelsLlm.benchModal.statMeta', {
                    std: fmtInt(Math.round(bench.job.result.steady.ttft.std_dev)),
                    min: fmtInt(Math.round(bench.job.result.steady.ttft.min)),
                    max: fmtInt(Math.round(bench.job.result.steady.ttft.max)),
                  })
                }}
              </div>
            </div>
          </div>

          <div class="view-title" style="margin-top: 14px">
            {{ t('modelsLlm.benchModal.roundsTitle') }} <span class="tag">{{ t('modelsLlm.benchModal.roundsTag') }}</span>
          </div>
          <div class="card tight">
            <table>
              <thead>
                <tr>
                  <th>{{ t('modelsLlm.benchModal.round') }}</th>
                  <th>{{ t('modelsLlm.benchModal.kind') }}</th>
                  <th>{{ t('modelsLlm.benchModal.decodeRate') }}</th>
                  <th>{{ t('modelsLlm.benchModal.prefillRate') }}</th>
                  <th>{{ t('modelsLlm.benchModal.ttftMs') }}</th>
                  <th>{{ t('modelsLlm.benchModal.genTokens') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="r in bench.job.result.rounds" :key="r.round">
                  <td class="mono">{{ r.round }}</td>
                  <td>
                    <span class="chip" :class="{ s2: r.warmup }">{{
                      r.warmup ? t('modelsLlm.benchModal.warmup') : t('modelsLlm.benchModal.measured')
                    }}</span>
                  </td>
                  <td class="mono">{{ fmtInt(Math.round(r.decode_tok_s)) }}</td>
                  <td class="mono">{{ fmtInt(Math.round(r.prefill_tok_s)) }}</td>
                  <td class="mono">{{ r.ttft_s === null ? '—' : fmtInt(Math.round(r.ttft_s * 1000)) }}</td>
                  <td class="mono">{{ fmtInt(r.gen_tokens) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="modal-actions">
            <button class="bench-btn wide" @click="runBench(bench.upstream, bench.model)">{{ t('modelsLlm.benchModal.again') }}</button>
          </div>
        </template>
      </div>
    </div>
  </div>

  <!-- Hover speed-history popover -->
  <div
    v-if="tip.show"
    class="tip"
    :style="{ left: tip.x + 'px', top: tip.y + 'px' }"
  >
    <div class="tip-head mono">{{ tip.model }} · {{ tip.upstream }}</div>
    <div v-if="tip.loading" class="tip-row tip-mute">{{ t('modelsLlm.benchModal.historyLoading') }}</div>
    <div v-else-if="tip.error" class="tip-row tip-err">{{ tip.error }}</div>
    <div v-else-if="!tip.records.length" class="tip-row tip-mute">{{ t('modelsLlm.benchModal.historyEmpty') }}</div>
    <template v-else>
      <div v-for="rec in tip.records" :key="rec.id" class="tip-rec">
        <div class="tip-row tip-time">{{ fmtClock(rec.created_at) }} · <span class="badge" :class="sourceClass(rec.timing_source)">{{ sourceText(rec.timing_source) }}</span></div>
        <div class="tip-row">
          <span>Decode</span><b>{{ fmtInt(Math.round(rec.decode_tok_s || 0)) }} tok/s</b>
        </div>
        <div class="tip-row">
          <span>Prefill</span><b>{{ fmtInt(Math.round(rec.prefill_tok_s || 0)) }} tok/s</b>
        </div>
        <div class="tip-row">
          <span>TTFT</span><b>{{ rec.ttft_ms === null ? '—' : fmtInt(Math.round(rec.ttft_ms)) }} ms</b>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.inp {
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border-strong);
  border-radius: 7px;
  padding: 6px 9px;
  font-size: 12px;
  font-family: inherit;
  min-width: 240px;
}

.chip.s2 {
  margin-left: 6px;
}

/* Column sizing: the "运行态" badge and the memory figure were fighting over the
   leftover width, which squeezed the state column into two lines. Give every tight
   column an explicit hint and let the model name absorb the remaining space.
   `table-layout: fixed` makes the browser honour those hints — under the default
   auto layout a long "16.1 GiB / 16.1 GiB" simply overflowed its cell and ran under
   the state badge. Cells clip instead, so nothing can overlap. */
.tbl-models {
  table-layout: fixed;
}
.tbl-models .c-upstream {
  width: 96px;
}
.tbl-models .c-type {
  width: 88px;
}
.tbl-models .c-ctx {
  width: 78px;
}
.tbl-models .c-device {
  width: 104px;
}
.tbl-models .c-mem {
  width: 152px;
}
.tbl-models .c-state {
  width: 96px;
}
.tbl-models .c-op {
  width: 84px;
}

.tbl-models th,
.tbl-models td {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tbl-models td:first-child {
  white-space: normal;
}

.bench-btn {
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  padding: 3px 10px;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.15s ease;
}

.bench-btn:hover:not(:disabled) {
  background: var(--accent);
  color: #fff;
}

.bench-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.bench-btn.wide {
  margin-top: 12px;
  padding: 7px 16px;
}

/* ---- modal ---- */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.modal {
  width: min(680px, 92vw);
  max-height: 86vh;
  overflow-y: auto;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45);
}

.modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  background: var(--surface);
  z-index: 1;
}

.modal-title {
  font-size: 15px;
  font-weight: 600;
}

.modal-sub {
  font-size: 12px;
  color: var(--text-dim);
  margin-top: 2px;
}

.modal-body {
  padding: 16px;
}

.bench-progress {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 4px;
}

.bp-line {
  font-size: 14px;
  font-weight: 600;
}

.bp-sub {
  font-size: 12px;
  color: var(--text-dim);
  margin-top: 4px;
}

.bench-error {
  background: rgba(248, 81, 73, 0.12);
  border: 1px solid rgba(248, 81, 73, 0.4);
  color: var(--red);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 13px;
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-top: 12px;
}

.stat-card {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px;
  text-align: center;
}

.stat-label {
  font-size: 12px;
  color: var(--text-dim);
}

.stat-value {
  font-size: 26px;
  font-weight: 700;
  color: var(--text);
  margin-top: 4px;
  font-variant-numeric: tabular-nums;
}

.stat-unit {
  font-size: 11px;
  color: var(--text-mute);
}

.stat-meta {
  font-size: 11px;
  color: var(--text-dim);
  margin-top: 6px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
}

/* ---- spinner (reuses the topbar spinner look) ---- */
.spinner {
  width: 18px;
  height: 18px;
  border: 2px solid var(--border-strong);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: bench-spin 0.8s linear infinite;
  display: inline-block;
  flex: none;
}

@keyframes bench-spin {
  to {
    transform: rotate(360deg);
  }
}

/* ---- hover tip ---- */
.tip {
  position: fixed;
  z-index: 120;
  width: 220px;
  background: var(--surface-2);
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.4);
  padding: 10px;
  pointer-events: none;
}

.tip-head {
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 6px;
}

.tip-rec {
  padding: 6px 0;
  border-top: 1px solid var(--border);
}

.tip-rec:first-of-type {
  border-top: none;
}

.tip-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--text-dim);
  line-height: 1.7;
}

.tip-row b {
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

.tip-time {
  color: var(--text-mute);
  margin-bottom: 2px;
}

.tip-mute {
  color: var(--text-mute);
}

.tip-err {
  color: var(--red);
}
</style>
