"""Pure ledger-replay domain logic.

Everything here takes the canonical ledger (see `trades.models.LedgerEvent`)
and derives positions, gains, and metrics from it — no I/O, no broker
awareness (see docs/architecture.md).
"""
