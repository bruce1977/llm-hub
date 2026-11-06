import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

import { ensureSession, session } from '@/stores/session'

declare module 'vue-router' {
  interface RouteMeta {
    /** Set on the login gate: the guard must not bounce it to itself. */
    public?: boolean
  }
}

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('@/views/ShellView.vue'),
    children: [
      { path: '', name: 'dashboard', component: () => import('@/views/DashboardView.vue') },
      { path: 'upstreams', name: 'upstreams', component: () => import('@/views/UpstreamsView.vue') },
      { path: 'models/llm', name: 'models-llm', component: () => import('@/views/ModelsLlmView.vue') },
      { path: 'models/embed', name: 'models-embed', component: () => import('@/views/ModelsEmbedView.vue') },
      { path: 'models/rerank', name: 'models-rerank', component: () => import('@/views/ModelsRerankView.vue') },
      {
        path: 'models/systemone',
        name: 'models-systemone',
        component: () => import('@/views/ModelsSystemOneView.vue'),
      },
      { path: 'tools/websearch', name: 'tools-websearch', component: () => import('@/views/ToolsWebSearchView.vue') },
      { path: 'requests', name: 'requests', component: () => import('@/views/RequestsView.vue') },
      { path: 'settings', name: 'settings', component: () => import('@/views/SettingsView.vue') },
      { path: 'api-keys', name: 'api-keys', component: () => import('@/views/ApiKeysView.vue') },
      { path: 'about', name: 'about', component: () => import('@/views/AboutView.vue') },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: { name: 'dashboard' } },
]

export const router = createRouter({
  history: createWebHistory('/admin/'),
  routes,
})

// Every view except the gate needs the session cookie; the guard asks the protected
// heartbeat once per load, so a reload with a live cookie stays on the current page.
// Once a session is established (heartbeat ok or a successful login) the guard stops
// probing -- otherwise the login redirect would reuse the pre-login "401" promise.
router.beforeEach(async (to) => {
  if (to.meta.public) {
    if (to.name === 'login' && session.authenticated) return { name: 'dashboard' }
    return true
  }
  if (session.authenticated && session.checked) return true
  return (await ensureSession()) ? true : { name: 'login', query: { redirect: to.fullPath } }
})
