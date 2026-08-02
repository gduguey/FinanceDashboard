from datetime import date, datetime

import polars as pl
import pytest

from accounting.ledger.currency import (
    DisplayCurrency,
    UnconvertibleCurrencyError,
    convert,
    with_converted_amount,
)

RATES = {"USD": 1.0, "EUR": 1.08}


def test_convert_same_currency_is_a_no_op() -> None:
    assert convert(100.0, "USD", "USD", RATES) == pytest.approx(100.0)


def test_convert_eur_to_usd_multiplies_by_the_rate() -> None:
    assert convert(100.0, "EUR", "USD", RATES) == pytest.approx(108.0)


def test_convert_usd_to_eur_divides_by_the_rate() -> None:
    assert convert(108.0, "USD", "EUR", RATES) == pytest.approx(100.0)


def test_convert_preserves_sign() -> None:
    assert convert(-50.0, "EUR", "USD", RATES) == pytest.approx(-54.0)


def test_convert_supports_a_third_currency_via_the_shared_base() -> None:
    rates = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27}
    assert convert(100.0, "EUR", "GBP", rates) == pytest.approx(108.0 / 1.27)


def _flow(currencies: list[str | None]) -> pl.LazyFrame:
    """A minimal flow frame — one row per currency, all on the same day.

    Returns
    -------
    polars.LazyFrame
    """
    return pl.LazyFrame(
        {
            "amount": [10.0] * len(currencies),
            "currency": currencies,
            "date": [datetime(2026, 3, 1)] * len(currencies),
        },
        schema={"amount": pl.Float64, "currency": pl.Utf8, "date": pl.Datetime("us")},
    )


_DATED_RATES = pl.DataFrame(
    {
        "currency": ["USD", "EUR"],
        "rate_date": [date(2026, 3, 1)] * 2,
        "rate_into_display": [1.0, 1.08],
    },
    schema={"currency": pl.Utf8, "rate_date": pl.Date, "rate_into_display": pl.Float64},
)


def test_a_currency_with_no_dated_rate_raises_instead_of_nulling_the_amount() -> None:
    """Item A7: the left join used to turn an unknown currency into a null amount every `sum` skipped."""
    display = DisplayCurrency("USD", RATES, rates_by_date=_DATED_RATES)

    with pytest.raises(UnconvertibleCurrencyError, match="'GBP'"):
        with_converted_amount(_flow(["USD", "GBP"]), display, "currency", "date").collect()


def test_a_currency_with_no_scalar_rate_raises_too() -> None:
    """The `rates_by_date is None` path left-joins as well, and A7's entry only described the dated one."""
    display = DisplayCurrency("USD", {"USD": 1.0})

    with pytest.raises(UnconvertibleCurrencyError, match="'EUR'"):
        with_converted_amount(_flow(["USD", "EUR"]), display, "currency", "date").collect()


def test_a_null_currency_raises_rather_than_vanishing_from_the_sum() -> None:
    """The same silent drop, arriving one join earlier than A7's entry describes.

    `dashboard.income_statement.real_income_expense_legs` left-joins
    `account_currency` off the `accounts` dict it was handed, so a posting
    on an account missing from that dict reaches this function already
    carrying a null currency — and a null joins to nothing exactly the way
    an unknown code does.
    """
    display = DisplayCurrency("USD", RATES, rates_by_date=_DATED_RATES)

    with pytest.raises(UnconvertibleCurrencyError, match="None"):
        with_converted_amount(_flow(["USD", None]), display, "currency", "date").collect()


def test_every_covered_currency_still_converts_at_its_own_dates_rate() -> None:
    """The guard rejects only what has no rate — the conversion itself is unchanged."""
    display = DisplayCurrency("USD", RATES, rates_by_date=_DATED_RATES)

    converted = with_converted_amount(_flow(["USD", "EUR"]), display, "currency", "date").collect()

    assert converted["amount"].to_list() == pytest.approx([10.0, 10.8])
    assert converted.columns == ["amount", "currency", "date"]
