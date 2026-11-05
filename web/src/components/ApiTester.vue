<script setup lang="ts">
import { computed, nextTick, onMounted, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { errorDetail } from '@/api/client'
import { api } from '@/api/management'
import JsonPane from '@/components/JsonPane.vue'
import { useApiTestContext } from '@/composables/apiTestContext'
import type { ApiTestKind, ApiTestModel, ApiTestResult } from '@/types/api'
import type { TestField } from '@/types/ui'
import { fmtInt, fmtMs } from '@/utils/format'

const props = defineProps<{
  /** Which documented endpoint this window tests (`app/api_test.py` KINDS). */
  kind: ApiTestKind
  /** The parameters the form exposes, with their documented defaults. */
  fields: TestField[]
}>()

const { t } = useI18n()
const {
  loading: contextLoading,
  loaded: contextLoaded,
  error: contextError,
  load,
  kindContext,
  maxResponseBytes,
} = useApiTestContext()
const context = computed(() => kindContext(props.kind))
const listId = useId()

/** Initial values are filled in from the start - the form shows what the endpoint would have
 *  used anyway, and 「清空」 is what leaves a key out of the body. Every field gets a value so a
 *  select with no default still shows its "not set" option instead of nothing. */
function defaultsOf(fields: TestField[]): Record<string, string> {
  const seeded: Record<string, string> = {}
  for (const field of fields) seeded[field.key] = field.def ?? ''
  return seeded
}

function blankOf(fields: TestField[]): Record<string, string> {
  const blank: Record<string, string> = {}
  for (const field of fields) blank[field.key] = isFlag(field) ? 'false' : ''
  return blank
}

const values = ref<Record<string, string>>(defaultsOf(props.fields))

// A view can learn a default only after its own config arrives (System One takes its example
// from that projection), so a later field list still fills every box the caller has not typed
// into yet.
watch(
  () => props.fields,
  (fields) => {
    const seeded = defaultsOf(fields)
    for (const [key, value] of Object.entries(seeded)) {
      if (!values.value[key]) values.value[key] = value
    }
  },
)

const pickedModel = ref('')
const manualModel = ref('')
/** `systemone` / `websearch` accept names the list does not carry, so their select offers a
 *  「自定义模型…」 entry that swaps in the free-text box instead of typing into the dropdown. */
const customMode = ref(false)
const running = ref(false)
const result = ref<ApiTestResult | null>(null)
const formError = ref('')
const sendError = ref('')

// The selector starts on the model the backend preselected (the first usable one, or the
// configured `systemone.model`); a web-search tester stays empty because omitting `model`
// lets the provider apply its own default.
watch(
  context,
  (current) => {
    if (current?.default_model && !pickedModel.value) pickedModel.value = current.default_model
  },
  { immediate: true },
)

onMounted(() => {
  void load()
})

const models = computed<ApiTestModel[]>(() => context.value?.models ?? [])
const allowsFreeModel = computed(() => context.value?.free_model === true)
const selected = computed<ApiTestModel | null>(
  () => models.value.find((row) => row.model === pickedModel.value) ?? null,
)
const model = computed(() => (customMode.value ? manualModel.value.trim() : pickedModel.value))

/** The sentinel `<option>` that opens the free-text box. It never becomes the model itself. */
const CUSTOM_OPTION = '__custom__'

/** One select for every window: listed names, plus (free-model kinds only) the custom entry.
 *  Picking the custom entry clears the selection so the box starts empty; picking a listed name
 *  leaves custom mode, which is how a user gets back to the dropdown after typing. */
const modelChoice = computed({
  get: () => (customMode.value ? CUSTOM_OPTION : pickedModel.value),
  set: (value: string) => {
    manualModel.value = ''
    if (value === CUSTOM_OPTION) {
      pickedModel.value = ''
      customMode.value = true
      return
    }
    pickedModel.value = value
    customMode.value = false
  },
})

/** The free-text box mounts only in custom mode, so it takes the caret explicitly. */
const customInput = ref<HTMLInputElement | null>(null)
watch(customMode, async (active) => {
  if (!active) return
  await nextTick()
  customInput.value?.focus()
})

/** The selector lists names only - the alias when one is configured, otherwise the name from
 *  `config.yaml`. Everything else about the row stays in the hover tooltip. */
function optionLabel(row: ApiTestModel): string {
  return row.display
}

/** Which real model the typed name maps to, and what to know when it maps to nothing. */
function optionTitle(row: ApiTestModel): string {
  const parts: string[] = []
  if (row.unknown) parts.push(t('apiTester.unservedTip'))
  else if (row.derived) parts.push(t('common.derivedFrom', { target: row.target ?? '' }))
  else if (row.alias) parts.push(t('common.realModel', { model: row.model }))
  if (row.upstream) parts.push(row.upstream)
  return parts.join(' · ')
}

// --------------------------------------------------------------------------- //
// Values -> request body. An empty input is left out entirely, so the endpoint answers with
// its own default; only what the user typed is sent - except a documented boolean flag, which
// is a checkbox and always carries true / false.
// --------------------------------------------------------------------------- //
/** A boolean with a documented value is a real flag and renders as a checkbox, so unchecked
 *  means "send false", never "omit". Without a `def` it stays a three-state select where
 *  "not set" still omits the key (System One's optional `stream`, for example). */
function isFlag(field: TestField): boolean {
  return field.type === 'bool' && field.def !== undefined
}

function setFlag(key: string, event: Event): void {
  values.value[key] = (event.target as HTMLInputElement).checked ? 'true' : 'false'
}

/** `v-model` on a `type="number"` input hands back a number, not a string, so every read goes
 *  through `String` before anything string-like touches it. */
function trimmed(key: string): string {
  return String(values.value[key] ?? '').trim()
}

function asNumber(raw: string): number | null {
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
}

function fieldProblem(field: TestField): string {
  const raw = trimmed(field.key)
  if (!raw) return ''
  if (field.type === 'number') return asNumber(raw) === null ? t('apiTester.badNumber') : ''
  if (field.type === 'json') {
    try {
      JSON.parse(raw)
    } catch {
      return t('apiTester.badJson')
    }
  }
  return ''
}

const problems = computed<Record<string, string>>(() => {
  const found: Record<string, string> = {}
  for (const field of props.fields) {
    const problem = fieldProblem(field)
    if (problem) found[field.key] = problem
  }
  return found
})

function assign(target: Record<string, unknown>, path: string, value: unknown): void {
  const parts = path.split('.')
  let node = target
  for (const part of parts.slice(0, -1)) {
    const child = node[part]
    if (child === null || typeof child !== 'object' || Array.isArray(child)) node[part] = {}
    node = node[part] as Record<string, unknown>
  }
  node[parts[parts.length - 1]] = value
}

function buildBody(): Record<string, unknown> {
  const body: Record<string, unknown> = {}
  if (model.value) body.model = model.value
  for (const field of props.fields) {
    const raw = trimmed(field.key)
    if (isFlag(field)) {
      assign(body, field.key, raw === 'true')
      continue
    }
    if (!raw) continue
    let value: unknown = raw
    if (field.type === 'number') value = asNumber(raw)
    else if (field.type === 'bool') value = raw === 'true'
    else if (field.type === 'lines') value = raw.split('\n').map((line) => line.trim()).filter(Boolean)
    else if (field.type === 'json') value = JSON.parse(raw)
    assign(body, field.key, value)
  }
  return body
}

const preview = computed<string>(() => {
  if (Object.keys(problems.value).length) return ''
  try {
    return JSON.stringify(buildBody(), null, 2)
  } catch {
    return ''
  }
})

function clearModel(): void {
  pickedModel.value = ''
  manualModel.value = ''
  customMode.value = false
}

function restoreDefaults(): void {
  values.value = defaultsOf(props.fields)
  clearModel()
  const fallback = context.value?.default_model ?? ''
  if (fallback) pickedModel.value = fallback
}

function clearForm(): void {
  values.value = blankOf(props.fields)
  clearModel()
}

function placeholderFor(field: TestField): string {
  if (field.def === undefined) return ''
  return field.type === 'text' || field.type === 'textarea'
    ? t('apiTester.defaultIs', { value: field.def })
    : field.def
}

function selectLabel(field: TestField, option: string): string {
  if (option === '') return t('apiTester.unset')
  return option === field.def ? t('apiTester.withDefault', { value: option }) : option
}

// --------------------------------------------------------------------------- //
// Run + read back
// --------------------------------------------------------------------------- //
async function run(): Promise<void> {
  const current = context.value
  if (!current || running.value) return
  formError.value = ''
  sendError.value = ''
  if (Object.keys(problems.value).length) {
    formError.value = t('apiTester.fixProblems')
    return
  }
  if (current.model_required && !model.value) {
    formError.value = t('apiTester.needModel')
    return
  }

  running.value = true
  result.value = null
  try {
    const response = await api.apiTestRun(current.kind, buildBody())
    result.value = response.data
  } catch (error) {
    // The route itself refused (unknown kind, missing model, no credential): the request
    // never reached the endpoint, so there is no envelope to show.
    sendError.value = errorDetail(error)
  } finally {
    running.value = false
  }
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

/** The text a chat/generate answer carries, whether the endpoint returned one JSON object or
 *  streamed NDJSON / SSE chunks. */
function textOf(value: Record<string, unknown> | null): string {
  if (!value) return ''
  if (typeof value.response === 'string') return value.response
  const choice = record(Array.isArray(value.choices) ? value.choices[0] : null)
  const message = record(choice?.message) ?? choice?.delta
  const part = record(message)
  return typeof part?.content === 'string' ? part.content : ''
}

function streamedText(text: string): string {
  let answer = ''
  for (const line of text.split('\n')) {
    const raw = line.trim()
    if (!raw) continue
    const payload = raw.startsWith('data:') ? raw.slice(5).trim() : raw
    if (!payload || payload === '[DONE]') continue
    const parsed = record(safeParse(payload))
    if (parsed) answer += textOf(parsed)
  }
  return answer
}

function safeParse(raw: string): unknown {
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

const answer = computed<string>(() => {
  const current = result.value
  if (!current) return ''
  const fromBody = textOf(record(current.body))
  if (fromBody) return fromBody
  return chunked(current) ? streamedText(current.text) : ''
})

/** `stream` is the transport the gateway chose, which for an omitted flag is its own default;
 *  the caller only sees chunked text when the response really came as NDJSON / SSE. */
function chunked(current: ApiTestResult): boolean {
  return /ndjson|event-stream/i.test(current.content_type ?? '')
}

const timings = computed<string[]>(() => {
  const current = result.value
  if (!current) return []
  const lines = [t('apiTester.duration', { value: fmtMs(current.duration_ms) })]
  if (current.ttft_ms !== null && chunked(current)) lines.push(t('apiTester.ttft', { value: fmtMs(current.ttft_ms) }))
  if (current.content_type) lines.push(current.content_type)
  return lines
})

/** `q35 → qwen3:8b @ ollama-gpu` plus the parameters the alias forces, so the tester shows
 *  what the gateway actually routed instead of leaving the caller to guess. */
const routed = computed<string>(() => {
  const current = result.value
  if (!current?.model) return ''
  const head =
    current.requested_model && current.requested_model !== current.model
      ? `${current.requested_model} → ${current.model}`
      : current.model
  return current.upstream ? `${head} @ ${current.upstream}` : head
})

const injected = computed<string>(() => {
  const params = result.value?.injected
  if (!params) return ''
  return Object.entries(params)
    .map(([key, value]) => `${key}=${typeof value === 'string' ? value : JSON.stringify(value)}`)
    .join(', ')
})

const keyLines = computed(() => {
  const current = context.value
  if (!current) return []
  const lines: { label: string; mask: string; env: string }[] = []
  if (current.api_key)
    lines.push({ label: t('apiTester.providerKey'), mask: current.api_key, env: current.api_key_env ?? '' })
  if (selected.value?.api_key)
    lines.push({
      label: t('apiTester.upstreamKey', { upstream: selected.value.upstream ?? '' }),
      mask: selected.value.api_key,
      env: '',
    })
  return lines
})

const truncationNote = computed(() => t('apiTester.truncated', { size: fmtInt(maxResponseBytes.value / 1024) }))
</script>

<template>
  <div class="tester">
    <div class="meta">
      <span v-if="context && !context.enabled" class="badge err">{{ t('apiTester.disabled') }}</span>
      <span v-if="running" class="badge ok">{{ t('apiTester.running') }}</span>
      <span class="spacer" />
      <button class="btn" type="button" :disabled="contextLoading" @click="load(true)">
        {{ contextLoading ? t('apiTester.probing') : t('apiTester.reprobe') }}
      </button>
    </div>

    <div class="tb">
      <div v-if="contextLoading || !contextLoaded" class="wide note">
        {{ contextError ? t('apiTester.probeFailed', { detail: contextError }) : t('common.loading') }}
      </div>

      <template v-else-if="context">
        <div class="f">
          <label :for="allowsFreeModel && customMode ? `${listId}-model-custom` : `${listId}-model`">
            {{ t('apiTester.model') }}
          </label>
          <div class="model-row">
            <select :id="`${listId}-model`" v-model="modelChoice" class="sel">
              <option value="">{{ t('apiTester.pickModel') }}</option>
              <option
                v-for="row in models"
                :key="row.model"
                :value="row.model"
                :title="optionTitle(row)"
              >
                {{ optionLabel(row) }}
              </option>
              <option v-if="allowsFreeModel" :value="CUSTOM_OPTION">
                {{ t('apiTester.customModel') }}
              </option>
            </select>
            <button
              v-if="model || customMode"
              class="btn clr"
              type="button"
              :title="t('apiTester.clearModel')"
              :aria-label="t('apiTester.clearModel')"
              @click="clearModel"
            >
              ×
            </button>
          </div>
          <template v-if="allowsFreeModel && customMode">
            <input
              :id="`${listId}-model-custom`"
              ref="customInput"
              v-model="manualModel"
              class="inp"
              type="text"
              :list="`${listId}-model-list`"
              :placeholder="t('apiTester.modelOrType')"
            />
            <datalist :id="`${listId}-model-list`">
              <option
                v-for="row in models"
                :key="row.model"
                :value="row.model"
                :label="optionLabel(row)"
              />
            </datalist>
          </template>
          <div class="def">
            {{ t('apiTester.fromSource', { source: context.source }) }}
            <template v-if="context.model_required">{{ t('apiTester.requiredTag') }}</template>
          </div>
        </div>

        <div class="f">
          <label>{{ t('apiTester.credentials') }}</label>
          <div v-if="keyLines.length" class="keys">
            <div v-for="line in keyLines" :key="line.label" class="key">
              <span>{{ line.label }}</span>
              <code>{{ line.mask }}</code>
              <code v-if="line.env" class="env">{{ line.env }}</code>
            </div>
          </div>
          <div v-else class="def">{{ t('apiTester.noKey') }}</div>
          <div class="def">{{ t('apiTester.keyNote') }}</div>
        </div>

        <div v-for="field in fields" :key="field.key" class="f" :class="{ wide: field.wide }">
          <label v-if="isFlag(field)" class="check" :for="`${listId}-${field.key}`">
            <input
              :id="`${listId}-${field.key}`"
              type="checkbox"
              :checked="trimmed(field.key) === 'true'"
              @change="setFlag(field.key, $event)"
            />
            <span>
              <code class="key-name">{{ field.key }}</code> {{ field.label }}
              <span v-if="field.required" class="req">*</span>
            </span>
          </label>

          <template v-else>
            <label :for="`${listId}-${field.key}`">
              <code class="key-name">{{ field.key }}</code> {{ field.label }}
              <span v-if="field.required" class="req">*</span>
            </label>

            <textarea
              v-if="field.type === 'textarea' || field.type === 'json' || field.type === 'lines'"
              :id="`${listId}-${field.key}`"
              v-model="values[field.key]"
              class="inp long"
              :class="{ bad: problems[field.key] }"
              :placeholder="placeholderFor(field)"
              rows="3"
              spellcheck="false"
            />

            <select
              v-else-if="field.type === 'bool' || field.type === 'select'"
              :id="`${listId}-${field.key}`"
              v-model="values[field.key]"
              class="sel"
            >
              <option
                v-for="option in field.type === 'bool' ? ['', 'true', 'false'] : field.options ?? []"
                :key="option"
                :value="option"
              >
                {{ selectLabel(field, option) }}
              </option>
            </select>

            <input
              v-else
              :id="`${listId}-${field.key}`"
              v-model="values[field.key]"
              class="inp"
              :class="{ bad: problems[field.key] }"
              :type="field.type === 'number' ? 'number' : 'text'"
              :placeholder="placeholderFor(field)"
            />

            <div v-if="problems[field.key]" class="bad-text">{{ problems[field.key] }}</div>
          </template>

          <div v-if="field.hint" class="def">{{ field.hint }}</div>
        </div>

        <div class="actions wide">
          <button class="btn primary" type="button" :disabled="running" @click="run">
            {{ running ? t('apiTester.running') : t('apiTester.send') }}
          </button>
          <button class="btn" type="button" :disabled="running" @click="restoreDefaults">
            {{ t('apiTester.restoreDefaults') }}
          </button>
          <button class="btn" type="button" :disabled="running" @click="clearForm">
            {{ t('apiTester.clear') }}
          </button>
          <span v-if="formError" class="bad-text">{{ formError }}</span>
          <span v-else-if="sendError" class="bad-text">{{ sendError }}</span>
        </div>

        <details v-if="preview" class="wide">
          <summary>{{ t('apiTester.preview') }}</summary>
          <JsonPane :code="preview" maxHeight="180px" wrap />
        </details>

        <div v-if="result" class="result wide">
          <div class="rline">
            <span class="badge" :class="result.ok ? 'ok' : 'err'">{{ result.status }}</span>
            <span v-if="result.error" class="badge err">{{ result.error }}</span>
            <span v-for="line in timings" :key="line" class="chip s2">{{ line }}</span>
            <span v-if="chunked(result)" class="chip s2">{{ t('apiTester.stream') }}</span>
          </div>

          <div v-if="routed" class="def mono">
            {{ t('apiTester.routedTo', { route: routed }) }}
            <template v-if="injected"> · {{ t('apiTester.injected', { params: injected }) }}</template>
          </div>

          <div v-if="answer" class="block">
            <div class="block-h">{{ t('apiTester.answer') }}</div>
            <pre class="answer">{{ answer }}</pre>
          </div>

          <div v-if="result.body !== null" class="block">
            <div class="block-h">{{ t('apiTester.rawBody') }}</div>
            <JsonPane :code="result.body" height="260px" wrap />
          </div>
          <div v-else-if="result.text" class="block">
            <div class="block-h">{{ t('apiTester.rawBody') }}</div>
            <pre class="answer tall">{{ result.text }}</pre>
          </div>

          <div v-if="result.truncated" class="note">{{ truncationNote }}</div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.tester {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.meta {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 12px;
}

.meta .spacer {
  flex: 1;
}

.tb {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px 14px;
}

.f,
.result {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.f label {
  font-size: 11px;
  color: var(--text-dim);
}

.check {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  line-height: 1.6;
  cursor: pointer;
}

.check input {
  margin: 2px 0 0;
  accent-color: var(--violet);
}

.key-name {
  font-family: ui-monospace, Monaco, Consolas, monospace;
  font-size: 10.5px;
  color: var(--text-mute);
}

.req {
  color: var(--red);
}

.wide {
  grid-column: 1 / -1;
}

.inp,
.sel {
  width: 100%;
}

.model-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.model-row .inp,
.model-row .sel {
  flex: 1;
  width: auto;
  min-width: 0;
}

.model-row .clr {
  flex: none;
  padding: 4px 9px;
  font-size: 14px;
  line-height: 1.2;
}

.inp.long {
  font-family: ui-monospace, Monaco, Consolas, monospace;
  resize: vertical;
  line-height: 1.5;
}

.inp.bad {
  border-color: var(--red);
}

.bad-text {
  font-size: 11px;
  color: var(--red);
}

.def {
  font-size: 10.5px;
  color: var(--text-mute);
  line-height: 1.6;
}

.def.mono {
  font-family: ui-monospace, Monaco, Consolas, monospace;
}

.note {
  font-size: 11.5px;
  color: var(--text-dim);
}

.keys {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 11px;
  color: var(--text-dim);
}

.keys .key {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}

.keys code {
  font-family: ui-monospace, Monaco, Consolas, monospace;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 1px 6px;
  color: var(--violet);
}

.keys .env {
  color: var(--text-mute);
}

.actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 2px;
}

summary {
  font-size: 11px;
  color: var(--text-dim);
  cursor: pointer;
}

.result {
  gap: 8px;
  border-top: 1px solid var(--border);
  padding-top: 12px;
}

.rline {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.block-h {
  font-size: 10.5px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  color: var(--text-mute);
}

.answer {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 11px 12px;
  font-family: ui-monospace, Monaco, Consolas, monospace;
  font-size: 11.5px;
  line-height: 1.65;
  color: var(--text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 260px;
  overflow: auto;
}

.answer.tall {
  color: var(--text-dim);
}

@media (max-width: 900px) {
  .tb {
    grid-template-columns: 1fr;
  }
}
</style>
