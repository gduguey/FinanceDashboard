import json
from datetime import date

import polars as pl
import pytest

from trades.config import AppConfig
from trades.market_data import hysa_rates
from trades.utils import cache_backup


def _config(tmp_path) -> AppConfig:
    return AppConfig(hysa_rates={"cache_dir": tmp_path})


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        pass


class _FakeSession:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict] = []

    def get(self, url, headers, timeout):
        self.calls.append({"url": url})
        return _FakeResponse(self._text)


def _fake_page(accounts: list[dict]) -> str:
    """Mimic apyarchives.com's Next.js RSC payload: real JSON, wrapped as a JS string literal."""
    inner = f'3:{{"accounts":{json.dumps(accounts)}}}'
    chunk = json.dumps(inner)
    return f"<html><script>self.__next_f.push([1,{chunk}])</script></html>"


_ACCOUNTS = [
    {
        "id": "marcus-by-goldman-sachs",
        "name": "Marcus by Goldman Sachs",
        "provider": "Online Savings Account",
        "history": [
            {"date": "2024-01-01", "apy": 4.5},
            {"date": "2024-06-01", "apy": 4.25},
        ],
        "currentAPY": 4.25,
        "color": "#000",
    },
    {
        "id": "ally-bank",
        "name": "Ally Bank",
        "provider": "Online Savings Account",
        "history": [{"date": "2024-03-01", "apy": 4.0}],
        "currentAPY": 4.0,
        "color": "#111",
    },
]


def test_fetch_hysa_rates_parses_every_bank_and_history_point() -> None:
    session = _FakeSession(_fake_page(_ACCOUNTS))
    df = hysa_rates.fetch_hysa_rates(AppConfig(), session=session)
    assert len(df) == 3
    marcus = df.filter(pl.col("bank_id") == "marcus-by-goldman-sachs").sort("rate_date")
    assert marcus["rate_date"].to_list() == [date(2024, 1, 1), date(2024, 6, 1)]
    assert marcus["apy_pct"].to_list() == pytest.approx([4.5, 4.25])
    assert marcus["bank_name"].to_list() == ["Marcus by Goldman Sachs", "Marcus by Goldman Sachs"]


def test_fetch_hysa_rates_handles_quotes_embedded_in_the_surrounding_payload() -> None:
    # A realistic page has other JSON-escaped content (quotes, braces) both
    # before and after the accounts array in the same RSC chunk; the
    # extraction must not get confused by escaped quotes elsewhere in it.
    inner = '2:{"unrelated":"a \\"quoted\\" value"}\n' + f'3:{{"accounts":{json.dumps(_ACCOUNTS)}}}'
    chunk = json.dumps(inner)
    noisy_page = f"<html><script>self.__next_f.push([1,{chunk}])</script></html>"
    session = _FakeSession(noisy_page)
    df = hysa_rates.fetch_hysa_rates(AppConfig(), session=session)
    assert len(df) == 3


def test_fetch_hysa_rates_raises_a_clear_error_if_the_page_shape_changed() -> None:
    session = _FakeSession("<html>no data here</html>")
    with pytest.raises(ValueError, match="accounts"):
        hysa_rates.fetch_hysa_rates(AppConfig(), session=session)


def test_update_hysa_rates_cache_writes_and_returns_the_series(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        hysa_rates,
        "fetch_hysa_rates",
        lambda config, session=None: pl.DataFrame({
            "bank_id": ["ally-bank"],
            "bank_name": ["Ally Bank"],
            "rate_date": [date(2024, 1, 1)],
            "apy_pct": [4.0],
        }),
    )
    config = _config(tmp_path)

    result = hysa_rates.update_hysa_rates_cache(config)

    assert len(result) == 1
    assert (tmp_path / "rates.csv").exists()
    reloaded = hysa_rates.load_hysa_rates_cache(config)
    assert reloaded["bank_id"].to_list() == ["ally-bank"]


def test_load_hysa_rates_cache_missing_file_returns_empty_frame(tmp_path) -> None:
    df = hysa_rates.load_hysa_rates_cache(_config(tmp_path))
    assert df.is_empty()
    assert df.columns == ["bank_id", "bank_name", "rate_date", "apy_pct"]


_HISTORY = pl.DataFrame({
    "bank_id": ["ally-bank", "ally-bank", "marcus"],
    "bank_name": ["Ally Bank", "Ally Bank", "Marcus"],
    "rate_date": [date(2024, 1, 1), date(2024, 6, 1), date(2024, 3, 1)],
    "apy_pct": [4.0, 4.25, 5.0],
})


def test_rate_as_of_rolls_back_to_the_most_recent_change_for_that_bank() -> None:
    assert hysa_rates.rate_as_of(_HISTORY, "ally-bank", date(2024, 3, 15)) == pytest.approx(4.0)
    assert hysa_rates.rate_as_of(_HISTORY, "ally-bank", date(2024, 7, 1)) == pytest.approx(4.25)
    assert hysa_rates.rate_as_of(_HISTORY, "ally-bank", date(2023, 12, 1)) is None
    assert hysa_rates.rate_as_of(_HISTORY, "unknown-bank", date(2024, 6, 1)) is None


def test_list_banks_returns_unique_bank_id_and_name_pairs() -> None:
    banks = hysa_rates.list_banks(_HISTORY)
    assert banks.sort("bank_id")["bank_id"].to_list() == ["ally-bank", "marcus"]


def test_update_hysa_rates_cache_backs_up_the_written_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        hysa_rates,
        "fetch_hysa_rates",
        lambda config, session=None: pl.DataFrame({
            "bank_id": ["ally-bank"],
            "bank_name": ["Ally Bank"],
            "rate_date": [date(2024, 1, 1)],
            "apy_pct": [4.0],
        }),
    )
    config = _config(tmp_path)
    backup_calls = []
    monkeypatch.setattr(
        hysa_rates, "backup_cache_file", lambda local_path, backup_key: backup_calls.append((local_path, backup_key))
    )

    hysa_rates.update_hysa_rates_cache(config)

    assert backup_calls == [(tmp_path / "rates.csv", "hysa_rates.csv")]


def test_load_hysa_rates_cache_recovers_from_corruption_when_a_backup_exists(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "backup-root")
    config = _config(tmp_path)
    path = tmp_path / "rates.csv"

    good = pl.DataFrame({
        "bank_id": ["ally-bank"],
        "bank_name": ["Ally Bank"],
        "rate_date": [date(2024, 1, 1)],
        "apy_pct": [4.0],
    })
    good.write_csv(path)
    cache_backup.backup_cache_file(path, "hysa_rates.csv")

    path.write_text('bank_id,bank_name,rate_date,apy_pct\n"unterminated\n')

    result = hysa_rates.load_hysa_rates_cache(config)

    assert result["bank_id"].to_list() == ["ally-bank"]


def test_load_hysa_rates_cache_raises_when_corrupted_with_no_backup_available(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "backup-root")
    config = _config(tmp_path)
    path = tmp_path / "rates.csv"
    path.write_text('bank_id,bank_name,rate_date,apy_pct\n"unterminated\n')

    with pytest.raises(pl.exceptions.ComputeError):
        hysa_rates.load_hysa_rates_cache(config)
