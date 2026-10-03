import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // Listen on all interfaces so phones/tablets on the same network can open the dev server.
    host: '0.0.0.0',
    proxy: {
      '/api': process.env.STARDECK_API_URL ?? 'http://localhost:8000',
    },
  },
})
