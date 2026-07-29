"""One module per aggregate root — each loads and writes its own tables, independently.

This package replaces the single `AccountingStore` load-mutate-save cycle
that used to front every accounting table at once (VISION-AUDIT T2). That
shape forced three costs on every caller: a read of ~25 tables to change
one row, a blanket `DELETE`-and-reinsert of ~16 tables to write it back,
and a single whole-store version counter that made two unrelated edits
conflict with each other.

The boundaries here are the aggregate roots the DB-design audit's target
shape names, not the tables themselves:

- `accounts` — accounts, their opening balances, and the manual
  transfers between them.
- `taxonomy` — the reference data postings point at (categories, tags),
  plus two standalone user-entered lists with no better home yet.
- `planning` — budgets, goals, and the automations that fund them.
- `interpretation` — everything layered *on top of* the immutable ledger.

A module here owns its tables' row-to-model translation in both
directions, and nothing outside it writes those tables.
"""
