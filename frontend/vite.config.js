import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiBaseUrl = (
    env.VITE_API_PROXY_TARGET
    || env.VITE_API_BASE_URL
    || 'http://localhost:8000'
  )

  return {
    plugins: [react()],
    server: {
      host: '0.0.0.0',
      port: 5173,
      proxy: {
        '/health': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/api': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
      },
    },
    preview: {
      host: '0.0.0.0',
      port: 4173,
      // Mirror the dev-server proxy so that `vite preview` also reaches the backend
      // without cross-origin requests. When the frontend is deployed behind a
      // reverse proxy, CORS_ALLOWED_ORIGINS on the backend covers the gap instead.
      proxy: {
        '/health': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
        '/api': {
          target: apiBaseUrl,
          changeOrigin: true,
        },
      },
    },
  }
})
