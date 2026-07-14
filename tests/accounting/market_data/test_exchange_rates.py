import json
import re
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from accounting.config import AccountingConfig
from accounting.market_data import exchange_rates
from accounting.utils import cache_backup


def _config(tmp_path) -> AccountingConfig:
    return AccountingConfig(data_dir=tmp_path)


_URL_DATE_RANGE = re.compile(r"/v1/(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})")


def _requested_range(url: str) -> tuple[date, date]:
    match = _URL_DATE_RANGE.search(url)
    assert match is not None
    return date.fromisoformat(match.group(1)), date.fromisoformat(match.group(2))


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        pass


class _FakeSession:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict] = []

    def get(self, url, params, timeout):
        self.calls.append({"url": url, "params": params})
        return _FakeResponse(self._text)


_PAYLOAD = json.dumps({
    "amount": 1.0,
    "base": "USD",
    "start_date": "2026-06-01",
    "end_date": "2026-06-03",
    "rates": {
        "2026-06-01": {"EUR": 0.86},
        "2026-06-02": {"EUR": 0.87},
        "2026-06-03": {"EUR": 0.88},
    },
})


def test_fetch_rate_history_inverts_frankfurter_rates_into_base_units(tmp_path) -> None:
    session = _FakeSession(_PAYLOAD)
    history = exchange_rates.fetch_rate_history(_config(tmp_path), session=session)
    row = history.filter(pl.col("date") == date(2026, 6, 1)).row(0, named=True)
    assert row["currency"] == "EUR"
    assert row["rate_to_base"] == pytest.approx(1 / 0.86)


def test_fetch_rate_history_requests_every_non_base_currency(tmp_path) -> None:
    session = _FakeSession(_PAYLOAD)
    exchange_rates.fetch_rate_history(_config(tmp_path), session=session)
    assert session.calls[0]["params"]["base"] == "USD"
    assert "EUR" in session.calls[0]["params"]["symbols"]


def test_fetch_rate_history_archives_the_raw_response(tmp_path) -> None:
    config = _config(tmp_path)
    exchange_rates.fetch_rate_history(config, session=_FakeSession(_PAYLOAD))
    archived = list(config.exchange_rates_raw_dir.glob("*.json"))
    assert len(archived) == 1
    assert json.loads(archived[0].read_text()) == json.loads(_PAYLOAD)


def test_load_rate_history_missing_cache_returns_empty_frame(tmp_path) -> None:
    history = exchange_rates.load_rate_history(_config(tmp_path))
    assert history.is_empty()
    assert history.columns == ["date", "currency", "rate_to_base"]


def test_update_rate_history_cache_writes_and_returns_the_history(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        exchange_rates,
        "fetch_rate_history",
        lambda cfg, history_years=2, session=None: pl.DataFrame(
            {"date": [date(2026, 6, 1)], "currency": ["EUR"], "rate_to_base": [1.16]},
            schema=exchange_rates.RATE_HISTORY_SCHEMA,
        ),
    )
    result = exchange_rates.update_rate_history_cache(config)
    assert list(result["rate_to_base"]) == pytest.approx([1.16])
    reloaded = exchange_rates.load_rate_history(config)
    assert list(reloaded["rate_to_base"]) == pytest.approx([1.16])


def test_update_rate_history_cache_requests_the_full_history_years_window_when_cache_is_empty(tmp_path) -> None:
    config = _config(tmp_path)
    session = _FakeSession(_PAYLOAD)

    exchange_rates.update_rate_history_cache(config, history_years=2, session=session)

    requested_start, requested_end = _requested_range(session.calls[0]["url"])
    today = datetime.now(tz=UTC).date()
    assert requested_end == today
    assert requested_start == today - timedelta(days=2 * 365)


def test_update_rate_history_cache_requests_only_the_gap_since_the_cached_max_date(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    existing = _history(("2026-06-01", "EUR", 1.10))
    monkeypatch.setattr(exchange_rates, "load_rate_history", lambda cfg: existing)
    session = _FakeSession(_PAYLOAD)

    exchange_rates.update_rate_history_cache(config, session=session)

    requested_start, requested_end = _requested_range(session.calls[0]["url"])
    today = datetime.now(tz=UTC).date()
    assert requested_start == date(2026, 6, 2)
    assert requested_end == today


def test_update_rate_history_cache_merges_fetched_rows_with_the_existing_cache(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    existing = _history(("2026-06-01", "EUR", 1.10))
    monkeypatch.setattr(exchange_rates, "load_rate_history", lambda cfg: existing)
    monkeypatch.setattr(
        exchange_rates,
        "_fetch_rate_history_range",
        lambda cfg, start, end, session=None: _history(("2026-06-02", "EUR", 1.20)),
    )

    result = exchange_rates.update_rate_history_cache(config)

    assert sorted(result["date"].to_list()) == [date(2026, 6, 1), date(2026, 6, 2)]
    written_directly = pl.read_csv(config.exchange_rates_csv_path, try_parse_dates=True)
    assert sorted(written_directly["date"].to_list()) == [date(2026, 6, 1), date(2026, 6, 2)]


def test_update_rate_history_cache_backs_up_the_cache_file_after_writing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "backups")
    config = _config(tmp_path)
    monkeypatch.setattr(
        exchange_rates,
        "fetch_rate_history",
        lambda cfg, history_years=2, session=None: _history(("2026-06-01", "EUR", 1.16)),
    )

    exchange_rates.update_rate_history_cache(config)

    versions = list((tmp_path / "backups" / "exchange_rates.csv").glob("*.csv"))
    assert len(versions) == 1
    assert versions[0].read_bytes() == config.exchange_rates_csv_path.read_bytes()


def test_load_rate_history_repairs_a_corrupted_cache_from_backup(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "backups")
    config = _config(tmp_path)
    monkeypatch.setattr(
        exchange_rates,
        "fetch_rate_history",
        lambda cfg, history_years=2, session=None: _history(("2026-06-01", "EUR", 1.16)),
    )
    exchange_rates.update_rate_history_cache(config)
    good_bytes = config.exchange_rates_csv_path.read_bytes()
    config.exchange_rates_csv_path.write_text("date,currency,rate_to_base\nnotadate,EUR,1.1\n")

    history = exchange_rates.load_rate_history(config)

    assert history.filter(pl.col("currency") == "EUR")["rate_to_base"].item() == pytest.approx(1.16)
    assert config.exchange_rates_csv_path.read_bytes() == good_bytes


def test_load_rate_history_raises_when_cache_is_corrupted_and_no_backup_exists(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "backups")
    config = _config(tmp_path)
    config.exchange_rates_csv_path.parent.mkdir(parents=True, exist_ok=True)
    config.exchange_rates_csv_path.write_text("date,currency,rate_to_base\nnotadate,EUR,1.1\n")

    with pytest.raises(pl.exceptions.ComputeError):
        exchange_rates.load_rate_history(config)


def test_update_rate_history_cache_makes_no_network_call_when_already_current(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    today = datetime.now(tz=UTC).date()
    existing = _history((today.isoformat(), "EUR", 1.15))
    monkeypatch.setattr(exchange_rates, "load_rate_history", lambda cfg: existing)

    called: list[object] = []
    monkeypatch.setattr(
        exchange_rates,
        "_fetch_rate_history_range",
        lambda cfg, start, end, session=None: (
            called.append((start, end)) or pl.DataFrame(schema=exchange_rates.RATE_HISTORY_SCHEMA)
        ),
    )

    result = exchange_rates.update_rate_history_cache(config)

    assert called == []
    assert result.equals(existing)


def _history(*rows: tuple[str, str, float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [date.fromisoformat(d) for d, _, _ in rows],
            "currency": [c for _, c, _ in rows],
            "rate_to_base": [r for _, _, r in rows],
        },
        schema=exchange_rates.RATE_HISTORY_SCHEMA,
    )


def test_smoothed_rate_as_of_averages_the_trailing_window() -> None:
    history = _history(("2026-06-01", "EUR", 1.10), ("2026-06-02", "EUR", 1.20))
    rate = exchange_rates.smoothed_rate_as_of(history, "EUR", date(2026, 6, 2), window_days=30)
    assert rate == pytest.approx(1.15)


def test_smoothed_rate_as_of_ignores_rows_outside_the_window() -> None:
    history = _history(("2026-01-01", "EUR", 5.0), ("2026-06-02", "EUR", 1.20))
    rate = exchange_rates.smoothed_rate_as_of(history, "EUR", date(2026, 6, 2), window_days=30)
    assert rate == pytest.approx(1.20)


def test_smoothed_rate_as_of_returns_none_when_nothing_is_in_range() -> None:
    history = _history(("2026-01-01", "EUR", 5.0))
    assert exchange_rates.smoothed_rate_as_of(history, "EUR", date(2026, 6, 2), window_days=30) is None


def test_current_rates_to_base_always_maps_the_base_currency_to_one() -> None:
    history = _history(("2026-06-02", "EUR", 1.20))
    rates = exchange_rates.current_rates_to_base(history, date(2026, 6, 2))
    assert rates["USD"] == pytest.approx(1.0)
    assert rates["EUR"] == pytest.approx(1.20)


def test_current_rates_to_base_raises_when_a_currency_has_no_history() -> None:
    with pytest.raises(ValueError, match="EUR"):
        exchange_rates.current_rates_to_base(pl.DataFrame(schema=exchange_rates.RATE_HISTORY_SCHEMA), date(2026, 6, 2))
