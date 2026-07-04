# Portfolio Dashboard — Implementation Spec

Remaining tasks

---

Not implemented:

6.8 Look-through concentration panel — No code exists for this anywhere. It requires fetching published ETF holdings, aggregating top underlying names weighted by your allocation, and showing the top-10 + US/intl/bonds/cash split. This is genuinely absent.

6.9 Trust & data-quality layer (partially) — data_quality() exists (last price sync date per symbol), but the two other sub-items are missing:

Monthly reconciliation — dashboard value vs. broker statement to the cent, with drift warnings
Ledger export (CSV/JSON one-click)
6.10 Anti-overmonitoring defaults — This is a pure frontend/UX concern. No code enforces "default to since-inception range" or de-emphasizes daily change — depends on how the frontend time-range picker is implemented.