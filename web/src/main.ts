import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import '@/styles/theme.css'

import ElementPlus from 'element-plus'
import { createApp } from 'vue'

import App from './App.vue'
import { setUnauthorizedHandler } from '@/api/client'
import { i18n } from '@/i18n'
import { router } from '@/router'
import { expire } from '@/stores/session'

const app = createApp(App)

app.use(ElementPlus)
app.use(i18n)
app.use(router)

// Any protected call that comes back 401 means the cookie is gone: drop straight back to
// the login gate instead of showing an empty dashboard.
setUnauthorizedHandler(() => {
  expire(false)
  const current = router.currentRoute.value
  if (!current.meta.public) {
    router.replace({ name: 'login', query: { redirect: current.fullPath } })
  }
})

app.config.errorHandler = (err: unknown) => {
  // Surface unexpected failures in the console rather than a blank page.
  console.error('[llm-hub console]', err)
}

app.mount('#app')
