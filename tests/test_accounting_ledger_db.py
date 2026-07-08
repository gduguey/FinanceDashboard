"""`load_ledger`/`_write_ledger`/`remap_ledger_category_ids` against real Postgres.

`Posting.polars_schema` is the contract every other ledger/dashboard module
(`ledger.replay`, `dashboard.income_statement`, ...) already depends on —
these tests exist to pin that the Postgres-backed boundary still returns
exactly that shape, so none of those downstream modules need to change.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import polars as pl
import pytest

import db.models
from accounting.importers.ingest import _write_ledger, load_ledger, remap_ledger_category_ids
from accounting.models import Account, Posting, Tag
from accounting.store import load_store, save_store

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _posting(
    posting_id: str,
    transaction_id: str,
    account_id: str = "checking:test",
    amount: float = 10.0,
    category_id: str | None = None,
    subcategory_id: str | None = None,
    tag_ids: list[str] | None = None,
) -> Posting:
    return Posting(
        posting_id=posting_id,
        transaction_id=transaction_id,
        account_id=account_id,
        posted_at=datetime(2026, 1, 1, tzinfo=UTC),
        amount=amount,
        currency="USD",
        category_id=category_id,
        subcategory_id=subcategory_id,
        tag_ids=tag_ids or [],
        description="test",
        meta={"source": "test"},
    )


def _register_account(session: Session, user_id: uuid.UUID, account_id: str = "checking:test") -> None:
    store = load_store(session, user_id=user_id)
    if account_id not in store.accounts:
        store = store.model_copy(
            update={
                "accounts": {
                    **store.accounts,
                    account_id: Account(account_id=account_id, name="Test", kind="checking", institution="x", currency="USD"),
                }
            }
        )
        save_store(store, session, user_id=user_id)


def _frame(*postings: Posting) -> pl.DataFrame:
    records = [p.model_dump(mode="python") for p in postings]
    return pl.DataFrame(records, schema=Posting.polars_schema) if records else pl.DataFrame(schema=Posting.polars_schema)


def test_load_ledger_with_no_postings_yet_is_an_empty_frame_with_the_right_schema(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    ledger = load_ledger(db_session, user_id=test_user_id)
    assert ledger.is_empty()
    assert ledger.schema == Posting.polars_schema


def test_write_then_load_ledger_round_trips_a_posting(db_session: Session, test_user_id: uuid.UUID) -> None:
    _register_account(db_session, test_user_id)
    _write_ledger(_frame(_posting("p1", "t1", amount=42.5)), db_session, user_id=test_user_id)

    reloaded = load_ledger(db_session, user_id=test_user_id)
    assert reloaded.height == 1
    row = reloaded.row(0, named=True)
    assert row["posting_id"] == "p1"
    assert row["transaction_id"] == "t1"
    assert row["amount"] == pytest.approx(42.5)
    assert row["meta"] == {"source": "test"}


def test_write_then_load_ledger_round_trips_tag_ids(db_session: Session, test_user_id: uuid.UUID) -> None:
    _register_account(db_session, test_user_id)
    store = load_store(db_session, user_id=test_user_id)
    store = store.model_copy(update={"tags": {**store.tags, "trip": Tag(tag_id="trip", name="Trip")}})
    save_store(store, db_session, user_id=test_user_id)

    _write_ledger(_frame(_posting("p1", "t1", tag_ids=["trip"])), db_session, user_id=test_user_id)
    reloaded = load_ledger(db_session, user_id=test_user_id)
    assert reloaded.row(0, named=True)["tag_ids"] == ["trip"]


def test_write_ledger_is_a_full_overwrite_like_the_json_era_one_was(db_session: Session, test_user_id: uuid.UUID) -> None:
    _register_account(db_session, test_user_id)
    _write_ledger(_frame(_posting("p1", "t1")), db_session, user_id=test_user_id)
    _write_ledger(_frame(_posting("p1", "t1"), _posting("p2", "t2")), db_session, user_id=test_user_id)

    reloaded = load_ledger(db_session, user_id=test_user_id)
    assert sorted(reloaded["posting_id"].to_list()) == ["p1", "p2"]


def test_write_ledger_dropping_a_posting_deletes_it_when_nothing_references_it(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _register_account(db_session, test_user_id)
    _write_ledger(_frame(_posting("p1", "t1"), _posting("p2", "t2")), db_session, user_id=test_user_id)
    _write_ledger(_frame(_posting("p1", "t1")), db_session, user_id=test_user_id)

    reloaded = load_ledger(db_session, user_id=test_user_id)
    assert reloaded["posting_id"].to_list() == ["p1"]


def test_ledger_is_scoped_per_user(db_session: Session, test_user_id: uuid.UUID) -> None:
    other_user_id = uuid.uuid4()
    db_session.add(db.models.User(id=other_user_id, email=f"{other_user_id}@x.com", hashed_password="unset"))  # noqa: S106
    db_session.commit()

    _register_account(db_session, test_user_id)
    _write_ledger(_frame(_posting("p1", "t1")), db_session, user_id=test_user_id)

    assert load_ledger(db_session, user_id=other_user_id).is_empty()


def test_remap_ledger_category_ids_is_a_noop_for_an_empty_remap(db_session: Session, test_user_id: uuid.UUID) -> None:
    _register_account(db_session, test_user_id)
    load_store(db_session, user_id=test_user_id)  # seeds "expense:food-drink" for the posting to reference
    _write_ledger(_frame(_posting("p1", "t1", category_id="expense:food-drink")), db_session, user_id=test_user_id)
    remap_ledger_category_ids({}, db_session, user_id=test_user_id)
    assert load_ledger(db_session, user_id=test_user_id).row(0, named=True)["category_id"] == "expense:food-drink"


def test_remap_ledger_category_ids_updates_category_and_subcategory(db_session: Session, test_user_id: uuid.UUID) -> None:
    _register_account(db_session, test_user_id)
    # Both already exist among the seeded defaults — a real merge (see
    # `store.plan_category_rename`) always remaps onto another real category.
    load_store(db_session, user_id=test_user_id)
    _write_ledger(
        _frame(_posting("p1", "t1", category_id="expense:food-drink", subcategory_id="expense:food-drink:groceries")),
        db_session,
        user_id=test_user_id,
    )
    remap_ledger_category_ids(
        {"expense:food-drink": "expense:transport", "expense:food-drink:groceries": "expense:transport:gas"},
        db_session,
        user_id=test_user_id,
    )

    row = load_ledger(db_session, user_id=test_user_id).row(0, named=True)
    assert row["category_id"] == "expense:transport"
    assert row["subcategory_id"] == "expense:transport:gas"
