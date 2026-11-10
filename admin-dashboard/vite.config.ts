import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Use a separate hostname: cookies are not isolated by port.
export default defineConfig({
  plugins: [react(), {
    name: 'admin-api-boundary',
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const path = (request.url ?? '').split('?')[0]
        if (path.startsWith('/api') && !/^\/api\/(?:auth\/(?:login|logout|me)|admin\/v1(?:\/.*)?)$/.test(path)) {
          response.statusCode = 404
          response.end()
          return
        }
        next()
      })
    },
  }],
  server: {
    host: '127.0.0.1', port: 5175, strictPort: true,
    proxy: { '/api': { target: process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8000', changeOrigin: true,
      rewrite: (path) => path.replace(/^\/api/, '') } },
  },
})
