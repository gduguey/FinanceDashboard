from datetime import date

import pandas as pd
import pytest

from trades import prices
from trades.config import PriceApiConfig
from trades.models import PriceObservation


def _config(tmp_path) -> PriceApiConfig:
    return PriceApiConfig(cache_dir=tmp_path)


def test_price_observation_rejects_non_positive_close() -> None:
    with pytest.raises(ValueError):
        PriceObservation(symbol="VOO", price_date=date(2026, 1, 1), close=0)


def test_load_price_cache_missing_file_returns_empty_frame(tmp_path) -> None:
    df = prices.load_price_cache("VOO", _config(tmp_path))
    assert df.empty
    assert list(df.columns) == prices.CACHE_COLUMNS


def test_missing_ranges_empty_cache_wants_full_range() -> None:
    empty = pd.DataFrame(columns=prices.CACHE_COLUMNS)
    gaps = prices._missing_ranges(empty, since=date(2026, 1, 1), as_of=date(2026, 6, 1))
    assert gaps == [(date(2026, 1, 1), date(2026, 6, 1))]


def test_missing_ranges_fully_covered_wants_nothing() -> None:
    existing = pd.DataFrame(
        {"price_date": pd.to_datetime(["2026-01-01", "2026-06-01"]), "close": [1.0, 2.0]}
    )
    gaps = prices._missing_ranges(existing, since=date(2026, 1, 1), as_of=date(2026, 6, 1))
    assert gaps == []


def test_missing_ranges_wants_front_and_back_gaps() -> None:
    existing = pd.DataFrame(
        {"price_date": pd.to_datetime(["2026-03-01", "2026-03-10"]), "close": [1.0, 2.0]}
    )
    gaps = prices._missing_ranges(existing, since=date(2026, 1, 1), as_of=date(2026, 6, 1))
    assert gaps == [(date(2026, 1, 1), date(2026, 2, 28)), (date(2026, 3, 11), date(2026, 6, 1))]


def test_update_price_cache_writes_and_is_idempotent(tmp_path, monkeypatch) -> None:
    calls = []

    def fake_fetch(symbol, start, end, config, session=None):
        calls.append((start, end))
        return pd.DataFrame(
            {"price_date": pd.to_datetime(["2026-01-01", "2026-01-02"]), "close": [100.0, 101.0]}
        )

    monkeypatch.setattr(prices, "fetch_price_history", fake_fetch)
    config = _config(tmp_path)

    first = prices.update_price_cache(
        "VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 2), config=config
    )
    assert len(first) == 2
    assert len(calls) == 1
    assert (tmp_path / "VOO.csv").exists()

    # Second call for the same range should not hit the network again.
    second = prices.update_price_cache(
        "VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 2), config=config
    )
    assert len(calls) == 1
    assert len(second) == 2


def test_update_price_cache_only_fetches_the_new_gap(tmp_path, monkeypatch) -> None:
    calls = []

    def fake_fetch(symbol, start, end, config, session=None):
        calls.append((start, end))
        return pd.DataFrame({"price_date": pd.to_datetime([str(start)]), "close": [100.0]})

    monkeypatch.setattr(prices, "fetch_price_history", fake_fetch)
    config = _config(tmp_path)

    prices.update_price_cache("VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 1), config=config)
    prices.update_price_cache("VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 5), config=config)

    assert len(calls) == 2
    assert calls[1] == (date(2026, 1, 2), date(2026, 1, 5))


def test_price_as_of_rolls_back_over_gaps() -> None:
    history = pd.DataFrame(
        {"price_date": pd.to_datetime(["2026-01-02", "2026-01-05"]), "close": [100.0, 110.0]}
    )
    assert prices.price_as_of(history, date(2026, 1, 4)) == pytest.approx(100.0)
    assert prices.price_as_of(history, date(2026, 1, 5)) == pytest.approx(110.0)
    assert prices.price_as_of(history, date(2026, 1, 1)) is None
