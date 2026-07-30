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
