import pytest

from accounting.ledger.currency import convert


def test_convert_same_currency_is_a_no_op() -> None:
    assert convert(100.0, "USD", "USD", eur_usd_rate=1.08) == pytest.approx(100.0)


def test_convert_eur_to_usd_multiplies_by_the_rate() -> None:
    assert convert(100.0, "EUR", "USD", eur_usd_rate=1.08) == pytest.approx(108.0)


def test_convert_usd_to_eur_divides_by_the_rate() -> None:
    assert convert(108.0, "USD", "EUR", eur_usd_rate=1.08) == pytest.approx(100.0)


def test_convert_preserves_sign() -> None:
    assert convert(-50.0, "EUR", "USD", eur_usd_rate=1.08) == pytest.approx(-54.0)
