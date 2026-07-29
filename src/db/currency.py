"""The currency vocabulary, declared once *below* both ledgers rather than inside one of them.

`CurrencyCode` used to live in `accounting.models`, and that placement is
exactly why `trades.ledger_events.currency` had no constraint at all: it is
the identical column, but `trades` may not import `accounting` (see
`docs/architecture.md`), so there was nothing for it to restate. Moving the
list down here — into `db`, which both packages already depend on for
`db.money` and `db.base` — is what lets one declaration cover both.

`CurrencyCode` stays a hand-written `typing.Literal` rather than being
derived from the `public.currencies` table. It has to: a `Literal` is what
makes `currency: CurrencyCode` a *narrow union* in mypy, in pydantic's
validation, in the generated OpenAPI schema, and therefore in the SPA's
`schema.ts`. None of those can be computed from rows in a database at type-check
time, and a `str` field with a runtime check would trade a compile-time
guarantee for a 422. So the Literal is the **API vocabulary** and the table
is the **referential guarantee**, and they are kept in step by
`CURRENCY_REFERENCE` below being keyed by `CurrencyCode` (so mypy rejects a
row for a currency the Literal doesn't have) plus
`tests/db/test_schema_invariants.py`'s exhaustiveness check (which rejects a
Literal arm with no row).

What the table holds is what this app already knows about a currency and
nothing more: its code, the glyph it renders with, and how many decimal
places it is quoted to. That is precisely the content of what used to be
`accounting.models.SUPPORTED_CURRENCIES`, which is now a projection of this.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

CurrencyCode = Literal["USD", "EUR"]
"""Every currency this app knows how to hold money in or convert between.

The API vocabulary — see this module's docstring for why it stays a
`Literal` now that `public.currencies` exists.
"""


class CurrencyReference(NamedTuple):
    """One row of `public.currencies`: a currency code and how to render an amount in it."""

    symbol: str
    """The glyph an amount in this currency is prefixed with, e.g. `$`."""
    decimal_places: int
    """How many fractional digits this currency is quoted to."""


CURRENCY_REFERENCE: dict[CurrencyCode, CurrencyReference] = {
    "USD": CurrencyReference(symbol="$", decimal_places=2),
    "EUR": CurrencyReference(symbol="€", decimal_places=2),
}
"""The seeded contents of `public.currencies` — the reference list every `currency` column foreign-keys into.

Keyed by `CurrencyCode` so mypy refuses an entry for a currency the Literal
does not have; the other direction (a Literal arm with no entry here) is
asserted by the schema-invariant test, since a mapping's exhaustiveness over
a `Literal` is not something the type checker can check.

Adding a currency is now **one** edit — a new arm on `CurrencyCode` and its
entry here — and it reaches the API contract, the display registry
(`accounting.models.SUPPORTED_CURRENCIES`) and the database's own reference
table from that single place. No migration touches any of the nine columns
that reference it.
"""
