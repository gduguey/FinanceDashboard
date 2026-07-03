from datetime import date, datetime

import pandas as pd
import pytest

from trades.brokers.ibkr import api
from trades.config import IbkrFlexApiConfig, IbkrFlexCredentials
from trades.brokers.ibkr.models import drop_tz_suffix

FIXTURE_XML = """<FlexQueryResponse queryName="Trade History API" type="AF">
<FlexStatements count="1">
<FlexStatement
    accountId="U24174819" fromDate="2026-06-30" toDate="2026-06-30"
    period="LastBusinessDay" whenGenerated="2026-07-01 06:00:00 EDT">
<CashReport>
<CashReportCurrency
    accountId="U24174819" currency="BASE_SUMMARY" fromDate="2026-06-30"
    toDate="2026-06-30" endingCash="1234.56" endingSettledCash="1200.00"/>
</CashReport>
<OpenPositions>
<OpenPosition
    accountId="U24174819" currency="USD" assetCategory="STK" symbol="VOO"
    reportDate="2026-06-30" position="10" markPrice="687.08" positionValue="6870.80"/>
</OpenPositions>
<Trades>
<Trade
    accountId="U24174819" currency="USD" assetCategory="STK" symbol="VOO"
    buySell="BUY" tradeID="1" transactionID="9001" tradeDate="2026-06-30"
    dateTime="2026-06-30 09:48:03 EDT" notes="P"
    quantity="2" tradePrice="681.81" tradeMoney="1363.62" ibCommission="-1.00"
    netCash="-1364.62"/>
</Trades>
</FlexStatement>
</FlexStatements>
</FlexQueryResponse>"""

EARLIER_FIXTURE_XML = """<FlexQueryResponse queryName="Trade History API" type="AF">
<FlexStatements count="1">
<FlexStatement
    accountId="U24174819" fromDate="2026-06-24" toDate="2026-06-24"
    period="LastBusinessDay" whenGenerated="2026-06-25 06:00:00 EDT">
<CashReport>
<CashReportCurrency
    accountId="U24174819" currency="BASE_SUMMARY" fromDate="2026-06-24"
    toDate="2026-06-24" endingCash="500.00" endingSettledCash="480.00"/>
</CashReport>
<OpenPositions>
<OpenPosition
    accountId="U24174819" currency="USD" assetCategory="STK" symbol="VOO"
    reportDate="2026-06-24" position="8" markPrice="670.00" positionValue="5360.00"/>
</OpenPositions>
<Trades>
<Trade
    accountId="U24174819" currency="USD" assetCategory="STK" symbol="VOO"
    buySell="BUY" tradeID="2" transactionID="8000" tradeDate="2026-06-24"
    dateTime="2026-06-24 09:30:00 EDT" notes="P"
    quantity="1" tradePrice="600.00" tradeMoney="600.00" ibCommission="-1.00"
    netCash="-601.00"/>
</Trades>
</FlexStatement>
</FlexStatements>
</FlexQueryResponse>"""

CREDENTIALS = IbkrFlexCredentials(token="test-token", query_id="12345", _env_file=None)


def _config(tmp_path) -> IbkrFlexApiConfig:
    return IbkrFlexApiConfig(cache_dir=tmp_path)


def _ledger_row(event_id: str, event_datetime: str, event_type: str = "BUY") -> dict:
    return {
        "event_id": event_id,
        "event_datetime": pd.Timestamp(event_datetime),
        "symbol": "VOO",
        "event_type": event_type,
        "shares": 1.0,
        "price": 600.0,
        "amount": 600.0,
        "currency": "USD",
        "meta": "{}",
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-07-01 06:00:00 EDT", "2026-07-01 06:00:00"),  # real IBKR format: always TZ-suffixed
        ("2026-07-01 06:00:00", "2026-07-01 06:00:00"),  # tolerate no suffix too
    ],
)
def test_drop_tz_suffix_ignores_timezone_abbreviation(raw, expected) -> None:
    """`parse_statement` uses `brokers.models.drop_tz_suffix` for
    `whenGenerated`, the same helper `IbkrTrade`/`IbkrCashTransaction` use
    for `dateTime`."""
    assert drop_tz_suffix(raw) == expected


def test_parse_statement_extracts_trades() -> None:
    statement = api._parse_statement(FIXTURE_XML)
    assert statement.from_date == date(2026, 6, 30)
    assert statement.to_date == date(2026, 6, 30)
    assert statement.when_generated.isoformat() == "2026-07-01T06:00:00"
    assert len(statement.trades) == 1

    trade = statement.trades[0]
    assert trade.transaction_id == "9001"
    assert trade.symbol == "VOO"
    assert trade.buy_sell == "BUY"
    assert trade.net_cash == pytest.approx(-1364.62)
    assert trade.date_time.isoformat() == "2026-06-30T09:48:03"
    assert trade.notes == "P"


@pytest.mark.parametrize(
    ("last_covered", "new_from", "expected"),
    [
        (date(2026, 6, 29), date(2026, 6, 30), False),  # back-to-back weekdays
        (date(2026, 6, 26), date(2026, 6, 29), False),  # Fri -> Mon, weekend only
        (date(2026, 6, 29), date(2026, 7, 1), True),  # Mon -> Wed skips Tuesday
        (date(2026, 6, 30), date(2026, 6, 30), False),  # same day, no gap
    ],
)
def test_has_uncovered_weekday_gap(last_covered, new_from, expected) -> None:
    assert api._has_uncovered_weekday_gap(last_covered, new_from) is expected


def test_merge_ledger_dedupes_by_event_id() -> None:
    existing = pd.DataFrame([_ledger_row("ibkr:9001", "2026-06-30 09:48:03")])
    new = pd.DataFrame(
        [
            _ledger_row("ibkr:9001", "2026-06-30 09:48:03"),
            _ledger_row("ibkr:9002", "2026-07-01 09:48:03"),
        ]
    )
    merged = api._merge_ledger(existing, new)
    assert len(merged) == 2
    assert set(merged["event_id"]) == {"ibkr:9001", "ibkr:9002"}


def test_sync_ibkr_account_writes_ledger_and_returns_result(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api, "fetch_flex_statement", lambda credentials, config: FIXTURE_XML)
    config = _config(tmp_path)

    result = api.sync_ibkr_account(CREDENTIALS, config)

    assert result.statement_from_date == date(2026, 6, 30)
    assert result.new_event_count == 2  # one BUY + its FEE (ibCommission=-1.00)
    assert result.total_event_count == 2
    assert (tmp_path / "ledger.csv").exists()
    assert not (tmp_path / "trades.csv").exists()
    assert not (tmp_path / "position_snapshots.csv").exists()
    assert not (tmp_path / "cash_snapshots.csv").exists()

    ledger = api.load_ledger(config)
    assert len(ledger) == 2
    assert set(ledger["event_type"]) == {"BUY", "FEE"}
    assert ledger[ledger["event_type"] == "BUY"].iloc[0]["symbol"] == "VOO"


def test_sync_ibkr_account_is_idempotent_same_day(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api, "fetch_flex_statement", lambda credentials, config: FIXTURE_XML)
    config = _config(tmp_path)

    api.sync_ibkr_account(CREDENTIALS, config)
    second = api.sync_ibkr_account(CREDENTIALS, config)

    assert second.new_event_count == 0
    assert second.total_event_count == 2


def test_sync_ibkr_account_raises_on_uncovered_gap(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    existing_ledger = pd.DataFrame(
        [_ledger_row("ibkr:8000", "2026-06-24 09:30:00")]  # several weekdays before the fixture
    )
    api._atomic_write_csv(config.cache_dir / "ledger.csv", existing_ledger)
    monkeypatch.setattr(api, "fetch_flex_statement", lambda credentials, config: FIXTURE_XML)

    with pytest.raises(api.TradeHistoryGapError):
        api.sync_ibkr_account(CREDENTIALS, config)


def test_sync_ibkr_account_archives_raw_statement_before_parsing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api, "fetch_flex_statement", lambda credentials, config: FIXTURE_XML)
    config = _config(tmp_path)

    api.sync_ibkr_account(CREDENTIALS, config)

    archived = list((tmp_path / "raw_statements").glob("*.xml"))
    assert len(archived) == 1
    assert archived[0].read_text(encoding="utf-8") == FIXTURE_XML


def test_sync_ibkr_account_archives_every_call_without_overwriting(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api, "fetch_flex_statement", lambda credentials, config: FIXTURE_XML)
    config = _config(tmp_path)

    api.sync_ibkr_account(CREDENTIALS, config)
    api.sync_ibkr_account(CREDENTIALS, config)

    archived = list((tmp_path / "raw_statements").glob("*.xml"))
    assert len(archived) == 2
    assert all(path.read_text(encoding="utf-8") == FIXTURE_XML for path in archived)


def test_last_synced_at_returns_none_without_any_archive(tmp_path) -> None:
    assert api.last_synced_at(_config(tmp_path)) is None


def test_last_synced_at_reads_the_latest_raw_statement_filename(tmp_path) -> None:
    config = _config(tmp_path)
    raw_dir = tmp_path / "raw_statements"
    raw_dir.mkdir()
    (raw_dir / "20260101T060000.xml").write_text(FIXTURE_XML, encoding="utf-8")
    (raw_dir / "20260701T190908.xml").write_text(FIXTURE_XML, encoding="utf-8")
    # "-1" suffix is the same-second collision tag `_save_raw_statement` appends
    (raw_dir / "20260701T190908-1.xml").write_text(FIXTURE_XML, encoding="utf-8")

    assert api.last_synced_at(config) == datetime(2026, 7, 1, 19, 9, 8)


def test_rebuild_from_raw_statements_raises_without_archive(tmp_path) -> None:
    config = _config(tmp_path)
    with pytest.raises(FileNotFoundError):
        api.rebuild_from_raw_statements(config)


def test_rebuild_from_raw_statements_recomputes_ledger(tmp_path) -> None:
    config = _config(tmp_path)
    raw_dir = config.cache_dir / "raw_statements"
    raw_dir.mkdir(parents=True)
    (raw_dir / "20260625T060000.xml").write_text(EARLIER_FIXTURE_XML, encoding="utf-8")
    (raw_dir / "20260701T060000.xml").write_text(FIXTURE_XML, encoding="utf-8")

    result = api.rebuild_from_raw_statements(config)

    assert result.statement_from_date == date(2026, 6, 24)
    assert result.statement_to_date == date(2026, 6, 30)
    assert result.total_event_count == 4  # 2 trades x (BUY + FEE) each

    ledger = api.load_ledger(config)
    assert set(ledger["event_id"]) == {
        "ibkr:8000",
        "ibkr:8000:fee",
        "ibkr:9001",
        "ibkr:9001:fee",
    }


def test_rebuild_from_raw_statements_recovers_a_corrupted_derived_cache(tmp_path) -> None:
    config = _config(tmp_path)
    raw_dir = config.cache_dir / "raw_statements"
    raw_dir.mkdir(parents=True)
    (raw_dir / "20260701T060000.xml").write_text(FIXTURE_XML, encoding="utf-8")

    # Simulate a corrupted/wrong derived cache — the exact failure mode this
    # feature exists to make recoverable.
    api._atomic_write_csv(config.cache_dir / "ledger.csv", pd.DataFrame({"garbage": [1, 2, 3]}))

    api.rebuild_from_raw_statements(config)

    ledger = api.load_ledger(config)
    assert set(ledger["event_id"]) == {"ibkr:9001", "ibkr:9001:fee"}
