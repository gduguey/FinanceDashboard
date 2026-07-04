"""Ledger-consuming logic through a ledger-replay.

Everything here takes the canonical ledger (see `trades.models.LedgerEvent`)
and derives positions, gains, and metrics from it.
"""
