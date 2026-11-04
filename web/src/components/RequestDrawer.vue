<script setup lang="ts">
import { ref, watch } from 'vue'

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
      <h3>请求详情 <span class="tag">req-log</span></h3>
      <button class="icon-btn" title="关闭" @click="open = false">✕</button>
    </div>

    <div v-if="loadError" class="warn-strip">⚠ {{ loadError }}</div>
    <div v-else-if="!detail" class="empty">加载中…</div>

    <div v-else class="db">
      <div class="kv">
        <div class="k">到达时间</div>
        <div class="v mono">{{ fmtClock(detail.ts) }}<span class="dim">（{{ fmtDateTime(detail.ts) }} UTC）</span></div>
        <div class="k">完成时间</div>
        <div class="v mono">{{ fmtClock(detail.ts_end) }}</div>
        <div class="k">编号</div>
        <div class="v mono">#{{ detail.id }}</div>
        <div class="k">方法</div>
        <div class="v">{{ detail.method }}</div>
        <div class="k">路径</div>
        <div class="v mono">{{ detail.path }}</div>
        <div class="k">查询串</div>
        <div class="v mono">{{ detail.query || '(空)' }}</div>
        <div class="k">请求模型</div>
        <div class="v mono">{{ detail.requested_model || '—' }}</div>
        <div class="k">解析后</div>
        <div class="v mono">{{ detail.model || '—' }}</div>
        <div class="k">上游</div>
        <div class="v">{{ detail.upstream || '—' }}</div>
        <div class="k">状态</div>
        <div class="v">
          <span class="badge" :class="statusBadge(detail.status).cls">{{ statusBadge(detail.status).text }}</span>
        </div>
        <div class="k">耗时</div>
        <div class="v mono">{{ fmtMs(detail.duration_ms) }}</div>
        <div class="k">TTFB</div>
        <div class="v mono">{{ fmtMs(detail.ttfb_ms) }}</div>
        <div class="k">流式</div>
        <div class="v">{{ detail.stream ? '是' : '否' }}</div>
        <div class="k">Token</div>
        <div class="v mono">{{ fmtInt(detail.tokens) }}</div>
        <div class="k">请求 / 响应大小</div>
        <div class="v mono">{{ fmtBytes(detail.req_size) }} / {{ fmtBytes(detail.res_size) }}</div>
        <div class="k">客户端</div>
        <div class="v mono">{{ detail.client_ip || '—' }}</div>
        <div v-if="detail.error" class="k">错误</div>
        <div v-if="detail.error" class="v">{{ detail.error }}</div>
      </div>

      <div>
        <div class="card-h">
          请求体
          <span class="tag">{{ detail.request_body_truncated ? '已截断 8KB' : '完整' }} · 敏感字段脱敏</span>
        </div>
        <JsonPane v-if="body(detail.request_body)" :code="body(detail.request_body) ?? ''" max-height="320px" />
        <div v-else class="empty">未记录请求体</div>
      </div>

      <div>
        <div class="card-h">
          响应体
          <span class="tag">{{ detail.response_body_truncated ? '首 8KB' : '完整' }}</span>
        </div>
        <JsonPane v-if="body(detail.response_body)" :code="body(detail.response_body) ?? ''" max-height="320px" />
        <div v-else class="empty">未记录响应体</div>
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
