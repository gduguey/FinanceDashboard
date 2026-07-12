"""CSV import: the only layer that knows a bank's native file format.

Every function here maps a bank's raw export onto `accounting.models.Posting`
rows and validates through it before returning — the canonical-schema rule
already used throughout `trades.brokers.ibkr`.
"""
