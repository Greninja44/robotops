import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backend = process.env.ROBOTOPS_BACKEND ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: '127.0.0.1',
    proxy: {
      '/api': backend,
      '/ws': { target: backend.replace('http', 'ws'), ws: true },
    },
  },
})
