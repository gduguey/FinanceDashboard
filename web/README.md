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
this is a private, Clerk-authenticated, multi-user dashboard with no
anonymous visitors and no SEO surface at all, so a second always-on
process (with its own memory budget and crash recovery, alongside
Postgres and the Python API) would be pure cost with no corresponding
benefit.

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

`routeTable.tsx` scans that folder at build time with `import.meta.glob`
and exports the finished `routes` array; `App.tsx` only maps it into
`<Route>` elements. Adding a page is just adding a file, nothing to
hand-register elsewhere. Each route file is a thin re-export of the real
page component (which lives in `src/pages/`) plus one
`requiresStore: boolean` flag saying whether that page depends on the
accounting store (and should show a fallback if it failed to load).

The glob is deliberately **not** `{ eager: true }`: each page is its own
lazily-fetched chunk, so landing on the overview does not also download
the guide, the importer and the tax panel. Because `requiresStore` is
unreadable until its chunk arrives, `routeTable.tsx` applies the store
gate inside the loader rather than leaving it to `App.tsx`.

This is a lighter-weight version of what frameworks like TanStack Router
do natively (generated route trees, typed dynamic segments, pathless
layout routes) — we don't have any of those features, just the "folder
shape = URL shape" convention, layered on top of plain `react-router-dom`.

### Why there's no `_authed/`-style folder for auth

Apps built on TanStack Router/Next.js often put a pathless layout route
(e.g. `_authed.tsx` wrapping an `_authed/` folder) above every gated page,
with the public landing page as a sibling outside it — folder nesting
doubles as layout composition there, and their landing route renders for
both signed-in and signed-out visitors (real public marketing content).
Our routing has no nesting/layout concept at all (see above — one file is
always one flat page), and every page here requires auth, with no public
content to split out. So instead `AuthGate` (`components/layout/AuthGate.tsx`)
wraps the *entire* app above `<Routes>`, in `App.tsx` — not a route at
all, just whatever renders in place of any URL while signed out. Since it
never touches the URL, a signed-out visit to e.g. `/net-worth` still shows
that page immediately once signed in, with no redirect-back bookkeeping
needed.

The backend's `src/trades/api/routers/` and `src/accounting/api/routers/`
are a different, unrelated kind of "router" — FastAPI `APIRouter`s that
group HTTP endpoints by domain (`dashboard.py`, `settings.py`,
`categories.py`, ...) rather than map folder paths to URLs. Don't
conflate the two: this
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
| `npm run lint` | Biome's linter, `--error-on-warnings` (see below) |
| `npm run format` / `format:check` | Biome's formatter (write / check-only) |
| `npm run check` | Biome's full `check` — lint + format + import organization in one pass, also `--error-on-warnings`; this is the one that catches import-order drift (`lint`/`format` alone don't) |
| `npm run test` | Run the Vitest suite |
| `npm run generate:schema` | Regenerate `schema.ts` from `web/openapi.json` (run `uv run python -m trades.api.export_openapi` first to refresh that file) |

Biome is the single tool for both linting and formatting — no separate
linter (this project used to run oxlint alongside Biome; that split is
gone now). Biome's own **`recommended` preset is on** (`web/biome.json`),
turned on in PR 5 after the ~100 pre-existing findings it surfaced — mostly
form-accessibility rules like `noLabelWithoutControl` — were fixed. Three
rules are named on top of it: `useHookAtTopLevel` and `noUselessFragments`
pinned to `error`, and `useComponentExportOnlyModules`, which the preset
does not cover at all, at `info`.

**`npm run lint` and `npm run check` fail on a warning.** (Not `npm run
build`, which only typechecks and bundles — but CI runs all three, so a
warning fails the pipeline.) `biome lint` exits 0 on warnings, so until
PR A a warn-level rule enforced nothing. Four rules were pinned to `error`
to work around that, which covered those four and nothing else — any
warn-level rule the preset gained later would have been silently
unenforced. Both scripts now pass `--error-on-warnings`, so the pins are
gone, with two deliberate exceptions:

- `noUselessFragments` stays pinned. The recommended preset gives it
  **`info`**, not `warn`, and `--error-on-warnings` does not promote info —
  so this is the one of the original four that failing on warnings does not
  subsume.
- `useComponentExportOnlyModules` is set to **`info`** rather than `warn`.
  It is meant to be advisory (the four shadcn/ui files under
  `components/ui/` legitimately export a component beside its `cva`
  variants, and suppress it individually), and `warn` stopped being an
  advisory level the moment warnings started failing. `info` is now what
  "surfaced, never fatal" is spelled as.

Tests run under Vitest with `jsdom` as the environment for every file —
`@testing-library/react`, `@testing-library/user-event` and
`@testing-library/jest-dom` are installed, so a test can render a
component and drive it the way a person would, as well as call a plain
function. `vitest.config.ts` sets the environment globally rather than
leaving each file to declare `@vitest-environment`, so a component test
cannot fail obscurely for having forgotten it; see that file's own comment
for why it is separate from `vite.config.ts`.

Test files are colocated next to the code they cover
(`postingClassification.ts` + `postingClassification.test.ts` in the same
folder), not under a separate `tests/` tree.
