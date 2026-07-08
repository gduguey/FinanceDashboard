from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

import polars as pl
import pytest

from trades.brokers.ibkr import api, main
from trades.brokers.ibkr import models as ibkr_models
from trades.config import AppConfig, IbkrFlexCredentials

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

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
    netCash="-1364.62" levelOfDetail="EXECUTION"/>
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
    netCash="-601.00" levelOfDetail="EXECUTION"/>
</Trades>
</FlexStatement>
</FlexStatements>
</FlexQueryResponse>"""

CREDENTIALS = IbkrFlexCredentials(token="test-token", query_id="12345", _env_file=None)  # noqa: S106


def _config(tmp_path) -> AppConfig:
    return AppConfig(ibkr={"cache_dir": tmp_path})


def _ledger_row(event_id: str, event_datetime: str, event_type: str = "BUY") -> dict:
    return {
        "event_id": event_id,
        "event_datetime": datetime.fromisoformat(event_datetime),
        "symbol": "VOO",
        "event_type": event_type,
        "shares": 1.0,
        "price": 600.0,
        "amount": 600.0,
        "currency": "USD",
        "meta": {},
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-07-01 06:00:00 EDT", "2026-07-01T10:00:00"),  # summer: UTC-4
        ("2026-01-15 06:00:00 EST", "2026-01-15T11:00:00"),  # winter: UTC-5
    ],
)
def test_parse_ibkr_datetime_converts_to_utc(raw, expected) -> None:
    assert ibkr_models.parse_ibkr_datetime(raw).isoformat() == expected


def test_parse_ibkr_datetime_passes_through_non_strings() -> None:
    value = datetime(2026, 7, 1, 6, 0, 0)
    assert ibkr_models.parse_ibkr_datetime(value) is value


def test_parse_ibkr_datetime_rejects_unrecognized_abbreviation() -> None:
    with pytest.raises(ValueError, match="Unrecognized IBKR timezone abbreviation"):
        ibkr_models.parse_ibkr_datetime("2026-07-01 06:00:00 GMT")


def test_parse_statement_extracts_trades() -> None:
    statement = api.parse_statement(FIXTURE_XML)
    assert statement.from_date == date(2026, 6, 30)
    assert statement.to_date == date(2026, 6, 30)
    # 06:00 EDT (UTC-4) -> 10:00 UTC.
    assert statement.when_generated.isoformat() == "2026-07-01T10:00:00"
    assert len(statement.trades) == 1

    trade = statement.trades[0]
    assert trade.transaction_id == "9001"
    assert trade.symbol == "VOO"
    assert trade.buy_sell == "BUY"
    assert trade.net_cash == pytest.approx(-1364.62)
    # 09:48:03 EDT (UTC-4) -> 13:48:03 UTC; already UTC by the time it leaves this model.
    assert trade.date_time.isoformat() == "2026-06-30T13:48:03"
    assert trade.notes == "P"


def test_merge_ledger_dedupes_by_event_id() -> None:
    existing = pl.DataFrame([_ledger_row("ibkr:9001", "2026-06-30 09:48:03")])
    new = pl.DataFrame([
        _ledger_row("ibkr:9001", "2026-06-30 09:48:03"),
        _ledger_row("ibkr:9002", "2026-07-01 09:48:03"),
    ])
    merged = main._merge_ledger(existing, new)
    assert len(merged) == 2
    assert set(merged["event_id"]) == {"ibkr:9001", "ibkr:9002"}


def test_sync_ibkr_account_writes_ledger_and_returns_result(
    tmp_path, monkeypatch, db_session: Session, test_user_id: uuid.UUID
) -> None:
    monkeypatch.setattr(main, "fetch_flex_statement", lambda credentials, config, on_progress=None: FIXTURE_XML)
    config = _config(tmp_path)

    result = main.sync_ibkr_account(CREDENTIALS, config, db_session, user_id=test_user_id)

    assert result.statement_from_date == date(2026, 6, 30)
    assert result.new_event_count == 2  # one BUY + its FEE (ibCommission=-1.00)
    assert result.total_event_count == 2

    ledger = main.load_ledger(db_session, user_id=test_user_id)
    assert len(ledger) == 2
    assert set(ledger["event_type"]) == {"BUY", "FEE"}
    assert ledger.filter(pl.col("event_type") == "BUY")["symbol"][0] == "VOO"


def test_sync_ibkr_account_reports_progress_through_each_stage(
    tmp_path, monkeypatch, db_session: Session, test_user_id: uuid.UUID
) -> None:
    def fake_fetch(credentials, config, on_progress=None):
        if on_progress:
            on_progress("Requesting IBKR statement", 5.0)
        return FIXTURE_XML

    monkeypatch.setattr(main, "fetch_flex_statement", fake_fetch)
    config = _config(tmp_path)
    steps = []

    main.sync_ibkr_account(
        CREDENTIALS, config, db_session, user_id=test_user_id, on_progress=lambda step, pct: steps.append((step, pct))
    )

    assert steps[0] == ("Requesting IBKR statement", 5.0)
    assert any(step == "Parsing statement" for step, _ in steps)
    assert any(step == "Merging into ledger" for step, _ in steps)


def test_sync_ibkr_account_is_idempotent_same_day(
    tmp_path, monkeypatch, db_session: Session, test_user_id: uuid.UUID
) -> None:
    monkeypatch.setattr(main, "fetch_flex_statement", lambda credentials, config, on_progress=None: FIXTURE_XML)
    config = _config(tmp_path)

    main.sync_ibkr_account(CREDENTIALS, config, db_session, user_id=test_user_id)
    second = main.sync_ibkr_account(CREDENTIALS, config, db_session, user_id=test_user_id)

    assert second.new_event_count == 0
    assert second.total_event_count == 2


def test_sync_ibkr_account_raises_on_uncovered_gap(
    tmp_path, monkeypatch, db_session: Session, test_user_id: uuid.UUID
) -> None:
    config = _config(tmp_path)
    existing_ledger = pl.DataFrame([_ledger_row("ibkr:8000", "2026-06-24 09:30:00")])
    main._write_ledger(existing_ledger, db_session, user_id=test_user_id)
    monkeypatch.setattr(main, "fetch_flex_statement", lambda credentials, config, on_progress=None: FIXTURE_XML)

    with pytest.raises(main.TradeHistoryGapError):
        main.sync_ibkr_account(CREDENTIALS, config, db_session, user_id=test_user_id)


def test_sync_ibkr_account_archives_raw_statement_before_parsing(
    tmp_path, monkeypatch, db_session: Session, test_user_id: uuid.UUID
) -> None:
    monkeypatch.setattr(main, "fetch_flex_statement", lambda credentials, config, on_progress=None: FIXTURE_XML)
    config = _config(tmp_path)

    main.sync_ibkr_account(CREDENTIALS, config, db_session, user_id=test_user_id)

    archived = list(config.ibkr.raw_statement_dir.glob("*.xml"))
    assert len(archived) == 1
    assert archived[0].read_text(encoding="utf-8") == FIXTURE_XML


def test_sync_ibkr_account_archives_every_call_without_overwriting(
    tmp_path, monkeypatch, db_session: Session, test_user_id: uuid.UUID
) -> None:
    monkeypatch.setattr(main, "fetch_flex_statement", lambda credentials, config, on_progress=None: FIXTURE_XML)
    config = _config(tmp_path)

    main.sync_ibkr_account(CREDENTIALS, config, db_session, user_id=test_user_id)
    main.sync_ibkr_account(CREDENTIALS, config, db_session, user_id=test_user_id)

    archived = list(config.ibkr.raw_statement_dir.glob("*.xml"))
    assert len(archived) == 2
    assert all(path.read_text(encoding="utf-8") == FIXTURE_XML for path in archived)


def test_last_synced_at_returns_none_without_any_archive(tmp_path) -> None:
    assert api.last_synced_at(_config(tmp_path)) is None


def test_last_synced_at_reads_the_latest_raw_statement_filename(tmp_path) -> None:
    config = _config(tmp_path)
    raw_dir = config.ibkr.raw_statement_dir
    raw_dir.mkdir(parents=True)
    (raw_dir / "20260101T060000.xml").write_text(FIXTURE_XML, encoding="utf-8")
    (raw_dir / "20260701T190908.xml").write_text(FIXTURE_XML, encoding="utf-8")
    # "-1" suffix is the same-second collision tag `save_raw_statement` appends
    (raw_dir / "20260701T190908-1.xml").write_text(FIXTURE_XML, encoding="utf-8")

    assert api.last_synced_at(config) == datetime(2026, 7, 1, 19, 9, 8)


def test_rebuild_from_raw_statements_raises_without_archive(
    tmp_path, db_session: Session, test_user_id: uuid.UUID
) -> None:
    config = _config(tmp_path)
    with pytest.raises(FileNotFoundError):
        main.rebuild_from_raw_statements(config, db_session, user_id=test_user_id)


def test_rebuild_from_raw_statements_recomputes_ledger(
    tmp_path, db_session: Session, test_user_id: uuid.UUID
) -> None:
    config = _config(tmp_path)
    raw_dir = config.ibkr.raw_statement_dir
    raw_dir.mkdir(parents=True)
    (raw_dir / "20260625T060000.xml").write_text(EARLIER_FIXTURE_XML, encoding="utf-8")
    (raw_dir / "20260701T060000.xml").write_text(FIXTURE_XML, encoding="utf-8")

    result = main.rebuild_from_raw_statements(config, db_session, user_id=test_user_id)

    assert result.statement_from_date == date(2026, 6, 24)
    assert result.statement_to_date == date(2026, 6, 30)
    assert result.total_event_count == 4  # 2 trades x (BUY + FEE) each

    ledger = main.load_ledger(db_session, user_id=test_user_id)
    assert set(ledger["event_id"]) == {
        "ibkr:8000",
        "ibkr:8000:fee",
        "ibkr:9001",
        "ibkr:9001:fee",
    }


def test_rebuild_from_raw_statements_recovers_a_wrong_derived_cache(
    tmp_path, db_session: Session, test_user_id: uuid.UUID
) -> None:
    config = _config(tmp_path)
    raw_dir = config.ibkr.raw_statement_dir
    raw_dir.mkdir(parents=True)
    (raw_dir / "20260701T060000.xml").write_text(FIXTURE_XML, encoding="utf-8")

    # Simulate a wrong/stale derived cache — the exact failure mode this
    # feature exists to make recoverable — with an event no archive backs.
    main._write_ledger(pl.DataFrame([_ledger_row("bogus:1", "2020-01-01 00:00:00")]), db_session, user_id=test_user_id)

    main.rebuild_from_raw_statements(config, db_session, user_id=test_user_id)

    ledger = main.load_ledger(db_session, user_id=test_user_id)
    assert set(ledger["event_id"]) == {"ibkr:9001", "ibkr:9001:fee"}
