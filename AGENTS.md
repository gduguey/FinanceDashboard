# Notes for AI assistants (Claude, or any other)

`CLAUDE.md` is a symlink to this file, not a separate copy — edit either
one, both change. Keep it that way: if a tool ever needs its own
differently-named file (e.g. `.cursorrules`), symlink it here too rather
than duplicating content that can then drift.

For what this repo is and how it's organized, read `README.md`,
`docs/architecture.md` (how the `trades`/`accounting` modules and the one
FastAPI app fit together), and each module's own
`docs/trades/architecture.md`/`docs/accounting/architecture.md` first. The
two conventions below are standing rules for this repo, not one-off
preferences.

## 1. New data source -> canonical schema, always

If you add a way to read new data into this repo (a new broker, a new
external API, a new file format, or even just a new column pulled from an
existing source) that overlaps with an existing concept:

- **Don't let the source's native field/column names leak past the module
  that reads it.** Every module downstream of the ledger — `trades.ledger`,
  `trades.dashboard`, `accounting.ledger`, `accounting.dashboard` — must
  stay ignorant of which broker or API anything came from.
- **The canonical name for a concept is declared once**, as the field
  names on its pydantic model in `models.py` (e.g. `LedgerEvent` for
  `trades`' ledger rows, `Posting` for `accounting`'s) — never re-typed as
  a string literal on a separate config object, never invented ad hoc by a
  new preprocessor. A model's field names *are* its column names; there's
  no parallel schema class to keep in sync.
- **Add a `standardize_{source}_...` function to that source's own
  preprocessing/importer module** (e.g. `trades/brokers/ibkr/preprocessing.py`,
  `accounting/importers/<institution>/`) that maps the source's native
  shape onto that canonical schema, and validates the result through the
  matching pydantic model before returning it. If no matching model exists
  yet for this concept, that's a sign to add one — not a reason to skip
  validation.

See `docs/trades/architecture.md` ("The ledger") for the concrete example this
pattern is based on (`IBKR <Trade> rows -> LedgerEvent`, in
`trades.brokers.ibkr.preprocessing.statement_to_ledger`).

## 2. Caching fetched external data: archive raw, derive everything else

Atomic writes (temp file + rename) only protect against a *crash*
mid-write. They do nothing against a *logic* bug that produces a wrong but
fully-formed result and confidently overwrites the last good copy — and if
the upstream API has a limited retrieval window (like IBKR's Flex Query
Period), that overwritten history may not even be re-fetchable afterward.

So: when caching data fetched from an external source, save the raw
response verbatim, timestamped, and never overwritten (see
`trades.brokers.ibkr.api`'s `save_raw_statement` / `raw_statements/`)
*before* attempting to parse or merge anything. Whatever derived/merged
file the rest of the app actually reads should be treated as a disposable
cache of that raw archive — cheap to delete and regenerate (see
`rebuild_from_raw_statements`), never the only copy of the data. Apply this
to any new fetched-and-cached data source, not just IBKR.

## 3. Guarding tests against real credentials in `.env`

A pydantic-settings model reading a real credential (a database URL, an R2
key, an encryption key, ...) needs protecting from *both* of its own
sources, independently — disabling only one still leaves the other:

- **Passing `_env_file=None` alone is not enough.** It only stops the
  model reading the `.env` *file* — it does nothing about a real value
  already sitting in `os.environ`. Editors that auto-load
  `${workspaceFolder}/.env` (e.g. VS Code's Python extension, on by
  default) put those values there before the test process even starts, so
  a "disabled" credentials object can still resolve to a production
  secret from the environment.
- **Clearing the env var alone is not enough either.** If the model
  still has a real `.env` file to fall back to (its own `model_config`'s
  `env_file` still points at one), stripping the env var with
  `monkeypatch.delenv(..., raising=False)` just makes it silently read
  the same real secret out of the file instead — no error, no signal
  anything went wrong.

Both have to be closed for the same model, every time: strip the env var
*and* remove its `.env`-file fallback (either by constructing it with
`_env_file=None`, or — for a model built by production code you can't
pass constructor args to — blanking `env_file` on its own `model_config`
for the test). See `tests/conftest.py`'s `_no_r2_by_default` (env-var
side) and `_no_real_database_by_default` (both sides, including the
`model_config` patch) fixtures.

## 4. Rules for coding

Avoid using for loops. Use instead polars or numpy expressions whenever possible.

Functions that take in dataframes should be able to be lazy dataframes or regular dataframes and then return the same type. 
However internal calculations should always be done lazy and only collect if a regular dataframe was passed in.

Make sure you add type hints to code, and the types are as STRICT as possible.
Run MYPY and ruff at the end and fix any errors that come up. Also run ruff format

When usings tests make sure to write tests first, make sure the fail before moving writing the code. Use the /tdd skill for tests.

Use Numpy style docstrings on public methods.

Keep the code as simple as possible. Don't add random checks, and assertions unless strictly necessary. Make sure functions aren't just a single line of code, If that's the case it's normally better to be explicit rather than implicit and have that line of code visible. 
