from datetime import date

import pytest

from accounting.importers.canonical.parsing import find_column, parse_amount_flexible, parse_date_flexible

# --- Dates ---------------------------------------------------------------


def test_parse_date_flexible_reads_iso_dates() -> None:
    assert parse_date_flexible("2026-06-30") == date(2026, 6, 30)


def test_parse_date_flexible_reads_us_style_dates() -> None:
    assert parse_date_flexible("06/30/2026") == date(2026, 6, 30)


def test_parse_date_flexible_reads_named_month_dates() -> None:
    assert parse_date_flexible("Jan 5, 2026") == date(2026, 1, 5)


def test_parse_date_flexible_reads_day_month_year_with_dashes() -> None:
    assert parse_date_flexible("05-Jan-2026") == date(2026, 1, 5)


def test_parse_date_flexible_returns_none_for_garbage() -> None:
    assert parse_date_flexible("not a date") is None


# --- Amounts ---------------------------------------------------------------


def test_parse_amount_flexible_reads_a_plain_number() -> None:
    assert parse_amount_flexible("123.45") == pytest.approx(123.45)


def test_parse_amount_flexible_reads_us_thousands_separator() -> None:
    assert parse_amount_flexible("1,234.56") == pytest.approx(1234.56)


def test_parse_amount_flexible_reads_european_thousands_separator() -> None:
    assert parse_amount_flexible("1.234,56") == pytest.approx(1234.56)


def test_parse_amount_flexible_reads_a_currency_symbol() -> None:
    assert parse_amount_flexible("$1,234.56") == pytest.approx(1234.56)
    assert parse_amount_flexible("€1.234,56") == pytest.approx(1234.56)


def test_parse_amount_flexible_reads_parentheses_as_negative() -> None:
    assert parse_amount_flexible("(123.45)") == pytest.approx(-123.45)


def test_parse_amount_flexible_reads_a_trailing_minus_sign() -> None:
    assert parse_amount_flexible("123.45-") == pytest.approx(-123.45)


def test_parse_amount_flexible_reads_comma_only_thousands_with_no_decimal() -> None:
    assert parse_amount_flexible("1,234") == pytest.approx(1234.0)


def test_parse_amount_flexible_reads_dot_only_european_thousands_with_no_decimal() -> None:
    assert parse_amount_flexible("1.234") == pytest.approx(1234.0)


def test_parse_amount_flexible_returns_none_for_garbage() -> None:
    assert parse_amount_flexible("n/a") is None


# --- Column matching ---------------------------------------------------------------


def test_find_column_matches_case_and_whitespace_insensitively() -> None:
    header = ["Transaction Date", " Description ", "Amount"]
    assert find_column(header, {"date", "transaction date"}) == "Transaction Date"
    assert find_column(header, {"description"}) == " Description "


def test_find_column_returns_none_when_nothing_matches() -> None:
    assert find_column(["Foo", "Bar"], {"date"}) is None
