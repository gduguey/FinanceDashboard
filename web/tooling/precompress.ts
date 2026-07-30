import { readdir, readFile, stat, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { promisify } from 'node:util'
import { brotliCompress, constants, gzip } from 'node:zlib'
import type { Plugin } from 'vite'

const brotli = promisify(brotliCompress)
const gz = promisify(gzip)

// Compressing an already-compressed format wastes build time and produces a
// *larger* file — woff2, png and the like carry their own compression. Only
// the text formats are worth a variant.
const COMPRESSIBLE = new Set(['.js', '.css', '.html', '.svg', '.json', '.txt', '.map'])

// Below roughly one MTU there is nothing to win: the response still costs one
// packet, and a shared cache now has two entities to keep instead of one.
const MIN_BYTES = 1024

async function* walk(dir: string): AsyncGenerator<string> {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) yield* walk(full)
    else if (entry.isFile()) yield full
  }
}

/**
 * Write `.br` and `.gz` siblings next to every compressible build output.
 *
 * The server picks the variant the request's `Accept-Encoding` permits
 * (`_PrecompressedStaticFiles` in `src/trades/api/api.py`), so the expensive
 * compression happens once per build instead of once per request. That is what
 * makes brotli quality 11 affordable at all — it is far too slow to run on the
 * request path, and it is the setting that turns the 1.58 MB entry chunk into
 * ~360 kB rather than gzip's ~447 kB.
 *
 * A missing variant is not an error anywhere: the server falls back to the
 * uncompressed file, so a build that skipped this step still serves correctly.
 */
export function precompress(): Plugin {
  return {
    name: 'precompress',
    // `closeBundle`, not `writeBundle`: files copied from `public/` land in the
    // output directory outside the bundle, and they are worth compressing too.
    // This hook runs after everything has been written.
    apply: 'build',
    async closeBundle() {
      const outDir = path.resolve('dist')
      const written: string[] = []
      for await (const file of walk(outDir)) {
        if (!COMPRESSIBLE.has(path.extname(file))) continue
        const source = await readFile(file)
        if (source.byteLength < MIN_BYTES) continue
        const variants: [string, Buffer][] = [
          [
            `${file}.br`,
            await brotli(source, {
              params: {
                [constants.BROTLI_PARAM_QUALITY]: constants.BROTLI_MAX_QUALITY,
                [constants.BROTLI_PARAM_SIZE_HINT]: source.byteLength,
              },
            }),
          ],
          [`${file}.gz`, await gz(source, { level: constants.Z_BEST_COMPRESSION })],
        ]
        for (const [target, body] of variants) {
          // A variant bigger than the original would make the response worse.
          // Leaving it off disk is also how the server learns not to offer it.
          if (body.byteLength >= source.byteLength) continue
          await writeFile(target, body)
          written.push(path.relative(outDir, target))
        }
      }
      const total = await Promise.all(written.map(async (f) => (await stat(path.join(outDir, f))).size))
      this.info(
        `precompressed ${written.length} file(s), ${(total.reduce((a, b) => a + b, 0) / 1024).toFixed(1)} kB of variants`,
      )
    },
  }
}
