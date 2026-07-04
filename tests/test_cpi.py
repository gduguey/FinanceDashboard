from datetime import date

import polars as pl
import pytest

from trades.market_data import cpi
from trades.config import AppConfig


def _config(tmp_path) -> AppConfig:
    return AppConfig(cpi={"cache_dir": tmp_path})


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


_CSV_TEXT = "DATE,CPIAUCSL\n2026-01-01,300.1\n2026-02-01,301.2\n2026-03-01,.\n"


def test_fetch_cpi_series_parses_rows_and_drops_missing_values() -> None:
    session = _FakeSession(_CSV_TEXT)
    df = cpi.fetch_cpi_series(AppConfig(), session=session)
    assert list(df["observation_date"]) == [date(2026, 1, 1), date(2026, 2, 1)]
    assert list(df["value"]) == pytest.approx([300.1, 301.2])


def test_fetch_cpi_series_handles_missing_value_beyond_the_schema_inference_sample() -> None:
    # polars' default schema inference only samples the first ~100 rows;
    # FRED's "." sentinel for a not-yet-published month sits at the very
    # end of a real, decades-long series, so a naive read_csv infers the
    # value column as numeric and then chokes on "." when it actually
    # parses that row. Reproduce with 150 real rows before the sentinel.
    header = "DATE,CPIAUCSL\n"
    rows = "".join(f"2020-{(i % 12) + 1:02d}-01,{300.0 + i}\n" for i in range(150))
    csv_text = header + rows + "2033-01-01,.\n"
    session = _FakeSession(csv_text)

    df = cpi.fetch_cpi_series(AppConfig(), session=session)

    assert len(df) == 150


def test_fetch_cpi_series_requests_the_configured_series_id() -> None:
    session = _FakeSession(_CSV_TEXT)
    config = AppConfig(cpi={"series_id": "CPIAUCNS"})
    cpi.fetch_cpi_series(config, session=session)
    assert session.calls[0]["params"]["id"] == "CPIAUCNS"


def test_load_cpi_cache_missing_file_returns_empty_frame(tmp_path) -> None:
    df = cpi.load_cpi_cache(_config(tmp_path))
    assert df.is_empty()
    assert df.columns == ["observation_date", "value"]


def test_update_cpi_cache_writes_and_returns_the_series(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        cpi,
        "fetch_cpi_series",
        lambda config, session=None: pl.DataFrame({
            "observation_date": [date(2026, 1, 1), date(2026, 2, 1)],
            "value": [300.1, 301.2],
        }),
    )
    config = _config(tmp_path)

    result = cpi.update_cpi_cache(config)

    assert list(result["value"]) == pytest.approx([300.1, 301.2])
    assert (tmp_path / "CPIAUCSL.csv").exists()
    reloaded = cpi.load_cpi_cache(config)
    assert list(reloaded["value"]) == pytest.approx([300.1, 301.2])


def test_cpi_as_of_rolls_back_to_latest_known_month() -> None:
    history = pl.DataFrame({"observation_date": [date(2026, 1, 1), date(2026, 2, 1)], "value": [300.1, 301.2]})
    assert cpi.cpi_as_of(history, date(2026, 2, 15)) == pytest.approx(301.2)
    assert cpi.cpi_as_of(history, date(2026, 1, 15)) == pytest.approx(300.1)
    assert cpi.cpi_as_of(history, date(2025, 12, 1)) is None
