<script setup lang="ts">
import { ref, watch } from 'vue'

// Edits a `Record<string, any>` as key/value rows. Values are lightly coerced on emit
// (true/false/null, ints, floats) so numeric options like `num_ctx` survive round-trips.
const props = defineProps<{
  modelValue?: Record<string, unknown>
  placeholderKey?: string
  placeholderValue?: string
}>()
const emit = defineEmits<{ (e: 'update:modelValue', v: Record<string, unknown>): void }>()

interface Row {
  key: string
  value: string
}

const rows = ref<Row[]>([])
let lastEmitted: Record<string, unknown> | null = null

function syncFromProp() {
  const obj = props.modelValue || {}
  rows.value = Object.entries(obj).map(([k, v]) => ({ key: k, value: String(v) }))
}

function coerce(raw: string): unknown {
  const t = raw.trim()
  if (t === 'true') return true
  if (t === 'false') return false
  if (t === 'null') return null
  if (/^-?\d+$/.test(t)) return parseInt(t, 10)
  if (/^-?\d*\.\d+$/.test(t)) return parseFloat(t)
  return raw
}

function buildObject(): Record<string, unknown> {
  const obj: Record<string, unknown> = {}
  for (const r of rows.value) {
    if (r.key.trim() === '') continue
    obj[r.key.trim()] = coerce(r.value)
  }
  return obj
}

function emitValue() {
  const obj = buildObject()
  lastEmitted = obj
  emit('update:modelValue', obj)
}

function addRow() {
  rows.value.push({ key: '', value: '' })
}
function removeRow(i: number) {
  rows.value.splice(i, 1)
  emitValue()
}

syncFromProp()
// Re-sync only when the parent replaces the object (e.g. a fresh config load), never for
// our own emit - that would rebuild the rows and steal input focus mid-keystroke.
watch(
  () => props.modelValue,
  (v) => {
    if (v === lastEmitted) return
    syncFromProp()
  },
)
</script>

<template>
  <div class="kv-editor">
    <div v-for="(r, i) in rows" :key="i" class="kv-row">
      <input
        class="kv-k inp"
        v-model="r.key"
        :placeholder="placeholderKey || 'key'"
        @input="emitValue"
      />
      <input
        class="kv-v inp"
        v-model="r.value"
        :placeholder="placeholderValue || 'value'"
        @input="emitValue"
      />
      <button class="kv-del btn ghost" type="button" :title="'移除'" @click="removeRow(i)">✕</button>
    </div>
    <button class="kv-add btn ghost" type="button" @click="addRow">+ {{ $t('common.add') }}</button>
  </div>
</template>

<style scoped>
.kv-editor {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.kv-row {
  display: flex;
  gap: 6px;
  align-items: center;
}
.kv-k {
  flex: 1 1 40%;
  min-width: 0;
}
.kv-v {
  flex: 1 1 60%;
  min-width: 0;
}
.kv-del {
  flex: 0 0 auto;
  padding: 2px 8px;
  line-height: 1.4;
}
.kv-add {
  align-self: flex-start;
  padding: 2px 10px;
}
</style>
