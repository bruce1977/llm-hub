<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import KeyValueEditor from '@/components/KeyValueEditor.vue'
import TagListEditor from '@/components/TagListEditor.vue'
import TopBar from '@/components/TopBar.vue'
import UpstreamCard from '@/components/UpstreamCard.vue'
import { api } from '@/api/management'
import type { ConfigPayload, ConfigUpstream } from '@/types/api'
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
const segmentErrors = ref<Record<string, string>>({})
const activeTab = ref('upstream')
const confirmOpen = ref(false)

const RERANK_MODES = ['logprobs']
const ON_ERROR_OPTIONS = ['score_zero', 'fail']
const UPSTREAM_API_OPTIONS = ['auto', 'generate', 'chat']

// Web search is proxied through an `opencode` upstream; only those are valid choices.
// An empty value means "the first opencode upstream" (backend fallback, see WebSearchConfig).
const opencodeUpstreams = computed<ConfigUpstream[]>(() =>
  (config.value?.upstreams || []).filter((u) => u.type === 'opencode' && !!u.name),
)

function fld(key: string): string {
  return t(`settings.fields.${key}`)
}

function tabLabel(seg: string): string {
  const map: Record<string, string> = {
    upstreams: t('settings.tabs.upstream'),
    rerank: t('settings.tabs.rerank'),
    systemone: t('settings.tabs.systemone'),
    websearch: t('settings.tabs.websearch'),
  }
  return map[seg] ?? seg
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
}

function openSave() {
  segmentErrors.value = {}
  saveError.value = ''
  savedOk.value = false
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
  } catch (e) {
    if (statusOf(e) === 422) {
      const body = errorResponseBody<{ detail?: { segments?: Record<string, string> } }>(e)
      const segs = body?.detail?.segments
      if (segs && Object.keys(segs).length) {
        segmentErrors.value = segs
        saveError.value = t('settings.saveValidationFailed')
        const order = ['upstreams', 'rerank', 'systemone', 'websearch']
        const hit = order.find((s) => s in segs)
        if (hit) {
          activeTab.value =
            hit === 'upstreams'
              ? 'upstream'
              : hit === 'rerank'
                ? 'rerank'
                : hit === 'systemone'
                  ? 'systemone'
                  : 'websearch'
        }
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

onMounted(load)
</script>

<template>
  <TopBar :title="t('nav.settings')" :sub="t('settings.sub')" />

  <div v-if="loading" class="muted">{{ t('common.loading') }}</div>
  <div v-else-if="loadError" class="warn-strip">⚠ {{ loadError }}</div>

  <template v-else-if="config">
    <div class="tabs" role="tablist">
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

    <div class="save-bar">
      <button class="btn primary" type="button" @click="openSave">{{ t('settings.save') }}</button>
      <span v-if="savedOk" class="ok-tag">✓ {{ t('settings.saveOk') }}</span>
    </div>

    <el-alert
      v-if="saveError"
      type="error"
      :closable="false"
      show-icon
      :title="saveError"
    />
    <el-alert
      v-if="Object.keys(segmentErrors).length"
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
    <p>{{ t('settings.saveConfirmBody') }}</p>
    <template #footer>
      <button class="btn" type="button" @click="confirmOpen = false">{{ t('common.cancel') }}</button>
      <button class="btn danger" type="button" :disabled="saving" @click="doSave">{{ t('settings.saveConfirmYes') }}</button>
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
</style>
