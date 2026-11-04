<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { errorDetail, statusOf } from '@/api/client'
import { api } from '@/api/management'
import ApiDoc from '@/components/ApiDoc.vue'
import ConfigCard from '@/components/ConfigCard.vue'
import TopBar from '@/components/TopBar.vue'
import type { ToolsWebSearch } from '@/types/api'
import type { ApiParam, ConfigRow } from '@/types/ui'
import { fmtClock } from '@/utils/format'

const cfg = ref<ToolsWebSearch | null>(null)
const lastUpdated = ref('')
const loadError = ref('')

const params: ApiParam[] = [
  { name: 'query', type: 'string', required: true, desc: '搜索关键词或自然语言问题（唯一必填字段）' },
  { name: 'model', type: 'string', required: false, desc: 'Provider 侧模型 id，原样透传，不参与本地 alias 解析' },
  { name: 'top_k', type: 'integer', required: false, desc: '检索页面数，范围 1–50；省略时取 Provider 默认值' },
  { name: 'max_chars_per_page', type: 'integer', required: false, desc: '单页字符预算，超出截断并把 truncated 置为 true' },
  { name: 'locale', type: 'string', required: false, desc: '地区/语言提示，如 zh-CN' },
  { name: 'recency', type: 'string', required: false, desc: '时间范围枚举：day / week / month / year' },
  { name: 'synthesize', type: 'boolean', required: false, desc: 'true 时额外返回 answer（由模型综合生成的答案），推理耗时显著增加' },
  { name: 'include_sources', type: 'boolean', required: false, desc: 'true 时在响应中附带扁平 sources[] URL 列表' },
]

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
      badge: c.enabled ? { cls: 'ok', text: '● 开启' } : { cls: 'idle', text: '● 关闭' },
    },
    { label: 'Upstream', value: c.upstream, mono: true, note: c.upstream_type ? `type: ${c.upstream_type}` : '' },
    { label: 'Base URL', value: c.base_url, mono: true, note: '取自 upstream' },
    { label: 'Provider Path', value: c.path, mono: true },
    { label: 'API Key Env', value: c.api_key_env, mono: true },
    { label: '凭据', value: c.api_key || '未设置（不签名）', mono: true },
    { label: 'Timeout', value: `${c.timeout}s`, mono: true },
  ]
})

async function load() {
  try {
    const { data } = await api.toolsWebSearch()
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
  <TopBar title="Tools · Web Search" sub="Web Search 配置（只读）" refreshable :last-updated="lastUpdated" @refresh="load" />

  <div v-if="loadError" class="warn-strip">⚠ {{ loadError }}</div>

  <template v-if="cfg">
    <div v-if="!cfg.provider_configured" class="warn-strip">
      ⚠ 未找到提供检索的 upstream：请求会返回 503。请在 <code>websearch.upstream</code> 指定一个 upstream，或配置 <code>type: "opencode"</code> 的 upstream。
    </div>

    <ConfigCard title="Web Search 配置" tag="web-search" :rows="rows" />

    <ApiDoc
      tag="/v1/tools/web-search"
      :params="params"
      :request="{
        method: 'POST',
        path: cfg.endpoint,
        headers: [['Authorization', 'Bearer <API_KEY>'], ['Content-Type', 'application/json']],
        body: requestExample,
      }"
      :response="{
        status: '200',
        contentType: 'application/json',
        note: '透传 · 状态码与响应体原样返回 Provider 契约',
        body: responseExample,
      }"
      note="请求体原样转发给 Provider（未知字段保留、不重写），响应体与状态码也原样回传，因此字段以 Provider 契约为准；query 为唯一必填。enabled=false 返回 404，Provider upstream 缺失返回 503，Provider 连不上返回 502/504。"
    />

    <div class="card">
      <div class="card-h">Provider 模型 <span class="tag">聚合自 upstream</span></div>
      <div class="api-note">
        Web Search 的 Provider 是一个普通 upstream，其模型会出现在 <code>/v1/models</code> 与 Models · LLM 页面中，
        因此聊天请求也可路由到它。模型 id 以斜杠分隔（如 <code>opencode/big-pickle</code>），路由通配需写成
        <code>"opencode/*"</code>，冒号形式匹配不到任何模型。
      </div>
    </div>
  </template>
  <div v-else class="card empty">加载中…</div>

  <div class="foot">llm-hub 管理台 · Tools · Web Search（只读视图）</div>
</template>

<style scoped>
.api-note code,
.warn-strip code {
  color: var(--text-dim);
}
</style>
