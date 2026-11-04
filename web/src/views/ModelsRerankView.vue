<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { errorDetail, statusOf } from '@/api/client'
import { api } from '@/api/management'
import ApiDoc from '@/components/ApiDoc.vue'
import ConfigCard from '@/components/ConfigCard.vue'
import TopBar from '@/components/TopBar.vue'
import type { ModelsRerank } from '@/types/api'
import type { ApiParam, ConfigRow } from '@/types/ui'
import { fmtClock } from '@/utils/format'

const cfg = ref<ModelsRerank | null>(null)
const lastUpdated = ref('')
const loadError = ref('')

const rows = computed<ConfigRow[]>(() => {
  const c = cfg.value
  if (!c) return []
  return [
    { label: 'Endpoint', value: c.endpoint, mono: true },
    {
      label: 'Enabled',
      badge: c.enabled ? { cls: 'ok', text: '● 开启' } : { cls: 'idle', text: '● 关闭' },
    },
    { label: '打分方式 (mode)', value: c.mode, mono: true },
    { label: '上游协议 (upstream_api)', value: c.upstream_api, mono: true, note: 'auto = 按上游类型选择' },
    { label: '失败策略 (on_error)', value: c.on_error, mono: true },
    { label: 'keep_alive', value: c.scoring?.keep_alive, mono: true, note: '空闲后卸载' },
    { label: 'top_logprobs', value: c.scoring?.top_logprobs, mono: true },
    { label: 'temperature', value: c.scoring?.temperature, mono: true },
    { label: 'max_tokens', value: c.scoring?.max_tokens, mono: true },
    { label: '并发上限', value: c.limits?.max_concurrency, mono: true },
    { label: '文档数上限', value: c.limits?.max_documents || '不限', mono: true },
    { label: '单文档字符上限', value: c.limits?.max_document_chars || '不限', mono: true },
  ]
})

const modelRows = computed<string[]>(() => (cfg.value?.models ?? []).map((m) => m.model))

const params: ApiParam[] = [
  { name: 'model', type: 'string', required: true, desc: 'Rerank 模型名，取值见上方 Models（命中其一即可）' },
  { name: 'query', type: 'string', required: true, desc: '用于检索排序的查询文本' },
  { name: 'documents', type: 'string[]', required: true, desc: '待排序的候选文档数组' },
  { name: 'top_n', type: 'integer', required: false, desc: '返回前 N 条，默认全部' },
  { name: 'return_documents', type: 'boolean', required: false, desc: '是否在结果中回带原文' },
  { name: 'options', type: 'object', required: false, desc: '本次覆盖上游推理选项（合并到 rerank.options）' },
]

async function load() {
  try {
    const { data } = await api.modelsRerank()
    cfg.value = data
    lastUpdated.value = fmtClock(new Date().toISOString())
    loadError.value = ''
  } catch (error) {
    if (statusOf(error) !== 401) loadError.value = errorDetail(error)
  }
}

onMounted(load)
</script>

<template>
  <TopBar title="Models · Rerank" sub="Rerank 配置（只读）" refreshable :last-updated="lastUpdated" @refresh="load" />

  <div v-if="loadError" class="warn-strip">⚠ {{ loadError }}</div>

  <template v-if="cfg">
    <ConfigCard title="Rerank 配置" tag="rerank" :rows="rows" />

    <div class="card">
      <div class="card-h">可用模型 <span class="tag">models</span></div>
      <table>
        <thead>
          <tr>
            <th>模型</th>
            <th>上游</th>
            <th>类型</th>
            <th>Base URL</th>
            <th>凭据</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in cfg.models" :key="m.model">
            <td class="mono">{{ m.model }}</td>
            <td>{{ m.upstream || '—' }}</td>
            <td><span class="chip">{{ m.type || '—' }}</span></td>
            <td class="mono">{{ m.base_url || '—' }}</td>
            <td class="mono">{{ m.api_key || '未配置' }}</td>
          </tr>
          <tr v-if="!cfg.models.length">
            <td colspan="5" class="empty">未配置 rerank 模型</td>
          </tr>
        </tbody>
      </table>
      <div v-if="modelRows.length" class="api-note">
        网关按 <code>mode</code> 选择打分实现；<code>model_modes</code> 可为单个模型覆盖打分方式。
      </div>
    </div>

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
        note: 'results[] 每项含 index / relevance_score',
        body: cfg.response_example,
      }"
      note="rerank 由上游模型打分（logprobs 或 embedding），网关负责协议转换与结果归一化；失败请求可在 Requests 页按状态筛选查看。"
    />
  </template>
  <div v-else class="card empty">加载中…</div>

  <div class="foot">llm-hub 管理台 · Models · Rerank（只读视图）</div>
</template>

<style scoped>
.api-note code {
  color: var(--text-dim);
}
</style>
