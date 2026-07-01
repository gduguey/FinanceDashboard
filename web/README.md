# Investments dashboard (frontend)

React + TypeScript + Vite + Tailwind + shadcn/ui + Recharts + TanStack Query.
Fetches from the FastAPI server in `src/trades/api.py` via a dev-time proxy
(`/api/*` → `http://localhost:8000`, configured in `vite.config.ts`).

See the repo root [README.md](../README.md) for how to run this alongside
the API server. Quick version:

```bash
npm install
npm run dev      # http://localhost:5173, with the API server already running on :8000
```

`npm run build` typechecks (`tsc -b`) and produces a production build;
`npm run lint` runs oxlint.
