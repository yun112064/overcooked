import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'
import { fakeApi } from './dev/fakeApi.ts'

/** 管理 API 只由獨立管理網站轉發；與 frontend/ 的規則一致。 */
const consumerApiBoundary: Plugin = {
  name: 'consumer-api-boundary',
  configureServer(server) {
    server.middlewares.use((request, response, next) => {
      const path = (request.url ?? '').split('?')[0]
      if (path === '/api/admin' || path.startsWith('/api/admin/')) {
        response.statusCode = 404
        response.end()
        return
      }
      next()
    })
  },
}

// `vite --mode fake` 以記憶體中的假 API 取代後端，用來做不碰任何資料庫的例行檢查。
export default defineConfig(({ mode }) => ({
  plugins: [react(), consumerApiBoundary, ...(mode === 'fake' ? [fakeApi()] : [])],
  server: {
    proxy: mode === 'fake' ? undefined : {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
}))
