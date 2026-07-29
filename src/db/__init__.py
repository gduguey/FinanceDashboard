"""The two things `accounting` and `trades` are allowed to share: whose data it is, and what a currency is.

Every other table belongs to exactly one of `accounting.db.models` or
`trades.db.models`, each in its own Postgres schema. What lives here instead,
in the default `public` schema, is what *both* modules' rows have to agree on:

- **`users`** (and `user_secrets`) — because both need to answer "which user
  owns this row" and there can only ever be one such answer per person.
- **`currencies`** (and the vocabulary behind it, `db.currency`) — because
  both store money in one, and until DB-audit move #3 the list lived in
  `accounting.models`, which `trades` may not import. That is precisely why
  `trades.ledger_events.currency` was the one currency column in the schema
  with no constraint on it at all: there was no shared place to declare the
  list, so the package that couldn't reach it simply went unchecked. Putting
  it below both is the fix, not a convenience.

Mirrors the single deliberate `accounting` -> `trades` coupling point
described in `docs/architecture.md`: shared storage is allowed, but only where
sharing is the fact being modelled.
"""
