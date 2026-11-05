<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import ApiTester from '@/components/ApiTester.vue'
import JsonPane from '@/components/JsonPane.vue'
import type { ApiParam, ApiRequestSpec, ApiResponseSpec, TestSpec } from '@/types/ui'

withDefaults(
  defineProps<{
    title?: string
    tag?: string
    params?: ApiParam[]
    request: ApiRequestSpec
    response: ApiResponseSpec
    note?: string
    /** Live test window, opened from the button at the end of the parameters header. */
    tester?: TestSpec
  }>(),
  { title: '', tag: '', params: () => [], note: '', tester: undefined },
)

const { t } = useI18n()

const testerOpen = ref(false)
// Mounted on the first open and only hidden afterwards, so the model probe stays lazy while a
// closed dialog never throws away what was typed or the answer that came back.
const testerMounted = ref(false)

function openTester(): void {
  testerMounted.value = true
  testerOpen.value = true
}
</script>

<template>
  <div>
    <div class="view-title">{{ title || t('apiDoc.defaultTitle') }} <span v-if="tag" class="tag">{{ tag }}</span></div>
    <div class="api-doc">
      <template v-if="params.length || tester">
        <div class="card-h">
          <span>{{ t('apiDoc.params') }} <span class="tag">{{ request.method }} {{ request.path }}</span></span>
          <button v-if="tester" class="btn tester-open" type="button" @click="openTester">
            {{ t('apiDoc.test') }}
          </button>
        </div>
        <table v-if="params.length" class="param-table">
          <thead>
            <tr>
              <th>{{ t('apiDoc.param') }}</th>
              <th>{{ t('apiDoc.type') }}</th>
              <th>{{ t('apiDoc.required') }}</th>
              <th>{{ t('apiDoc.desc') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in params" :key="p.name">
              <td class="pn">{{ p.name }}</td>
              <td class="pt">{{ p.type }}</td>
              <td class="pr" :class="{ no: !p.required }">{{ p.required ? t('common.yes') : t('common.no') }}</td>
              <td class="pd">{{ p.desc }}</td>
            </tr>
          </tbody>
        </table>
      </template>

      <div class="api-grid">
        <div class="api-pane">
          <div class="ph">
            <span class="pill" :class="request.method === 'GET' ? 'get' : 'post'">{{ request.method }}</span>
            <span class="path">{{ request.path }}</span>
          </div>
          <div class="pb">
            <div class="hdrs">
              <template v-for="(header, index) in request.headers" :key="header[0]">
                <b>{{ header[0] }}</b>: {{ header[1] }}<br v-if="index < request.headers.length - 1" />
              </template>
            </div>
            <JsonPane :code="request.body" />
          </div>
        </div>

        <div class="api-pane">
          <div class="ph">
            <span class="pill">{{ response.status }}</span>
            <span class="path">{{ response.contentType }}</span>
          </div>
          <div class="pb">
            <div v-if="response.note" class="hdrs">{{ response.note }}</div>
            <JsonPane :code="response.body" />
          </div>
        </div>
      </div>

      <div v-if="note" class="api-note">{{ note }}</div>
    </div>

    <div v-if="testerMounted" v-show="testerOpen" class="modal-overlay" @click.self="testerOpen = false">
      <div class="modal" role="dialog" aria-modal="true" :aria-label="`${t('apiTester.title')} ${request.path}`">
        <div class="modal-head">
          <div>
            <div class="modal-title">{{ t('apiTester.title') }}</div>
            <div class="modal-sub mono">{{ request.method }} {{ request.path }}</div>
          </div>
          <button class="icon-btn" type="button" :aria-label="t('common.close')" @click="testerOpen = false">✕</button>
        </div>
        <div class="modal-body">
          <ApiTester :kind="tester!.kind" :fields="tester!.fields" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tester-open {
  margin-left: auto;
}

.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.55);
}

.modal {
  display: flex;
  flex-direction: column;
  width: min(940px, 94vw);
  max-height: 88vh;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45);
}

.modal-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
}

.modal-title {
  font-size: 15px;
  font-weight: 600;
}

.modal-sub {
  font-size: 12px;
  color: var(--text-dim);
  margin-top: 2px;
}

.modal-body {
  padding: 16px;
  overflow-y: auto;
}
</style>