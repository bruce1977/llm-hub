<script setup lang="ts">
import { computed } from 'vue'
import type { CSSProperties } from 'vue'

const props = defineProps<{
  /** Anything renderable as JSON, or a raw string that is already formatted. */
  code?: unknown
  maxHeight?: string
  /** Fixed height instead of max-height: the pane never grows, it scrolls. */
  height?: string
  /** Soft-wrap long lines instead of scrolling horizontally (req/res bodies). */
  wrap?: boolean
}>()

function stringify(value: unknown): string {
  if (value === undefined) return ''
  if (typeof value === 'string') {
    // req/res bodies are stored as the raw single-line JSON the client sent. Pretty-print
    // it when it parses (so the pane is readable without horizontal scrolling); a body
    // truncated at 8 KB or a non-JSON payload simply falls back to the raw text.
    const trimmed = value.trim()
    if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
      try {
        return JSON.stringify(JSON.parse(trimmed), null, 2)
      } catch {
        return value
      }
    }
    return value
  }
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

// A fixed height wins over max-height: req/res bodies get a stable block that scrolls
// instead of pushing the drawer, and `wrap` keeps one long JSON line readable.
const paneStyle = computed<CSSProperties | null>(() => {
  if (!props.height && !props.maxHeight) return null
  // Wrapped text never needs a horizontal scrollbar; hiding it also avoids a stray
  // scrollbar when one token happens to be wider than the pane.
  return {
    ...(props.height ? { height: props.height } : { maxHeight: props.maxHeight }),
    overflowY: 'auto',
    overflowX: props.wrap ? 'hidden' : 'auto',
  }
})
</script>

<template>
  <pre class="code2" :class="{ wrap }" :style="paneStyle" v-html="html"></pre>
</template>
