"""Money exactness: the guarantee that a stored amount is the amount you get back.

These are the smoke tests behind VISION's "money is exact — never floating
point". They deliberately use the values that break under IEEE-754, so a
regression to `float` anywhere on the persistence or domain path fails here
rather than in someone's balance.

`test_a_money_column_round_trips_exactly` is the load-bearing one: it goes
through a real Postgres `NUMERIC` column, which is what actually broke
before (`asdecimal=False` handed every stored value back as a float).

The last three tests pin something different: not exactness, but the *wire
representation*. `db.money._AS_JSON_NUMBER` deliberately overrides Pydantic
v2's default (which renders a `Decimal` as a JSON string) so that making
money exact in Python changed no HTTP contract. These tests state that
choice out loud, so the cutover to decimal strings has to delete an
assertion rather than silently alter every money field in the API.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import accounting.db as adb
from accounting.models import Posting
from db.base import ensure_reference_rows
from db.money import (
    MONEY_QUANTUM,
    ZERO,
    quantize_money,
    quantize_rate,
    quantize_shares,
    round_to_currency,
    to_decimal,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# The canonical float-drift demonstration: 0.1 + 0.2 != 0.3 in IEEE-754.
_DRIFTY_ADDENDS = (Decimal("0.1"), Decimal("0.2"))
_DRIFTY_SUM = Decimal("0.3")


def test_float_addition_really_does_drift() -> None:
    """Pin the premise, so the tests below are demonstrably not vacuous."""
    assert 0.1 + 0.2 != 0.3  # noqa: RUF069 — demonstrating the drift is the point of this test


def test_decimal_addition_does_not_drift() -> None:
    left, right = _DRIFTY_ADDENDS
    assert left + right == _DRIFTY_SUM


def test_to_decimal_routes_a_float_through_its_literal_not_its_binary_error() -> None:
    # Decimal(0.1) would be 0.1000000000000000055511151231257827021181583404541015625.
    assert to_decimal(0.1) == Decimal("0.1")


def test_quantize_money_rounds_half_up_not_half_even() -> None:
    # Banker's rounding would give 0.0002 here; the stated policy is half-up.
    assert quantize_money(Decimal("0.00025")) == Decimal("0.0003")
    assert quantize_money(Decimal("0.00035")) == Decimal("0.0004")


def test_quantize_money_keeps_the_storage_scale() -> None:
    assert quantize_money(Decimal("1.23456789")).as_tuple().exponent == -4
    assert quantize_money(1) == Decimal("1.0000")


def test_quantize_rate_and_shares_keep_their_own_wider_scales() -> None:
    assert quantize_rate(Decimal("1.23456789")).as_tuple().exponent == -6
    assert quantize_shares(Decimal("1.234567891")).as_tuple().exponent == -8


def test_round_to_currency_uses_the_currency_own_precision() -> None:
    assert round_to_currency(Decimal("1.005"), 2) == Decimal("1.01")
    assert round_to_currency(Decimal("1.005"), 0) == Decimal(1)


def test_a_hundred_cent_additions_sum_to_exactly_one_dollar() -> None:
    """The classic accumulation drift: 100 x 0.01 is 1.00, not 1.0000000000000007."""
    total = ZERO
    for _ in range(100):
        total += Decimal("0.01")
    assert total == Decimal("1.00")

    drifting = 0.0
    for _ in range(100):
        drifting += 0.01
    assert drifting != 1.0  # noqa: RUF069 — demonstrating the drift is the point of this test


def test_a_posting_amount_stays_exact_through_the_domain_model() -> None:
    posting = Posting(
        posting_id="p1",
        transaction_id="t1",
        account_id="a1",
        posted_at=datetime(2026, 1, 1),
        amount=Decimal("0.1"),
        currency="USD",
    )
    assert posting.amount + Decimal("0.2") == _DRIFTY_SUM


def test_a_money_field_accepts_a_decimal_string_without_going_through_float() -> None:
    posting = Posting(
        posting_id="p1",
        transaction_id="t1",
        account_id="a1",
        posted_at=datetime(2026, 1, 1),
        amount="12345678901234.5678",  # type: ignore[arg-type] — pydantic coerces the string
        currency="USD",
    )
    # A float64 has ~15-17 significant digits; this value has 18 and would be
    # rounded by any float round trip.
    assert posting.amount == Decimal("12345678901234.5678")


def test_a_money_column_round_trips_exactly(db_session: Session, test_user_id: uuid.UUID) -> None:
    """The one that actually exercises Postgres: NUMERIC in, exact Decimal out.

    This is what `asdecimal=False` used to break — the value was stored
    exactly and then handed back to Python as a float on every read.
    """
    # `accounts.institution` is a real reference now — the app's own write path
    # creates it (`repositories.accounts.replace_accounts`), and a test writing
    # the ORM row directly does the same thing rather than skipping the step.
    ensure_reference_rows(db_session, adb.Institution, ["Test"])
    account = adb.Account(
        id=uuid.uuid4(),
        user_id=test_user_id,
        natural_key="exactness:checking",
        name="Exactness",
        kind="checking",
        institution="Test",
        currency="USD",
    )
    other_asset = adb.OtherAsset(
        id=uuid.uuid4(),
        user_id=test_user_id,
        natural_key="exactness:asset",
        name="Exactness",
        value=Decimal("0.1"),
        currency="USD",
    )
    db_session.add(account)
    db_session.add(other_asset)
    db_session.flush()
    db_session.expire_all()

    reloaded = db_session.get(adb.OtherAsset, other_asset.id)
    assert reloaded is not None
    assert isinstance(reloaded.value, Decimal)
    assert reloaded.value + Decimal("0.2") == _DRIFTY_SUM
    # And the stored scale is the declared one, so nothing was silently widened.
    assert reloaded.value == reloaded.value.quantize(MONEY_QUANTUM)


def test_a_money_field_serializes_to_json_as_a_number() -> None:
    """`Money` is a `Decimal` in Python and a JSON number on the wire.

    Pydantic v2 would render a bare `Decimal` as a JSON *string*; the
    `PlainSerializer` in `db.money` overrides that. Asserting on the parsed
    type rather than the raw text keeps this about the JSON type, not about
    how many trailing zeros the encoder happens to emit.
    """
    posting = Posting(
        posting_id="p1",
        transaction_id="t1",
        account_id="a1",
        posted_at=datetime(2026, 1, 1),
        amount=Decimal("12.34"),
        currency="USD",
    )
    amount = json.loads(posting.model_dump_json())["amount"]
    assert isinstance(amount, float)
    assert to_decimal(amount) == Decimal("12.34")


def test_a_money_field_declares_itself_a_number_in_the_json_schema() -> None:
    """The OpenAPI schema agrees with the serializer, so the generated TS client types money as `number`.

    Both modes are pinned: `WithJsonSchema` is declared twice in
    `db.money` precisely because validation and serialization schemas are
    generated separately, and a mismatch would make FastAPI emit one of
    each into `web/openapi.json`.
    """
    for mode in ("validation", "serialization"):
        schema = Posting.model_json_schema(mode=mode)  # type: ignore[arg-type] — literal is a valid JsonSchemaMode
        assert schema["properties"]["amount"] == {"type": "number", "title": "Amount"}


def test_a_money_field_still_validates_from_a_json_number_exactly() -> None:
    """A number arriving in a request body becomes an exact `Decimal`, not a float.

    `to_decimal` routes a float through `str`, so the value the client
    wrote is the value that is stored — this is the half of the contract
    that survives the cutover to decimal strings, since a request body
    goes on accepting both forms.
    """
    posting = Posting.model_validate({
        "posting_id": "p1",
        "transaction_id": "t1",
        "account_id": "a1",
        "posted_at": "2026-01-01T00:00:00",
        "amount": 0.1,
        "currency": "USD",
    })
    assert isinstance(posting.amount, Decimal)
    assert posting.amount + Decimal("0.2") == _DRIFTY_SUM
