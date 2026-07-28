"""Personal accounting: bank/credit-card transactions, categorization, and net worth.

Independent of `trades` (investments) at the code level — the only coupling
is one direction, `dashboard.net_worth` reading `trades.dashboard` for the
portfolio's value, never the reverse. See `docs/accounting/architecture.md`
for the full module map and core conventions.
"""
