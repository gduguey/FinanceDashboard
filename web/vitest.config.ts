import path from 'node:path'
import { defineConfig } from 'vitest/config'

// Deliberately separate from vite.config.ts, not merged into it: vitest 3's
// bundled internal Vite copy doesn't yet match vite 8's (Rolldown-based)
// plugin types, so feeding it the same react()/tailwindcss() plugins fails
// to typecheck. None of that is needed here — every test today is plain
// logic, no JSX/CSS involved — so this config only carries what vitest
// itself needs: the same `@/` alias tests import through, and where to look.
export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
})
