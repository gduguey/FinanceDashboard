"""The one piece of storage `accounting` and `trades` are allowed to share: whose data it is.

Every other table belongs to exactly one of `accounting.db.models` or
`trades.db.models`, each in its own Postgres schema. `users` (and
`user_secrets`) live here instead, in the default `public` schema, because
both modules' rows need to answer "which user owns this row" and there can
only ever be one such answer per person — mirrors the single deliberate
`accounting` -> `trades` coupling point described in `docs/architecture.md`.
"""
