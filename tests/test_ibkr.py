from datetime import date, datetime

import pandas as pd
import pytest

from trades.brokers import ibkr
from trades.config import IbkrFlexApiConfig, IbkrFlexCredentials

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
    quantity="1" tradePrice="600.00" tradeMoney="600.00" ibCommission="-1.00"
    netCash="-601.00"/>
</Trades>
</FlexStatement>
</FlexStatements>
</FlexQueryResponse>"""

CREDENTIALS = IbkrFlexCredentials(token="test-token", query_id="12345", _env_file=None)


def _config(tmp_path) -> IbkrFlexApiConfig:
    return IbkrFlexApiConfig(cache_dir=tmp_path)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-07-01 06:00:00 EDT", "2026-07-01T06:00:00"),  # real IBKR format: always TZ-suffixed
        ("2026-07-01 06:00:00", "2026-07-01T06:00:00"),  # tolerate no suffix too
    ],
)
def test_parse_when_generated_ignores_timezone_abbreviation(raw, expected) -> None:
    assert ibkr._parse_when_generated(raw).isoformat() == expected


def test_parse_statement_extracts_all_sections() -> None:
    statement = ibkr._parse_statement(FIXTURE_XML)
    assert statement.from_date == date(2026, 6, 30)
    assert statement.to_date == date(2026, 6, 30)
    assert statement.when_generated.isoformat() == "2026-07-01T06:00:00"
    assert len(statement.trades) == 1
    assert len(statement.positions) == 1
    assert len(statement.cash_balances) == 1

    trade = statement.trades[0]
    assert trade.transaction_id == "9001"
    assert trade.symbol == "VOO"
    assert trade.buy_sell == "BUY"
    assert trade.net_cash == pytest.approx(-1364.62)

    assert statement.positions[0].position_value == pytest.approx(6870.80)
    assert statement.cash_balances[0].ending_cash == pytest.approx(1234.56)


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
    assert ibkr._has_uncovered_weekday_gap(last_covered, new_from) is expected


def test_merge_trade_history_dedupes_by_transaction_id() -> None:
    existing = pd.DataFrame(
        [{"transaction_id": "9001", "trade_date": pd.Timestamp("2026-06-30"), "symbol": "VOO"}]
    )
    new = pd.DataFrame(
        [
            {"transaction_id": "9001", "trade_date": pd.Timestamp("2026-06-30"), "symbol": "VOO"},
            {"transaction_id": "9002", "trade_date": pd.Timestamp("2026-07-01"), "symbol": "VOO"},
        ]
    )
    merged = ibkr._merge_trade_history(existing, new)
    assert len(merged) == 2
    assert set(merged["transaction_id"]) == {"9001", "9002"}


def test_sync_ibkr_account_writes_caches_and_returns_result(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ibkr, "fetch_flex_statement", lambda credentials, config: FIXTURE_XML)
    config = _config(tmp_path)

    result = ibkr.sync_ibkr_account(CREDENTIALS, config)

    assert result.statement_from_date == date(2026, 6, 30)
    assert result.new_trade_count == 1
    assert result.total_trade_count == 1
    assert (tmp_path / "trades.csv").exists()
    assert (tmp_path / "position_snapshots.csv").exists()
    assert (tmp_path / "cash_snapshots.csv").exists()

    trades = ibkr.load_trade_history(config)
    assert len(trades) == 1
    assert trades.loc[0, "symbol"] == "VOO"


def test_sync_ibkr_account_is_idempotent_same_day(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ibkr, "fetch_flex_statement", lambda credentials, config: FIXTURE_XML)
    config = _config(tmp_path)

    ibkr.sync_ibkr_account(CREDENTIALS, config)
    second = ibkr.sync_ibkr_account(CREDENTIALS, config)

    assert second.new_trade_count == 0
    assert second.total_trade_count == 1
    assert len(ibkr.load_position_snapshots(config)) == 2  # snapshots are never deduped
    assert len(ibkr.load_cash_snapshots(config)) == 2


def test_sync_ibkr_account_raises_on_uncovered_gap(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    existing_trades = pd.DataFrame(
        [
            {
                "account_id": "U24174819",
                "transaction_id": "8000",
                "trade_id": "1",
                "symbol": "VOO",
                "asset_category": "STK",
                "currency": "USD",
                "buy_sell": "BUY",
                "trade_date": pd.Timestamp("2026-06-24"),  # several weekdays before the fixture
                "quantity": 1,
                "trade_price": 600.0,
                "trade_money": 600.0,
                "ib_commission": -1.0,
                "net_cash": -601.0,
            }
        ]
    )
    ibkr._atomic_write_csv(config.cache_dir / "trades.csv", existing_trades)
    monkeypatch.setattr(ibkr, "fetch_flex_statement", lambda credentials, config: FIXTURE_XML)

    with pytest.raises(ibkr.TradeHistoryGapError):
        ibkr.sync_ibkr_account(CREDENTIALS, config)


def test_sync_ibkr_account_archives_raw_statement_before_parsing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ibkr, "fetch_flex_statement", lambda credentials, config: FIXTURE_XML)
    config = _config(tmp_path)

    ibkr.sync_ibkr_account(CREDENTIALS, config)

    archived = list((tmp_path / "raw_statements").glob("*.xml"))
    assert len(archived) == 1
    assert archived[0].read_text(encoding="utf-8") == FIXTURE_XML


def test_sync_ibkr_account_archives_every_call_without_overwriting(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ibkr, "fetch_flex_statement", lambda credentials, config: FIXTURE_XML)
    config = _config(tmp_path)

    ibkr.sync_ibkr_account(CREDENTIALS, config)
    ibkr.sync_ibkr_account(CREDENTIALS, config)

    archived = list((tmp_path / "raw_statements").glob("*.xml"))
    assert len(archived) == 2
    assert all(path.read_text(encoding="utf-8") == FIXTURE_XML for path in archived)


def test_last_synced_at_returns_none_without_any_archive(tmp_path) -> None:
    assert ibkr.last_synced_at(_config(tmp_path)) is None


def test_last_synced_at_reads_the_latest_raw_statement_filename(tmp_path) -> None:
    config = _config(tmp_path)
    raw_dir = tmp_path / "raw_statements"
    raw_dir.mkdir()
    (raw_dir / "20260101T060000.xml").write_text(FIXTURE_XML, encoding="utf-8")
    (raw_dir / "20260701T190908.xml").write_text(FIXTURE_XML, encoding="utf-8")
    # "-1" suffix is the same-second collision tag `_save_raw_statement` appends
    (raw_dir / "20260701T190908-1.xml").write_text(FIXTURE_XML, encoding="utf-8")

    assert ibkr.last_synced_at(config) == datetime(2026, 7, 1, 19, 9, 8)


def test_rebuild_from_raw_statements_raises_without_archive(tmp_path) -> None:
    config = _config(tmp_path)
    with pytest.raises(FileNotFoundError):
        ibkr.rebuild_from_raw_statements(config)


def test_rebuild_from_raw_statements_recomputes_derived_caches(tmp_path) -> None:
    config = _config(tmp_path)
    raw_dir = config.cache_dir / "raw_statements"
    raw_dir.mkdir(parents=True)
    (raw_dir / "20260625T060000.xml").write_text(EARLIER_FIXTURE_XML, encoding="utf-8")
    (raw_dir / "20260701T060000.xml").write_text(FIXTURE_XML, encoding="utf-8")

    result = ibkr.rebuild_from_raw_statements(config)

    assert result.statement_from_date == date(2026, 6, 24)
    assert result.statement_to_date == date(2026, 6, 30)
    assert result.total_trade_count == 2

    trades = ibkr.load_trade_history(config)
    assert set(trades["transaction_id"]) == {"8000", "9001"}
    assert len(ibkr.load_position_snapshots(config)) == 2
    assert len(ibkr.load_cash_snapshots(config)) == 2


def test_rebuild_from_raw_statements_recovers_a_corrupted_derived_cache(tmp_path) -> None:
    config = _config(tmp_path)
    raw_dir = config.cache_dir / "raw_statements"
    raw_dir.mkdir(parents=True)
    (raw_dir / "20260701T060000.xml").write_text(FIXTURE_XML, encoding="utf-8")

    # Simulate a corrupted/wrong derived cache — the exact failure mode this
    # feature exists to make recoverable.
    ibkr._atomic_write_csv(config.cache_dir / "trades.csv", pd.DataFrame({"garbage": [1, 2, 3]}))

    ibkr.rebuild_from_raw_statements(config)

    trades = ibkr.load_trade_history(config)
    assert list(trades["transaction_id"]) == ["9001"]
