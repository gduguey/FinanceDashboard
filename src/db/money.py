"""The one money policy: exact decimals, one storage scale, one rounding mode.

Money is never an IEEE-754 float anywhere it is stored or modelled. Floats
drift (`0.1 + 0.2 != 0.3`), and drift in someone's balance is not an
acceptable failure mode, so every monetary value and every rate crosses the
persistence and domain layers as a `decimal.Decimal`.

Three things live here, and nothing else should redefine them:

- **The storage scale.** `MONEY_SCALE`/`MONEY_QUANTUM` mirror the SQL type
  `db.base.MONEY` (`NUMERIC(18, 4)`). Four places, not two, so a per-unit
  price, a split ratio, or a partial-cent interest accrual survives a round
  trip instead of being truncated on the way in.
- **The rounding mode.** `ROUNDING` is `ROUND_HALF_UP` — what a person means
  by "round to the nearest cent", and the convention finance and accounting
  practice expects. Python's own default is `ROUND_HALF_EVEN`, which is
  better for repeated statistical aggregation and worse for a ledger a human
  reads, so it is overridden explicitly at every call rather than inherited.
- **The wire pin.** `Money`/`Rate` are `Decimal` for validation and
  arithmetic, but serialize as a JSON *number*.

The wire pin needs its own explanation, because it is deliberately
temporary. Pydantic v2 serializes a bare `Decimal` as a JSON **string**, so
simply typing these fields `Decimal` would silently change every money
field in the public API from `12.34` to `"12.34"`, break the generated TS
client, and do it as an accident of an internal refactor rather than as a
decision. `PlainSerializer` + `WithJsonSchema` hold the wire exactly where
it is today: `number` in, `number` out, unchanged OpenAPI.

Flipping the wire to exact decimal *strings* was analysed and **declined**,
not deferred. The argument for it is that `12.34` as a JSON number is an
IEEE-754 double by the time any JavaScript client has parsed it. The
argument against it is that `"12.34"` is too, one line later: without a
decimal library in the browser the string is parsed straight back into the
same double, so the change buys nothing but a contract churn and a
generated client that now types every money field as `string`. Adding such
a library is its own decision with its own cost, and nothing in the app
today does arithmetic on a money value it received — it formats it.

The second reason is worse than the first. Every aggregate the dashboard
shows — balances, net worth, category totals, budget actuals — is already
a float *server-side*, computed in Polars through the boundary
`accounting.ledger.frame` declares. Serializing those as strings would
render a float's binary error as an exact-looking decimal and freeze it
into the contract, which is the opposite of honest. See
`docs/http-api-contract.md`, "Exact money and analytics money", for which
endpoint families are which.

So the two annotations below stay, and money stays a JSON number. What is
exact is exact in Python and in Postgres, which is where arithmetic on it
happens.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import PlainSerializer, WithJsonSchema

MONEY_SCALE = 4
"""Decimal places `db.base.MONEY` (`NUMERIC(18, 4)`) actually stores."""

MONEY_QUANTUM = Decimal("0.0001")
"""`MONEY_SCALE` as a quantize target."""

RATE_SCALE = 6
"""Decimal places a rate/percentage column stores.

Wider than money on purpose: a rate is multiplied *into* a money value, so
rounding it at the money scale first would push the error into the product.
"""

RATE_QUANTUM = Decimal("0.000001")
"""`RATE_SCALE` as a quantize target."""

SHARES_SCALE = 8
"""Decimal places `db.base.SHARES` (`NUMERIC(20, 8)`) stores — brokers report fractional shares this finely."""

SHARES_QUANTUM = Decimal("0.00000001")
"""`SHARES_SCALE` as a quantize target."""

ROUNDING = ROUND_HALF_UP
"""The single rounding mode. See this module's docstring for why not banker's rounding."""

ZERO = Decimal(0)
"""The additive identity, as a `Decimal` — never `0.0`, never `0`."""


def to_decimal(value: Decimal | str | float) -> Decimal:
    """Convert any incoming numeric to an exact `Decimal`.

    A `float` is routed through `str` rather than `Decimal(float)`, because
    the latter faithfully reproduces the float's binary error
    (`Decimal(0.1)` is `0.1000000000000000055511151231257827021181583404541015625`)
    while the former gives the decimal literal the author actually wrote.
    Floats should not reach this function in the first place — it accepts
    them only so the boundaries that still hand us one (a JSON request body,
    an analytics frame on its way back) convert predictably.

    Parameters
    ----------
    value
        The number to convert.

    Returns
    -------
    Decimal
        Exact.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


def quantize_money(value: Decimal | str | float) -> Decimal:
    """Round a monetary value to the scale Postgres will store it at.

    Apply this at a write boundary, so what is persisted is what later
    arithmetic sees — never let Postgres do the truncation implicitly,
    because then the in-memory value and the stored value silently disagree
    until the next read.

    Parameters
    ----------
    value
        The amount to round.

    Returns
    -------
    Decimal
        Quantized to `MONEY_SCALE` using `ROUNDING`.
    """
    return to_decimal(value).quantize(MONEY_QUANTUM, rounding=ROUNDING)


def quantize_rate(value: Decimal | str | float) -> Decimal:
    """Round a rate or percentage to `RATE_SCALE`.

    Parameters
    ----------
    value
        The rate to round.

    Returns
    -------
    Decimal
        Quantized to `RATE_SCALE` using `ROUNDING`.
    """
    return to_decimal(value).quantize(RATE_QUANTUM, rounding=ROUNDING)


def quantize_shares(value: Decimal | str | float) -> Decimal:
    """Round a share count to `SHARES_SCALE`.

    Parameters
    ----------
    value
        The share count to round.

    Returns
    -------
    Decimal
        Quantized to `SHARES_SCALE` using `ROUNDING`.
    """
    return to_decimal(value).quantize(SHARES_QUANTUM, rounding=ROUNDING)


def to_analytics_float(value: Decimal | float) -> float:
    """Convert an exact amount into the float representation the analytics layer uses.

    The single sanctioned `Decimal -> float` conversion in this codebase.
    Both ledgers aggregate with Polars over `Float64` columns, so exactness
    is deliberately given up at that boundary and nowhere else; see
    `accounting.ledger.frame` for the full argument. This exists as a named
    function rather than a bare `float(...)` so every such place is
    greppable.

    Parameters
    ----------
    value
        An exact amount, straight off a `Money`/`Shares` field or a
        `NUMERIC` column.

    Returns
    -------
    float
        For aggregation inside an analytics frame only. Never persist this,
        and never compare it for exact equality.
    """
    return float(value)


def round_to_currency(value: Decimal | str | float, decimal_places: int) -> Decimal:
    """Round a value to a specific currency's own presentation precision.

    Distinct from `quantize_money`: that one is about what the database
    stores, this one is about what the currency itself is denominated in
    (`accounting.models.Currency.decimal_places`). A USD balance shown to a
    user rounds to 2, while the stored value keeps 4.

    Parameters
    ----------
    value
        The amount to round.
    decimal_places
        The currency's decimal places, e.g. `Currency.decimal_places`.

    Returns
    -------
    Decimal
        Quantized to `decimal_places` using `ROUNDING`.
    """
    return to_decimal(value).quantize(Decimal(1).scaleb(-decimal_places), rounding=ROUNDING)


_AS_JSON_NUMBER = (
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema({"type": "number"}, mode="validation"),
    WithJsonSchema({"type": "number"}, mode="serialization"),
)
"""Hold a `Decimal` field's wire representation at `number`, which the module docstring argues is the honest one."""

Money = Annotated[Decimal, *_AS_JSON_NUMBER]
"""A monetary amount: exact `Decimal` in Python, JSON `number` on the wire."""

Rate = Annotated[Decimal, *_AS_JSON_NUMBER]
"""A rate or percentage: exact `Decimal` in Python, JSON `number` on the wire.

Distinct from `Money` by intent, not by representation — the two quantize at
different scales (`quantize_rate` vs `quantize_money`), and naming the
difference at the field keeps a rate from being rounded like a balance.
"""

Shares = Annotated[Decimal, *_AS_JSON_NUMBER]
"""A share count: exact `Decimal` in Python, JSON `number` on the wire.

Not money, but held to the same standard for the same reason — a fractional
share count feeds directly into cost basis and realized gain, so a float
here lands in someone's tax figure. Stored at `SHARES_SCALE`, wider than
money because brokers report fractional shares to eight places.
"""
