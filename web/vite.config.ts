import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// The console is served by the gateway itself under /admin (see app/main.py), so the
// built asset URLs have to carry that prefix. `dev` proxies the management API to a
// local gateway to keep the browser on a single origin (the session cookie is SameSite=Lax).
// The build lands in app/web next to the modules, which is also where the image puts them
// (/app/web), so one path resolves in both layouts and `COPY app/*.py`-style shipping works.
export default defineConfig({
  base: '/admin/',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    outDir: '../app/web',
    emptyOutDir: true,
    // echarts is one large chunk by design; the whole console stays under ~1.2 MB.
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5173,
    proxy: {
      '/v1/management': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
