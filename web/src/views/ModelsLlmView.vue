<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { errorDetail, statusOf } from '@/api/client'
import { api } from '@/api/management'
import TopBar from '@/components/TopBar.vue'
import type { LlmModel, ModelsLlm } from '@/types/api'
import { fmtClock, fmtInt } from '@/utils/format'

const data = ref<ModelsLlm>({ models: [], aliases: [], num_ctx_overrides: {} })
const lastUpdated = ref('')
const loadError = ref('')
const keyword = ref('')
// This projection probes every upstream, so it can take seconds when one of them is down;
// the tables must not read "no models" while the first response is still in flight.
const loaded = ref(false)

const models = computed<LlmModel[]>(() => {
  const needle = keyword.value.trim().toLowerCase()
  if (!needle) return data.value.models
  return data.value.models.filter(
    (m) =>
      m.model.toLowerCase().includes(needle) ||
      (m.alias || '').toLowerCase().includes(needle) ||
      m.upstream.toLowerCase().includes(needle),
  )
})

function paramsText(params: Record<string, unknown> | null | undefined): string {
  if (!params || !Object.keys(params).length) return '—'
  return JSON.stringify(params)
}

async function load() {
  try {
    const response = await api.modelsLlm()
    data.value = response.data
    loaded.value = true
    lastUpdated.value = fmtClock(new Date().toISOString())
    loadError.value = ''
  } catch (error) {
    if (statusOf(error) !== 401) loadError.value = errorDetail(error)
  }
}

onMounted(load)
</script>

<template>
  <TopBar title="Models · LLM" sub="LLM 模型配置（只读投影自 config.json）" refreshable :last-updated="lastUpdated" @refresh="load" />

  <div v-if="loadError" class="warn-strip">⚠ {{ loadError }}</div>

  <div class="filters">
    <div class="field">
      <label for="kw">筛选</label>
      <input id="kw" v-model="keyword" class="inp" placeholder="模型 / 别名 / 上游关键字" />
    </div>
    <div class="field" style="margin-left: auto; align-items: flex-end">
      <label>&nbsp;</label>
      <span class="chip">{{ loaded ? `${models.length} / ${data.models.length} 条` : '加载中…' }}</span>
    </div>
  </div>

  <div>
    <div class="view-title">上游模型映射 <span class="tag">upstreams → models</span></div>
    <div class="card tight">
      <table>
        <thead>
          <tr>
            <th>模型名</th>
            <th>上游</th>
            <th>类型</th>
            <th>别名</th>
            <th>num_ctx</th>
            <th>运行态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in models" :key="`${m.upstream}/${m.model}`">
            <td class="mono">
              {{ m.model }}
              <span v-if="m.wildcard" class="chip s2" title="前缀通配">wildcard</span>
            </td>
            <td>{{ m.upstream }}</td>
            <td><span class="chip">{{ m.type }}</span></td>
            <td class="mono">{{ m.alias || '—' }}</td>
            <td class="mono">{{ fmtInt(m.num_ctx) }}</td>
            <td>
              <span class="badge" :class="m.running ? 'ok' : 'idle'">● {{ m.running ? '运行中' : '未加载' }}</span>
            </td>
          </tr>
          <tr v-if="!models.length">
            <td colspan="6" class="empty">{{ loaded ? '没有匹配的模型' : '加载中… 上游模型发现可能需要数秒' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div>
    <div class="view-title">别名映射 <span class="tag">aliases</span></div>
    <div class="card tight">
      <table>
        <thead>
          <tr>
            <th>别名</th>
            <th>目标模型</th>
            <th>上游</th>
            <th>注入参数</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="a in data.aliases" :key="a.alias">
            <td class="mono">{{ a.alias }}</td>
            <td class="mono">{{ a.target }}</td>
            <td>{{ a.upstream || '—' }}</td>
            <td class="mono">{{ paramsText(a.params) }}</td>
            <td>{{ a.description || '—' }}</td>
          </tr>
          <tr v-if="!data.aliases.length">
            <td colspan="5" class="empty">{{ loaded ? '未配置别名' : '加载中…' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div>
    <div class="view-title">上下文固化 <span class="tag">num_ctx_overrides</span></div>
    <div class="card tight">
      <table>
        <thead>
          <tr>
            <th>模型</th>
            <th>num_ctx</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(value, model) in data.num_ctx_overrides" :key="model">
            <td class="mono">{{ model }}</td>
            <td class="mono">{{ fmtInt(value) }}</td>
          </tr>
          <tr v-if="!Object.keys(data.num_ctx_overrides).length">
            <td colspan="2" class="empty">{{ loaded ? '未固化任何模型的 num_ctx' : '加载中…' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="foot">llm-hub 管理台 · Models · LLM（只读视图）</div>
</template>

<style scoped>
.inp {
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border-strong);
  border-radius: 7px;
  padding: 6px 9px;
  font-size: 12px;
  font-family: inherit;
  min-width: 240px;
}

.chip.s2 {
  margin-left: 6px;
}
</style>
