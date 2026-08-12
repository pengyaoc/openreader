import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  // '/' for local dev and LAN use; the VM build sets VITE_BASE=/reader/ so
  // Apache can ProxyPass a path prefix on the existing pengyaochen.com vhost
  // instead of needing its own subdomain/cert. See frontend/src/api.ts,
  // which reads this back at runtime via import.meta.env.BASE_URL.
  base: process.env.VITE_BASE || '/',
  plugins: [react()],
  server: {
    host: '0.0.0.0', // reachable from other devices on the LAN (e.g. a phone)
    proxy: {
      '/api': 'http://127.0.0.1:8787',
    },
  },
})
