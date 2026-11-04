<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { errorDetail, statusOf } from '@/api/client'
import { api } from '@/api/management'
import ApiDoc from '@/components/ApiDoc.vue'
import ConfigCard from '@/components/ConfigCard.vue'
import JsonPane from '@/components/JsonPane.vue'
import TopBar from '@/components/TopBar.vue'
import type { ModelsSystemOne } from '@/types/api'
import type { ApiParam, ConfigRow } from '@/types/ui'
import { fmtClock } from '@/utils/format'

type Tab = 'models' | 'api'

const { t } = useI18n()

const cfg = ref<ModelsSystemOne | null>(null)
const tab = ref<Tab>('models')
const lastUpdated = ref('')
const loadError = ref('')
const refreshing = ref(false)

// System One has a single configured model, but the backend (Ollama 0.40+ / laya-server)
// accepts several decision models selected by the request body's `model` field.
const availableModels = [
  { name: 'tev1:0.8b', kind: 'systemone', endpoint: '/v1/systemone' },
  { name: 'tev1:latest', kind: 'systemone', endpoint: '/v1/systemone' },
  { name: 'nimble:latest', kind: 'systemone', endpoint: '/v1/systemone' },
]

// 后端透传，管理接口不回传响应体，因此响应示例取自 docs/API.md 的实测结构
const responseExample = {
  model: 'laya-rl-agent',
  answers: {
    refund: {
      type: 'noul',
      noul: 0.9937,
      confidence: 0.9937,
      action: { act_probability: 1.0 },
    },
  },
  usage: { input_tokens: 49, output_tokens: 0 },
  routing: { model: 'multilingual', reason: 'explicit model' },
}

const params = computed<ApiParam[]>(() => [
  { name: 'state', type: 'object', required: true, desc: t('modelsSystemOne.params.state') },
  { name: 'questions', type: 'object', required: true, desc: t('modelsSystemOne.params.questions') },
  { name: 'questions[].type', type: 'string', required: true, desc: t('modelsSystemOne.params.questionType') },
  { name: 'questions[].instructions', type: 'string', required: true, desc: t('modelsSystemOne.params.instructions') },
  { name: 'questions[].criteria', type: 'string[] | object', required: false, desc: t('modelsSystemOne.params.criteria') },
  { name: 'model', type: 'string', required: false, desc: t('modelsSystemOne.params.model') },
])

const rows = computed<ConfigRow[]>(() => {
  const c = cfg.value
  if (!c) return []
  return [
    { label: 'Endpoint', value: c.endpoint, mono: true },
    {
      label: 'Enabled',
      badge: c.enabled
        ? { cls: 'ok', text: t('modelsSystemOne.enabled') }
        : { cls: 'idle', text: t('modelsSystemOne.disabled') },
    },
    {
      label: 'Backend',
      value: c.backend,
      mono: true,
      note: c.path ? t('modelsSystemOne.rows.backendNote', { path: c.path }) : '',
    },
    { label: t('modelsSystemOne.rows.owner'), value: c.upstream, mono: true },
    {
      label: t('modelsSystemOne.rows.defaultModel'),
      value: c.linked_model,
      mono: true,
      note: t('modelsSystemOne.rows.defaultModelNote'),
    },
    { label: 'API Key Env', value: c.api_key_env, mono: true },
    { label: t('modelsSystemOne.rows.credentials'), value: c.api_key || t('modelsSystemOne.notConfigured'), mono: true },
    { label: 'Timeout', value: `${c.timeout}s`, mono: true },
  ]
})

async function load() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    const { data } = await api.modelsSystemOne()
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
    :title="t('nav.modelsSystemOne')"
    :sub="t('modelsSystemOne.sub')"
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
      {{ t('modelsSystemOne.tabs.models') }}
      <span class="count">{{ availableModels.length }}</span>
    </button>
    <button
      class="tab"
      :class="{ on: tab === 'api' }"
      role="tab"
      :aria-selected="tab === 'api'"
      @click="tab = 'api'"
    >
      {{ t('modelsSystemOne.tabs.api') }}
      <span class="count">1</span>
    </button>
  </div>

  <template v-if="cfg">
    <template v-if="tab === 'models'">
      <div class="card">
        <div class="card-h">{{ t('modelsSystemOne.introTitle') }} <span class="tag">decision</span></div>
        <i18n-t keypath="modelsSystemOne.intro" scope="global" tag="div" class="feature-intro">
          <template #endpoint><code>POST /v1/systemone</code></template>
          <template #noul><code>noul</code></template>
          <template #choice><code>choice</code></template>
          <template #score><code>score</code></template>
          <template #model><code>model</code></template>
          <template #stream><code>stream: true</code></template>
        </i18n-t>
      </div>

      <ConfigCard :title="t('modelsSystemOne.config.title')" tag="systemone" :rows="rows" />

      <div class="card">
        <div class="card-h">{{ t('modelsSystemOne.available.title') }} <span class="tag">models</span></div>
        <table>
          <thead>
            <tr>
              <th>{{ t('modelsSystemOne.available.model') }}</th>
              <th>{{ t('modelsSystemOne.available.type') }}</th>
              <th>{{ t('modelsSystemOne.available.endpoint') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in availableModels" :key="m.name">
              <td class="mono">{{ m.name }}</td>
              <td><span class="chip">{{ m.kind }}</span></td>
              <td class="mono">{{ m.endpoint }}</td>
            </tr>
          </tbody>
        </table>
        <div class="api-note">
          <i18n-t keypath="modelsSystemOne.availableNote" scope="global">
            <template #model><code>model</code></template>
            <template #injected><code>{{ cfg.linked_model }}</code></template>
            <template #local><code>local</code></template>
          </i18n-t>
        </div>
      </div>
    </template>

    <template v-else>
      <ApiDoc
        tag="/v1/systemone"
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
          note: t('modelsSystemOne.docs.responseNote'),
          body: responseExample,
        }"
        :note="t('modelsSystemOne.docs.note')"
      />

      <div class="card">
        <div class="card-h">{{ t('modelsSystemOne.docs.streamTitle') }} <span class="tag">text/event-stream</span></div>
        <JsonPane :code="cfg.stream_example" />
        <div class="api-note">
          <i18n-t keypath="modelsSystemOne.docs.streamNote" scope="global">
            <template #stream><code>stream: true</code></template>
            <template #answers><code>answers</code></template>
          </i18n-t>
        </div>
      </div>
    </template>
  </template>
  <div v-else class="card empty">{{ t('common.loading') }}</div>

  <div class="foot">{{ t('common.footReadOnly', { page: t('nav.modelsSystemOne') }) }}</div>
</template>

<style scoped>
.api-note code {
  color: var(--text-dim);
}
</style>
