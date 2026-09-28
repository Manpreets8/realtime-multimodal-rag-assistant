import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // In docker compose this points at the backend service; locally at uvicorn.
  // 127.0.0.1, not localhost: uvicorn listens on IPv4 and resolving localhost to ::1 first
  // can add a ~2 s connection fallback on Windows.
  const apiTarget = env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8000'

  return {
    plugins: [react(), tailwindcss()],
    server: {
      // Explicit IPv4: Node on Windows otherwise binds "localhost" to ::1 only,
      // which breaks http://127.0.0.1:5173. Docker overrides this with --host 0.0.0.0.
      host: '127.0.0.1',
      port: 5173,
      proxy: {
        '/api': { target: apiTarget, changeOrigin: true, ws: true },
        '/docs': { target: apiTarget, changeOrigin: true },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      restoreMocks: true,
      // A test may await several UI steps of up to 5 s each (asyncUtilTimeout in src/test/setup.ts);
      // coverage instrumentation slows the chat page enough to exceed Vitest's 5 s default.
      testTimeout: 15_000,
    },
  }
})
