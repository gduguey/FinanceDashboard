# Notes for AI assistants (Claude, or any other)

`CLAUDE.md` is a symlink to this file, not a separate copy — edit either
one, both change. Keep it that way: if a tool ever needs its own
differently-named file (e.g. `.cursorrules`), symlink it here too rather
than duplicating content that can then drift.

For what this repo is and how it's organized, read `README.md` and
`docs/architecture.md` first. The two conventions below are things a past
session got explicit, repeated instructions about — treat them as standing
rules for this repo, not one-off preferences.

## 1. New data source -> canonical schema, always

If you add a way to read new data into this repo (a new broker, a new
external API, a new file format, or even just a new column pulled from an
existing source) that overlaps with an existing concept:

- **Don't let the source's native field/column names leak past the module
  that reads it.** `transactions.py`, `returns.py`, and `visualization.py`
  must stay ignorant of which broker or API anything came from.
- **The canonical name for a concept is declared once**, as a field on a
  config object in `config.py` (e.g. `TradeSchema` for trade rows) —
  never re-typed as a string literal in multiple places, never invented ad
  hoc by a new preprocessor.
- **Add a `standardize_{source}_...` function to `preprocessing.py`** that
  maps the source's native shape onto that canonical schema, and validates
  the result through the matching pydantic model in `models.py` (e.g.
  `RawTrade`) before returning it. If no matching pydantic model or config
  schema exists yet for this concept, that's a sign to add one — not a
  reason to skip validation.

See `docs/architecture.md` ("Canonical trade schema") for the concrete
example this pattern is based on (`IBKR raw trades -> TradeSchema ->
RawTrade`, in `preprocessing.standardize_ibkr_trades`).

## 2. Caching fetched external data: archive raw, derive everything else

Atomic writes (temp file + rename) only protect against a *crash*
mid-write. They do nothing against a *logic* bug that produces a wrong but
fully-formed result and confidently overwrites the last good copy — and if
the upstream API has a limited retrieval window (like IBKR's Flex Query
Period), that overwritten history may not even be re-fetchable afterward.

So: when caching data fetched from an external source, save the raw
response verbatim, timestamped, and never overwritten (see
`brokers/ibkr.py`'s `_save_raw_statement` / `raw_statements/`) *before*
attempting to parse or merge anything. Whatever derived/merged file the
rest of the app actually reads should be treated as a disposable cache of
that raw archive — cheap to delete and regenerate (see
`rebuild_from_raw_statements`), never the only copy of the data. Apply this
to any new fetched-and-cached data source, not just IBKR.
