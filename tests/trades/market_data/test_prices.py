from datetime import date

import polars as pl
import pytest

from trades.market_data import prices
from trades.config import AppConfig
from trades.models import PriceObservation


def _config(tmp_path) -> AppConfig:
    return AppConfig(prices={"cache_dir": tmp_path})


def test_price_observation_rejects_non_positive_close() -> None:
    with pytest.raises(ValueError, match="close"):
        PriceObservation(symbol="VOO", price_date=date(2026, 1, 1), close=0)


def test_load_price_cache_missing_file_returns_empty_frame(tmp_path) -> None:
    df = prices.load_price_cache("VOO", _config(tmp_path))
    assert df.is_empty()
    assert df.columns == ["price_date", "close"]


def test_missing_ranges_empty_cache_wants_full_range() -> None:
    empty = pl.DataFrame(schema={"price_date": pl.Date, "close": pl.Float64})
    gaps = prices._missing_ranges(empty, since=date(2026, 1, 1), as_of=date(2026, 6, 1))
    assert gaps == [(date(2026, 1, 1), date(2026, 6, 1))]


def test_missing_ranges_fully_covered_wants_nothing() -> None:
    existing = pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 6, 1)], "close": [1.0, 2.0]})
    gaps = prices._missing_ranges(existing, since=date(2026, 1, 1), as_of=date(2026, 6, 1))
    assert gaps == []


def test_missing_ranges_wants_front_and_back_gaps() -> None:
    existing = pl.DataFrame({"price_date": [date(2026, 3, 1), date(2026, 3, 10)], "close": [1.0, 2.0]})
    gaps = prices._missing_ranges(existing, since=date(2026, 1, 1), as_of=date(2026, 6, 1))
    assert gaps == [(date(2026, 1, 1), date(2026, 2, 28)), (date(2026, 3, 11), date(2026, 6, 1))]


def test_update_price_cache_writes_and_is_idempotent(tmp_path, monkeypatch) -> None:
    calls = []

    def fake_fetch(symbol, start, end, config, *, adjusted=False, session=None):
        calls.append((start, end))
        return pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [100.0, 101.0]})

    monkeypatch.setattr(prices, "fetch_price_history", fake_fetch)
    config = _config(tmp_path)

    first = prices.update_price_cache("VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 2), config=config)
    assert len(first) == 2
    assert len(calls) == 1
    assert (tmp_path / "VOO.csv").exists()

    second = prices.update_price_cache("VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 2), config=config)
    assert len(calls) == 1
    assert len(second) == 2


def test_update_price_cache_only_fetches_the_new_gap(tmp_path, monkeypatch) -> None:
    calls = []

    def fake_fetch(symbol, start, end, config, *, adjusted=False, session=None):
        calls.append((start, end))
        return pl.DataFrame({"price_date": [start], "close": [100.0]})

    monkeypatch.setattr(prices, "fetch_price_history", fake_fetch)
    config = _config(tmp_path)

    prices.update_price_cache("VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 1), config=config)
    prices.update_price_cache("VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 5), config=config)

    assert len(calls) == 2
    assert calls[1] == (date(2026, 1, 2), date(2026, 1, 5))


def test_update_price_cache_writes_a_separate_file_when_adjusted(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        prices,
        "fetch_price_history",
        lambda symbol, start, end, config, *, adjusted=False, session=None: pl.DataFrame({
            "price_date": [date(2026, 1, 1)],
            "close": [90.0],
        }),
    )
    config = _config(tmp_path)

    adjusted = prices.update_price_cache(
        "VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 1), config=config, adjusted=True
    )

    assert list(adjusted["close"]) == pytest.approx([90.0])
    assert (tmp_path / "VOO.adjusted.csv").exists()
    assert prices.load_price_cache("VOO", config).is_empty()


def test_price_as_of_rolls_back_over_gaps() -> None:
    history = pl.DataFrame({"price_date": [date(2026, 1, 2), date(2026, 1, 5)], "close": [100.0, 110.0]})
    assert prices.price_as_of(history, date(2026, 1, 4)) == pytest.approx(100.0)
    assert prices.price_as_of(history, date(2026, 1, 5)) == pytest.approx(110.0)
    assert prices.price_as_of(history, date(2026, 1, 1)) is None


def test_price_as_of_accepts_a_lazyframe() -> None:
    history = pl.DataFrame({"price_date": [date(2026, 1, 2)], "close": [100.0]}).lazy()
    assert prices.price_as_of(history, date(2026, 1, 4)) == pytest.approx(100.0)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    """Records the call and returns a fixed chart-API-shaped payload with both a `close` and an
    `adjclose` series that differ, so a test can tell which one a function actually read.
    """

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls: list[dict] = []

    def get(self, url, params, headers, timeout):
        self.calls.append({"url": url, "params": params})
        return _FakeResponse(self._payload)


_CHART_PAYLOAD = {
    "chart": {
        "result": [
            {
                "timestamp": [1767225600, 1767312000],  # 2026-01-01, 2026-01-02 UTC
                "indicators": {
                    "quote": [{"close": [100.0, 101.0]}],
                    "adjclose": [{"adjclose": [90.0, 91.0]}],
                },
            }
        ]
    }
}


def test_fetch_price_history_reads_the_raw_close_series() -> None:
    session = _FakeSession(_CHART_PAYLOAD)
    df = prices.fetch_price_history("VOO", date(2026, 1, 1), date(2026, 1, 2), AppConfig(), session=session)
    assert list(df["close"]) == pytest.approx([100.0, 101.0])


def test_fetch_price_history_reads_the_adjclose_series_when_adjusted() -> None:
    session = _FakeSession(_CHART_PAYLOAD)
    df = prices.fetch_price_history(
        "VOO", date(2026, 1, 1), date(2026, 1, 2), AppConfig(), adjusted=True, session=session
    )
    assert list(df["close"]) == pytest.approx([90.0, 91.0])
