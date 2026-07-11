from datetime import date

import polars as pl
import pytest

from trades.market_data import prices
from trades.config import AppConfig
from trades.models import PriceObservation
from trades.utils import cache_backup
from trades.utils.io_utils import write_csv_atomic


def _config(tmp_path) -> AppConfig:
    return AppConfig(prices={"cache_dir": tmp_path})


def _daily_frame(start: date, end: date, close: float) -> pl.DataFrame:
    dates = pl.date_range(start, end, "1d", eager=True)
    return pl.DataFrame({"price_date": dates, "close": [close] * len(dates)}, schema=PriceObservation.polars_schema)


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


def test_update_price_cache_writes_and_only_rerequests_the_buffer_window_on_repeat_calls(tmp_path, monkeypatch) -> None:
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

    # The whole 2-day range requested falls inside `_SETTLEMENT_BUFFER_DAYS`
    # of `as_of`, so — unlike before the settlement-buffer fix — it's
    # re-requested again on a second call (see the dedicated
    # settlement-buffer tests below); the resulting cached data is
    # unchanged either way.
    second = prices.update_price_cache("VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 2), config=config)
    assert len(calls) == 2
    assert len(second) == 2
    assert list(second["close"]) == pytest.approx([100.0, 101.0])


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


# --- settlement-buffer re-fetching (part 1: settled/unsettled price bug) ---


def test_update_price_cache_settlement_buffer_forces_rerequest_of_recent_cached_dates(tmp_path, monkeypatch) -> None:
    calls = []

    def fake_fetch(symbol, start, end, config, *, adjusted=False, session=None):
        calls.append((start, end))
        return _daily_frame(start, end, 100.0)

    monkeypatch.setattr(prices, "fetch_price_history", fake_fetch)
    config = _config(tmp_path)

    prices.update_price_cache("VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 5), config=config)
    assert calls == [(date(2026, 1, 1), date(2026, 1, 5))]
    calls.clear()

    # Without the settlement buffer, `_missing_ranges` would see the cache
    # already fully covering [Jan 1, Jan 5] and skip fetching entirely; the
    # buffer instead forces the trailing `_SETTLEMENT_BUFFER_DAYS` days to
    # be re-requested every time, regardless of what's already cached.
    prices.update_price_cache("VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 5), config=config)
    assert calls == [(date(2026, 1, 4), date(2026, 1, 5))]


def test_update_price_cache_settlement_buffer_preserves_old_value_when_refetch_is_partial(
    tmp_path, monkeypatch
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        prices,
        "fetch_price_history",
        lambda symbol, start, end, config, *, adjusted=False, session=None: _daily_frame(start, end, 100.0),
    )
    prices.update_price_cache("VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 5), config=config)

    # Second call: the settlement buffer re-requests [Jan 4, Jan 6] (Jan 4-5
    # already cached, Jan 6 a genuine new gap), but the fresh fetch only
    # comes back with Jan 6 — as if Yahoo had no update for the
    # already-cached Jan 4-5 window (e.g. a transient gap). Jan 4-5's old
    # cached values must survive the merge, not be dropped.
    monkeypatch.setattr(
        prices,
        "fetch_price_history",
        lambda symbol, start, end, config, *, adjusted=False, session=None: pl.DataFrame(
            {"price_date": [date(2026, 1, 6)], "close": [999.0]}, schema=PriceObservation.polars_schema
        ),
    )

    result = prices.update_price_cache("VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 6), config=config)

    by_date = dict(zip(result["price_date"].to_list(), result["close"].to_list(), strict=True))
    assert by_date[date(2026, 1, 4)] == pytest.approx(100.0)
    assert by_date[date(2026, 1, 5)] == pytest.approx(100.0)
    assert by_date[date(2026, 1, 6)] == pytest.approx(999.0)

    reloaded = prices.load_price_cache("VOO", config)
    assert reloaded.filter(pl.col("price_date") == date(2026, 1, 5))["close"].item() == pytest.approx(100.0)


def test_update_price_cache_does_not_rerequest_dates_older_than_the_buffer_window(tmp_path, monkeypatch) -> None:
    calls = []

    def fake_fetch(symbol, start, end, config, *, adjusted=False, session=None):
        calls.append((start, end))
        return _daily_frame(start, end, 100.0)

    monkeypatch.setattr(prices, "fetch_price_history", fake_fetch)
    config = _config(tmp_path)

    prices.update_price_cache("VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 10), config=config)
    calls.clear()

    prices.update_price_cache("VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 10), config=config)

    # Jan 1-8 are safely more than `_SETTLEMENT_BUFFER_DAYS` days old
    # relative to `as_of` and must not be re-requested — only the trailing
    # buffer window (Jan 9-10) is, confirming the existing
    # incremental-efficiency behavior for genuinely old dates is unchanged.
    assert calls == [(date(2026, 1, 9), date(2026, 1, 10))]


def test_update_price_cache_backs_up_the_written_file(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        prices,
        "fetch_price_history",
        lambda symbol, start, end, config, *, adjusted=False, session=None: pl.DataFrame(
            {"price_date": [start], "close": [1.0]}, schema=PriceObservation.polars_schema
        ),
    )
    backup_calls = []
    monkeypatch.setattr(
        prices, "backup_cache_file", lambda local_path, backup_key: backup_calls.append((local_path, backup_key))
    )

    prices.update_price_cache("VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 1), config=config)

    assert backup_calls == [(tmp_path / "VOO.csv", "prices/VOO.csv")]


def test_update_price_cache_writes_no_backup_call_when_nothing_changed(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        prices,
        "fetch_price_history",
        lambda symbol, start, end, config, *, adjusted=False, session=None: pl.DataFrame(
            schema=PriceObservation.polars_schema
        ),
    )
    backup_calls = []
    monkeypatch.setattr(
        prices, "backup_cache_file", lambda local_path, backup_key: backup_calls.append((local_path, backup_key))
    )

    prices.update_price_cache("VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 1), config=config)

    assert backup_calls == []


# --- full adjusted-close refresh (part 2: adjusted-close drift bug) ---


def test_refresh_adjusted_price_history_overwrites_the_full_cached_range_unconditionally(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    path = tmp_path / "VOO.adjusted.csv"

    # Seed a stale adjusted cache for dates that will be re-requested even
    # though they're already fully cached — Yahoo's `adjclose` retroactively
    # changes for the whole history whenever a symbol pays a new dividend,
    # so this must never skip a date the way `update_price_cache`'s
    # incremental fetch does.
    stale = pl.DataFrame({"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [10.0, 20.0]})
    write_csv_atomic(stale, path)

    calls = []

    def fake_fetch(symbol, start, end, config, *, adjusted=False, session=None):
        calls.append((start, end, adjusted))
        return pl.DataFrame(
            {"price_date": [date(2026, 1, 1), date(2026, 1, 2)], "close": [11.0, 22.0]},
            schema=PriceObservation.polars_schema,
        )

    monkeypatch.setattr(prices, "fetch_price_history", fake_fetch)

    result = prices.refresh_adjusted_price_history("VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 2), config=config)

    assert calls == [(date(2026, 1, 1), date(2026, 1, 2), True)]
    assert list(result["close"]) == pytest.approx([11.0, 22.0])
    reloaded = prices.load_price_cache("VOO", config, adjusted=True)
    assert list(reloaded["close"]) == pytest.approx([11.0, 22.0])


def test_refresh_adjusted_price_history_backs_up_the_written_file(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        prices,
        "fetch_price_history",
        lambda symbol, start, end, config, *, adjusted=False, session=None: pl.DataFrame(
            {"price_date": [start], "close": [1.0]}, schema=PriceObservation.polars_schema
        ),
    )
    backup_calls = []
    monkeypatch.setattr(
        prices, "backup_cache_file", lambda local_path, backup_key: backup_calls.append((local_path, backup_key))
    )

    prices.refresh_adjusted_price_history("VOO", since=date(2026, 1, 1), as_of=date(2026, 1, 1), config=config)

    assert backup_calls == [(tmp_path / "VOO.adjusted.csv", "prices/VOO.adjusted.csv")]


def test_refresh_adjusted_price_histories_updates_every_symbol(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        prices,
        "fetch_price_history",
        lambda symbol, start, end, config, *, adjusted=False, session=None: pl.DataFrame(
            {"price_date": [start], "close": [1.0]}, schema=PriceObservation.polars_schema
        ),
    )

    result = prices.refresh_adjusted_price_histories(
        ["AAPL", "VOO"], since=date(2026, 1, 1), as_of=date(2026, 1, 1), config=config
    )

    assert set(result.keys()) == {"AAPL", "VOO"}
    assert (tmp_path / "AAPL.adjusted.csv").exists()
    assert (tmp_path / "VOO.adjusted.csv").exists()


# --- cache-file corruption recovery (part 4, reader side) ---


def test_load_price_cache_recovers_from_corruption_when_a_backup_exists(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "backup-root")
    config = _config(tmp_path)
    path = tmp_path / "VOO.csv"

    good = pl.DataFrame({"price_date": [date(2026, 1, 1)], "close": [100.0]}, schema=PriceObservation.polars_schema)
    good.write_csv(path)
    cache_backup.backup_cache_file(path, "prices/VOO.csv")

    path.write_text('price_date,close\n"unterminated\n')

    result = prices.load_price_cache("VOO", config)

    assert result["close"].to_list() == pytest.approx([100.0])


def test_load_price_cache_raises_when_corrupted_with_no_backup_available(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "backup-root")
    config = _config(tmp_path)
    path = tmp_path / "VOO.csv"
    path.write_text('price_date,close\n"unterminated\n')

    with pytest.raises(pl.exceptions.ComputeError):
        prices.load_price_cache("VOO", config)
