<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { errorDetail, statusOf } from '@/api/client'
import { api } from '@/api/management'
import { gateway } from '@/stores/gateway'
import TopBar from '@/components/TopBar.vue'
import type { Overview } from '@/types/api'
import { fmtClock, fmtInt, fmtKiloTokens } from '@/utils/format'

const { t } = useI18n()

const REPO_URL = 'https://github.com/bruce1977/llm-hub'
// `web/public/*` is copied verbatim into the bundle and served under the router base,
// so the QR lives at /admin/wechat-qr.jpg - resolved from BASE_URL, never hardcoded.
const QR_SRC = `${import.meta.env.BASE_URL}wechat-qr.jpg`

const features = computed(() => [
  {
    ico: '⛁',
    t: t('about.features.routing.title'),
    d: t('about.features.routing.desc'),
  },
  {
    ico: '✦',
    t: t('about.features.aliases.title'),
    d: t('about.features.aliases.desc'),
  },
  {
    ico: '◍',
    t: t('about.features.embed.title'),
    d: t('about.features.embed.desc'),
  },
  {
    ico: '◈',
    t: t('about.features.systemOne.title'),
    d: t('about.features.systemOne.desc'),
  },
  {
    ico: '◎',
    t: t('about.features.webSearch.title'),
    d: t('about.features.webSearch.desc'),
  },
  {
    ico: '',
    t: t('about.features.security.title'),
    d: t('about.features.security.desc'),
  },
  {
    ico: '▤',
    t: t('about.features.insights.title'),
    d: t('about.features.insights.desc'),
  },
  {
    ico: '⌂',
    t: t('about.features.console.title'),
    d: t('about.features.console.desc'),
  },
  {
    ico: '↻',
    t: t('about.features.ops.title'),
    d: t('about.features.ops.desc'),
  },
])

const ovw = ref<Overview | null>(null)
const refreshing = ref(false)
const lastUpdated = ref('')
const loadError = ref('')
const qrBroken = ref(false)

const version = computed(() => ovw.value?.gateway_version || gateway.version || '')

async function load() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    const { data } = await api.overview()
    ovw.value = data
    gateway.version = data.gateway_version
    lastUpdated.value = fmtClock(new Date().toISOString())
    loadError.value = ''
  } catch (error) {
    if (statusOf(error) !== 401) loadError.value = errorDetail(error)
  } finally {
    refreshing.value = false
  }
}

onMounted(load)
</script>

<template>
  <TopBar
    :title="t('nav.about')"
    :sub="t('about.sub')"
    refreshable
    :loading="refreshing"
    :last-updated="lastUpdated"
    @refresh="load"
  />

  <div v-if="loadError" class="warn-strip">⚠ {{ loadError }}</div>

  <div class="card">
    <div class="card-h">{{ t('about.intro.title') }} <span class="tag">overview</span></div>
    <div class="feature-intro">{{ t('about.intro.body') }}</div>
  </div>

  <div class="card">
    <div class="card-h">{{ t('about.features.title') }} <span class="tag">features</span></div>
    <div class="feat-grid">
      <div v-for="f in features" :key="f.t" class="feat">
        <span class="fico">{{ f.ico }}</span>
        <div>
          <div class="ft">{{ f.t }}</div>
          <div class="fd">{{ f.d }}</div>
        </div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-h">{{ t('about.version.title') }} <span class="tag">runtime</span></div>
    <div class="kv">
      <div class="k">{{ t('about.version.gateway') }}</div>
      <div class="v mono">
        <span class="ver-dot" :class="{ busy: refreshing, on: !refreshing && !!version }"></span>
        <template v-if="version">v{{ version }}</template>
        <template v-else>—</template>
        <span class="chip note">{{ t('about.version.live') }}</span>
      </div>

      <div class="k">{{ t('about.version.console') }}</div>
      <div class="v mono">{{ t('about.version.consoleValue') }}</div>

      <div class="k">{{ t('about.version.upstreamsOnline') }}</div>
      <div class="v mono">
        {{ ovw ? `${fmtInt(ovw.kpi.upstreams_online)} / ${fmtInt(ovw.kpi.upstreams_total)}` : '—' }}
      </div>

      <div class="k">{{ t('about.version.runningModels') }}</div>
      <div class="v mono">{{ ovw ? fmtInt(ovw.kpi.running_models) : '—' }}</div>

      <div class="k">{{ t('about.version.today') }}</div>
      <div class="v mono">
        {{ ovw ? t('about.version.todayValue', { count: fmtInt(ovw.kpi.requests_today), tokens: fmtKiloTokens(ovw.kpi.tokens_today) }) : '—' }}
      </div>

      <div class="k">{{ t('about.version.logDb') }}</div>
      <div class="v mono">
        <span v-if="ovw" class="badge" :class="ovw.request_log.enabled ? 'ok' : 'idle'">
          {{ ovw.request_log.enabled ? t('about.version.logging') : t('about.version.logOff') }}
        </span>
        <template v-if="ovw">{{ t('about.version.retention', { days: fmtInt(ovw.request_log.retention_days) }) }}</template>
        <template v-else>—</template>
      </div>

      <div class="k">{{ t('about.version.dataTime') }}</div>
      <div class="v mono">{{ ovw ? fmtClock(ovw.generated_at) : '—' }}</div>
    </div>
  </div>

  <div class="grid-2">
    <div class="card">
      <div class="card-h">{{ t('about.repo.title') }} <span class="tag">github</span></div>
      <div class="kv">
        <div class="k">{{ t('about.repo.source') }}</div>
        <div class="v">
          <a class="link" :href="REPO_URL" target="_blank" rel="noreferrer">github.com/bruce1977/llm-hub</a>
        </div>
        <div class="k">{{ t('about.repo.docker') }}</div>
        <div class="v mono">bruce1977/llm-hub:latest</div>
        <div class="k">{{ t('about.repo.docs') }}</div>
        <div class="v mono">/docs · /openapi.json</div>
      </div>
      <div class="api-note">{{ t('about.repo.note') }}</div>
    </div>

    <div class="card">
      <div class="card-h">{{ t('about.wechat.title') }} <span class="tag">wechat</span></div>
      <div class="qr">
        <img v-if="!qrBroken" :src="QR_SRC" :alt="t('about.wechat.qrAlt')" @error="qrBroken = true" />
        <div v-else class="qr-missing">
          <i18n-t keypath="about.wechat.qrMissing" scope="global">
            <template #path><code>web/public/wechat-qr.jpg</code></template>
          </i18n-t>
        </div>
      </div>
      <div class="api-note">{{ t('about.wechat.note') }}</div>
    </div>
  </div>

  <div class="foot">{{ t('common.foot', { page: 'About' }) }}</div>
</template>

<style scoped>
.feat-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px 18px;
}

.feat {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.fico {
  flex: none;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  display: grid;
  place-items: center;
  font-size: 12px;
  color: var(--accent);
  background: var(--accent-soft);
}

.ft {
  font-size: 12.5px;
  color: var(--text);
  font-weight: 600;
}

.fd {
  font-size: 11.5px;
  line-height: 1.65;
  color: var(--text-dim);
}

.note {
  margin-left: 6px;
}

/* Hollow ring while the probe is in flight, filled once the live version lands. */
.ver-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
  margin-right: 6px;
  border: 1.5px solid var(--accent);
  background: transparent;
  vertical-align: middle;
}

.ver-dot.on {
  background: var(--accent);
}

.ver-dot.busy {
  animation: ver-pulse 1s ease-in-out infinite;
}

@keyframes ver-pulse {
  0%,
  100% {
    opacity: 0.35;
    transform: scale(0.85);
  }
  50% {
    opacity: 1;
    transform: scale(1.1);
  }
}

.qr {
  display: flex;
  justify-content: center;
  padding: 6px 0 2px;
}

.qr img {
  width: 168px;
  height: 168px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: #fff;
}

.qr-missing {
  font-size: 11.5px;
  color: var(--text-mute);
  line-height: 1.7;
  padding: 22px 8px;
  text-align: center;
}
</style>
