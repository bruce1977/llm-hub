<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { errorDetail, statusOf } from '@/api/client'
import { api } from '@/api/management'
import ApiDoc from '@/components/ApiDoc.vue'
import ConfigCard from '@/components/ConfigCard.vue'
import JsonPane from '@/components/JsonPane.vue'
import TopBar from '@/components/TopBar.vue'
import { useApiTestContext } from '@/composables/apiTestContext'
import type { ModelsSystemOne } from '@/types/api'
import type { ApiParam, ConfigRow, TestField } from '@/types/ui'
import { fmtClock } from '@/utils/format'

type Tab = 'models' | 'api'

const { t } = useI18n()

const cfg = ref<ModelsSystemOne | null>(null)
const tab = ref<Tab>('models')
const lastUpdated = ref('')
const loadError = ref('')
const refreshing = ref(false)

// The decision models are a projection of api_models.systemone, read from the very same
// /api-test/models payload the tester below uses. When that list is empty the backend derives
// it (systemone.model, then the declared pool), so the table follows that derivation too.
const {
  loading: contextLoading,
  error: contextError,
  load: loadContext,
  kindContext,
} = useApiTestContext()
const availableModels = computed(() =>
  (kindContext('systemone')?.models ?? []).map((m) => ({
    name: m.model,
    kind: 'systemone',
    endpoint: '/v1/systemone',
    upstream: m.upstream,
  })),
)
const soSource = computed(() => kindContext('systemone')?.source ?? 'config.yaml')

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

// The body is passed through untouched, so the tester starts from the documented example the
// projection returns - one click sends a request either backend accepts. `model` comes from the
// selector, which may also take a name typed by hand (laya wants an enum, Ollama a real model).
function jsonOf(value: unknown): string {
  return value === undefined ? '' : JSON.stringify(value)
}

const testFields = computed<TestField[]>(() => {
  const example: Record<string, unknown> = cfg.value?.request_example ?? {}
  return [
    {
      key: 'state',
      label: t('modelsSystemOne.params.state'),
      type: 'json',
      required: true,
      def: jsonOf(example.state),
      wide: true,
    },
    {
      key: 'questions',
      label: t('modelsSystemOne.params.questions'),
      type: 'json',
      required: true,
      def: jsonOf(example.questions),
      wide: true,
      hint: t('modelsSystemOne.params.questionType'),
    },
    { key: 'stream', label: t('modelsSystemOne.tester.stream'), type: 'bool' },
  ]
})

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

onMounted(() => {
  void load()
  void loadContext()
})
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
  <div v-if="contextError" class="warn-strip">⚠ {{ contextError }}</div>

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
        <div class="card-h">{{ t('modelsSystemOne.available.title') }} <span class="tag">{{ soSource }}</span></div>
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
              <td class="mono" :title="m.upstream || undefined">{{ m.name }}</td>
              <td><span class="chip">{{ m.kind }}</span></td>
              <td class="mono">{{ m.endpoint }}</td>
            </tr>
            <tr v-if="!availableModels.length">
              <td colspan="3" class="empty">
                {{ contextLoading ? t('common.loading') : t('modelsSystemOne.available.empty') }}
              </td>
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
        <div class="api-note">{{ t('modelsSystemOne.available.sourceNote', { source: soSource }) }}</div>
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
        :tester="{ kind: 'systemone', fields: testFields }"
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
