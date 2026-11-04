<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { errorDetail, statusOf } from '@/api/client'
import { api } from '@/api/management'
import ApiDoc from '@/components/ApiDoc.vue'
import ConfigCard from '@/components/ConfigCard.vue'
import JsonPane from '@/components/JsonPane.vue'
import TopBar from '@/components/TopBar.vue'
import type { ModelsSystemOne } from '@/types/api'
import type { ApiParam, ConfigRow } from '@/types/ui'
import { fmtClock } from '@/utils/format'

const cfg = ref<ModelsSystemOne | null>(null)
const lastUpdated = ref('')
const loadError = ref('')

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

const params: ApiParam[] = [
  { name: 'state', type: 'object', required: true, desc: '自由结构，承载待判定的上下文（如 message），原样透传给后端' },
  { name: 'questions', type: 'object', required: true, desc: '问题 id → 问题定义，至少 1 项；type 决定判定形态' },
  { name: 'questions[].type', type: 'string', required: true, desc: 'noul（是/否概率）· choice（单选标签）· score（打分择优）' },
  { name: 'questions[].instructions', type: 'string', required: true, desc: '该问题的判定指令' },
  { name: 'questions[].criteria', type: 'string[] | object', required: false, desc: 'choice / score 的候选标签：choice 用 object、score 用 2–10 项数组，两家后端均通过校验' },
  { name: 'model', type: 'string', required: false, desc: '缺省时由网关按配置注入；laya-server 只接受枚举，Ollama 需真实模型名' },
]

const rows = computed<ConfigRow[]>(() => {
  const c = cfg.value
  if (!c) return []
  return [
    { label: 'Endpoint', value: c.endpoint, mono: true },
    {
      label: 'Enabled',
      badge: c.enabled ? { cls: 'ok', text: '● 开启' } : { cls: 'idle', text: '● 关闭' },
    },
    { label: 'Backend', value: c.backend, mono: true, note: c.path ? `上游路径 ${c.path}` : '' },
    { label: '归属上游', value: c.upstream, mono: true },
    { label: '默认 model', value: c.linked_model, mono: true, note: 'local = 不注入' },
    { label: 'API Key Env', value: c.api_key_env, mono: true },
    { label: '后端凭据', value: c.api_key || '未配置', mono: true },
    { label: 'Timeout', value: `${c.timeout}s`, mono: true },
  ]
})

async function load() {
  try {
    const { data } = await api.modelsSystemOne()
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
  <TopBar title="Models · SystemOne" sub="System One 决策 API 配置（只读）" refreshable :last-updated="lastUpdated" @refresh="load" />

  <div v-if="loadError" class="warn-strip">⚠ {{ loadError }}</div>

  <template v-if="cfg">
    <ConfigCard title="SystemOne 配置" tag="systemone" :rows="rows" />

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
        note: '透传 · 决策结果由 System One 后端返回',
        body: responseExample,
      }"
      note="请求体与响应体均原样透传给 System One 后端（laya-server 或 Ollama 0.40+）；网关只做调用方 API Key 鉴权，并用 api_key_env 指向的环境变量密钥签名上游调用。"
    />

    <div class="card">
      <div class="card-h">流式响应示例 <span class="tag">text/event-stream</span></div>
      <JsonPane :code="cfg.stream_example" />
      <div class="api-note">
        <code>stream: true</code> 时后端按 SSE 逐条回带 <code>answers</code>；网关不改写事件内容，直接透传给调用方。
      </div>
    </div>
  </template>
  <div v-else class="card empty">加载中…</div>

  <div class="foot">llm-hub 管理台 · Models · SystemOne（只读视图）</div>
</template>

<style scoped>
.api-note code {
  color: var(--text-dim);
}
</style>
