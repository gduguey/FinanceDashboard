from datetime import date, datetime

import polars as pl
import pytest

from trades.config import AppConfig
from trades.ledger.nav import growth_of_100, nav_series, period_pnl, time_weighted_return

CONFIG = AppConfig()


def _values(*rows: tuple[str, float]) -> pl.DataFrame:
    return pl.DataFrame([{"date": date.fromisoformat(d), "value": v} for d, v in rows])


def _flows(*rows: tuple[str, float]) -> pl.DataFrame:
    return pl.DataFrame([{"event_datetime": datetime.fromisoformat(d), "amount": a} for d, a in rows])


def test_nav_series_bootstraps_nav_to_100_on_first_deposit() -> None:
    result = nav_series(_values(("2026-01-01", 1000.0)), _flows(("2026-01-01", -1000.0)))
    row = result.row(0, named=True)
    assert row["nav"] == pytest.approx(100.0)
    assert row["units_outstanding"] == pytest.approx(10.0)


def test_nav_series_grows_with_market_value_between_flows() -> None:
    result = nav_series(
        _values(("2026-01-01", 1000.0), ("2026-01-02", 1100.0)),
        _flows(("2026-01-01", -1000.0)),
    )
    day2 = result.row(1, named=True)
    assert day2["nav"] == pytest.approx(110.0)
    assert day2["units_outstanding"] == pytest.approx(10.0)


def test_nav_series_a_withdrawal_burns_units_using_pre_flow_nav() -> None:
    result = nav_series(
        _values(("2026-01-01", 1000.0), ("2026-01-02", 988.0)),
        _flows(("2026-01-01", -1000.0), ("2026-01-02", 200.0)),
    )
    day2 = result.row(1, named=True)
    expected_nav = 1188.0 / 10.0
    assert day2["nav"] == pytest.approx(expected_nav)
    assert day2["units_outstanding"] == pytest.approx(10.0 - 200.0 / expected_nav)


def test_nav_series_accepts_a_lazyframe() -> None:
    result = nav_series(
        _values(("2026-01-01", 1000.0)).lazy(),
        _flows(("2026-01-01", -1000.0)).lazy(),
    )
    assert isinstance(result, pl.LazyFrame)
    row = result.collect().row(0, named=True)
    assert row["nav"] == pytest.approx(100.0)


def _nav(*rows: tuple[str, float]) -> pl.DataFrame:
    return pl.DataFrame([{"date": date.fromisoformat(d), "nav": n} for d, n in rows])


def test_time_weighted_return_is_raw_under_the_annualization_threshold() -> None:
    nav = _nav(("2026-01-01", 100.0), ("2026-06-01", 110.0))
    result = time_weighted_return(nav, start=date(2026, 1, 1), end=date(2026, 6, 1), config=CONFIG)
    assert result.raw_pct == pytest.approx(10.0)
    assert result.annualized_pct is None


def test_time_weighted_return_annualizes_at_or_beyond_the_threshold() -> None:
    nav = _nav(("2025-01-01", 100.0), ("2026-01-01", 121.0))
    result = time_weighted_return(nav, start=date(2025, 1, 1), end=date(2026, 1, 1), config=CONFIG)
    assert result.raw_pct == pytest.approx(21.0)
    assert result.annualized_pct == pytest.approx(21.0)


def test_time_weighted_return_raises_when_a_date_has_no_nav() -> None:
    nav = _nav(("2026-01-01", 100.0))
    with pytest.raises(ValueError, match="No NAV available"):
        time_weighted_return(nav, start=date(2026, 1, 1), end=date(2026, 6, 1), config=CONFIG)


def test_growth_of_100_reindexes_a_series_so_it_starts_at_100() -> None:
    series = pl.DataFrame({"date": [date(2026, 1, 1), date(2026, 1, 2)], "price": [50.0, 55.0]})
    result = growth_of_100(series, "price")
    assert result["index"].to_list() == pytest.approx([100.0, 110.0])


def test_growth_of_100_accepts_a_lazyframe() -> None:
    series = pl.DataFrame({"date": [date(2026, 1, 1)], "price": [50.0]}).lazy()
    result = growth_of_100(series, "price")
    assert isinstance(result, pl.LazyFrame)
    assert result.collect()["index"].to_list() == pytest.approx([100.0])


def test_period_pnl_nets_out_contributions_from_the_value_change() -> None:
    gain = period_pnl(
        _flows(("2026-06-15", -200.0)),
        value_start=1000.0,
        value_end=1300.0,
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
    )
    assert gain == pytest.approx(100.0)


def test_period_pnl_ignores_flows_outside_the_period() -> None:
    gain = period_pnl(
        _flows(("2026-05-15", -200.0)),
        value_start=1000.0,
        value_end=1100.0,
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
    )
    assert gain == pytest.approx(100.0)
