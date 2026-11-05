<script setup lang="ts">
import { ref } from 'vue'

// Edits a list of strings as removable chips plus a free-text input, mirroring the
// native `.inp` look used everywhere else. Enter / comma commits a tag; Backspace on an
// empty input removes the last one. This replaces el-select multiple + allow-create so the
// Settings form needs no Element Plus styling at all.
const props = defineProps<{
  modelValue?: string[]
  placeholder?: string
}>()
const emit = defineEmits<{ (e: 'update:modelValue', v: string[]): void }>()

const draft = ref('')

function commit(raw: string) {
  const parts = raw
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean)
  if (!parts.length) return
  const next = [...(props.modelValue || [])]
  for (const p of parts) if (!next.includes(p)) next.push(p)
  emit('update:modelValue', next)
  draft.value = ''
}

function removeAt(i: number) {
  const next = [...(props.modelValue || [])]
  next.splice(i, 1)
  emit('update:modelValue', next)
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault()
    commit(draft.value)
  } else if (e.key === 'Backspace' && !draft.value) {
    const list = props.modelValue || []
    if (list.length) removeAt(list.length - 1)
  }
}
</script>

<template>
  <div class="tag-editor">
    <div class="tag-list">
      <span v-for="(tag, i) in modelValue || []" :key="i" class="tag-chip">
        {{ tag }}
        <button type="button" class="tag-x" :title="'移除'" @click="removeAt(i)">✕</button>
      </span>
      <input
        class="tag-inp"
        v-model="draft"
        :placeholder="placeholder || '输入后回车添加'"
        @keydown="onKey"
        @blur="commit(draft)"
      />
    </div>
  </div>
</template>

<style scoped>
.tag-editor {
  width: 100%;
}
.tag-list {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  min-height: 34px;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: 7px;
}
.tag-list:focus-within {
  border-color: var(--accent);
}
.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  font-size: 12px;
  color: var(--accent);
  background: var(--accent-soft);
  border-radius: 5px;
  line-height: 1.5;
}
.tag-x {
  border: none;
  background: transparent;
  color: var(--accent);
  cursor: pointer;
  font-size: 11px;
  line-height: 1;
  padding: 0;
}
.tag-x:hover {
  color: var(--red);
}
.tag-inp {
  flex: 1 1 120px;
  min-width: 100px;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text);
  font-size: 12px;
  font-family: inherit;
  padding: 2px;
}
.tag-inp::placeholder {
  color: var(--text-mute);
}
</style>
