<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { errorDetail } from '@/api/client'
import { api } from '@/api/management'
import TopBar from '@/components/TopBar.vue'
import type { ApiKeyItem, ApiKeysResponse } from '@/types/api'
import { fmtDateTime } from '@/utils/format'

const { t } = useI18n()

const data = ref<ApiKeysResponse | null>(null)
const refreshing = ref(false)
const lastUpdated = ref('')
const loadError = ref('')
const actionError = ref('')

/** Row id awaiting a second click, so revoking is deliberate rather than a stray hit. */
const confirming = ref<number | null>(null)
const deleting = ref<number | null>(null)

// ---- create modal ----
const modalOpen = ref(false)
const label = ref('')
const creating = ref(false)
const createError = ref('')
/** Set once the key has been generated: the plaintext is then shown, one time only. */
const secret = ref('')
const secretItem = ref<ApiKeyItem | null>(null)
const copied = ref(false)

const rows = computed<ApiKeyItem[]>(() => data.value?.items ?? [])
const canCreate = computed(() => label.value.trim().length > 0 && !creating.value)

async function load() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    const { data: res } = await api.apiKeys()
    data.value = res
    lastUpdated.value = fmtDateTime(new Date().toISOString())
    loadError.value = ''
  } catch (error) {
    loadError.value = errorDetail(error)
  } finally {
    refreshing.value = false
    confirming.value = null
  }
}

function openCreate() {
  label.value = ''
  secret.value = ''
  secretItem.value = null
  createError.value = ''
  copied.value = false
  modalOpen.value = true
}

function closeModal() {
  modalOpen.value = false
  // The generated key is already in the table, so the list must be re-read before it appears.
  if (secret.value) load()
}

async function create() {
  if (!canCreate.value) return
  creating.value = true
  createError.value = ''
  try {
    const { data: res } = await api.apiKeyCreate(label.value.trim())
    secretItem.value = res.item
    secret.value = res.secret
    actionError.value = ''
  } catch (error) {
    createError.value = errorDetail(error)
  } finally {
    creating.value = false
  }
}

async function copySecret() {
  if (!secret.value) return
  let ok = false
  try {
    await navigator.clipboard.writeText(secret.value)
    ok = true
  } catch {
    // Clipboard access can be refused (insecure origin, permissions). Selecting the text
    // instead is the fallback the operator can finish with Ctrl+C.
    ok = legacyCopy(secret.value)
  }
  copied.value = ok
  if (!ok) actionError.value = t('apiKeys.copyFailed')
}

function legacyCopy(text: string): boolean {
  const box = document.createElement('textarea')
  box.value = text
  box.setAttribute('readonly', 'readonly')
  box.style.position = 'fixed'
  box.style.opacity = '0'
  document.body.appendChild(box)
  box.select()
  let done = false
  try {
    done = document.execCommand('copy')
  } catch {
    done = false
  }
  document.body.removeChild(box)
  if (!done) {
    const field = document.getElementById('secret-box') as HTMLTextAreaElement | null
    field?.focus()
    field?.select()
  }
  return done
}

async function remove(item: ApiKeyItem) {
  if (confirming.value !== item.id || deleting.value !== null) return
  deleting.value = item.id
  try {
    await api.apiKeyDelete(item.id)
    actionError.value = ''
    await load()
  } catch (error) {
    actionError.value = errorDetail(error)
    confirming.value = null
  } finally {
    deleting.value = null
  }
}

onMounted(load)
</script>

<template>
  <TopBar
    :title="t('nav.apiKeys')"
    :sub="t('apiKeys.sub')"
    refreshable
    :loading="refreshing"
    :last-updated="lastUpdated"
    @refresh="load"
  />

  <div v-if="loadError" class="warn-strip">⚠ {{ loadError }}</div>
  <div v-if="actionError" class="warn-strip">⚠ {{ actionError }}</div>

  <div class="card">
    <div class="card-h">{{ t('apiKeys.intro.title') }} <span class="tag">{{ t('apiKeys.intro.tag') }}</span></div>
    <div class="feature-intro">{{ t('apiKeys.intro.body') }}</div>
  </div>

  <div class="card">
    <div class="card-h">
      {{ t('apiKeys.list.title') }} <span class="tag">{{ t('apiKeys.list.tag') }}</span>
      <button class="btn primary new-key" @click="openCreate">{{ t('apiKeys.list.create') }}</button>
    </div>
    <table>
      <thead>
        <tr>
          <th>{{ t('apiKeys.list.colKey') }}</th>
          <th>{{ t('apiKeys.list.colLabel') }}</th>
          <th>{{ t('apiKeys.list.colCreated') }}</th>
          <th class="right">{{ t('apiKeys.list.colAction') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.id">
          <td class="mono">{{ row.masked }}</td>
          <td>
            <span v-if="row.label">{{ row.label }}</span>
            <span v-else class="dim">{{ t('apiKeys.list.noLabel') }}</span>
          </td>
          <td class="mono">{{ fmtDateTime(row.created_at) }}</td>
          <td class="right">
            <button
              v-if="confirming === row.id"
              class="btn danger"
              :disabled="deleting === row.id"
              @click="remove(row)"
            >
              {{ deleting === row.id ? t('apiKeys.list.revoking') : t('apiKeys.list.confirmRevoke') }}
            </button>
            <button v-if="confirming === row.id" class="btn" :disabled="deleting === row.id" @click="confirming = null">
              {{ t('apiKeys.list.cancel') }}
            </button>
            <button v-else class="btn" @click="confirming = row.id">{{ t('apiKeys.list.revoke') }}</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="4" class="empty">
            <template v-if="loadError">{{ t('apiKeys.list.loadFailed') }}</template>
            <template v-else>{{ t('apiKeys.list.empty') }}</template>
          </td>
        </tr>
      </tbody>
    </table>
    <div class="api-note">{{ t('apiKeys.list.note') }}</div>
  </div>

  <div class="card">
    <div class="card-h">{{ t('apiKeys.format.title') }} <span class="tag">{{ t('apiKeys.format.tag') }}</span></div>
    <div class="kv">
      <div class="k">{{ t('apiKeys.format.shape') }}</div>
      <div class="v mono">{{ t('apiKeys.format.shapeValue') }}</div>

      <div class="k">{{ t('apiKeys.format.stored') }}</div>
      <div class="v">{{ t('apiKeys.format.storedValue') }}</div>

      <div class="k">{{ t('apiKeys.format.checked') }}</div>
      <div class="v">{{ t('apiKeys.format.checkedValue') }}</div>

      <div class="k">{{ t('apiKeys.format.seeded') }}</div>
      <div class="v">{{ t('apiKeys.format.seededValue') }}</div>
    </div>
  </div>

  <div class="foot">{{ t('common.foot', { page: 'API Keys' }) }}</div>

  <!-- Create modal: the generated key is readable here and nowhere else. -->
  <div v-if="modalOpen" class="modal-overlay" @click.self="closeModal">
    <div class="modal" role="dialog" aria-modal="true">
      <div class="modal-head">
        <div>
          <div class="modal-title">{{ t('apiKeys.modal.title') }}</div>
          <div class="modal-sub">{{ secret ? t('apiKeys.modal.created') : t('apiKeys.modal.sub') }}</div>
        </div>
        <button class="icon-btn" :aria-label="t('apiKeys.modal.close')" @click="closeModal">✕</button>
      </div>

      <div class="modal-body">
        <template v-if="!secret">
          <div v-if="createError" class="modal-error">⚠ {{ createError }}</div>

          <label class="field-label" for="key-label">{{ t('apiKeys.modal.label') }}</label>
          <input
            id="key-label"
            v-model="label"
            class="inp wide"
            type="text"
            maxlength="100"
            :placeholder="t('apiKeys.modal.labelPlaceholder')"
            @keyup.enter="create"
          />
          <div class="field-help">{{ t('apiKeys.modal.labelHelp') }}</div>

          <div class="modal-actions">
            <button class="btn" @click="closeModal">{{ t('apiKeys.modal.cancel') }}</button>
            <button class="btn primary" :disabled="!canCreate" @click="create">
              {{ creating ? t('apiKeys.modal.generating') : t('apiKeys.modal.generate') }}
            </button>
          </div>
        </template>

        <template v-else>
          <div class="secret-warn">{{ t('apiKeys.modal.secretWarn') }}</div>
          <div class="secret-row">
            <textarea id="secret-box" class="inp secret-box" rows="2" readonly :value="secret"></textarea>
            <button class="btn primary" @click="copySecret">
              {{ copied ? t('apiKeys.modal.copied') : t('apiKeys.modal.copy') }}
            </button>
          </div>
          <div class="kv">
            <div class="k">{{ t('apiKeys.modal.label') }}</div>
            <div class="v">{{ secretItem?.label }}</div>
            <div class="k">{{ t('apiKeys.modal.masked') }}</div>
            <div class="v mono">{{ secretItem?.masked }}</div>
          </div>
          <div class="modal-actions">
            <button class="btn primary" @click="closeModal">{{ t('apiKeys.modal.done') }}</button>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.new-key {
  margin-left: auto;
}

.right {
  text-align: right;
  white-space: nowrap;
}

.right .btn + .btn {
  margin-left: 8px;
}

.dim {
  color: var(--text-mute);
}

.inp.wide {
  display: block;
  width: 100%;
}

.field-label {
  display: block;
  font-size: 12px;
  color: var(--text-dim);
  margin-bottom: 6px;
}

.field-help {
  font-size: 11.5px;
  color: var(--text-mute);
  margin-top: 6px;
  line-height: 1.7;
}

.modal {
  width: min(620px, 92vw);
  max-height: 86vh;
  overflow-y: auto;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.45);
}

.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  background: var(--surface);
  z-index: 1;
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
}

.modal-error {
  background: rgba(248, 81, 73, 0.12);
  border: 1px solid rgba(248, 81, 73, 0.4);
  color: var(--red);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 13px;
  margin-bottom: 12px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}

.secret-warn {
  background: rgba(210, 153, 34, 0.12);
  border: 1px solid rgba(210, 153, 34, 0.4);
  color: var(--amber);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 12.5px;
  line-height: 1.7;
  margin-bottom: 12px;
}

.secret-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.secret-box {
  flex: 1;
  font-family: 'SF Mono', ui-monospace, Menlo, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.6;
  resize: none;
  height: auto;
}
</style>
