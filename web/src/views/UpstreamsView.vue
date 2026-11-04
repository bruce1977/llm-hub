<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { errorDetail, errorResponseBody, statusOf } from '@/api/client'
import { api } from '@/api/management'
import TopBar from '@/components/TopBar.vue'
import type { ProbeResult, RunningModel, UpstreamDetail } from '@/types/api'
import { deviceBadge, fmtClock, fmtInt } from '@/utils/format'

const upstreams = ref<ProbeResult[]>([])
// /upstreams strips running_models, so the per-instance device table is taken from
// /overview, which reports the same probe results plus the models resident in memory.
const runningByUpstream = ref<Record<string, RunningModel[]>>({})
const lastUpdated = ref('')
const loadError = ref('')
const expanded = ref('')
// name -> probe + installed models. The 503 "unreachable" answer carries only a subset,
// hence Partial: the template guards on `unreachable` before reading the model list.
const details = ref<Record<string, Partial<UpstreamDetail>>>({})
const detailBusy = ref('')
let timer: ReturnType<typeof setInterval> | null = null

async function load() {
  try {
    const { data } = await api.overview()
    upstreams.value = data.upstreams ?? []
    const grouped: Record<string, RunningModel[]> = {}
    for (const model of data.running_models ?? []) {
      grouped[model.upstream] = grouped[model.upstream] || []
      grouped[model.upstream].push(model)
    }
    runningByUpstream.value = grouped
    lastUpdated.value = fmtClock(data.generated_at)
    loadError.value = ''
    // Keep an open row in sync with the refreshed list.
    if (expanded.value) await loadDetail(expanded.value)
  } catch (error) {
    if (statusOf(error) !== 401) loadError.value = errorDetail(error)
  }
}

async function loadDetail(name: string) {
  detailBusy.value = name
  try {
    const { data } = await api.upstream(name)
    details.value = { ...details.value, [name]: data }
  } catch (error) {
    const body = errorResponseBody<{ unreachable?: boolean }>(error)
    // 503 with unreachable: true is the documented answer for a dead instance.
    details.value = {
      ...details.value,
      [name]: {
        unreachable: body?.unreachable === true || statusOf(error) === 503,
        models: [],
      },
    }
  } finally {
    detailBusy.value = ''
  }
}

async function toggle(name: string) {
  if (expanded.value === name) {
    expanded.value = ''
    return
  }
  expanded.value = name
  if (!details.value[name]) await loadDetail(name)
}

function vramText(u: ProbeResult): string {
  if (u.vram_used_gib === null || u.vram_used_gib === undefined) return '—'
  const used = Number(u.vram_used_gib).toFixed(1)
  return u.vram_total_gib ? `${used} / ${Number(u.vram_total_gib).toFixed(1)} GiB` : `${used} GiB`
}

onMounted(() => {
  load()
  timer = setInterval(load, 10000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  timer = null
})
</script>

<template>
  <TopBar title="Upstreams" sub="上游实例运行状态 · 点击任意行展开查看运行中 / 已装模型" refreshable :last-updated="lastUpdated" @refresh="load" />

  <div v-if="loadError" class="warn-strip">⚠ {{ loadError }}</div>

  <div>
    <div class="section-title">上游实例 <span class="tag">live</span></div>
    <div class="card tight">
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>类型</th>
            <th>状态</th>
            <th>已加载</th>
            <th>显存占用</th>
            <th>探测延迟</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="u in upstreams" :key="u.name">
            <tr class="dt-row" @click="toggle(u.name)">
              <td class="mono">{{ u.name }}</td>
              <td><span class="chip">{{ u.type }}</span></td>
              <td><span class="badge" :class="u.reachable ? 'ok' : 'err'">● {{ u.reachable ? '在线' : '离线' }}</span></td>
              <td>{{ fmtInt(u.loaded) }}</td>
              <td class="mono">{{ vramText(u) }}</td>
              <td class="mono">{{ u.reachable ? `${u.probe_ms} ms` : '超时' }}</td>
            </tr>
            <tr v-if="expanded === u.name" class="row-expand">
              <td colspan="6">
                <div v-if="detailBusy === u.name && !details[u.name]" class="meta" style="color: var(--text-dim)">
                  探测中…
                </div>
                <div v-else-if="details[u.name]?.unreachable" class="meta" style="color: var(--red)">
                  实例不可达（{{ u.error || '探测超时' }}），无法获取运行态与已装模型。
                </div>
                <div v-else class="exp-grid">
                  <div>
                    <div class="card-h">运行中模型 <span class="tag">/api/ps</span></div>
                    <table v-if="u.ps_supported">
                      <thead>
                        <tr>
                          <th>模型</th>
                          <th>设备</th>
                          <th>上下文</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="m in runningByUpstream[u.name] || []" :key="m.model">
                          <td class="mono">{{ m.model }}</td>
                          <td>
                            <span class="badge" :class="deviceBadge(m.device, m.device_ratio).cls">
                              {{ deviceBadge(m.device, m.device_ratio).text }}
                            </span>
                          </td>
                          <td class="mono">{{ fmtInt(m.num_ctx) }}</td>
                        </tr>
                        <tr v-if="!(runningByUpstream[u.name] || []).length">
                          <td colspan="3" class="empty">当前没有驻留的模型</td>
                        </tr>
                      </tbody>
                    </table>
                    <div v-else class="meta" style="font-size: 12px; color: var(--text-dim)">
                      {{ u.type }} 不支持 /api/ps，无运行态探测。
                    </div>
                  </div>
                  <div>
                    <div class="card-h">已装模型 <span class="tag">/api/tags</span></div>
                    <table>
                      <thead>
                        <tr>
                          <th>模型</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="m in details[u.name]?.models || []" :key="m">
                          <td class="mono">{{ m }}</td>
                        </tr>
                        <tr v-if="!(details[u.name]?.models || []).length">
                          <td class="empty">{{ details[u.name]?.models_error ? `模型列表拉取失败：${details[u.name].models_error}` : '无已装模型' }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              </td>
            </tr>
          </template>
          <tr v-if="!upstreams.length">
            <td colspan="6" class="empty">加载中…</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="foot">llm-hub 管理台 · Upstreams（只读视图）</div>
</template>

<style scoped>
.meta {
  font-size: 12px;
}
</style>
