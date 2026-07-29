"""`load_ledger`/`_write_ledger` and category resolution against real Postgres.

`LEDGER_FRAME_SCHEMA` is the contract every other ledger/dashboard module
(`ledger.replay`, `dashboard.income_statement`, ...) already depends on —
these tests exist to pin that the Postgres-backed boundary still returns
exactly that shape, so none of those downstream modules need to change.

The category tests here used to drive `remap_ledger_category_ids`/
`uncategorize_ledger_postings`, which rewrote `postings.category_id` in
place on every merge and delete (DB-audit D14). Both are gone. The same
observable outcomes — a merged category's postings reading as the
survivor, a deleted category's reading as uncategorized — are now
retirement on the `categories` row plus
`ledger.categorization.apply_category_redirects`, and what these assert on
top of that is the property the rewrite could never have: the stored
posting is byte-for-byte what was imported, whatever happened to the
taxonomy since.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import polars as pl
import pytest
from sqlalchemy import text

import accounting.db as adb
import db.models
from accounting.importers.ingest import _write_ledger, load_ledger
from accounting.ledger.categorization import apply_category_redirects
from accounting.ledger.frame import LEDGER_FRAME_SCHEMA
from accounting.ledger.transfers import make_transfer_link
from accounting.models import (
    Account,
    Goal,
    GoalContribution,
    ManualTransfer,
    Posting,
    PostingMerge,
    Tag,
    TransferRule,
)
from accounting.repositories.interpretation import (
    insert_transfer_links,
    load_posting_merges,
    load_transfer_links,
    load_transfer_rules,
    replace_posting_merges,
    replace_rule_exclusions,
    replace_transfer_rules,
)
from accounting.repositories.accounts import insert_manual_transfers, load_manual_transfers, replace_accounts
from accounting.repositories.planning import insert_goal, load_goal_contributions, upsert_goal_contribution
from accounting.repositories.taxonomy import (
    load_categories,
    load_category_redirects,
    replace_categories,
    replace_tags,
    retire_categories,
)
from accounting.taxonomy import seed_new_user_defaults, seeded_accounts, seeded_categories

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _posting(
    posting_id: str,
    transaction_id: str,
    account_id: str = "checking:test",
    amount: Decimal | float = 10.0,
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
    if account_id not in seeded_accounts(session, user_id):
        replace_accounts(
            session,
            user_id,
            [Account(account_id=account_id, name="Test", kind="checking", institution="x", currency="USD")],
            prune=False,
        )
        session.commit()


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


def test_a_bulk_ledger_rewrite_still_satisfies_the_deferred_zero_sum_guard(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """The reason the zero-sum trigger is `DEFERRABLE INITIALLY DEFERRED` rather than checked per statement.

    `_write_ledger` upserts leg by leg and prunes what it no longer
    produces in a separate pass, so it passes through several states where
    a transaction has one leg, or none. An immediate check would abort on
    the first of them. `SET CONSTRAINTS ALL IMMEDIATE` runs what `COMMIT`
    would have run, once, over the finished state — see
    `tests/db/test_schema_invariants` on why a test has to ask for it.
    """
    _register_account(db_session, test_user_id)
    _register_account(db_session, test_user_id, account_id="payee:test")
    _write_ledger(
        _frame(_posting("p1", "t1", amount=-100.0), _posting("p2", "t1", account_id="payee:test", amount=100.0)),
        db_session,
        user_id=test_user_id,
    )
    _write_ledger(
        _frame(_posting("p3", "t2", amount=-250.0), _posting("p4", "t2", account_id="payee:test", amount=250.0)),
        db_session,
        user_id=test_user_id,
    )

    db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

    assert sorted(load_ledger(db_session, user_id=test_user_id)["posting_id"].to_list()) == ["p3", "p4"]


def test_write_ledger_adds_no_precision_loss_of_its_own_beyond_the_float_projection(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """A magnitude a double cannot hold must lose precision exactly once, not twice.

    The round-trip test above uses `42.5` and `pytest.approx`, which a
    double holds exactly — so it cannot see this. `_write_ledger` reads
    `amount` off a `LEDGER_FRAME_SCHEMA` frame, where it is `Float64`, and
    handed that Python `float` straight to a `NUMERIC(18, 4)` column;
    Postgres then ran a `float8 -> numeric` cast that truncates at 15
    significant digits, stacking a second loss on the projection's own.
    `12345678901234.5678` landed as `12345678901234.6000`.

    That matters more here than in `trades`, because these amounts start
    out genuinely exact — Chase, SoFi, and canonical CSV all parse to
    `Money` — so the cast was destroying precision that really existed.
    The projection's `Decimal -> float` rounding is the single loss this
    repo accepts (`accounting.ledger.frame`, "Exact again on the way
    out"); the write boundary must contribute none, so the expected values
    are the float's own shortest round-trip literal at the storage scale.
    No `approx`, deliberately: `approx` is what hid the bug.
    """
    _register_account(db_session, test_user_id)
    _register_account(db_session, test_user_id, account_id="payee:test")
    _write_ledger(
        _frame(
            _posting("p1", "t1", amount=Decimal("12345678901234.5678")),
            _posting("p2", "t1", account_id="payee:test", amount=Decimal("-12345678901234.5678")),
        ),
        db_session,
        user_id=test_user_id,
    )

    amounts = {
        posting.natural_key: posting.amount
        for posting in db_session.query(adb.Posting).filter_by(user_id=test_user_id).all()
    }
    assert amounts == {
        "p1": Decimal("12345678901234.5680"),
        "p2": Decimal("-12345678901234.5680"),
    }


def test_write_then_load_ledger_round_trips_tag_ids(db_session: Session, test_user_id: uuid.UUID) -> None:
    _register_account(db_session, test_user_id)
    replace_tags(db_session, test_user_id, [Tag(tag_id="trip", name="Trip")], prune=False)
    db_session.commit()

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


def _resolved(session: Session, user_id: uuid.UUID) -> pl.DataFrame:
    """The raw ledger with the taxonomy's own redirects applied, exactly as `api.dependencies` does.

    Returns
    -------
    polars.DataFrame
    """
    return apply_category_redirects(load_ledger(session, user_id=user_id), load_category_redirects(session, user_id))


def test_nothing_retired_leaves_every_category_resolving_to_itself(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _register_account(db_session, test_user_id)
    seed_new_user_defaults(db_session, test_user_id)  # seeds "expense:food-drink" for the posting to reference
    _write_ledger(_frame(_posting("p1", "t1", category_id="expense:food-drink")), db_session, user_id=test_user_id)

    assert load_category_redirects(db_session, test_user_id) == {}
    assert _resolved(db_session, test_user_id).row(0, named=True)["category_id"] == "expense:food-drink"


def test_a_merged_category_resolves_to_its_successor_without_the_posting_changing(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _register_account(db_session, test_user_id)
    # All four already exist among the seeded defaults — a real merge (see
    # `taxonomy.plan_category_rename`) always retires onto another real category.
    seed_new_user_defaults(db_session, test_user_id)
    _write_ledger(
        _frame(_posting("p1", "t1", category_id="expense:food-drink", subcategory_id="expense:food-drink:groceries")),
        db_session,
        user_id=test_user_id,
    )
    retire_categories(
        db_session,
        test_user_id,
        {"expense:food-drink": "expense:transport", "expense:food-drink:groceries": "expense:transport:gas"},
    )
    db_session.commit()

    stored = load_ledger(db_session, user_id=test_user_id).row(0, named=True)
    assert stored["category_id"] == "expense:food-drink"
    assert stored["subcategory_id"] == "expense:food-drink:groceries"

    row = _resolved(db_session, test_user_id).row(0, named=True)
    assert row["category_id"] == "expense:transport"
    assert row["subcategory_id"] == "expense:transport:gas"


def test_a_deleted_category_resolves_to_uncategorized(db_session: Session, test_user_id: uuid.UUID) -> None:
    _register_account(db_session, test_user_id)
    seed_new_user_defaults(db_session, test_user_id)
    _write_ledger(
        _frame(_posting("p1", "t1", category_id="expense:food-drink", subcategory_id="expense:food-drink:groceries")),
        db_session,
        user_id=test_user_id,
    )
    # A real caller passes the already-cascaded set from
    # `taxonomy.category_ids_to_delete` — deleting the parent includes its
    # subcategories, and a delete retires with no successor at all.
    retire_categories(db_session, test_user_id, dict.fromkeys(["expense:food-drink", "expense:food-drink:groceries"]))
    db_session.commit()

    row = _resolved(db_session, test_user_id).row(0, named=True)
    assert row["category_id"] is None
    assert row["subcategory_id"] is None


def test_deleting_only_the_subcategory_leaves_the_parent_category_resolving(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _register_account(db_session, test_user_id)
    seed_new_user_defaults(db_session, test_user_id)
    _write_ledger(
        _frame(_posting("p1", "t1", category_id="expense:food-drink", subcategory_id="expense:food-drink:groceries")),
        db_session,
        user_id=test_user_id,
    )
    retire_categories(db_session, test_user_id, {"expense:food-drink:groceries": None})
    db_session.commit()

    row = _resolved(db_session, test_user_id).row(0, named=True)
    assert row["category_id"] == "expense:food-drink"
    assert row["subcategory_id"] is None


def test_retirement_leaves_postings_under_other_categories_untouched(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _register_account(db_session, test_user_id)
    seed_new_user_defaults(db_session, test_user_id)
    _write_ledger(
        _frame(
            _posting("p1", "t1", category_id="expense:food-drink"),
            _posting("p2", "t2", category_id="expense:transport"),
        ),
        db_session,
        user_id=test_user_id,
    )
    retire_categories(db_session, test_user_id, {"expense:food-drink": None})
    db_session.commit()

    rows = {row["posting_id"]: row["category_id"] for row in _resolved(db_session, test_user_id).to_dicts()}
    assert rows["p1"] is None
    assert rows["p2"] == "expense:transport"


def test_a_retired_category_leaves_the_live_tree_but_keeps_its_row(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """What makes a delete structurally safe: the row a posting foreign-keys into never goes away."""
    _register_account(db_session, test_user_id)
    seed_new_user_defaults(db_session, test_user_id)
    _write_ledger(_frame(_posting("p1", "t1", category_id="expense:food-drink")), db_session, user_id=test_user_id)
    retire_categories(db_session, test_user_id, {"expense:food-drink": None})
    db_session.commit()

    assert "expense:food-drink" not in load_categories(db_session, test_user_id)
    assert load_category_redirects(db_session, test_user_id)["expense:food-drink"] is None
    # Still stored, still valid — the posting's own foreign key never dangled.
    assert load_ledger(db_session, user_id=test_user_id).row(0, named=True)["category_id"] == "expense:food-drink"


def test_retiring_a_successor_collapses_the_earlier_merge_onto_the_new_one(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """A tombstone never names another tombstone, so resolution stays one hop."""
    seed_new_user_defaults(db_session, test_user_id)
    retire_categories(db_session, test_user_id, {"expense:food-drink": "expense:transport"})
    retire_categories(db_session, test_user_id, {"expense:transport": "expense:shopping"})
    db_session.commit()

    redirects = load_category_redirects(db_session, test_user_id)
    assert redirects["expense:food-drink"] == "expense:shopping"
    assert redirects["expense:transport"] == "expense:shopping"


def test_collapsing_a_chain_inside_one_mapping_does_not_depend_on_iteration_order(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """The one-hop guarantee must hold however the mapping happens to be ordered.

    Repointing inbound tombstones only fixes rows that *already* point at
    the row being retired. So when a successor is itself retired later in
    the same mapping, the earlier pass left a tombstone naming a tombstone
    — and `load_category_redirects` then resolved onto a retired category
    that appears in no live tree. Which of the two orderings broke was
    decided purely by `dict` insertion order.
    """
    seed_new_user_defaults(db_session, test_user_id)
    # "transport" is retired outright, and "food-drink" merges into it in the
    # same call — so food-drink must end up resolving to nothing, not to a
    # tombstone.
    retire_categories(db_session, test_user_id, {"expense:transport": None, "expense:food-drink": "expense:transport"})
    db_session.commit()

    redirects = load_category_redirects(db_session, test_user_id)
    assert redirects["expense:transport"] is None
    assert redirects["expense:food-drink"] is None


def test_deleting_a_categorys_successor_turns_the_earlier_merge_into_a_delete(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    seed_new_user_defaults(db_session, test_user_id)
    retire_categories(db_session, test_user_id, {"expense:food-drink": "expense:transport"})
    retire_categories(db_session, test_user_id, {"expense:transport": None})
    db_session.commit()

    assert load_category_redirects(db_session, test_user_id)["expense:food-drink"] is None


def test_writing_a_retired_category_again_brings_it_back(db_session: Session, test_user_id: uuid.UUID) -> None:
    categories = seeded_categories(db_session, test_user_id)
    retire_categories(db_session, test_user_id, {"expense:food-drink": None})
    db_session.commit()

    replace_categories(db_session, test_user_id, [categories["expense:food-drink"]], prune=False)
    db_session.commit()

    assert "expense:food-drink" in load_categories(db_session, test_user_id)
    assert load_category_redirects(db_session, test_user_id) == {}


def test_write_ledger_never_prunes_a_manual_transaction(db_session: Session, test_user_id: uuid.UUID) -> None:
    """The whole point of `transactions.origin`: a rebuild owns the imported half and only that half."""
    _register_account(db_session, test_user_id)
    _register_account(db_session, test_user_id, account_id="savings:test")
    _write_ledger(_frame(_posting("p1", "t1")), db_session, user_id=test_user_id)
    insert_manual_transfers(
        [
            ManualTransfer(
                transfer_id="closing",
                date=datetime(2026, 2, 1, tzinfo=UTC),
                from_account_id="checking:test",
                to_account_id="savings:test",
                from_amount=250,
                to_amount=250,
                description="Closing balance",
            )
        ],
        db_session,
        test_user_id,
    )
    db_session.commit()

    # A rebuild replays the archives and produces only the imported half —
    # here, nothing at all. The manual transfer must survive it untouched.
    _write_ledger(_frame(), db_session, user_id=test_user_id)

    assert [transfer.transfer_id for transfer in load_manual_transfers(db_session, test_user_id)] == ["closing"]
    posting_ids = load_ledger(db_session, user_id=test_user_id)["posting_id"].to_list()
    assert sorted(posting_ids) == ["manual-transfer:closing:from", "manual-transfer:closing:to"]


def test_a_transactions_date_and_description_are_stored_once_and_projected_onto_every_leg(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """The behavioural half of `tests/db/test_schema_invariants`'s structural claim.

    `posted_at`/`description` live on `transactions` and the frame carries
    one of each per leg, so this is what proves the join puts the *same* one
    on both: exactly one row in `transactions` holds them, and the two legs
    of that transaction come back agreeing, because they are reading the
    same value rather than two copies that happen to match.
    """
    _register_account(db_session, test_user_id)
    _register_account(db_session, test_user_id, account_id="savings:test")
    _write_ledger(
        _frame(
            _posting("t1:0", "t1", amount=-40.0),
            _posting("t1:1", "t1", account_id="savings:test", amount=40.0),
        ),
        db_session,
        user_id=test_user_id,
    )

    stored = db_session.query(adb.Transaction).filter_by(user_id=test_user_id).all()
    assert [(row.natural_key, row.posted_at, row.description) for row in stored] == [
        ("t1", datetime(2026, 1, 1), "test")
    ]

    ledger = load_ledger(db_session, user_id=test_user_id)
    assert ledger.height == 2
    assert ledger["posted_at"].unique().to_list() == [datetime(2026, 1, 1)]
    assert ledger["description"].unique().to_list() == ["test"]


def test_re_recording_a_manual_transfer_updates_its_one_date_and_description(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """The upsert that used to land on the postings has to land on the transaction now, or an edit is silently lost."""
    _register_account(db_session, test_user_id)
    _register_account(db_session, test_user_id, account_id="savings:test")

    def _record(date: datetime, description: str) -> None:
        insert_manual_transfers(
            [
                ManualTransfer(
                    transfer_id="closing",
                    date=date,
                    from_account_id="checking:test",
                    to_account_id="savings:test",
                    from_amount=250,
                    to_amount=250,
                    description=description,
                )
            ],
            db_session,
            test_user_id,
        )
        db_session.commit()

    _record(datetime(2026, 2, 1, tzinfo=UTC), "Closing balance")
    _record(datetime(2026, 3, 15, tzinfo=UTC), "Corrected closing balance")

    transfer = load_manual_transfers(db_session, test_user_id)[0]
    assert transfer.date == datetime(2026, 3, 15)
    assert transfer.description == "Corrected closing balance"
    ledger = load_ledger(db_session, user_id=test_user_id, origin="manual")
    assert ledger["description"].unique().to_list() == ["Corrected closing balance"]


def test_load_ledger_restricted_to_imported_hides_the_manual_half(db_session: Session, test_user_id: uuid.UUID) -> None:
    _register_account(db_session, test_user_id)
    _register_account(db_session, test_user_id, account_id="savings:test")
    _write_ledger(_frame(_posting("p1", "t1")), db_session, user_id=test_user_id)
    insert_manual_transfers(
        [
            ManualTransfer(
                transfer_id="closing",
                date=datetime(2026, 2, 1, tzinfo=UTC),
                from_account_id="checking:test",
                to_account_id="savings:test",
                from_amount=250,
                to_amount=250,
            )
        ],
        db_session,
        test_user_id,
    )
    db_session.commit()

    assert len(load_ledger(db_session, user_id=test_user_id)) == 3
    assert load_ledger(db_session, user_id=test_user_id, origin="imported")["posting_id"].to_list() == ["p1"]
    assert len(load_ledger(db_session, user_id=test_user_id, origin="manual")) == 2


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

    assert load_transfer_links(db_session, test_user_id) == []


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

    assert load_posting_merges(db_session, test_user_id) == {}


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

    reloaded = load_posting_merges(db_session, test_user_id)["m1"]
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

    reloaded = load_transfer_rules(db_session, test_user_id)[0]
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
