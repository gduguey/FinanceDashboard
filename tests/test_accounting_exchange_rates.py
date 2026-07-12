import json
from datetime import date

import polars as pl
import pytest

from accounting.config import AccountingConfig
from accounting.market_data import exchange_rates


def _config(tmp_path) -> AccountingConfig:
    return AccountingConfig(data_dir=tmp_path)


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
