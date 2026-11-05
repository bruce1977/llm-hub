<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { errorDetail, errorResponseBody, statusOf } from '@/api/client'
import { api } from '@/api/management'
import TopBar from '@/components/TopBar.vue'
import type { ProbeResult, RunningModel, UpstreamDetail } from '@/types/api'
import { deviceBadge, fmtClock, fmtInt, memoryText } from '@/utils/format'

const { t } = useI18n()

const upstreams = ref<ProbeResult[]>([])
const lastUpdated = ref('')
const loadError = ref('')
const refreshing = ref(false)
const expanded = ref('')
// name -> probe + the models declared for it in config.yaml + what is resident in memory.
// The 503 "unreachable" answer carries only a subset, hence Partial: the template guards
// on `unreachable` before reading the model list.
const details = ref<Record<string, Partial<UpstreamDetail>>>({})
const detailBusy = ref('')
let timer: ReturnType<typeof setInterval> | null = null

async function load() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    const { data } = await api.overview()
    upstreams.value = data.upstreams ?? []
    lastUpdated.value = fmtClock(data.generated_at)
    loadError.value = ''
    // Keep an open row in sync with the refreshed list.
    if (expanded.value) await loadDetail(expanded.value)
  } catch (error) {
    if (statusOf(error) !== 401) loadError.value = errorDetail(error)
  } finally {
    refreshing.value = false
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
        running_models: [],
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
  await loadDetail(name)
}

function vramText(u: ProbeResult): string {
  if (u.vram_used_gib === null || u.vram_used_gib === undefined) return '—'
  const used = Number(u.vram_used_gib).toFixed(1)
  return u.vram_total_gib ? `${used} / ${Number(u.vram_total_gib).toFixed(1)} GiB` : `${used} GiB`
}

/** One row per model declared in config.yaml, left-joined with the live /api/ps state.
 *  A declared model that is not resident is rendered as "未加载" instead of being
 *  listed in a separate table. Models resident but never declared are appended so the
 *  live state is never silently dropped. */
interface ModelRow {
  name: string
  alias: string | null
  derived: boolean
  running: RunningModel | null
  title?: string
}

function modelRows(u: ProbeResult): ModelRow[] {
  const detail = details.value[u.name]
  const declared = detail?.models ?? []
  const running = detail?.running_models ?? []

  const byDisplay = new Map<string, RunningModel>()
  const byModel = new Map<string, RunningModel>()
  for (const m of running) {
    byDisplay.set(m.display, m)
    byModel.set(m.model, m)
  }

  const rows: ModelRow[] = declared.map((name) => {
    const hit = byDisplay.get(name) ?? byModel.get(name) ?? null
    return {
      name,
      alias: hit?.alias ?? null,
      derived: hit?.derived ?? false,
      running: hit,
      title: rowTitle(hit),
    }
  })

  // /api/ps can report models that config.yaml never declared; keep them visible.
  const declaredSet = new Set(declared)
  for (const m of running) {
    if (declaredSet.has(m.display) || declaredSet.has(m.model)) continue
    rows.push({
      name: m.display,
      alias: m.alias,
      derived: m.derived,
      running: m,
      title: rowTitle(m),
    })
  }
  return rows
}

/** A derived model shares its target's instance, so the hover names that instance instead of a
 *  real id it does not have; a renamed model still reveals the id behind the short name. */
function rowTitle(m: RunningModel | null): string | undefined {
  if (!m) return undefined
  if (m.derived) return t('common.derivedFrom', { target: m.target ?? '' })
  return m.model !== m.display ? t('common.realModel', { model: m.model }) : undefined
}

onMounted(() => {
  load()
  timer = setInterval(load, 60000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  timer = null
})
</script>

<template>
  <TopBar
    :title="t('nav.upstreams')"
    :sub="t('upstreams.sub')"
    refreshable
    :loading="refreshing"
    :last-updated="lastUpdated"
    @refresh="load"
  />

  <div v-if="loadError" class="warn-strip">⚠ {{ loadError }}</div>

  <div>
    <div class="section-title">{{ t('upstreams.title') }} <span class="tag">live</span></div>
    <div class="card tight">
      <table>
        <thead>
          <tr>
            <th>{{ t('upstreams.name') }}</th>
            <th>{{ t('upstreams.type') }}</th>
            <th>{{ t('upstreams.status') }}</th>
            <th>{{ t('upstreams.loaded') }}</th>
            <th>{{ t('upstreams.vram') }}</th>
            <th>{{ t('upstreams.probe') }}</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="u in upstreams" :key="u.name">
            <tr class="dt-row" @click="toggle(u.name)">
              <td class="mono">{{ u.name }}</td>
              <td><span class="chip">{{ u.type }}</span></td>
              <td>
                <span class="badge" :class="u.reachable ? 'ok' : 'err'"
                  >● {{ u.reachable ? t('common.online') : t('common.offline') }}</span
                >
              </td>
              <td>{{ fmtInt(u.loaded) }}</td>
              <td class="mono">{{ vramText(u) }}</td>
              <td class="mono">{{ u.reachable ? `${u.probe_ms} ms` : t('common.timeout') }}</td>
            </tr>
            <tr v-if="expanded === u.name" class="row-expand">
              <td colspan="6">
                <div v-if="detailBusy === u.name && !details[u.name]" class="meta" style="color: var(--text-dim)">
                  {{ t('upstreams.probing') }}
                </div>
                <div v-else-if="details[u.name]?.unreachable" class="meta" style="color: var(--red)">
                  <i18n-t keypath="upstreams.unreachable" scope="global">
                    <template #reason>{{ u.error || t('upstreams.probeTimeout') }}</template>
                  </i18n-t>
                </div>
                <div v-else>
                  <div class="card-h">
                    {{ t('upstreams.models') }}
                    <span class="tag">config.yaml · upstreams[].models</span>
                    <span class="tag">{{ u.ps_supported ? '/api/ps' : t('upstreams.psLive') }}</span>
                  </div>
                  <table v-if="modelRows(u).length" class="model-table">
                    <colgroup>
                      <col />
                      <col style="width: 108px" />
                      <col style="width: 96px" />
                      <col style="width: 168px" />
                    </colgroup>
                    <thead>
                      <tr>
                        <th>{{ t('upstreams.modelTable.model') }}</th>
                        <th>{{ t('upstreams.modelTable.device') }}</th>
                        <th>{{ t('upstreams.modelTable.context') }}</th>
                        <th>{{ t('upstreams.modelTable.memory') }}</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="row in modelRows(u)" :key="row.name">
                        <td class="mono" :title="row.title">
                          {{ row.name }}
                          <span v-if="row.alias" class="chip s2" :title="t('common.aliasTip')">alias</span>
                          <span v-if="row.derived" class="chip s2" :title="t('common.derivedTip')">derived</span>
                        </td>
                        <td>
                          <span
                            v-if="row.running"
                            class="badge"
                            :class="deviceBadge(row.running.device, row.running.device_ratio).cls"
                          >
                            {{ deviceBadge(row.running.device, row.running.device_ratio).text }}
                          </span>
                          <span v-else class="badge idle">{{ t('common.notLoaded') }}</span>
                        </td>
                        <td class="mono">{{ fmtInt(row.running?.num_ctx) }}</td>
                        <td class="mono">{{ memoryText(row.running?.vram_gib, row.running?.size_gib) }}</td>
                      </tr>
                    </tbody>
                  </table>
                  <div v-else class="meta" style="font-size: 12px; color: var(--text-dim)">
                    {{ t('upstreams.noModels') }}
                  </div>
                </div>
              </td>
            </tr>
          </template>
          <tr v-if="!upstreams.length">
            <td colspan="6" class="empty">{{ t('common.loading') }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="foot">{{ t('common.footReadOnly', { page: t('nav.upstreams') }) }}</div>
</template>

<style scoped>
.meta {
  font-size: 12px;
}

/* Fixed layout + explicit colgroup widths keep the 设备 column stable and stop the
   上下文 / 显存 / 占用 headers from wrapping into three lines on a narrow grid half. */
.model-table {
  table-layout: fixed;
}

.model-table th,
.model-table td {
  white-space: nowrap;
}

/* Long declared model ids are the only cells allowed to wrap. */
.model-table th:first-child,
.model-table td:first-child {
  white-space: normal;
  overflow-wrap: anywhere;
}

.model-table td:not(:first-child) {
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
