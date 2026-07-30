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

/**
 * What one route may add on top of the landing, brotli-compressed.
 *
 * Measures each lazily-loaded route's *static* closure — the chunks the
 * browser must have before that page renders at all — minus whatever the
 * landing already preloaded. Anything the page pulls in through a further
 * `import()`, a chart above all, is deliberately not counted: it streams in
 * behind a skeleton rather than blocking the page.
 *
 * This is the check that keeps recharts off page critical paths. Every chart
 * goes through `lazyChart`, but one ordinary `import` of a chart component
 * anywhere in a page's static graph silently undoes that for the whole route
 * — which is exactly how `FinancialHealthStrip`'s compact sparkline kept all
 * ~109 kB of recharts on the landing until it was found by measurement. With
 * this budget in place that mistake fails the build instead.
 *
 * Measured range with every chart deferred: 1,739 B (onboarding) to 48,704 B
 * (import). Eight routes were between 101 kB and 146 kB before, all of it
 * recharts — the overview alone came down from 137,276 B to 19,650 B.
 */
const ROUTE_BUDGET = 60_000

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

      // Static imports are `from"./chunk.js"`; a dynamic one is
      // ``import(`./chunk.js`)``. Only the former puts a chunk on the
      // importer's critical path, which is the whole distinction this budget
      // rests on.
      const assetsDir = path.join(outDir, 'assets')
      const sources = new Map<string, string>()
      for (const file of await readdir(assetsDir)) {
        if (path.extname(file) === '.js') sources.set(file, await readFile(path.join(assetsDir, file), 'utf8'))
      }
      const staticImports = (chunk: string) => [
        ...new Set([...(sources.get(chunk) ?? '').matchAll(/(?:from|import)"\.\/([^"]+\.js)"/g)].map((m) => m[1])),
      ]
      const staticClosure = (entry: string) => {
        const seen = new Set<string>()
        const queue = [entry]
        while (queue.length > 0) {
          const chunk = queue.pop()
          if (chunk === undefined || seen.has(chunk)) continue
          seen.add(chunk)
          queue.push(...staticImports(chunk))
        }
        return seen
      }

      const entry = preloaded.find((f) => f.endsWith('.js'))
      if (entry === undefined) this.error('bundle-budget found no entry script in index.html')
      const entryName = path.basename(entry)
      const alreadyLoaded = new Set(preloaded.map((f) => path.basename(f)))
      // The entry's dynamic imports are exactly the lazy routes. Matched in
      // either quoting the bundler emits — a template literal today, a plain
      // string in other output modes — because a matcher that silently finds
      // nothing turns the route budget below into a loop over zero routes that
      // passes every build.
      const routeChunks = [
        ...new Set(
          [...(sources.get(entryName) ?? '').matchAll(/import\(\s*['"`]\.\/([^'"`]+\.js)['"`]\s*\)/g)].map((m) => m[1]),
        ),
      ].sort()
      if (routeChunks.length === 0) {
        this.error(
          `bundle-budget found no dynamic imports in ${entryName}, so the route budget checked nothing. ` +
            'The bundler changed how it emits `import()`; update the matcher in web/tooling/budget.ts.',
        )
      }

      const overBudget: string[] = []
      for (const route of routeChunks) {
        const extra = [...staticClosure(route)].filter((chunk) => !alreadyLoaded.has(chunk))
        const cost = (await Promise.all(extra.map((c) => servedSize(path.join(assetsDir, c))))).reduce(
          (a, b) => a + b,
          0,
        )
        if (cost > ROUTE_BUDGET) overBudget.push(`${route} needs ${cost} B beyond the landing`)
      }
      if (overBudget.length > 0) {
        this.error(
          `${overBudget.length} route(s) over the ${ROUTE_BUDGET} B route budget:\n  ${overBudget.join('\n  ')}\n` +
            'A chart imported directly instead of through `lazyChart` is the usual cause. ' +
            'Otherwise raise ROUTE_BUDGET in web/tooling/budget.ts and say why in the commit.',
        )
      }

      this.info(
        `cold landing ${landing} B brotli across ${preloaded.length} file(s), budget ${LANDING_BUDGET} B; ` +
          `${routeChunks.length} route(s) within ${ROUTE_BUDGET} B each`,
      )
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
