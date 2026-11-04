<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { errorDetail, statusOf } from '@/api/client'
import { api } from '@/api/management'
import JsonPane from '@/components/JsonPane.vue'
import type { RequestDetail } from '@/types/api'
import { fmtBytes, fmtClock, fmtDateTime, fmtInt, fmtMs, statusBadge } from '@/utils/format'

const props = withDefaults(
  defineProps<{
    recordId?: number | null
  }>(),
  { recordId: null },
)

const open = defineModel<boolean>({ default: false })

const { t } = useI18n()

const detail = ref<RequestDetail | null>(null)
const loadError = ref('')

async function load(id: number) {
  detail.value = null
  loadError.value = ''
  try {
    const { data } = await api.requestDetail(id)
    detail.value = data
  } catch (error) {
    if (statusOf(error) !== 401) loadError.value = errorDetail(error)
  }
}

watch(
  () => [open.value, props.recordId] as const,
  ([isOpen, id]) => {
    if (isOpen && id !== null && id !== undefined) load(id)
  },
  { immediate: true },
)

function body(value: string | null | undefined): string | null {
  return value === null || value === undefined || value === '' ? null : value
}
</script>

<template>
  <el-drawer v-model="open" size="620px" :with-header="false" class="req-drawer">
    <div class="dh">
      <h3>{{ t('requests.drawer.title') }} <span class="tag">req-log</span></h3>
      <button class="icon-btn" :title="t('common.close')" @click="open = false">✕</button>
    </div>

    <div v-if="loadError" class="warn-strip">⚠ {{ loadError }}</div>
    <div v-else-if="!detail" class="empty">{{ t('common.loading') }}</div>

    <div v-else class="db">
      <div class="kv">
        <div class="k">{{ t('requests.drawer.arrived') }}</div>
        <div class="v mono">
          {{ fmtClock(detail.ts) }}<span class="dim">{{ t('requests.drawer.utc', { time: fmtDateTime(detail.ts) }) }}</span>
        </div>
        <div class="k">{{ t('requests.drawer.finished') }}</div>
        <div class="v mono">{{ fmtClock(detail.ts_end) }}</div>
        <div class="k">{{ t('requests.drawer.id') }}</div>
        <div class="v mono">#{{ detail.id }}</div>
        <div class="k">{{ t('requests.drawer.method') }}</div>
        <div class="v">{{ detail.method }}</div>
        <div class="k">{{ t('requests.drawer.path') }}</div>
        <div class="v mono">{{ detail.path }}</div>
        <div class="k">{{ t('requests.drawer.query') }}</div>
        <div class="v mono">{{ detail.query || t('requests.drawer.emptyQuery') }}</div>
        <div class="k">{{ t('requests.drawer.requestedModel') }}</div>
        <div class="v mono">{{ detail.requested_model || '—' }}</div>
        <div class="k">{{ t('requests.drawer.resolvedModel') }}</div>
        <div class="v mono">{{ detail.model || '—' }}</div>
        <div class="k">{{ t('requests.drawer.upstream') }}</div>
        <div class="v">{{ detail.upstream || '—' }}</div>
        <div class="k">{{ t('requests.drawer.status') }}</div>
        <div class="v">
          <span class="badge" :class="statusBadge(detail.status).cls">{{ statusBadge(detail.status).text }}</span>
        </div>
        <div class="k">{{ t('requests.drawer.duration') }}</div>
        <div class="v mono">{{ fmtMs(detail.duration_ms) }}</div>
        <div class="k">TTFB</div>
        <div class="v mono">{{ fmtMs(detail.ttfb_ms) }}</div>
        <div class="k">{{ t('requests.drawer.stream') }}</div>
        <div class="v">{{ detail.stream ? t('common.yes') : t('common.no') }}</div>
        <div class="k">{{ t('requests.drawer.tokens') }}</div>
        <div class="v mono">
          {{ fmtInt(detail.tokens_in) }} / {{ fmtInt(detail.tokens_out) }}<span class="dim">{{
            t('requests.drawer.totalTokens', { n: fmtInt(detail.tokens) })
          }}</span>
        </div>
        <div class="k">{{ t('requests.drawer.size') }}</div>
        <div class="v mono">{{ fmtBytes(detail.req_size) }} / {{ fmtBytes(detail.res_size) }}</div>
        <div class="k">{{ t('requests.drawer.client') }}</div>
        <div class="v mono">{{ detail.client_ip || '—' }}</div>
        <div v-if="detail.error" class="k">{{ t('requests.drawer.error') }}</div>
        <div v-if="detail.error" class="v">{{ detail.error }}</div>
      </div>

      <div>
        <div class="card-h">
          {{ t('requests.drawer.requestBody') }}
          <span class="tag"
            >{{ t(detail.request_body_truncated ? 'requests.drawer.truncated8kb' : 'requests.drawer.full')
            }}{{ t('requests.drawer.redacted') }}</span
          >
        </div>
        <JsonPane v-if="body(detail.request_body)" :code="body(detail.request_body) ?? ''" height="320px" wrap />
        <div v-else class="empty">{{ t('requests.drawer.noRequestBody') }}</div>
      </div>

      <div>
        <div class="card-h">
          {{ t('requests.drawer.responseBody') }}
          <span class="tag">{{ t(detail.response_body_truncated ? 'requests.drawer.first8kb' : 'requests.drawer.full') }}</span>
        </div>
        <JsonPane v-if="body(detail.response_body)" :code="body(detail.response_body) ?? ''" height="320px" wrap />
        <div v-else class="empty">{{ t('requests.drawer.noResponseBody') }}</div>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.db {
  display: grid;
  gap: 18px;
}

.dh {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 14px;
}

.dh h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
}

.db .kv {
  grid-template-columns: 132px 1fr;
}

.dim {
  color: var(--text-dim);
  font-weight: 400;
}
</style>
