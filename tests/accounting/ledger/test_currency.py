import pytest

from accounting.ledger.currency import convert

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
