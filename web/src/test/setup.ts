// `@testing-library/jest-dom/vitest` registers the DOM matchers
// (`toBeInTheDocument`, `toHaveValue`, `toBeDisabled`, …) against vitest's
// `expect` and augments its `Assertion` type, so importing it here is what
// makes them exist at runtime and typecheck across every test file.
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Testing Library unmounts what a test rendered only when it can find a global
// `afterEach`, which vitest exposes exclusively under `globals: true`. This
// project keeps globals off, so the hook is registered by hand — without it
// every render in a file piles up in the same `document.body`, and a query
// like `getByRole('spinbutton')` starts failing with "found multiple
// elements" the moment a second test renders the same component.
afterEach(cleanup)

// jsdom implements no layout and ships no `ResizeObserver`, so every element
// reports `offsetHeight === 0` and nothing ever reports a resize. A row
// virtualizer reads exactly those two things — how tall its scroll container
// is, and how tall each row measured — and from zeroes concludes that no rows
// fit. A test of a virtualized table then becomes a test of an empty
// `<tbody>`, passing or failing for reasons that have nothing to do with the
// component.
//
// The shims below supply the two answers jsdom cannot compute. A row is
// recognized by the `data-index` the virtualizer itself puts on every element
// it measures; everything else is treated as the container.
const VIEWPORT_HEIGHT = 800
const VIEWPORT_WIDTH = 1200
const ROW_HEIGHT = 45

if (!('ResizeObserver' in globalThis)) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

for (const [property, viewport] of [
  ['offsetHeight', VIEWPORT_HEIGHT],
  ['offsetWidth', VIEWPORT_WIDTH],
] as const) {
  Object.defineProperty(HTMLElement.prototype, property, {
    configurable: true,
    get(this: HTMLElement) {
      return this.hasAttribute('data-index') ? ROW_HEIGHT : viewport
    },
  })
}
