<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import ApiDoc from '@/components/ApiDoc.vue'
import ConfigCard from '@/components/ConfigCard.vue'
import TopBar from '@/components/TopBar.vue'
import type { ApiParam, ApiRequestSpec, ApiResponseSpec, ConfigRow, TestField } from '@/types/ui'

type Tab = 'models' | 'api'

const { t } = useI18n()

const tab = ref<Tab>('models')

// Embedding has no config block of its own: both endpoints are catch-all pass-through, so
// this list documents the models the gateway routes to them. They must also appear in
// upstreams[].models (or be covered by a prefix wildcard) to be routable.
const availableModels = [
  { name: 'bge-m3:latest', kind: 'embedding', endpoint: '/api/embed · /v1/embeddings' },
]

// Embedding has no config block: both endpoints are catch-all pass-through, so the page
// documents the transport instead of projecting a configuration.
const rows = computed<ConfigRow[]>(() => [
  { label: t('modelsEmbed.rows.native'), value: '/api/embed', mono: true },
  { label: t('modelsEmbed.rows.openai'), value: '/v1/embeddings', mono: true },
  { label: t('modelsEmbed.rows.auth'), value: 'X-API-Key / Authorization: Bearer', mono: true },
  {
    label: t('modelsEmbed.rows.resolve'),
    value: t('modelsEmbed.rows.resolveValue'),
    mono: true,
    note: t('modelsEmbed.rows.resolveNote'),
  },
  {
    label: t('modelsEmbed.rows.response'),
    value: t('modelsEmbed.rows.responseValue'),
    mono: true,
    note: t('modelsEmbed.rows.responseNote'),
  },
])

const authHeaders: string[][] = [
  ['Authorization', 'Bearer <API_KEY>'],
  ['Content-Type', 'application/json'],
]

const ollamaParams = computed<ApiParam[]>(() => [
  { name: 'model', type: 'string', required: true, desc: t('modelsEmbed.params.model') },
  { name: 'input', type: 'string | string[]', required: true, desc: t('modelsEmbed.params.input') },
  { name: 'truncate', type: 'boolean', required: false, desc: t('modelsEmbed.params.truncate') },
  { name: 'options', type: 'object', required: false, desc: t('modelsEmbed.params.options') },
  { name: 'keep_alive', type: 'string | number', required: false, desc: t('modelsEmbed.params.keepAlive') },
])

const ollamaRequest = computed<ApiRequestSpec>(() => ({
  method: 'POST',
  path: '/api/embed',
  headers: authHeaders,
  body: {
    model: 'bge-m3:latest',
    input: ['你好世界', '机器学习入门'],
    keep_alive: '5m',
  },
}))

const ollamaResponse = computed<ApiResponseSpec>(() => ({
  status: '200',
  contentType: 'application/json',
  note: t('modelsEmbed.responses.embeddingsNote'),
  body: {
    model: 'bge-m3:latest',
    embeddings: [
      [0.123, 0.456, 0.789],
      [0.987, 0.654, 0.321],
    ],
    total_duration: 128000000,
    prompt_eval_count: 12,
  },
}))

const openaiParams = computed<ApiParam[]>(() => [
  { name: 'model', type: 'string', required: true, desc: t('modelsEmbed.params.model') },
  { name: 'input', type: 'string | string[]', required: true, desc: t('modelsEmbed.params.input') },
  { name: 'encoding_format', type: '"float" | "base64"', required: false, desc: t('modelsEmbed.params.encodingFormat') },
  { name: 'dimensions', type: 'integer', required: false, desc: t('modelsEmbed.params.dimensions') },
  { name: 'user', type: 'string', required: false, desc: t('modelsEmbed.params.user') },
])

const openaiRequest = computed<ApiRequestSpec>(() => ({
  method: 'POST',
  path: '/v1/embeddings',
  headers: authHeaders,
  body: {
    model: 'bge-m3:latest',
    input: ['你好世界', '机器学习入门'],
  },
}))

const openaiResponse = computed<ApiResponseSpec>(() => ({
  status: '200',
  contentType: 'application/json',
  note: t('modelsEmbed.responses.openaiNote'),
  body: {
    object: 'list',
    data: [
      { object: 'embedding', index: 0, embedding: [0.123, 0.456, 0.789] },
      { object: 'embedding', index: 1, embedding: [0.987, 0.654, 0.321] },
    ],
    model: 'bge-m3:latest',
    usage: { prompt_tokens: 12, total_tokens: 12 },
  },
}))

// Both testers mirror the parameter tables above minus `model`, which the selector owns. A
// blank input is omitted, so the endpoint answers with its own default.
const ollamaFields = computed<TestField[]>(() => [
  {
    key: 'input',
    label: t('modelsEmbed.params.input'),
    type: 'lines',
    required: true,
    def: 'Hello\nWorld',
    wide: true,
    hint: t('common.onePerLine'),
  },
  { key: 'truncate', label: t('modelsEmbed.params.truncate'), type: 'bool', def: 'true' },
  { key: 'keep_alive', label: t('modelsEmbed.params.keepAlive'), type: 'text', def: '5m' },
  { key: 'options', label: t('modelsEmbed.params.options'), type: 'json', wide: true },
])

const openaiFields = computed<TestField[]>(() => [
  {
    key: 'input',
    label: t('modelsEmbed.params.input'),
    type: 'lines',
    required: true,
    def: 'Hello\nWorld',
    wide: true,
    hint: t('common.onePerLine'),
  },
  {
    key: 'encoding_format',
    label: t('modelsEmbed.params.encodingFormat'),
    type: 'select',
    options: ['', 'float', 'base64'],
    def: 'float',
  },
  { key: 'dimensions', label: t('modelsEmbed.params.dimensions'), type: 'number' },
  { key: 'user', label: t('modelsEmbed.params.user'), type: 'text' },
])
</script>

<template>
  <TopBar :title="t('nav.modelsEmbed')" :sub="t('modelsEmbed.sub')" />

  <div class="tabs" role="tablist">
    <button
      class="tab"
      :class="{ on: tab === 'models' }"
      role="tab"
      :aria-selected="tab === 'models'"
      @click="tab = 'models'"
    >
      {{ t('modelsEmbed.tabs.models') }}
      <span class="count">{{ availableModels.length }}</span>
    </button>
    <button
      class="tab"
      :class="{ on: tab === 'api' }"
      role="tab"
      :aria-selected="tab === 'api'"
      @click="tab = 'api'"
    >
      {{ t('modelsEmbed.tabs.api') }}
      <span class="count">2</span>
    </button>
  </div>

  <template v-if="tab === 'models'">
    <div class="card">
      <div class="card-h">{{ t('modelsEmbed.introTitle') }} <span class="tag">embedding</span></div>
      <i18n-t keypath="modelsEmbed.intro" scope="global" tag="div" class="feature-intro">
        <template #ollama><code>POST /api/embed</code></template>
        <template #openai><code>POST /v1/embeddings</code></template>
        <template #model><code>model</code></template>
        <template #models><code>upstreams[].models</code></template>
        <template #aliases><code>aliases</code></template>
      </i18n-t>
    </div>

    <ConfigCard :title="t('modelsEmbed.config.title')" tag="passthrough" :rows="rows" />

    <div class="card">
      <div class="card-h">{{ t('modelsEmbed.available.title') }} <span class="tag">models</span></div>
      <table>
        <thead>
          <tr>
            <th>{{ t('modelsEmbed.available.model') }}</th>
            <th>{{ t('modelsEmbed.available.type') }}</th>
            <th>{{ t('modelsEmbed.available.endpoint') }}</th>
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
        <i18n-t keypath="modelsEmbed.availableNote" scope="global">
          <template #models><code>upstreams[].models</code></template>
          <template #aliases><code>aliases</code></template>
          <template #model><code>model</code></template>
          <template #code><b>404</b></template>
        </i18n-t>
      </div>
    </div>

    <div class="card">
      <div class="card-h">{{ t('modelsEmbed.compare.title') }} <span class="tag">embed</span></div>
      <table>
        <thead>
          <tr>
            <th>{{ t('modelsEmbed.compare.endpoint') }}</th>
            <th>{{ t('modelsEmbed.compare.protocol') }}</th>
            <th>{{ t('modelsEmbed.compare.request') }}</th>
            <th>{{ t('modelsEmbed.compare.response') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td class="mono">/api/embed</td>
            <td><span class="chip">Ollama</span></td>
            <td class="mono">model, input, truncate, options, keep_alive</td>
            <td class="mono">embeddings[][]</td>
          </tr>
          <tr>
            <td class="mono">/v1/embeddings</td>
            <td><span class="chip">OpenAI</span></td>
            <td class="mono">model, input, encoding_format, dimensions, user</td>
            <td class="mono">data[].embedding</td>
          </tr>
        </tbody>
      </table>
      <div class="api-note">{{ t('modelsEmbed.compareNote') }}</div>
    </div>
  </template>

  <template v-else>
    <div class="api-lead">
      <i18n-t keypath="modelsEmbed.apiLead" scope="global">
        <template #model><code>model</code></template>
        <template #method><span class="mono">POST</span></template>
        <template #code><b>405</b></template>
        <template #input><code>input</code></template>
      </i18n-t>
    </div>

    <ApiDoc
      :title="t('modelsEmbed.docs.ollamaTitle')"
      tag="/api/embed"
      :params="ollamaParams"
      :request="ollamaRequest"
      :response="ollamaResponse"
      :note="t('modelsEmbed.docs.ollamaNote')"
      :tester="{ kind: 'embed_ollama', fields: ollamaFields }"
    />

    <ApiDoc
      :title="t('modelsEmbed.docs.openaiTitle')"
      tag="/v1/embeddings"
      :params="openaiParams"
      :request="openaiRequest"
      :response="openaiResponse"
      :note="t('modelsEmbed.docs.openaiNote')"
      :tester="{ kind: 'embed_openai', fields: openaiFields }"
    />
  </template>

  <div class="foot">{{ t('common.footReadOnly', { page: t('nav.modelsEmbed') }) }}</div>
</template>

<style scoped>
.api-note code {
  color: var(--text-dim);
}
</style>
