"""`trades.brokers.ibkr.main.load_ledger`/`_write_ledger` against real Postgres.

Mirrors `tests/test_accounting_ledger_db.py` — `LedgerEvent.polars_schema`
is the contract every other trades ledger/dashboard module already depends
on, so these tests exist to pin that the Postgres-backed boundary still
returns exactly that shape.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import polars as pl
import pytest

import db.models
import trades.db as tdb
from trades.brokers.ibkr.main import _write_ledger, load_ledger
from trades.models import LedgerEvent

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _event(
    event_id: str,
    event_type: str = "BUY",
    symbol: str = "VOO",
    shares: float | None = 1.0,
    price: float | None = 600.0,
    amount: float = 600.0,
) -> LedgerEvent:
    return LedgerEvent(
        event_id=event_id,
        event_datetime=datetime(2026, 6, 30, 9, 48, 3, tzinfo=UTC),
        symbol=symbol,
        event_type=event_type,  # type: ignore[arg-type]
        shares=shares,
        price=price,
        amount=amount,
        currency="USD",
        meta={"source": "test"},
    )


def _frame(*events: LedgerEvent) -> pl.DataFrame:
    records = [e.model_dump(mode="python") for e in events]
    return (
        pl.DataFrame(records, schema=LedgerEvent.polars_schema)
        if records
        else pl.DataFrame(schema=LedgerEvent.polars_schema)
    )


def test_load_ledger_with_no_events_yet_is_an_empty_frame_with_the_right_schema(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    ledger = load_ledger(db_session, user_id=test_user_id)
    assert ledger.is_empty()
    assert ledger.schema == LedgerEvent.polars_schema


def test_write_then_load_ledger_round_trips_an_event(db_session: Session, test_user_id: uuid.UUID) -> None:
    _write_ledger(_frame(_event("ibkr:9001", amount=1363.62)), db_session, user_id=test_user_id)

    reloaded = load_ledger(db_session, user_id=test_user_id)
    assert reloaded.height == 1
    row = reloaded.row(0, named=True)
    assert row["event_id"] == "ibkr:9001"
    assert row["symbol"] == "VOO"
    assert row["amount"] == pytest.approx(1363.62)
    assert row["meta"] == {"source": "test"}


def test_write_then_load_ledger_round_trips_a_non_trade_event_with_no_shares_or_price(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _write_ledger(
        _frame(_event("ibkr:9001:fee", event_type="FEE", shares=None, price=None, amount=1.0)),
        db_session,
        user_id=test_user_id,
    )
    row = load_ledger(db_session, user_id=test_user_id).row(0, named=True)
    assert row["event_type"] == "FEE"
    assert row["shares"] is None
    assert row["price"] is None


def test_write_ledger_is_a_full_overwrite(db_session: Session, test_user_id: uuid.UUID) -> None:
    _write_ledger(_frame(_event("ibkr:9001")), db_session, user_id=test_user_id)
    _write_ledger(_frame(_event("ibkr:9001"), _event("ibkr:9002")), db_session, user_id=test_user_id)

    reloaded = load_ledger(db_session, user_id=test_user_id)
    assert sorted(reloaded["event_id"].to_list()) == ["ibkr:9001", "ibkr:9002"]


def test_write_ledger_dropping_an_event_deletes_it(db_session: Session, test_user_id: uuid.UUID) -> None:
    _write_ledger(_frame(_event("ibkr:9001"), _event("ibkr:9002")), db_session, user_id=test_user_id)
    _write_ledger(_frame(_event("ibkr:9001")), db_session, user_id=test_user_id)

    reloaded = load_ledger(db_session, user_id=test_user_id)
    assert reloaded["event_id"].to_list() == ["ibkr:9001"]


def test_ledger_is_scoped_per_user(db_session: Session, test_user_id: uuid.UUID) -> None:
    other_user_id = uuid.uuid4()
    db_session.add(db.models.User(id=other_user_id, email=f"{other_user_id}@x.com", hashed_password="unset"))  # noqa: S106
    db_session.commit()

    _write_ledger(_frame(_event("ibkr:9001")), db_session, user_id=test_user_id)

    assert load_ledger(db_session, user_id=other_user_id).is_empty()


def test_write_ledger_reuses_the_same_default_broker_connection_across_calls(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _write_ledger(_frame(_event("ibkr:9001")), db_session, user_id=test_user_id)
    _write_ledger(_frame(_event("ibkr:9001"), _event("ibkr:9002")), db_session, user_id=test_user_id)

    connections = db_session.query(tdb.BrokerConnection).filter_by(user_id=test_user_id).all()
    assert len(connections) == 1
