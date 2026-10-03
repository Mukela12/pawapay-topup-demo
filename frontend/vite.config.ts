import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The Flask API runs on 5001 in local dev. In production one Railway service
// serves both the API and this build, so the client always calls same origin.
// RINGWISE_API_TARGET overrides the target, for example to point at a second backend.
const API_TARGET = process.env.RINGWISE_API_TARGET ?? 'http://127.0.0.1:5001'

export default defineConfig({
  base: '/',
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: false },
      '/webhooks': { target: API_TARGET, changeOrigin: false },
      '/healthz': { target: API_TARGET, changeOrigin: false },
    },
  },
})
