<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import KeyValueEditor from '@/components/KeyValueEditor.vue'
import TagListEditor from '@/components/TagListEditor.vue'
import TopBar from '@/components/TopBar.vue'
import UpstreamCard from '@/components/UpstreamCard.vue'
import { api } from '@/api/management'
import type {
  ConfigPayload,
  ConfigServer,
  ConfigUpstream,
  RequestCleanupPreview,
  RequestCleanupResult,
} from '@/types/api'
import { errorDetail, errorResponseBody, statusOf } from '@/api/client'

const { t } = useI18n()

const config = ref<ConfigPayload | null>(null)
// Non-null view of the loaded config so the template can bind without null guards.
const cfg = computed(() => config.value as ConfigPayload)
const loading = ref(false)
const loadError = ref('')
const saving = ref(false)
const saveError = ref('')
const savedOk = ref(false)
// Which tab the last successful save came from. An "already live / restart needed"
// hint must not appear just because the operator switched to another tab afterwards.
const savedTab = ref('')
const segmentErrors = ref<Record<string, string>>({})
// Tab order mirrors the config.yaml surface: Service (server) first, then Security,
// then the Requests maintenance tab. Requests holds console actions that do not edit
// config.yaml (the Save bar below is hidden there on purpose).
const activeTab = ref('service')
const confirmOpen = ref(false)

const RERANK_MODES = ['logprobs']
const ON_ERROR_OPTIONS = ['score_zero', 'fail']
const UPSTREAM_API_OPTIONS = ['auto', 'generate', 'chat']
// Security tab: the backend only accepts these verbs as path_allowlist keys, and
// these three values for public.admin_endpoints (see config.HTTP_METHOD_NAMES).
const ALLOW_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']
const ADMIN_ENDPOINTS = ['deny', 'readonly', 'allow']
// Server tab: uvicorn's accepted log levels.
const LOG_LEVELS = ['critical', 'error', 'warning', 'info', 'debug', 'trace']
const MIB = 1024 * 1024
const DEFAULT_BODY_BYTES = 32 * MIB

// PUT /config reports errors by segment name, not by tab name. Keeping the mapping
// in one place means a newly editable segment only needs one entry here.
const TAB_OF_SEGMENT: Record<string, string> = {
  server: 'service',
  security: 'security',
  upstreams: 'upstream',
  rerank: 'rerank',
  systemone: 'systemone',
  websearch: 'websearch',
}

// Web search is proxied through an `opencode` upstream; only those are valid choices.
// An empty value means "the first opencode upstream" (backend fallback, see WebSearchConfig).
const opencodeUpstreams = computed<ConfigUpstream[]>(() =>
  (config.value?.upstreams || []).filter((u) => u.type === 'opencode' && !!u.name),
)

function fld(key: string): string {
  return t(`settings.fields.${key}`)
}

function tabLabel(seg: string): string {
  const tab = TAB_OF_SEGMENT[seg] ?? seg
  return t(`settings.tabs.${tab}`)
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const { data } = await api.configGet()
    // Make sure list/dict fields the dumper may have omitted (because they matched the
    // default) exist, so the form inputs always bind to a defined value.
    for (const up of data.upstreams as ConfigUpstream[]) {
      if (!Array.isArray(up.models)) up.models = []
      if (!up.model_aliases || typeof up.model_aliases !== 'object') up.model_aliases = {}
    }
    if (data.rerank) {
      if (!Array.isArray(data.rerank.models)) data.rerank.models = []
      if (!Array.isArray(data.rerank.stop)) data.rerank.stop = []
      if (!data.rerank.options || typeof data.rerank.options !== 'object') data.rerank.options = {}
    }
    // GET /config returns the full validated server node. The guard only keeps the
    // Service form usable against an older gateway that omitted it.
    if (!data.server || typeof data.server !== 'object') {
      data.server = {
        host: '0.0.0.0',
        port: 8000,
        log_level: 'info',
        request_timeout: 600,
        max_concurrency: 4,
        upstream_queue_timeout: 120,
        upstream_keepalive_expiry: 30,
      }
    }
    // GET /config already returns the full, validated security node. These guards
    // only keep the form usable against an older gateway that omitted a field.
    if (data.security) {
      const s = data.security
      if (!s.internal || typeof s.internal !== 'object') {
        s.internal = { allow_anonymous: true, expose_version: true, expose_docs: true }
      }
      if (!s.public || typeof s.public !== 'object') {
        s.public = { port: null, path_allowlist: {}, admin_endpoints: 'deny' }
      }
      if (!s.public.path_allowlist || typeof s.public.path_allowlist !== 'object') {
        s.public.path_allowlist = {}
      }
      for (const method of Object.keys(s.public.path_allowlist)) {
        if (!Array.isArray(s.public.path_allowlist[method])) s.public.path_allowlist[method] = []
      }
      for (const key of ['blocked_paths', 'blocked_ips', 'cors_origins'] as const) {
        if (!Array.isArray(s[key])) s[key] = []
      }
      if (!s.rate_limit || typeof s.rate_limit !== 'object') {
        s.rate_limit = { enabled: false, per_minute: 60, burst: 20, by: 'ip' }
      }
    }
    config.value = data
  } catch (e) {
    loadError.value = errorDetail(e)
  } finally {
    loading.value = false
  }
}

function addUpstream() {
  if (!config.value) return
  config.value.upstreams.push({
    name: '',
    base_url: '',
    type: 'ollama',
    models: [],
    model_aliases: {},
    timeout: null,
    priority: 0,
    description: '',
  })
}
function removeUpstream(i: number) {
  config.value?.upstreams.splice(i, 1)
}

function toNumOrNull(v: unknown): number | null {
  if (v === '' || v === null || v === undefined) return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}
function toNumOr(v: unknown, fallback: number): number {
  const n = toNumOrNull(v)
  return n === null ? fallback : n
}

// Native inputs hand back "" for a cleared field; coerce the numeric fields to real numbers
// (or null / the model default) so pydantic accepts them. A pure integer keep_alive ("600")
// is sent as a number so Ollama reads it as seconds; "3m" / "-1" stay verbatim.
function normalizeBeforeSave(cfg: ConfigPayload) {
  // Service: every numeric field is a native number input; coerce "" back to a
  // valid number so pydantic accepts it. Port is bounded but left to the backend
  // (and its validator) to reject out-of-range values with a named error.
  const sv = cfg.server as ConfigServer
  if (sv) {
    sv.port = toNumOr(sv.port, 8000)
    sv.request_timeout = toNumOr(sv.request_timeout, 600)
    sv.max_concurrency = toNumOr(sv.max_concurrency, 4)
    sv.upstream_queue_timeout = toNumOr(sv.upstream_queue_timeout, 120)
    sv.upstream_keepalive_expiry = toNumOr(sv.upstream_keepalive_expiry, 30)
  }
  const r = cfg.rerank
  if (r) {
    r.temperature = toNumOr(r.temperature, 0)
    r.top_logprobs = toNumOr(r.top_logprobs, 20)
    r.max_tokens = toNumOr(r.max_tokens, 4)
    r.token_scan_depth = toNumOr(r.token_scan_depth, 4)
    r.max_concurrency = toNumOr(r.max_concurrency, 8)
    const ka = r.keep_alive
    if (typeof ka === 'string' && /^-?\d+$/.test(ka.trim())) r.keep_alive = parseInt(ka.trim(), 10)
  }
  if (cfg.systemone) cfg.systemone.timeout = toNumOr(cfg.systemone.timeout, 60)
  if (cfg.websearch) cfg.websearch.timeout = toNumOr(cfg.websearch.timeout, 60)
  for (const up of cfg.upstreams || []) {
    up.timeout = toNumOrNull(up.timeout)
    up.priority = toNumOr(up.priority, 0)
  }
  // Security: a cleared port input means "no public listener" (null), not 0.
  // The body limit and bucket sizes are native number inputs, so coerce "" back
  // to a valid number. A method with no patterns is not a rule: drop that key.
  const s = cfg.security
  if (s) {
    s.public.port = toNumOrNull(s.public.port)
    s.max_body_bytes = toNumOr(s.max_body_bytes, DEFAULT_BODY_BYTES)
    const rl = s.rate_limit
    rl.per_minute = toNumOr(rl.per_minute, 60)
    rl.burst = toNumOr(rl.burst, 20)
    for (const method of Object.keys(s.public.path_allowlist || {})) {
      if (!s.public.path_allowlist[method].length) delete s.public.path_allowlist[method]
    }
  }
}

// --------------------------------------------------------------------------- //
// Security tab: path_allowlist is a method -> regex-lines mapping. The verb list
// comes from a fixed set, so the console can never introduce an unknown key (the
// backend rejects those with a 422 and names the security segment).
// --------------------------------------------------------------------------- //
const newAllowMethod = ref('')
const allowlistMethods = computed(() =>
  Object.keys(config.value?.security?.public?.path_allowlist || {}),
)
const availableAllowMethods = computed(() =>
  ALLOW_METHODS.filter((m) => !allowlistMethods.value.includes(m)),
)

function addAllowMethod() {
  const method = newAllowMethod.value
  if (!method || !config.value?.security) return
  config.value.security.public.path_allowlist[method] = []
  newAllowMethod.value = ''
}

function removeAllowMethod(method: string) {
  if (!config.value?.security) return
  delete config.value.security.public.path_allowlist[method]
}

function setAllowPatterns(method: string, patterns: string[]) {
  if (!config.value?.security) return
  config.value.security.public.path_allowlist[method] = patterns
}

// config.yaml stores bytes; the form edits MiB.
const securityBodyLimitMb = computed({
  get: (): number => {
    const bytes = config.value?.security?.max_body_bytes
    return bytes && bytes > 0 ? Math.round((bytes / MIB) * 100) / 100 : 32
  },
  set: (v: number | string | null | undefined) => {
    if (!config.value?.security) return
    const n = Number(v)
    config.value.security.max_body_bytes =
      Number.isFinite(n) && n > 0 ? Math.round(n * MIB) : DEFAULT_BODY_BYTES
  },
})

const saveConfirmText = ref(t('settings.saveConfirmBody'))

function openSave() {
  segmentErrors.value = {}
  saveError.value = ''
  savedOk.value = false
  savedTab.value = ''
  // Service and Security replace a whole infrastructure/policy node whose changes
  // need a restart; make the confirmation name what is actually at stake.
  saveConfirmText.value =
    activeTab.value === 'service'
      ? t('settings.server.confirmBody')
      : activeTab.value === 'security'
        ? t('settings.security.confirmBody')
        : t('settings.saveConfirmBody')
  confirmOpen.value = true
}

async function doSave() {
  confirmOpen.value = false
  if (!config.value) return
  saving.value = true
  saveError.value = ''
  segmentErrors.value = {}
  savedOk.value = false
  try {
    normalizeBeforeSave(config.value)
    await api.configSave(config.value)
    savedOk.value = true
    savedTab.value = activeTab.value
  } catch (e) {
    if (statusOf(e) === 422) {
      const body = errorResponseBody<{ detail?: { segments?: Record<string, string> } }>(e)
      const segs = body?.detail?.segments
      if (segs && Object.keys(segs).length) {
        segmentErrors.value = segs
        saveError.value = t('settings.saveValidationFailed')
        const order = ['server', 'security', 'upstreams', 'rerank', 'systemone', 'websearch']
        const hit = order.find((s) => s in segs)
        if (hit) activeTab.value = TAB_OF_SEGMENT[hit] ?? hit
      } else {
        saveError.value = errorDetail(e)
      }
    } else {
      saveError.value = errorDetail(e)
    }
  } finally {
    saving.value = false
  }
}

// --------------------------------------------------------------------------- //
// General: delete request-log rows older than a date (default: one month ago).
// The backend owns the cutoff semantics (UTC midnight, strict "ts < before"), so
// the console only ever sends a plain YYYY-MM-DD and shows the normalised value
// it got back. A preview is mandatory before the destructive call; changing the
// date invalidates it so a stale count can never appear in the confirm dialog.
// --------------------------------------------------------------------------- //
function toDateInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/** Same calendar day one month back, clamped so 31 Mar -> 28/29 Feb, not 3 Mar. */
function oneMonthAgo(): string {
  const now = new Date()
  const day = now.getDate()
  const d = new Date(now.getFullYear(), now.getMonth(), 1)
  d.setMonth(d.getMonth() - 1)
  const lastDay = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate()
  d.setDate(Math.min(day, lastDay))
  return toDateInput(d)
}

function disableFuture(d: Date): boolean {
  return d.getTime() > Date.now()
}

function fmtBytes(n: number | null | undefined): string {
  if (!n || n < 0) return '—'
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v.toFixed(v >= 10 ? 0 : 1)} ${units[i]}`
}

function fmtTs(v: string | null): string {
  return v ? v.replace('T', ' ') : '—'
}

const cleanupBefore = ref(oneMonthAgo())
const cleanupVacuum = ref(false)
const cleanupPreview = ref<RequestCleanupPreview | null>(null)
const cleanupResult = ref<RequestCleanupResult | null>(null)
const cleanupPreviewing = ref(false)
const cleanupRunning = ref(false)
const cleanupError = ref('')
const cleanupNotice = ref('')
const cleanupConfirmOpen = ref(false)

watch(cleanupBefore, () => {
  cleanupPreview.value = null
  cleanupResult.value = null
  cleanupError.value = ''
  cleanupNotice.value = ''
})

function resetCleanupDefault() {
  cleanupBefore.value = oneMonthAgo()
}

function cleanupSummary(): string {
  const p = cleanupPreview.value
  if (!p) return t('settings.cleanup.noPreview')
  if (!p.count) return t('settings.cleanup.summaryEmpty')
  return t('settings.cleanup.summary', {
    count: p.count,
    total: p.total,
    oldest: fmtTs(p.oldest),
    newest: fmtTs(p.newest),
  })
}

async function doPreview() {
  cleanupError.value = ''
  cleanupNotice.value = ''
  cleanupResult.value = null
  if (!cleanupBefore.value) {
    cleanupError.value = t('settings.cleanup.invalidDate')
    return
  }
  cleanupPreviewing.value = true
  try {
    const { data } = await api.requestCleanupPreview(cleanupBefore.value)
    cleanupPreview.value = data
  } catch (e) {
    cleanupPreview.value = null
    cleanupError.value = errorDetail(e)
  } finally {
    cleanupPreviewing.value = false
  }
}

async function openCleanup() {
  cleanupError.value = ''
  cleanupNotice.value = ''
  cleanupResult.value = null
  if (!cleanupBefore.value) {
    cleanupError.value = t('settings.cleanup.invalidDate')
    return
  }
  if (!cleanupPreview.value) {
    await doPreview()
    if (!cleanupPreview.value) return
  }
  if (!cleanupPreview.value.count) {
    cleanupNotice.value = t('settings.cleanup.doneNoop')
    return
  }
  cleanupConfirmOpen.value = true
}

async function doCleanup() {
  cleanupConfirmOpen.value = false
  cleanupRunning.value = true
  cleanupError.value = ''
  try {
    const { data } = await api.requestCleanup(cleanupBefore.value, cleanupVacuum.value)
    cleanupResult.value = data
    // Refresh the preview without clearing the success message: the count should
    // now be zero for the same cutoff, which confirms the delete took effect. A
    // failed refresh must not turn a successful delete into an error banner.
    try {
      const refreshed = await api.requestCleanupPreview(cleanupBefore.value)
      cleanupPreview.value = refreshed.data
    } catch {
      cleanupPreview.value = null
    }
  } catch (e) {
    cleanupPreview.value = null
    cleanupError.value = errorDetail(e)
  } finally {
    cleanupRunning.value = false
  }
}

onMounted(async () => {
  await load()
  // Pre-count the Requests tab cleanup impact so switching there already shows
  // what is at stake; the Service tab is the default, not the maintenance one.
  if (config.value) await doPreview()
})
</script>

<template>
  <TopBar :title="t('nav.settings')" :sub="t('settings.sub')" />

  <div v-if="loading" class="muted">{{ t('common.loading') }}</div>
  <div v-else-if="loadError" class="warn-strip">⚠ {{ loadError }}</div>

  <template v-else-if="config">
    <div class="tabs" role="tablist">
      <button
        class="tab"
        :class="{ on: activeTab === 'service' }"
        role="tab"
        :aria-selected="activeTab === 'service'"
        @click="activeTab = 'service'"
      >{{ t('settings.tabs.service') }}</button>
      <button
        class="tab"
        :class="{ on: activeTab === 'security' }"
        role="tab"
        :aria-selected="activeTab === 'security'"
        @click="activeTab = 'security'"
      >{{ t('settings.tabs.security') }}</button>
      <button
        class="tab"
        :class="{ on: activeTab === 'general' }"
        role="tab"
        :aria-selected="activeTab === 'general'"
        @click="activeTab = 'general'"
      >{{ t('settings.tabs.general') }}</button>
      <button
        class="tab"
        :class="{ on: activeTab === 'upstream' }"
        role="tab"
        :aria-selected="activeTab === 'upstream'"
        @click="activeTab = 'upstream'"
      >{{ t('settings.tabs.upstream') }}</button>
      <button
        class="tab"
        :class="{ on: activeTab === 'rerank' }"
        role="tab"
        :aria-selected="activeTab === 'rerank'"
        @click="activeTab = 'rerank'"
      >{{ t('settings.tabs.rerank') }}</button>
      <button
        class="tab"
        :class="{ on: activeTab === 'systemone' }"
        role="tab"
        :aria-selected="activeTab === 'systemone'"
        @click="activeTab = 'systemone'"
      >{{ t('settings.tabs.systemone') }}</button>
      <button
        class="tab"
        :class="{ on: activeTab === 'websearch' }"
        role="tab"
        :aria-selected="activeTab === 'websearch'"
        @click="activeTab = 'websearch'"
      >{{ t('settings.tabs.websearch') }}</button>
    </div>

    <!-- Service: the config.yaml server node (saved as a whole, needs a restart) -->
    <div v-show="activeTab === 'service'">
      <p class="fnote security-intro">{{ t('settings.server.hint') }}</p>

      <div class="card fform">
        <div class="card-h">{{ t('settings.server.listener.title') }}</div>
        <p class="fnote">{{ t('settings.server.listener.hint') }}</p>
        <div class="frow">
          <label>{{ t('settings.server.host') }}</label>
          <div class="fctl col">
            <input class="inp" v-model="cfg.server.host" style="width: 260px" />
            <p class="fnote">{{ t('settings.server.hostHint') }}</p>
          </div>
        </div>
        <div class="frow">
          <label>{{ t('settings.server.port') }}</label>
          <div class="fctl col">
            <input class="inp" type="number" min="1" max="65535" step="1" v-model="cfg.server.port" style="width: 220px" />
            <p class="fnote">{{ t('settings.server.portHint') }}</p>
          </div>
        </div>
        <div class="frow">
          <label>{{ t('settings.server.logLevel') }}</label>
          <div class="fctl col">
            <select class="sel" v-model="cfg.server.log_level" style="width: 220px">
              <option v-for="lv in LOG_LEVELS" :key="lv" :value="lv">{{ lv }}</option>
            </select>
            <p class="fnote">{{ t('settings.server.logLevelHint') }}</p>
          </div>
        </div>
        <div class="frow">
          <label>{{ t('settings.server.requestTimeout') }}</label>
          <div class="fctl col">
            <input class="inp" type="number" min="1" step="1" v-model="cfg.server.request_timeout" style="width: 220px" />
            <p class="fnote">{{ t('settings.server.requestTimeoutHint') }}</p>
          </div>
        </div>
      </div>

      <div class="card fform">
        <div class="card-h">{{ t('settings.server.inference.title') }}</div>
        <p class="fnote">{{ t('settings.server.inference.hint') }}</p>
        <div class="frow">
          <label>{{ t('settings.server.maxConcurrency') }}</label>
          <div class="fctl col">
            <input class="inp" type="number" min="1" step="1" v-model="cfg.server.max_concurrency" style="width: 220px" />
            <p class="fnote">{{ t('settings.server.maxConcurrencyHint') }}</p>
          </div>
        </div>
        <div class="frow">
          <label>{{ t('settings.server.queueTimeout') }}</label>
          <div class="fctl col">
            <input class="inp" type="number" min="1" step="1" v-model="cfg.server.upstream_queue_timeout" style="width: 220px" />
            <p class="fnote">{{ t('settings.server.queueTimeoutHint') }}</p>
          </div>
        </div>
        <div class="frow">
          <label>{{ t('settings.server.keepaliveExpiry') }}</label>
          <div class="fctl col">
            <input class="inp" type="number" min="1" step="1" v-model="cfg.server.upstream_keepalive_expiry" style="width: 220px" />
            <p class="fnote">{{ t('settings.server.keepaliveExpiryHint') }}</p>
          </div>
        </div>
      </div>

      <el-alert
        v-if="savedOk && savedTab === 'service' && activeTab === 'service'"
        type="info"
        :closable="false"
        show-icon
        :title="t('settings.server.savedRestart')"
      />
    </div>

    <!-- Requests: console maintenance, not a config.yaml editor -->
    <div v-show="activeTab === 'general'">
      <div class="card cleanup-card">
        <div class="card-h">
          {{ t('settings.cleanup.title') }}
          <span class="tag">{{ t('settings.cleanup.tag') }}</span>
        </div>
        <p class="fnote cleanup-intro">{{ t('settings.cleanup.intro') }}</p>

        <div class="frow">
          <label for="cleanup-before">{{ t('settings.cleanup.cutoff') }}</label>
          <div class="fctl col">
            <div class="cleanup-row">
              <el-date-picker
                id="cleanup-before"
                v-model="cleanupBefore"
                type="date"
                format="YYYY-MM-DD"
                value-format="YYYY-MM-DD"
                :clearable="false"
                :disabled-date="disableFuture"
                style="width: 200px"
              />
              <button class="btn" type="button" @click="resetCleanupDefault">
                {{ t('settings.cleanup.resetDefault') }}
              </button>
              <span class="chip note">{{ t('settings.cleanup.defaultLabel') }}</span>
            </div>
            <p class="fnote">{{ t('settings.cleanup.cutoffHint') }}</p>
          </div>
        </div>

        <div class="frow">
          <label for="cleanup-vacuum">{{ t('settings.cleanup.vacuum') }}</label>
          <div class="fctl col">
            <label class="checkline" for="cleanup-vacuum">
              <input id="cleanup-vacuum" type="checkbox" v-model="cleanupVacuum" />
              <span>{{ t('settings.cleanup.vacuum') }}</span>
            </label>
            <p class="fnote">{{ t('settings.cleanup.vacuumHint') }}</p>
          </div>
        </div>

        <div class="cleanup-actions">
          <button class="btn" type="button" :disabled="cleanupPreviewing || cleanupRunning" @click="doPreview">
            {{ cleanupPreviewing ? t('settings.cleanup.previewing') : t('settings.cleanup.preview') }}
          </button>
          <button class="btn danger" type="button" :disabled="cleanupPreviewing || cleanupRunning" @click="openCleanup">
            {{ cleanupRunning ? t('settings.cleanup.cleaning') : t('settings.cleanup.clean') }}
          </button>
          <span v-if="cleanupPreview" class="cleanup-summary">
            {{ cleanupSummary() }}
            <span class="chip note">{{ t('settings.cleanup.dbSize', { size: fmtBytes(cleanupPreview.db_bytes) }) }}</span>
          </span>
          <span v-else class="fnote">{{ t('settings.cleanup.noPreview') }}</span>
        </div>

        <el-alert
          v-if="cleanupResult"
          type="success"
          :closable="false"
          show-icon
          :title="cleanupResult.deleted
            ? t('settings.cleanup.done', { count: cleanupResult.deleted, remaining: cleanupResult.remaining })
            : t('settings.cleanup.doneNoop')"
        >
          <div>
            {{ t('settings.cleanup.afterSize', {
              before: fmtBytes(cleanupResult.db_bytes_before),
              after: fmtBytes(cleanupResult.db_bytes_after),
            }) }}
          </div>
        </el-alert>
        <el-alert
          v-if="cleanupNotice"
          type="info"
          :closable="false"
          show-icon
          :title="cleanupNotice"
        />
        <el-alert
          v-if="cleanupError"
          type="error"
          :closable="false"
          show-icon
          :title="cleanupError"
        />
      </div>
    </div>

    <!-- Security: the config.yaml security node (saved as a whole) -->
    <div v-show="activeTab === 'security'">
      <p class="fnote security-intro">{{ t('settings.security.hint') }}</p>

      <div class="card fform">
        <div class="card-h">{{ t('settings.security.internal.title') }}</div>
        <p class="fnote">{{ t('settings.security.internal.hint') }}</p>
        <div class="frow">
          <label>{{ t('settings.security.internal.allowAnonymous') }}</label>
          <div class="fctl col">
            <label class="switch">
              <input type="checkbox" v-model="cfg.security.internal.allow_anonymous" />
              <span class="slider"></span>
            </label>
            <p class="fnote">{{ t('settings.security.internal.allowAnonymousHint') }}</p>
          </div>
        </div>
        <div class="frow">
          <label>{{ t('settings.security.internal.exposeVersion') }}</label>
          <div class="fctl col">
            <label class="switch">
              <input type="checkbox" v-model="cfg.security.internal.expose_version" />
              <span class="slider"></span>
            </label>
            <p class="fnote">{{ t('settings.security.internal.exposeVersionHint') }}</p>
          </div>
        </div>
        <div class="frow">
          <label>{{ t('settings.security.internal.exposeDocs') }}</label>
          <div class="fctl col">
            <label class="switch">
              <input type="checkbox" v-model="cfg.security.internal.expose_docs" />
              <span class="slider"></span>
            </label>
            <p class="fnote">{{ t('settings.security.internal.exposeDocsHint') }}</p>
          </div>
        </div>
      </div>

      <div class="card fform">
        <div class="card-h">{{ t('settings.security.publicZone.title') }}</div>
        <p class="fnote">{{ t('settings.security.publicZone.hint') }}</p>
        <div class="frow">
          <label>{{ t('settings.security.publicZone.port') }}</label>
          <div class="fctl col">
            <input
              class="inp"
              type="number"
              min="1"
              max="65535"
              step="1"
              v-model="cfg.security.public.port"
              :placeholder="t('settings.security.publicZone.portPlaceholder')"
              style="width: 220px"
            />
            <p class="fnote">{{ t('settings.security.publicZone.portHint') }}</p>
          </div>
        </div>
        <div class="frow">
          <label>{{ t('settings.security.publicZone.adminEndpoints') }}</label>
          <div class="fctl col">
            <select class="sel" v-model="cfg.security.public.admin_endpoints" style="width: 460px">
              <option v-for="m in ADMIN_ENDPOINTS" :key="m" :value="m">
                {{ t(`settings.security.publicZone.admin.${m}`) }}
              </option>
            </select>
            <p class="fnote">{{ t('settings.security.publicZone.adminEndpointsHint') }}</p>
          </div>
        </div>
        <div class="frow">
          <label>{{ t('settings.security.publicZone.allowlist') }}</label>
          <div class="fctl col">
            <div v-if="allowlistMethods.length" class="allow-rows">
              <div v-for="method in allowlistMethods" :key="method" class="allow-row">
                <span class="chip method">{{ method }}</span>
                <TagListEditor
                  :model-value="cfg.security.public.path_allowlist[method]"
                  :placeholder="t('settings.security.publicZone.patterns')"
                  @update:model-value="(v) => setAllowPatterns(method, v)"
                />
                <button
                  class="btn ghost"
                  type="button"
                  :title="t('settings.security.publicZone.removeMethod')"
                  @click="removeAllowMethod(method)"
                >✕</button>
              </div>
            </div>
            <p v-else class="fnote warn">{{ t('settings.security.publicZone.noMethods') }}</p>
            <div class="allow-add">
              <select class="sel" v-model="newAllowMethod" style="width: 160px">
                <option value="">{{ t('settings.security.publicZone.addMethod') }}</option>
                <option v-for="m in availableAllowMethods" :key="m" :value="m">{{ m }}</option>
              </select>
              <button class="btn" type="button" :disabled="!newAllowMethod" @click="addAllowMethod">
                + {{ t('settings.security.publicZone.addMethod') }}
              </button>
            </div>
            <p class="fnote">{{ t('settings.security.publicZone.allowlistHint') }}</p>
          </div>
        </div>
      </div>

      <div class="card fform">
        <div class="card-h">{{ t('settings.security.shared.title') }}</div>
        <div class="frow">
          <label>{{ t('settings.security.shared.blockedPaths') }}</label>
          <div class="fctl col">
            <TagListEditor v-model="cfg.security.blocked_paths" :placeholder="t('settings.tagPlaceholder')" />
            <p class="fnote">{{ t('settings.security.shared.blockedPathsHint') }}</p>
          </div>
        </div>
        <div class="frow">
          <label>{{ t('settings.security.shared.blockedIps') }}</label>
          <div class="fctl col">
            <TagListEditor v-model="cfg.security.blocked_ips" :placeholder="t('settings.tagPlaceholder')" />
            <p class="fnote">{{ t('settings.security.shared.blockedIpsHint') }}</p>
          </div>
        </div>
        <div class="frow">
          <label>{{ t('settings.security.shared.corsOrigins') }}</label>
          <div class="fctl col">
            <TagListEditor v-model="cfg.security.cors_origins" :placeholder="t('settings.tagPlaceholder')" />
            <p class="fnote">{{ t('settings.security.shared.corsOriginsHint') }}</p>
          </div>
        </div>
        <div class="frow">
          <label>{{ t('settings.security.shared.bodyLimit') }}</label>
          <div class="fctl col">
            <input class="inp" type="number" min="1" step="1" v-model="securityBodyLimitMb" style="width: 220px" />
            <p class="fnote">{{ t('settings.security.shared.bodyLimitHint') }}</p>
          </div>
        </div>
        <div class="frow">
          <label>{{ t('settings.security.shared.rateLimit') }}</label>
          <div class="fctl col">
            <label class="switch">
              <input type="checkbox" v-model="cfg.security.rate_limit.enabled" />
              <span class="slider"></span>
            </label>
            <p class="fnote">{{ t('settings.security.shared.rateLimitHint') }}</p>
          </div>
        </div>
        <div class="frow">
          <label>{{ t('settings.security.shared.perMinute') }}</label>
          <div class="fctl">
            <input class="inp" type="number" min="1" step="1" v-model.number="cfg.security.rate_limit.per_minute" style="width: 220px" />
          </div>
        </div>
        <div class="frow">
          <label>{{ t('settings.security.shared.burst') }}</label>
          <div class="fctl">
            <input class="inp" type="number" min="1" step="1" v-model.number="cfg.security.rate_limit.burst" style="width: 220px" />
          </div>
        </div>
        <div class="frow">
          <label>{{ t('settings.security.shared.by') }}</label>
          <div class="fctl">
            <select class="sel" v-model="cfg.security.rate_limit.by" style="width: 180px">
              <option value="ip">{{ t('settings.security.shared.byIp') }}</option>
              <option value="key">{{ t('settings.security.shared.byKey') }}</option>
            </select>
          </div>
        </div>
      </div>

      <el-alert
        v-if="cfg.security.blocked_ips.length"
        type="warning"
        :closable="false"
        show-icon
        :title="t('settings.security.shared.selfLockWarning')"
      />
      <el-alert
        v-if="savedOk && savedTab === 'security' && activeTab === 'security'"
        type="info"
        :closable="false"
        show-icon
        :title="t('settings.security.savedRestart')"
      />
    </div>

    <!-- Upstream -->
    <div v-show="activeTab === 'upstream'">
      <div class="seg-actions">
        <button class="btn primary" type="button" @click="addUpstream">{{ t('settings.addUpstream') }}</button>
      </div>
      <p v-if="!cfg.upstreams.length" class="muted">{{ t('settings.noUpstreams') }}</p>
      <UpstreamCard
        v-for="(up, i) in cfg.upstreams"
        :key="i"
        :upstream="up"
        :index="i"
        @remove="removeUpstream"
      />
    </div>

    <!-- Rerank -->
    <div v-show="activeTab === 'rerank'">
      <div class="card fform">
        <div class="frow">
          <label>{{ fld('enabled') }}</label>
          <div class="fctl">
            <label class="switch">
              <input type="checkbox" v-model="cfg.rerank.enabled" />
              <span class="slider"></span>
            </label>
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('endpoint') }}</label>
          <div class="fctl"><input class="inp w-full" v-model="cfg.rerank.endpoint" /></div>
        </div>
        <div class="frow">
          <label>{{ fld('mode') }}</label>
          <div class="fctl">
            <select class="sel" v-model="cfg.rerank.mode" style="width: 220px">
              <option v-for="m in RERANK_MODES" :key="m" :value="m">{{ m }}</option>
            </select>
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('models') }}</label>
          <div class="fctl">
            <TagListEditor v-model="cfg.rerank.models" :placeholder="t('settings.tagPlaceholder')" />
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('instruction') }}</label>
          <div class="fctl">
            <textarea class="inp w-full" rows="4" v-model="cfg.rerank.instruction"></textarea>
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('yes_token') }}</label>
          <div class="fctl"><input class="inp" v-model="cfg.rerank.yes_token" style="width: 220px" /></div>
        </div>
        <div class="frow">
          <label>{{ fld('no_token') }}</label>
          <div class="fctl"><input class="inp" v-model="cfg.rerank.no_token" style="width: 220px" /></div>
        </div>
        <div class="frow">
          <label>{{ fld('temperature') }}</label>
          <div class="fctl">
            <input class="inp" type="number" step="0.1" v-model.number="cfg.rerank.temperature" style="width: 220px" />
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('top_logprobs') }}</label>
          <div class="fctl">
            <input class="inp" type="number" min="1" step="1" v-model.number="cfg.rerank.top_logprobs" style="width: 220px" />
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('max_tokens') }}</label>
          <div class="fctl">
            <input class="inp" type="number" min="1" step="1" v-model.number="cfg.rerank.max_tokens" style="width: 220px" />
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('token_scan_depth') }}</label>
          <div class="fctl">
            <input class="inp" type="number" min="1" step="1" v-model.number="cfg.rerank.token_scan_depth" style="width: 220px" />
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('stop') }}</label>
          <div class="fctl">
            <TagListEditor v-model="cfg.rerank.stop" :placeholder="t('settings.tagPlaceholder')" />
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('keep_alive') }}</label>
          <div class="fctl">
            <input class="inp" v-model="cfg.rerank.keep_alive" placeholder="e.g. 3m / -1 / 0" style="width: 220px" />
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('normalize') }}</label>
          <div class="fctl">
            <label class="switch">
              <input type="checkbox" v-model="cfg.rerank.normalize" />
              <span class="slider"></span>
            </label>
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('return_documents') }}</label>
          <div class="fctl">
            <label class="switch">
              <input type="checkbox" v-model="cfg.rerank.return_documents" />
              <span class="slider"></span>
            </label>
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('on_error') }}</label>
          <div class="fctl">
            <select class="sel" v-model="cfg.rerank.on_error" style="width: 220px">
              <option v-for="o in ON_ERROR_OPTIONS" :key="o" :value="o">{{ o }}</option>
            </select>
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('upstream_api') }}</label>
          <div class="fctl">
            <select class="sel" v-model="cfg.rerank.upstream_api" style="width: 220px">
              <option v-for="o in UPSTREAM_API_OPTIONS" :key="o" :value="o">{{ o }}</option>
            </select>
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('max_concurrency') }}</label>
          <div class="fctl">
            <input class="inp" type="number" min="1" step="1" v-model.number="cfg.rerank.max_concurrency" style="width: 220px" />
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('disable_thinking') }}</label>
          <div class="fctl">
            <label class="switch">
              <input type="checkbox" v-model="cfg.rerank.disable_thinking" />
              <span class="slider"></span>
            </label>
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('options') }}</label>
          <div class="fctl">
            <KeyValueEditor v-model="cfg.rerank.options" placeholder-key="key" placeholder-value="value" />
          </div>
        </div>
      </div>
    </div>

    <!-- System One -->
    <div v-show="activeTab === 'systemone'">
      <div class="card fform">
        <div class="frow">
          <label>{{ fld('enabled') }}</label>
          <div class="fctl">
            <label class="switch">
              <input type="checkbox" v-model="cfg.systemone.enabled" />
              <span class="slider"></span>
            </label>
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('backend') }}</label>
          <div class="fctl">
            <input class="inp w-full" v-model="cfg.systemone.backend" placeholder="http://host:port" />
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('model') }}</label>
          <div class="fctl"><input class="inp w-full" v-model="cfg.systemone.model" /></div>
        </div>
        <div class="frow">
          <label>{{ fld('api_key_env') }}</label>
          <div class="fctl"><input class="inp w-full" v-model="cfg.systemone.api_key_env" /></div>
        </div>
        <div class="frow">
          <label>{{ fld('path') }}</label>
          <div class="fctl"><input class="inp w-full" v-model="cfg.systemone.path" /></div>
        </div>
        <div class="frow">
          <label>{{ fld('timeout') }}</label>
          <div class="fctl">
            <input class="inp" type="number" step="1" v-model.number="cfg.systemone.timeout" style="width: 220px" />
          </div>
        </div>
      </div>
    </div>

    <!-- Web Search -->
    <div v-show="activeTab === 'websearch'">
      <div class="card fform">
        <div class="frow">
          <label>{{ fld('enabled') }}</label>
          <div class="fctl">
            <label class="switch">
              <input type="checkbox" v-model="cfg.websearch.enabled" />
              <span class="slider"></span>
            </label>
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('upstream') }}</label>
          <div class="fctl col">
            <select class="sel" v-model="cfg.websearch.upstream" style="width: 260px">
              <option value="">{{ t('settings.upstreamAuto') }}</option>
              <option
                v-if="
                  cfg.websearch.upstream &&
                  !opencodeUpstreams.some((u) => u.name === cfg.websearch.upstream)
                "
                :value="cfg.websearch.upstream"
              >
                {{ cfg.websearch.upstream }} — {{ t('settings.upstreamMissing') }}
              </option>
              <option v-for="up in opencodeUpstreams" :key="up.name" :value="up.name">
                {{ up.name }}
              </option>
            </select>
            <p v-if="!opencodeUpstreams.length" class="fnote warn">
              {{ t('settings.noOpencodeUpstream') }}
            </p>
          </div>
        </div>
        <div class="frow">
          <label>{{ fld('path') }}</label>
          <div class="fctl"><input class="inp w-full" v-model="cfg.websearch.path" /></div>
        </div>
        <div class="frow">
          <label>{{ fld('api_key_env') }}</label>
          <div class="fctl"><input class="inp w-full" v-model="cfg.websearch.api_key_env" /></div>
        </div>
        <div class="frow">
          <label>{{ fld('timeout') }}</label>
          <div class="fctl">
            <input class="inp" type="number" step="1" v-model.number="cfg.websearch.timeout" style="width: 220px" />
          </div>
        </div>
      </div>
    </div>

    <div v-show="activeTab !== 'general'" class="save-bar">
      <button class="btn primary" type="button" @click="openSave">{{ t('settings.save') }}</button>
      <span v-if="savedOk" class="ok-tag">✓ {{ t('settings.saveOk') }}</span>
    </div>

    <el-alert
      v-if="saveError && activeTab !== 'general'"
      type="error"
      :closable="false"
      show-icon
      :title="saveError"
    />
    <el-alert
      v-if="Object.keys(segmentErrors).length && activeTab !== 'general'"
      type="error"
      :closable="false"
      show-icon
      :title="t('settings.saveValidationFailed')"
    >
      <ul class="seg-err">
        <li v-for="(msg, seg) in segmentErrors" :key="seg">
          <b>{{ tabLabel(seg) }}</b>: {{ msg }}
        </li>
      </ul>
    </el-alert>
  </template>

  <el-dialog
    v-model="confirmOpen"
    :title="t('settings.saveConfirmTitle')"
    width="460px"
  >
    <p>{{ saveConfirmText }}</p>
    <template #footer>
      <button class="btn" type="button" @click="confirmOpen = false">{{ t('common.cancel') }}</button>
      <button class="btn danger" type="button" :disabled="saving" @click="doSave">{{ t('settings.saveConfirmYes') }}</button>
    </template>
  </el-dialog>

  <el-dialog
    v-model="cleanupConfirmOpen"
    :title="t('settings.cleanup.confirmTitle')"
    width="460px"
  >
    <p>{{ t('settings.cleanup.confirmBody', { count: cleanupPreview?.count ?? 0, date: cleanupBefore }) }}</p>
    <template #footer>
      <button class="btn" type="button" @click="cleanupConfirmOpen = false">{{ t('common.cancel') }}</button>
      <button class="btn danger" type="button" :disabled="cleanupRunning" @click="doCleanup">
        {{ t('settings.cleanup.confirmYes') }}
      </button>
    </template>
  </el-dialog>
</template>

<style scoped>
.seg-actions {
  margin: 8px 0;
}
.save-bar {
  margin: 14px 0 6px;
  display: flex;
  align-items: center;
  gap: 12px;
}
.ok-tag {
  color: var(--ok, #2e9e5b);
  font-weight: 600;
}
.seg-err {
  margin: 4px 0 0;
  padding-left: 18px;
}
.seg-err li {
  margin: 2px 0;
}
.muted {
  color: var(--muted, #8a8f98);
}
.cleanup-card .frow {
  margin-bottom: 20px;
}
/* Stacked form layout: give adjacent config cards in the same tab breathing room.
   Scoped to .fform so it does not stack on top of UpstreamCard's own margin. */
.card.fform + .card.fform {
  margin-top: 16px;
}
.cleanup-intro {
  margin: 0 0 18px;
  max-width: 900px;
}
.cleanup-row,
.cleanup-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.cleanup-actions {
  margin: 18px 0 14px;
}
.cleanup-summary {
  font-size: 12px;
  color: var(--text-dim);
}
.checkline {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  font-size: 12px;
}
.cleanup-card :deep(.el-alert) {
  margin-top: 10px;
}
.security-intro {
  margin: 0 0 14px;
  max-width: 960px;
}
.allow-rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}
.allow-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.allow-row :deep(.tag-editor) {
  flex: 1 1 auto;
}
.allow-row .chip.method {
  flex: 0 0 72px;
  text-align: center;
}
.allow-add {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
</style>
