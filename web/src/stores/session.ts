import { reactive } from 'vue'

import { statusOf } from '@/api/client'
import { api } from '@/api/management'

export interface SessionState {
  authenticated: boolean
  checked: boolean
  // True when the gateway answered 503: ADMIN_PASSWORD is not set, so the console can
  // never be used. The login card switches to a locked state instead of a password form.
  locked: boolean
  user: string
}

export const session = reactive<SessionState>({
  authenticated: false,
  checked: false,
  locked: false,
  user: 'admin',
})

let bootstrap: Promise<boolean> | null = null

// The cookie is HttpOnly, so the browser cannot tell us whether we are signed in --
// ask the protected heartbeat once per app load and let the router guard wait for it.
export function ensureSession(): Promise<boolean> {
  if (!bootstrap) bootstrap = probe()
  return bootstrap
}

async function probe(): Promise<boolean> {
  try {
    await api.ping()
    session.authenticated = true
    session.locked = false
  } catch (error) {
    session.authenticated = false
    session.locked = statusOf(error) === 503
  } finally {
    session.checked = true
  }
  return session.authenticated
}

export async function signIn(username: string, password: string): Promise<void> {
  await api.login(username, password)
  session.authenticated = true
  session.locked = false
}

export async function signOut(): Promise<void> {
  try {
    await api.logout()
  } catch {
    // A failed logout still means "not signed in" as far as the UI is concerned.
  }
  session.authenticated = false
  bootstrap = null
  session.checked = false
}

export function expire(locked: boolean): void {
  session.authenticated = false
  session.locked = locked
  bootstrap = null
  session.checked = false
}
