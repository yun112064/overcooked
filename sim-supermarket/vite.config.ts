import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174, strictPort: true,
    proxy: {
      '/market-api/checkout': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: () => '/sim-market/checkout',
        configure: proxy => proxy.on('proxyReq', request => {
          request.removeHeader('cookie')
          request.removeHeader('authorization')
        }),
      },
    },
  },
})
