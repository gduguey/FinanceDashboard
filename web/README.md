# Investments dashboard (frontend)

React + TypeScript + Vite + Tailwind + shadcn/ui + Recharts + TanStack Query.
Talks to the FastAPI server in `src/trades/api/` (dev: via a proxy; prod: the
same process serves this app's own build — see "Dev vs. production" below).

See the repo root [README.md](../README.md) for how to run this alongside
the API server. Quick version:

```bash
npm install
npm run dev      # http://localhost:5173, with the API server already running on :8000
```

## Static SPA, not server-side rendering

This is a **Single Page Application (SPA)**: `npm run build` produces a
handful of static HTML/CSS/JS files under `web/dist/`, once, at build time.
Serving the app in production is then just "read this file, hand it back
unchanged" — no Node.js process, no per-request rendering. Navigating
between pages (`/settings`, `/accounts`, ...) never asks the server for a
new HTML page either; a router library swaps which React component is
shown, entirely in the browser.

The alternative — **server-side rendering (SSR)**, as in Next.js or
TanStack Start — runs your React code on a live, long-running Node.js
process that renders real HTML per request. That buys you fast first-paint
and good SEO for a public, anonymous-visitor site. Neither applies here:
this is a private, authenticated dashboard with one user who loads it
repeatedly, so a second always-on process (with its own memory budget and
crash recovery, alongside Postgres and the Python API) would be pure cost
with no corresponding benefit. See
[`docs/app-stack/ssr-vs-spa.md`](../docs/app-stack/ssr-vs-spa.md) for the
fuller argument, if that file exists in your checkout (it's a personal,
uncommitted note — not guaranteed to be there).

## Dev vs. production

- **Dev** (`npm run dev`): Vite's dev server serves this app on
  `:5173` with hot-reload, and proxies any `/api/*` request through to the
  real backend on `:8000` (`server.proxy` in `vite.config.ts`). Two
  processes, two ports.
- **Production**: `npm run build` typechecks (`tsc -b`) and bundles
  everything into `web/dist/`. The FastAPI app (`src/trades/api/api.py`)
  mounts that directory directly and serves it as static files, plus a
  catch-all route that falls back to `index.html` for any path that isn't a
  real file — so a hard refresh on `/settings` still lets the client-side
  router take over. One process, one port; Node.js and the whole JS build
  toolchain are gone by the time anything is actually served.

## Routing: one file, one page

`web/src/routes/` mirrors the URL tree it serves — a file's path under that
folder *is* the URL it renders at, and an `index.tsx` serves its parent
directory's own path:

```
src/routes/net-worth.tsx            ->  /net-worth
src/routes/investments/index.tsx    ->  /investments
src/routes/investments/allocation.tsx -> /investments/allocation
```

`App.tsx` scans that folder at build time with `import.meta.glob` and
builds the `<Routes>` list from it automatically — adding a page is just
adding a file, nothing to hand-register elsewhere. Each route file is a
thin re-export of the real page component (which lives in `src/pages/`)
plus one `requiresStore: boolean` flag saying whether that page depends on
the accounting store (and should show a fallback if it failed to load).

This is a lighter-weight version of what frameworks like TanStack Router
do natively (generated route trees, typed dynamic segments, pathless
layout routes) — we don't have any of those features, just the "folder
shape = URL shape" convention, layered on top of plain `react-router-dom`.

The backend's `src/trades/api/routers/` and `src/accounting/api/routers/`
are a different, unrelated kind of "router" — FastAPI `APIRouter`s that
group HTTP endpoints by domain (`dashboard.py`, `settings.py`, `store.py`,
...) rather than map folder paths to URLs. Don't conflate the two: this
folder's routing decides what the *browser* shows for a given URL; the
backend's routers decide what *HTTP endpoint* handles a given API path.

## Types: generated from the API, not hand-written

`web/src/types/schema.ts` is not something anyone edits — it's the
TypeScript mirror of the backend's own OpenAPI schema, produced by this
pipeline:

1. Every FastAPI endpoint (across `trades/api/routers/*.py` and
   `accounting/api/routers/*.py`) declares a real Pydantic
   `response_model=` — no bare `dict[str, Any]` responses.
2. `uv run python -m trades.api.export_openapi` dumps `app.openapi()` to
   `web/openapi.json`.
3. `npm run generate:schema` runs `openapi-typescript` over that file,
   producing `web/src/types/schema.ts` — one `paths` type (per endpoint)
   and one `components.schemas` type (per Pydantic model).
4. `web/src/types/portfolio.ts` and `accounting.ts` are thin named aliases
   onto `schema.ts` (e.g. `export type Overview = components['schemas']['Overview']`)
   — kept only because importing `components['schemas']['X']` everywhere
   would be noisier than a short, stable name. They contain no hand-typed
   field definitions.

A backend field rename shows up in `schema.ts` the next time it's
regenerated — and `.github/workflows/openapi-types.yml` regenerates it in
CI and fails the build (`git diff --exit-code`) if the committed
`schema.ts` doesn't match what the live API schema actually says. Types
can't silently drift out from under the code that generated them.

Two narrow exceptions stay hand-written, both documented inline where
they're defined: a couple of endpoints that genuinely return an
open-ended `symbol -> value` map with no fixed keys (no named schema for
openapi-typescript to generate against), and one client-side JSON shape
(`CanonicalCategoryOverrides`) sent as a JSON-encoded form field rather
than a structured request body FastAPI can describe.

## Tooling

| Command | What it does |
|---|---|
| `npm run dev` | Start the Vite dev server with hot-reload |
| `npm run build` | Typecheck (`tsc -b`), then bundle to `web/dist/` |
| `npm run typecheck` | `tsc -b --noEmit` — types only, no build output |
| `npm run lint` | Biome's linter |
| `npm run format` / `format:check` | Biome's formatter (write / check-only) |
| `npm run check` | Biome's full `check` — lint + format + import organization in one pass; this is the one that catches import-order drift (`lint`/`format` alone don't) |
| `npm run test` | Run the Vitest suite |
| `npm run generate:schema` | Regenerate `schema.ts` from `web/openapi.json` (run `uv run python -m trades.api.export_openapi` first to refresh that file) |

Biome is the single tool for both linting and formatting — no separate
linter (this project used to run oxlint alongside Biome; that split is
gone now). Its linter rules are intentionally **narrower** than Biome's own
"recommended" preset today: only `useHookAtTopLevel` and
`useComponentExportOnlyModules` are enabled (`web/biome.json`), matching
what was actually enforced before the oxlint migration rather than
silently adopting a stricter ruleset. Enabling the full recommended preset
is a deliberate, separate decision — it currently surfaces ~100 pre-existing
findings (mostly form-accessibility rules like `noLabelWithoutControl`)
that would need fixing or explicitly suppressing first.

Tests are plain-logic unit tests today (no `jsdom`/`@testing-library/react`
installed) — see `vitest.config.ts`'s comment for why it's a separate
config file from `vite.config.ts`. Test files are colocated next to the
code they cover (`postingClassification.ts` + `postingClassification.test.ts`
in the same folder), not under a separate `tests/` tree.
