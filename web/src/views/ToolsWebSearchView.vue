<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { errorDetail, statusOf } from '@/api/client'
import { api } from '@/api/management'
import ApiDoc from '@/components/ApiDoc.vue'
import ConfigCard from '@/components/ConfigCard.vue'
import TopBar from '@/components/TopBar.vue'
import type { LlmModel, ToolsWebSearch } from '@/types/api'
import type { ApiParam, ConfigRow } from '@/types/ui'
import { deviceBadge, fmtClock } from '@/utils/format'

type Tab = 'models' | 'api'

const { t } = useI18n()

const cfg = ref<ToolsWebSearch | null>(null)
// The provider is an ordinary upstream, so its models come from the same projection the
// Models · LLM page renders: config.yaml declarations, wildcards expanded, aliases applied.
const llmModels = ref<LlmModel[]>([])
const loaded = ref(false)
const tab = ref<Tab>('models')
const lastUpdated = ref('')
const loadError = ref('')
const refreshing = ref(false)

const models = computed<LlmModel[]>(() => {
  const provider = cfg.value?.upstream
  return provider ? llmModels.value.filter((m) => m.upstream === provider) : []
})

/** A hosted provider has no resident-model API: the probe answering is the only state. */
function stateText(m: LlmModel): string {
  if (m.type === 'opencode') return m.running ? t('common.online') : t('common.offline')
  return m.running ? t('common.running') : t('common.notLoaded')
}

const params = computed<ApiParam[]>(() => [
  { name: 'query', type: 'string', required: true, desc: t('webSearch.params.query') },
  {
    name: 'model',
    type: 'string',
    required: false,
    desc: t('webSearch.params.model'),
  },
  { name: 'top_k', type: 'integer', required: false, desc: t('webSearch.params.topK') },
  { name: 'max_chars_per_page', type: 'integer', required: false, desc: t('webSearch.params.maxCharsPerPage') },
  { name: 'locale', type: 'string', required: false, desc: t('webSearch.params.locale') },
  { name: 'recency', type: 'string', required: false, desc: t('webSearch.params.recency') },
  { name: 'synthesize', type: 'boolean', required: false, desc: t('webSearch.params.synthesize') },
  { name: 'include_sources', type: 'boolean', required: false, desc: t('webSearch.params.includeSources') },
])

const requestExample = {
  query: 'llm-hub 网关 使用教程',
  top_k: 5,
  locale: 'zh-CN',
  recency: 'month',
  synthesize: false,
}

const responseExample = {
  query: 'llm-hub 网关 使用教程',
  model: 'opencode/big-pickle',
  engine: 'opencode-websearch',
  answer: null,
  truncated: true,
  results: [
    {
      title: 'llm-hub 快速开始',
      url: 'https://example.com/doc',
      snippet: 'llm-hub 是统一代理本地模型的网关…',
      paragraphs: ['完整段落文本 …'],
    },
  ],
  text: '所有检索段落拼接，可直接塞进 prompt',
  sources: ['https://example.com/doc'],
}

const rows = computed<ConfigRow[]>(() => {
  const c = cfg.value
  if (!c) return []
  return [
    { label: 'Endpoint', value: c.endpoint, mono: true },
    {
      label: 'Enabled',
      badge: c.enabled ? { cls: 'ok', text: t('webSearch.enabled') } : { cls: 'idle', text: t('webSearch.disabled') },
    },
    {
      label: 'Upstream',
      value: c.upstream,
      mono: true,
      note: c.upstream_type ? t('webSearch.rows.typeNote', { type: c.upstream_type }) : '',
    },
    { label: 'Base URL', value: c.base_url, mono: true, note: t('webSearch.rows.upstreamNote') },
    { label: 'Provider Path', value: c.path, mono: true },
    { label: 'API Key Env', value: c.api_key_env, mono: true },
    { label: t('webSearch.rows.credentials'), value: c.api_key || t('webSearch.rows.notSet'), mono: true },
    { label: 'Timeout', value: `${c.timeout}s`, mono: true },
  ]
})

async function load() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    const [ws, llm] = await Promise.all([api.toolsWebSearch(), api.modelsLlm()])
    cfg.value = ws.data
    llmModels.value = llm.data.models
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
    :title="t('nav.toolsWebSearch')"
    :sub="t('webSearch.sub')"
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
      {{ t('webSearch.tabs.models') }}
      <span class="count">{{ loaded ? models.length : '—' }}</span>
    </button>
    <button
      class="tab"
      :class="{ on: tab === 'api' }"
      role="tab"
      :aria-selected="tab === 'api'"
      @click="tab = 'api'"
    >
      {{ t('webSearch.tabs.api') }}
      <span class="count">1</span>
    </button>
  </div>

  <template v-if="cfg">
    <template v-if="tab === 'models'">
      <div v-if="!cfg.provider_configured" class="warn-strip">
        ⚠
        <i18n-t keypath="webSearch.warnMissing" scope="global">
          <template #upstream><code>websearch.upstream</code></template>
          <template #type><code>type: "opencode"</code></template>
        </i18n-t>
      </div>

      <div class="card">
        <div class="card-h">{{ t('webSearch.introTitle') }} <span class="tag">web search</span></div>
        <i18n-t keypath="webSearch.intro" scope="global" tag="div" class="feature-intro">
          <template #endpoint><code>POST /v1/tools/web-search</code></template>
          <template #query><code>query</code></template>
          <template #topK><code>top_k</code></template>
          <template #locale><code>locale</code></template>
          <template #recency><code>recency</code></template>
          <template #includeSources><code>include_sources</code></template>
          <template #synthesize><code>synthesize: true</code></template>
          <template #text><code>text</code></template>
        </i18n-t>
      </div>

      <ConfigCard :title="t('webSearch.config.title')" tag="web-search" :rows="rows" />

      <div class="card">
        <div class="card-h">
          {{ t('webSearch.available.title') }} <span class="tag">{{ cfg.upstream || 'opencode-port' }}</span>
        </div>
        <div class="api-note">
          <i18n-t keypath="webSearch.providerNote" scope="global">
            <template #port><b>opencode-port</b></template>
            <template #repo
              ><a class="link" href="https://github.com/bruce1977/opencode-port" target="_blank" rel="noreferrer"
                >github.com/bruce1977/opencode-port</a
              ></template
            >
            <template #config><code>config.yaml</code></template>
            <template #typeOpencode><code>type: "opencode"</code></template>
            <template #addr><code>127.0.0.1:10000</code></template>
            <template #websearchUpstream><code>websearch.upstream</code></template>
            <template #wildcard><code>opencode/*</code></template>
            <template #free><code>opencode/*-free</code></template>
            <template #auto><code>auto</code></template>
            <template #ollamaLocal><code>ollama-local/*</code></template>
            <template #model><code>model</code></template>
          </i18n-t>
        </div>
        <table>
          <thead>
            <tr>
              <th>{{ t('webSearch.available.model') }}</th>
              <th>{{ t('webSearch.available.upstream') }}</th>
              <th>{{ t('webSearch.available.type') }}</th>
              <th>{{ t('webSearch.available.device') }}</th>
              <th>{{ t('webSearch.available.state') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in models" :key="`${m.upstream}/${m.model}`">
              <td class="mono" :title="m.alias ? t('common.realModel', { model: m.model }) : undefined">
                {{ m.display }}
                <span v-if="m.wildcard" class="chip s2" :title="t('common.wildcardTip')">wildcard</span>
                <span v-if="m.alias" class="chip s2" :title="t('common.aliasTip')">alias</span>
              </td>
              <td>{{ m.upstream }}</td>
              <td><span class="chip">{{ m.type }}</span></td>
              <td>
                <span class="badge" :class="deviceBadge(m.device, m.device_ratio).cls">{{
                  deviceBadge(m.device, m.device_ratio).text
                }}</span>
              </td>
              <td><span class="badge" :class="m.running ? 'ok' : 'idle'">● {{ stateText(m) }}</span></td>
            </tr>
            <tr v-if="!models.length">
              <td colspan="5" class="empty">
                {{
                  !loaded
                    ? t('webSearch.emptyLoading')
                    : cfg.upstream
                      ? t('webSearch.emptyProvider')
                      : t('webSearch.emptyNone')
                }}
              </td>
            </tr>
          </tbody>
        </table>
        <div class="api-note">
          <i18n-t keypath="webSearch.note2" scope="global">
            <template #models><code>/v1/models</code></template>
            <template #example><code>opencode/big-pickle</code></template>
            <template #wildcard><code>"opencode/*"</code></template>
          </i18n-t>
        </div>
      </div>
    </template>

    <template v-else>
      <ApiDoc
        tag="/v1/tools/web-search"
        :params="params"
        :request="{
          method: 'POST',
          path: cfg.endpoint,
          headers: [
            ['Authorization', 'Bearer <API_KEY>'],
            ['Content-Type', 'application/json'],
          ],
          body: requestExample,
        }"
        :response="{
          status: '200',
          contentType: 'application/json',
          note: t('webSearch.docs.responseNote'),
          body: responseExample,
        }"
        :note="t('webSearch.docs.note')"
      />
    </template>
  </template>
  <div v-else class="card empty">{{ t('common.loading') }}</div>

  <div class="foot">{{ t('common.footReadOnly', { page: t('nav.toolsWebSearch') }) }}</div>
</template>

<style scoped>
.api-note code,
.warn-strip code {
  color: var(--text-dim);
}

.api-note + table {
  margin-top: 10px;
}

.api-note a {
  text-decoration: none;
}
</style>
