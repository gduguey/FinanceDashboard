/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_CLERK_PUBLISHABLE_KEY: string
  // Optional: selects the active landing page (see
  // components/layout/landing-pages/index.ts for valid keys). Falls back to
  // a code-level default when unset or unrecognized.
  readonly VITE_LANDING_PAGE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
