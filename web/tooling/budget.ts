import { readdir, readFile, stat } from 'node:fs/promises'
import path from 'node:path'
import type { Plugin } from 'vite'

/**
 * What a cold landing is allowed to cost, in brotli-compressed bytes.
 *
 * This is the entry chunk plus everything `index.html` preloads alongside it —
 * the bytes a first-time visitor must have before the app renders anything.
 * It is not the size of `dist/`, which is mostly route chunks nobody fetches
 * on arrival.
 *
 * The number is not a standard; VISION sets no bundle target. It is the
 * measured cost after this PR's splitting, rounded up to leave honest room for
 * a feature or two:
 *
 *   before PR 4    1,676,664 B  (uncompressed, one chunk, every route eager)
 *   + compression    374,253 B
 *   + route split    193,649 B  <- the number this budget currently guards
 *
 * If a change genuinely deserves to exceed this, raise it in the same commit
 * and say why in the message. A documented number can be argued with; a build
 * that silently grew cannot.
 */
const LANDING_BUDGET = 200_000

/**
 * The ceiling for any single route chunk, brotli-compressed.
 *
 * Guards the failure mode splitting is meant to prevent: one dependency
 * quietly becoming a megabyte that some route drags in. Vite's own warning
 * fires at 500 kB *uncompressed*, which is a different and much looser thing.
 *
 * Applies only to chunks the landing does *not* preload — those are already
 * governed, collectively and more strictly, by `LANDING_BUDGET`, and counting
 * the entry chunk here too would just be the same limit written twice.
 */
const CHUNK_BUDGET = 90_000

async function* walk(dir: string): AsyncGenerator<string> {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) yield* walk(full)
    else if (entry.isFile()) yield full
  }
}

/** Size of the brotli variant, falling back to the raw file when none was written. */
async function servedSize(file: string): Promise<number> {
  try {
    return (await stat(`${file}.br`)).size
  } catch {
    return (await stat(file)).size
  }
}

/**
 * Fail the build when a cold landing, or any one chunk, outgrows its budget.
 *
 * Runs on the real build output rather than on rollup's module graph, so it
 * measures what a browser actually downloads — after minification, after
 * compression, including the CSS.
 */
export function budget(): Plugin {
  return {
    name: 'bundle-budget',
    apply: 'build',
    async closeBundle() {
      const outDir = path.resolve('dist')
      const html = await readFile(path.join(outDir, 'index.html'), 'utf8')
      // Everything index.html pulls in before the app can render: the entry
      // script, its `modulepreload` siblings, and the stylesheet.
      const preloaded = [...html.matchAll(/(?:href|src)="\/(assets\/[^"]+\.(?:js|css))"/g)].map((match) => match[1])
      if (preloaded.length === 0) this.error('bundle-budget found no assets in index.html — has the output moved?')

      const landing = (await Promise.all(preloaded.map((f) => servedSize(path.join(outDir, f))))).reduce(
        (a, b) => a + b,
        0,
      )

      const preloadedSet = new Set(preloaded)
      const oversized: string[] = []
      for await (const file of walk(outDir)) {
        if (path.extname(file) !== '.js' && path.extname(file) !== '.css') continue
        const relative = path.relative(outDir, file)
        if (preloadedSet.has(relative)) continue
        const size = await servedSize(file)
        if (size > CHUNK_BUDGET) oversized.push(`${relative} is ${size} B`)
      }

      this.info(`cold landing ${landing} B brotli across ${preloaded.length} file(s), budget ${LANDING_BUDGET} B`)
      if (landing > LANDING_BUDGET) {
        this.error(
          `cold landing is ${landing} B brotli, over the ${LANDING_BUDGET} B budget by ${landing - LANDING_BUDGET} B.\n` +
            `Preloaded: ${preloaded.join(', ')}\n` +
            'Either shrink it, or raise LANDING_BUDGET in web/tooling/budget.ts and say why in the commit.',
        )
      }
      if (oversized.length > 0) {
        this.error(
          `${oversized.length} chunk(s) over the ${CHUNK_BUDGET} B per-chunk budget:\n  ${oversized.join('\n  ')}\n` +
            'Split the chunk, or raise CHUNK_BUDGET in web/tooling/budget.ts and say why in the commit.',
        )
      }
    },
  }
}
