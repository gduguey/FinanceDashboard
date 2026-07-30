import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { budget } from './tooling/budget.ts'
import { precompress } from './tooling/precompress.ts'

// https://vite.dev/config/
export default defineConfig({
  // `budget` last: it measures the `.br` files `precompress` writes, so it has
  // to run after them. Vite calls `closeBundle` in plugin order.
  plugins: [react(), tailwindcss(), precompress(), budget()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
