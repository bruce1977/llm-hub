<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  /** Anything renderable as JSON, or a raw string that is already formatted. */
  code?: unknown
  maxHeight?: string
}>()

function stringify(value: unknown): string {
  if (value === undefined) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2) ?? String(value)
  } catch {
    return String(value)
  }
}

// Escape first, then colour: after escaping there are no <, > or & left to smuggle in,
// so the token spans below can be injected safely.
function highlight(text: string): string {
  const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return escaped.replace(
    /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b|(#[^\n]*)/g,
    (_match, str, colon, bool, num, comment) => {
      if (str) return `<span class="${colon ? 'k' : 's'}">${str}</span>${colon || ''}`
      if (bool) return `<span class="b">${bool}</span>`
      if (num) return `<span class="n">${num}</span>`
      if (comment) return `<span class="c">${comment}</span>`
      return _match
    },
  )
}

const html = computed(() => highlight(stringify(props.code)))
</script>

<template>
  <pre class="code2" :style="maxHeight ? { maxHeight, overflow: 'auto' } : null" v-html="html"></pre>
</template>
