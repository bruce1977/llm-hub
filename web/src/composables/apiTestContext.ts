import { computed, ref } from 'vue'

import { errorDetail } from '@/api/client'
import { api } from '@/api/management'
import type { ApiTestContext, ApiTestKind, ApiTestKindContext } from '@/types/api'

// One `/api-test/models` payload per session, shared by every tester on every page. Building
// it probes all the upstreams, so the first tester on a page fetches it and the rest read the
// same ref; `load(true)` re-probes after a config edit.
const EMPTY: ApiTestContext = { kinds: {}, max_response_bytes: 128 * 1024 }

const context = ref<ApiTestContext>(EMPTY)
const loading = ref(false)
const loaded = ref(false)
const error = ref('')
let inFlight: Promise<void> | null = null

function fetchContext(): Promise<void> {
  if (inFlight) return inFlight
  loading.value = true
  error.value = ''
  inFlight = api
    .apiTestModels()
    .then((response) => {
      context.value = response.data
      loaded.value = true
    })
    .catch((err: unknown) => {
      error.value = errorDetail(err)
    })
    .finally(() => {
      loading.value = false
      inFlight = null
    })
  return inFlight
}

export function useApiTestContext() {
  /** `undefined` until the context lands, so a tester renders a "loading" row first. */
  function kindContext(kind: ApiTestKind): ApiTestKindContext | undefined {
    return context.value.kinds[kind]
  }

  /** How much of a response the engine carries back, for the truncation notice. */
  const maxResponseBytes = computed(() => context.value.max_response_bytes)

  function load(force = false): Promise<void> {
    if (force || !loaded.value) return fetchContext()
    return Promise.resolve()
  }

  return { loading, loaded, error, load, kindContext, maxResponseBytes }
}
