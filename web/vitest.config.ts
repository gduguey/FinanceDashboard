import path from 'node:path'
import { defineConfig } from 'vitest/config'

// Deliberately separate from vite.config.ts, not merged into it: vitest 3's
// bundled internal Vite copy doesn't yet match vite 8's (Rolldown-based)
// plugin types, so feeding it the same react()/tailwindcss() plugins fails
// to typecheck. Nothing here needs them — vitest's own esbuild transform
// reads `"jsx": "react-jsx"` out of tsconfig.app.json and emits the automatic
// runtime, and no test renders a stylesheet — so this config only carries
// what vitest itself needs: the same `@/` alias tests import through, where
// to look, and the DOM the component tests render into.
export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    // jsdom for every file rather than a per-file `@vitest-environment`
    // docblock. Component tests are the majority of what gets written from
    // here on, and an opt-in a new file can silently forget surfaces as
    // `document is not defined` from inside React rather than at the line
    // that forgot it. The pure-logic tests are indifferent to the environment
    // and pay about 40 ms each to set one up.
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
})
