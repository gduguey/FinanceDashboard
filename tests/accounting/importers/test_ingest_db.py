"""`load_ledger`/`_write_ledger`/`remap_ledger_category_ids` against real Postgres.

`LEDGER_FRAME_SCHEMA` is the contract every other ledger/dashboard module
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
from accounting.importers.ingest import (
    _write_ledger,
    load_ledger,
    remap_ledger_category_ids,
    uncategorize_ledger_postings,
)
from accounting.ledger.frame import LEDGER_FRAME_SCHEMA
from accounting.ledger.transfers import make_transfer_link
from accounting.models import Account, Goal, GoalContribution, Posting, PostingMerge, Tag, TransferRule
from accounting.repositories.interpretation import (
    insert_transfer_links,
    replace_posting_merges,
    replace_rule_exclusions,
    replace_transfer_rules,
)
from accounting.repositories.planning import insert_goal, load_goal_contributions, upsert_goal_contribution
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
                    account_id: Account(
                        account_id=account_id, name="Test", kind="checking", institution="x", currency="USD"
                    ),
                }
            }
        )
        save_store(store, session, user_id=user_id)


def _frame(*postings: Posting) -> pl.DataFrame:
    records = [p.model_dump(mode="python") for p in postings]
    return pl.DataFrame(records, schema=LEDGER_FRAME_SCHEMA) if records else pl.DataFrame(schema=LEDGER_FRAME_SCHEMA)


def test_load_ledger_with_no_postings_yet_is_an_empty_frame_with_the_right_schema(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    ledger = load_ledger(db_session, user_id=test_user_id)
    assert ledger.is_empty()
    assert ledger.schema == LEDGER_FRAME_SCHEMA


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


def test_write_ledger_is_a_full_overwrite_like_the_json_era_one_was(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
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
    db_session.add(db.models.User(id=other_user_id, email=f"{other_user_id}@x.com"))
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


def test_remap_ledger_category_ids_updates_category_and_subcategory(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
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


def test_uncategorize_ledger_postings_is_a_noop_for_an_empty_set(db_session: Session, test_user_id: uuid.UUID) -> None:
    _register_account(db_session, test_user_id)
    load_store(db_session, user_id=test_user_id)
    _write_ledger(_frame(_posting("p1", "t1", category_id="expense:food-drink")), db_session, user_id=test_user_id)
    uncategorize_ledger_postings(set(), db_session, user_id=test_user_id)
    assert load_ledger(db_session, user_id=test_user_id).row(0, named=True)["category_id"] == "expense:food-drink"


def test_uncategorize_ledger_postings_clears_category_and_subcategory(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _register_account(db_session, test_user_id)
    load_store(db_session, user_id=test_user_id)
    _write_ledger(
        _frame(_posting("p1", "t1", category_id="expense:food-drink", subcategory_id="expense:food-drink:groceries")),
        db_session,
        user_id=test_user_id,
    )
    # A real caller passes the already-cascaded set from
    # `store.category_ids_to_delete` — deleting the parent includes its
    # subcategories, this function just clears exact matches.
    uncategorize_ledger_postings(
        {"expense:food-drink", "expense:food-drink:groceries"}, db_session, user_id=test_user_id
    )

    row = load_ledger(db_session, user_id=test_user_id).row(0, named=True)
    assert row["category_id"] is None
    assert row["subcategory_id"] is None


def test_uncategorize_ledger_postings_only_clears_the_subcategory_when_thats_all_thats_deleted(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _register_account(db_session, test_user_id)
    load_store(db_session, user_id=test_user_id)
    _write_ledger(
        _frame(_posting("p1", "t1", category_id="expense:food-drink", subcategory_id="expense:food-drink:groceries")),
        db_session,
        user_id=test_user_id,
    )
    uncategorize_ledger_postings({"expense:food-drink:groceries"}, db_session, user_id=test_user_id)

    row = load_ledger(db_session, user_id=test_user_id).row(0, named=True)
    assert row["category_id"] == "expense:food-drink"
    assert row["subcategory_id"] is None


def test_uncategorize_ledger_postings_leaves_unrelated_postings_untouched(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _register_account(db_session, test_user_id)
    load_store(db_session, user_id=test_user_id)
    _write_ledger(
        _frame(
            _posting("p1", "t1", category_id="expense:food-drink"),
            _posting("p2", "t2", category_id="expense:transport"),
        ),
        db_session,
        user_id=test_user_id,
    )
    uncategorize_ledger_postings({"expense:food-drink"}, db_session, user_id=test_user_id)

    rows = {row["posting_id"]: row["category_id"] for row in load_ledger(db_session, user_id=test_user_id).to_dicts()}
    assert rows["p1"] is None
    assert rows["p2"] == "expense:transport"


def test_write_ledger_dropping_a_transaction_referenced_by_a_transfer_link_deletes_the_whole_link(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _register_account(db_session, test_user_id)
    _write_ledger(_frame(_posting("p1", "t1"), _posting("p2", "t2")), db_session, user_id=test_user_id)
    insert_transfer_links(db_session, test_user_id, [make_transfer_link("t1", "t2")])
    db_session.commit()

    # Dropping t1 (as a real rebuild would if its raw statement disappeared)
    # must not raise a foreign-key error — and must take t2's membership in
    # the same link with it rather than leaving a link with only one side.
    _write_ledger(_frame(_posting("p2", "t2")), db_session, user_id=test_user_id)

    assert load_store(db_session, user_id=test_user_id).transfer_links == []


def test_write_ledger_dropping_a_transaction_kept_by_a_posting_merge_deletes_the_whole_merge(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _register_account(db_session, test_user_id)
    _write_ledger(_frame(_posting("p1", "t1"), _posting("p2", "t2")), db_session, user_id=test_user_id)
    merge = PostingMerge(merge_id="m1", kept_transaction_id="t1", duplicate_transaction_ids=["t2"])
    replace_posting_merges(db_session, test_user_id, [merge])
    db_session.commit()

    # Dropping t1, the merge's own kept_transaction_id, must not raise — and
    # must remove the merge entirely (its duplicate's own reference, cascading
    # off merge_id, would otherwise point at a merge decision that no longer
    # names a surviving transaction).
    _write_ledger(_frame(_posting("p2", "t2")), db_session, user_id=test_user_id)

    assert load_store(db_session, user_id=test_user_id).posting_merges == {}


def test_write_ledger_dropping_a_duplicate_transaction_removes_just_that_one_from_its_merge(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _register_account(db_session, test_user_id)
    _write_ledger(
        _frame(_posting("p1", "t1"), _posting("p2", "t2"), _posting("p3", "t3")), db_session, user_id=test_user_id
    )
    merge = PostingMerge(merge_id="m1", kept_transaction_id="t1", duplicate_transaction_ids=["t2", "t3"])
    replace_posting_merges(db_session, test_user_id, [merge])
    db_session.commit()

    # Dropping just one duplicate (t2) must not raise, and the merge itself
    # (kept_transaction_id=t1, still-real duplicate t3) survives untouched.
    _write_ledger(_frame(_posting("p1", "t1"), _posting("p3", "t3")), db_session, user_id=test_user_id)

    reloaded = load_store(db_session, user_id=test_user_id).posting_merges["m1"]
    assert reloaded.kept_transaction_id == "t1"
    assert reloaded.duplicate_transaction_ids == ["t3"]


def test_write_ledger_dropping_a_transaction_referenced_by_a_transfer_rule_exclusion_removes_just_that_exclusion(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _register_account(db_session, test_user_id)
    _write_ledger(_frame(_posting("p1", "t1")), db_session, user_id=test_user_id)
    rule = TransferRule(rule_id="r1", description_contains="x", excluded_transaction_ids=["t1"])
    replace_transfer_rules(db_session, test_user_id, [rule])
    replace_rule_exclusions(db_session, test_user_id, [rule])
    db_session.commit()

    # Dropping the excluded transaction itself must not raise — the
    # exclusion row is single-transaction, nothing else to keep in sync.
    _write_ledger(_frame(), db_session, user_id=test_user_id)

    reloaded = load_store(db_session, user_id=test_user_id).rules[0]
    assert reloaded.excluded_transaction_ids == []


def test_write_ledger_dropping_a_posting_a_goal_contribution_traces_back_to_keeps_the_contribution(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _register_account(db_session, test_user_id)
    _write_ledger(_frame(_posting("p1", "t1")), db_session, user_id=test_user_id)
    insert_goal(
        db_session,
        test_user_id,
        Goal(
            goal_id="g1",
            name="Emergency fund",
            target_amount=1000,
            target_date=datetime(2027, 1, 1, tzinfo=UTC),
            color="#abcdef",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    upsert_goal_contribution(
        GoalContribution(
            contribution_id="gc1",
            goal_id="g1",
            date=datetime(2026, 1, 5, tzinfo=UTC),
            amount=100,
            source_posting_id="p1",
        ),
        db_session,
        test_user_id,
    )

    # Dropping the posting the contribution traces back to must not raise —
    # and, unlike TransferLink/PostingMerge, must NOT delete the
    # contribution itself: its amount/date is the real financial record,
    # source_posting_id is purely a traceability link (see
    # `models.GoalContribution`'s own docstring). Only the link clears.
    _write_ledger(_frame(), db_session, user_id=test_user_id)

    reloaded = load_goal_contributions(db_session, test_user_id)["gc1"]
    assert reloaded.amount == 100
    assert reloaded.source_posting_id is None
