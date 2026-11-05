<script setup lang="ts">
import KeyValueEditor from '@/components/KeyValueEditor.vue'
import TagListEditor from '@/components/TagListEditor.vue'
import type { ConfigUpstream } from '@/types/api'
import { useI18n } from 'vue-i18n'

// One editable upstream row. The `upstream` object is passed by reference, so writing to
// its fields (native input / select / KeyValueEditor / TagListEditor v-model) mutates the
// parent's array entry directly - no reassign of the prop binding itself.
const props = defineProps<{
  upstream: ConfigUpstream
  index: number
}>()
const emit = defineEmits<{ (e: 'remove', index: number): void }>()

const { t } = useI18n()
const UPSTREAM_TYPES = ['ollama', 'lmstudio', 'opencode']

function fld(key: string): string {
  return t(`settings.fields.${key}`)
}
</script>

<template>
  <div class="card up-card">
    <div class="up-head">
      <strong>{{ upstream.name || '(unnamed)' }}</strong>
      <button class="btn danger" type="button" @click="emit('remove', index)">
        {{ t('common.remove') }}
      </button>
    </div>
    <div class="fform">
      <div class="frow">
        <label>{{ fld('name') }}</label>
        <div class="fctl"><input class="inp w-full" v-model="upstream.name" /></div>
      </div>
      <div class="frow">
        <label>{{ fld('type') }}</label>
        <div class="fctl">
          <select class="sel" v-model="upstream.type" style="width: 220px">
            <option v-for="tp in UPSTREAM_TYPES" :key="tp" :value="tp">{{ tp }}</option>
          </select>
        </div>
      </div>
      <div class="frow">
        <label>{{ fld('base_url') }}</label>
        <div class="fctl">
          <input class="inp w-full" v-model="upstream.base_url" placeholder="http://host:port" />
        </div>
      </div>
      <div class="frow">
        <label>{{ fld('models') }}</label>
        <div class="fctl">
          <TagListEditor v-model="upstream.models" :placeholder="t('settings.tagPlaceholder')" />
        </div>
      </div>
      <div class="frow">
        <label>{{ fld('model_aliases') }}</label>
        <div class="fctl">
          <KeyValueEditor
            v-model="upstream.model_aliases"
            placeholder-key="short"
            placeholder-value="real id"
          />
        </div>
      </div>
      <div class="frow">
        <label>{{ fld('timeout') }}</label>
        <div class="fctl">
          <input
            class="inp"
            type="number"
            min="0"
            step="1"
            v-model.number="upstream.timeout"
            style="width: 220px"
          />
        </div>
      </div>
      <div class="frow">
        <label>{{ fld('priority') }}</label>
        <div class="fctl">
          <input
            class="inp"
            type="number"
            min="0"
            step="1"
            v-model.number="upstream.priority"
            style="width: 220px"
          />
        </div>
      </div>
      <div class="frow">
        <label>{{ fld('description') }}</label>
        <div class="fctl">
          <textarea class="inp w-full" rows="2" v-model="upstream.description"></textarea>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.up-card {
  margin-bottom: 14px;
  padding-bottom: 4px;
}
.up-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
</style>
