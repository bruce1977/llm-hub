<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { errorDetail, statusOf } from '@/api/client'
import { api } from '@/api/management'
import ApiDoc from '@/components/ApiDoc.vue'
import ConfigCard from '@/components/ConfigCard.vue'
import TopBar from '@/components/TopBar.vue'
import type { ModelsRerank } from '@/types/api'
import type { ApiParam, ConfigRow } from '@/types/ui'
import { fmtClock } from '@/utils/format'

type Tab = 'models' | 'api'

const { t } = useI18n()

const cfg = ref<ModelsRerank | null>(null)
const tab = ref<Tab>('models')
const lastUpdated = ref('')
const loadError = ref('')
const refreshing = ref(false)

const rows = computed<ConfigRow[]>(() => {
  const c = cfg.value
  if (!c) return []
  return [
    { label: 'Endpoint', value: c.endpoint, mono: true },
    {
      label: 'Enabled',
      badge: c.enabled ? { cls: 'ok', text: t('modelsRerank.enabled') } : { cls: 'idle', text: t('modelsRerank.disabled') },
    },
    { label: t('modelsRerank.rows.mode'), value: c.mode, mono: true },
    {
      label: t('modelsRerank.rows.upstreamApi'),
      value: c.upstream_api,
      mono: true,
      note: t('modelsRerank.rows.upstreamApiNote'),
    },
    { label: t('modelsRerank.rows.onError'), value: c.on_error, mono: true },
    { label: 'keep_alive', value: c.scoring?.keep_alive, mono: true, note: t('modelsRerank.rows.keepAliveNote') },
    { label: 'top_logprobs', value: c.scoring?.top_logprobs, mono: true },
    { label: 'temperature', value: c.scoring?.temperature, mono: true },
    { label: 'max_tokens', value: c.scoring?.max_tokens, mono: true },
    { label: t('modelsRerank.rows.concurrency'), value: c.limits?.max_concurrency, mono: true },
    { label: t('modelsRerank.rows.maxDocuments'), value: c.limits?.max_documents || t('modelsRerank.rows.unlimited'), mono: true },
    {
      label: t('modelsRerank.rows.maxDocumentChars'),
      value: c.limits?.max_document_chars || t('modelsRerank.rows.unlimited'),
      mono: true,
    },
  ]
})

const modelRows = computed<string[]>(() => (cfg.value?.models ?? []).map((m) => m.model))

const params = computed<ApiParam[]>(() => [
  { name: 'model', type: 'string', required: true, desc: t('modelsRerank.params.model') },
  { name: 'query', type: 'string', required: true, desc: t('modelsRerank.params.query') },
  { name: 'documents', type: 'string[]', required: true, desc: t('modelsRerank.params.documents') },
  { name: 'top_n', type: 'integer', required: false, desc: t('modelsRerank.params.topN') },
  { name: 'return_documents', type: 'boolean', required: false, desc: t('modelsRerank.params.returnDocuments') },
  { name: 'options', type: 'object', required: false, desc: t('modelsRerank.params.options') },
])

async function load() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    const { data } = await api.modelsRerank()
    cfg.value = data
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
    :title="t('nav.modelsRerank')"
    :sub="t('modelsRerank.sub')"
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
      {{ t('modelsRerank.tabs.models') }}
      <span class="count">{{ cfg ? cfg.models.length : '—' }}</span>
    </button>
    <button
      class="tab"
      :class="{ on: tab === 'api' }"
      role="tab"
      :aria-selected="tab === 'api'"
      @click="tab = 'api'"
    >
      {{ t('modelsRerank.tabs.api') }}
      <span class="count">1</span>
    </button>
  </div>

  <template v-if="cfg">
    <template v-if="tab === 'models'">
      <div class="card">
        <div class="card-h">{{ t('modelsRerank.introTitle') }} <span class="tag">rerank</span></div>
        <i18n-t keypath="modelsRerank.intro" scope="global" tag="div" class="feature-intro">
          <template #endpoint><code>POST /v1/rerank</code></template>
          <template #query><code>query</code></template>
          <template #documents><code>documents</code></template>
          <template #score><code>results[].relevance_score</code></template>
          <template #topN><code>top_n</code></template>
          <template #returnDocuments><code>return_documents</code></template>
        </i18n-t>
      </div>

      <ConfigCard :title="t('modelsRerank.config.title')" tag="rerank" :rows="rows" />

      <div class="card">
        <div class="card-h">{{ t('modelsRerank.available.title') }} <span class="tag">models</span></div>
        <table>
          <thead>
            <tr>
              <th>{{ t('modelsRerank.available.model') }}</th>
              <th>{{ t('modelsRerank.available.upstream') }}</th>
              <th>{{ t('modelsRerank.available.type') }}</th>
              <th>{{ t('modelsRerank.available.baseUrl') }}</th>
              <th>{{ t('modelsRerank.available.credentials') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in cfg.models" :key="m.model">
              <td class="mono">{{ m.model }}</td>
              <td>{{ m.upstream || '—' }}</td>
              <td><span class="chip">{{ m.type || '—' }}</span></td>
              <td class="mono">{{ m.base_url || '—' }}</td>
              <td class="mono">{{ m.api_key || t('modelsRerank.notConfigured') }}</td>
            </tr>
            <tr v-if="!cfg.models.length">
              <td colspan="5" class="empty">{{ t('modelsRerank.emptyModels') }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="modelRows.length" class="api-note">
          <i18n-t keypath="modelsRerank.modeNote" scope="global">
            <template #mode><code>mode</code></template>
            <template #modelModes><code>model_modes</code></template>
          </i18n-t>
        </div>
      </div>
    </template>

    <template v-else>
      <ApiDoc
        tag="/v1/rerank"
        :params="params"
        :request="{
          method: 'POST',
          path: cfg.endpoint,
          headers: [['Authorization', 'Bearer <API_KEY>'], ['Content-Type', 'application/json']],
          body: cfg.request_example,
        }"
        :response="{
          status: '200',
          contentType: 'application/json',
          note: t('modelsRerank.docs.responseNote'),
          body: cfg.response_example,
        }"
        :note="t('modelsRerank.docs.note')"
      />
    </template>
  </template>
  <div v-else class="card empty">{{ t('common.loading') }}</div>

  <div class="foot">{{ t('common.footReadOnly', { page: t('nav.modelsRerank') }) }}</div>
</template>

<style scoped>
.api-note code {
  color: var(--text-dim);
}
</style>
