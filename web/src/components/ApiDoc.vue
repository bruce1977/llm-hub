<script setup lang="ts">
import JsonPane from '@/components/JsonPane.vue'
import type { ApiParam, ApiRequestSpec, ApiResponseSpec } from '@/types/ui'

withDefaults(
  defineProps<{
    title?: string
    tag?: string
    params?: ApiParam[]
    request: ApiRequestSpec
    response: ApiResponseSpec
    note?: string
  }>(),
  { title: '请求 API 格式', tag: '', params: () => [], note: '' },
)
</script>

<template>
  <div>
    <div class="view-title">{{ title }} <span v-if="tag" class="tag">{{ tag }}</span></div>
    <div class="api-doc">
      <template v-if="params.length">
        <div class="card-h">请求参数 <span class="tag">{{ request.method }} {{ request.path }}</span></div>
        <table class="param-table">
          <thead>
            <tr>
              <th>参数</th>
              <th>类型</th>
              <th>必填</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in params" :key="p.name">
              <td class="pn">{{ p.name }}</td>
              <td class="pt">{{ p.type }}</td>
              <td class="pr" :class="{ no: !p.required }">{{ p.required ? '是' : '否' }}</td>
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
  </div>
</template>
