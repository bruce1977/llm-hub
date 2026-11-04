import { reactive } from 'vue'

// Cosmetic shared state: the sidebar shows the gateway build, and /overview is the only
// endpoint that reports it. The dashboard fills this in; nothing re-probes for it.
export const gateway = reactive<{ version: string | null }>({ version: null })
