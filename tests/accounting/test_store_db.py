"""`load_store` and the accounts/taxonomy repositories against real Postgres.

Complements `test_accounting_store.py`, which covers the pure in-memory
logic (`plan_category_rename`, `normalize_categories`, ...) that never
touches storage. Everything here exercises the actual Postgres-backed
persistence boundary — the `db_session`/`test_user_id` fixtures come from
`tests/conftest.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import IntegrityError

import accounting.db as adb
import db.models
from db.base import derive_id
from accounting.models import (
    Account,
    Budget,
    CategoryPattern,
    DismissedSuggestion,
    GeneralBudget,
    Goal,
    GoalContribution,
    ManualOverride,
    ManualTransfer,
    OpeningBalance,
    OtherAsset,
    PostingMerge,
    PostingSplit,
    PostingSplitLeg,
    RecurringAddition,
    SimulatorScenario,
    Tag,
    TransferLink,
    TransferRule,
    WithdrawalPriorityEntry,
)
from accounting.repositories.accounts import (
    replace_accounts,
    replace_manual_transfers,
    replace_opening_balances,
)
from accounting.repositories.planning import (
    insert_goal,
    replace_budgets,
    replace_general_budgets,
    replace_goal_contributions,
    replace_recurring_additions,
    replace_withdrawal_priorities,
)
from accounting.repositories.interpretation import (
    delete_posting_split,
    dismiss_suggestion,
    dismissed_suggestion_ids,
    insert_transfer_links,
    list_dismissed_suggestions,
    load_overrides,
    load_overrides_for_postings,
    replace_category_patterns,
    replace_posting_merges,
    replace_posting_splits,
    replace_rule_exclusions,
    replace_transfer_rules,
    save_overrides,
    save_overrides_for_postings,
    save_posting_split,
    undismiss_suggestion,
    upsert_posting_merge,
    upsert_transfer_rule,
)
from accounting.repositories.taxonomy import (
    remap_tag_ids,
    replace_categories,
    replace_other_assets,
    replace_simulator_scenarios,
    replace_tags,
)
from accounting.store import (
    UNCATEGORIZED_EXPENSE_ACCOUNT_ID,
    UNCATEGORIZED_INCOME_ACCOUNT_ID,
    get_store_version,
    load_store,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_posting(session: Session, user_id: uuid.UUID, transaction_id: str, posting_id: str) -> None:
    """Insert the minimal Account/Transaction/Posting rows a PostingSplit/PostingMerge/GoalContribution can FK to.

    The account is registered *through* the accounts repository, not by
    inserting an `adb.Account` row directly — in real usage, that's the only
    way an account comes to exist at all. `load_store` runs first for the
    same reason every router does: it seeds a brand-new user's default
    category tree, which the overrides and splits below FK into. Safe to
    call more than once per test (e.g. one posting per transaction) — the
    shared "checking:test" account is only registered the first time.
    """
    store = load_store(session, user_id=user_id)
    if "checking:test" not in store.accounts:
        replace_accounts(
            session,
            user_id,
            [Account(account_id="checking:test", name="Test", kind="checking", institution="x", currency="USD")],
            prune=False,
        )
        session.commit()
    transaction_uuid = derive_id(user_id, "transactions", transaction_id)
    session.add(adb.Transaction(id=transaction_uuid, user_id=user_id, natural_key=transaction_id))
    session.flush()
    session.add(
        adb.Posting(
            id=derive_id(user_id, "postings", posting_id),
            user_id=user_id,
            natural_key=posting_id,
            transaction_id=transaction_uuid,
            account_id=derive_id(user_id, "accounts", "checking:test"),
            posted_at=datetime(2026, 1, 1, tzinfo=UTC),
            amount=10,
            currency="USD",
        )
    )
    session.flush()


def _seed_tags(session: Session, user_id: uuid.UUID, *tag_ids_and_names: tuple[str, str]) -> None:
    """Persist real `Tag` rows so a `PostingTag`/override can FK or reference them by natural key."""
    replace_tags(session, user_id, [Tag(tag_id=tag_id, name=name) for tag_id, name in tag_ids_and_names], prune=False)
    session.commit()


def _seed_posting_tag(session: Session, user_id: uuid.UUID, posting_id: str, tag_id: str) -> None:
    session.add(
        adb.PostingTag(
            user_id=user_id,
            posting_id=derive_id(user_id, "postings", posting_id),
            tag_id=derive_id(user_id, "tags", tag_id),
        )
    )
    session.flush()


def test_remap_tag_ids_is_a_noop_for_an_empty_remap(db_session: Session, test_user_id: uuid.UUID) -> None:
    remap_tag_ids({}, db_session, user_id=test_user_id)


def test_remap_tag_ids_repoints_a_posting_tags_row(db_session: Session, test_user_id: uuid.UUID) -> None:
    _seed_tags(db_session, test_user_id, ("tag:trip", "Trip"), ("tag:vacation", "Vacation"))
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    _seed_posting_tag(db_session, test_user_id, "p1", "tag:trip")

    remap_tag_ids({"tag:trip": "tag:vacation"}, db_session, user_id=test_user_id)

    row = db_session.query(adb.PostingTag).filter_by(user_id=test_user_id).one()
    assert row.tag_id == derive_id(test_user_id, "tags", "tag:vacation")


def test_remap_tag_ids_deletes_the_old_row_when_the_posting_already_has_the_target_tag(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _seed_tags(db_session, test_user_id, ("tag:trip", "Trip"), ("tag:vacation", "Vacation"))
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    _seed_posting_tag(db_session, test_user_id, "p1", "tag:trip")
    _seed_posting_tag(db_session, test_user_id, "p1", "tag:vacation")

    remap_tag_ids({"tag:trip": "tag:vacation"}, db_session, user_id=test_user_id)

    rows = db_session.query(adb.PostingTag).filter_by(user_id=test_user_id).all()
    assert len(rows) == 1
    assert rows[0].tag_id == derive_id(test_user_id, "tags", "tag:vacation")


def test_remap_tag_ids_repoints_a_tag_ids_override(db_session: Session, test_user_id: uuid.UUID) -> None:
    _seed_tags(db_session, test_user_id, ("tag:trip", "Trip"), ("tag:vacation", "Vacation"), ("tag:other", "Other"))
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    save_overrides({"p1": ManualOverride(tag_ids=["tag:trip", "tag:other"])}, db_session, user_id=test_user_id)

    remap_tag_ids({"tag:trip": "tag:vacation"}, db_session, user_id=test_user_id)

    reloaded = load_overrides(db_session, user_id=test_user_id)["p1"]
    # A join table carries no inherent order — unlike the old array, membership is
    # all that's preserved, not insertion order.
    assert set(reloaded.tag_ids) == {"tag:vacation", "tag:other"}


def test_remap_tag_ids_deletes_the_old_override_row_when_the_posting_already_has_the_target_tag(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _seed_tags(db_session, test_user_id, ("tag:trip", "Trip"), ("tag:vacation", "Vacation"))
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    save_overrides({"p1": ManualOverride(tag_ids=["tag:trip", "tag:vacation"])}, db_session, user_id=test_user_id)

    remap_tag_ids({"tag:trip": "tag:vacation"}, db_session, user_id=test_user_id)

    reloaded = load_overrides(db_session, user_id=test_user_id)["p1"]
    assert reloaded.tag_ids == ["tag:vacation"]


def test_save_overrides_rejects_a_tag_id_that_does_not_exist(db_session: Session, test_user_id: uuid.UUID) -> None:
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    with pytest.raises(IntegrityError):
        save_overrides({"p1": ManualOverride(tag_ids=["tag:does-not-exist"])}, db_session, user_id=test_user_id)


def test_deleting_a_tag_row_cascades_and_clears_a_posting_override_tag(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _seed_tags(db_session, test_user_id, ("tag:trip", "Trip"))
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    save_overrides({"p1": ManualOverride(tag_ids=["tag:trip"])}, db_session, user_id=test_user_id)

    db_session.query(adb.Tag).filter_by(user_id=test_user_id, id=derive_id(test_user_id, "tags", "tag:trip")).delete()
    db_session.commit()

    assert db_session.query(adb.PostingOverrideTag).filter_by(user_id=test_user_id).count() == 0


def test_load_store_with_no_data_yet_seeds_defaults(db_session: Session, test_user_id: uuid.UUID) -> None:
    store = load_store(db_session, user_id=test_user_id)
    assert UNCATEGORIZED_EXPENSE_ACCOUNT_ID in store.accounts
    assert UNCATEGORIZED_INCOME_ACCOUNT_ID in store.accounts
    assert "expense:food-drink" in store.categories
    assert store.rules == []


def test_load_store_seeds_only_once_and_persists(db_session: Session, test_user_id: uuid.UUID) -> None:
    load_store(db_session, user_id=test_user_id)
    persisted = db_session.query(adb.Account).filter_by(user_id=test_user_id).all()
    assert {a.natural_key for a in persisted} >= {UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID}


def test_replace_categories_with_an_empty_tree_prunes_every_category(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    load_store(db_session, user_id=test_user_id)  # seeds the default tree
    replace_categories(db_session, test_user_id, [])
    db_session.commit()
    reloaded = load_store(db_session, user_id=test_user_id)
    assert reloaded.categories == {}


def test_load_store_backfills_a_missing_placeholder_account(db_session: Session, test_user_id: uuid.UUID) -> None:
    load_store(db_session, user_id=test_user_id)
    replace_accounts(db_session, test_user_id, [])
    db_session.commit()

    reloaded = load_store(db_session, user_id=test_user_id)
    assert UNCATEGORIZED_EXPENSE_ACCOUNT_ID in reloaded.accounts
    assert UNCATEGORIZED_INCOME_ACCOUNT_ID in reloaded.accounts


def test_store_data_is_scoped_per_user(db_session: Session, test_user_id: uuid.UUID) -> None:
    other_user_id = uuid.uuid4()
    db_session.add(db.models.User(id=other_user_id, email=f"{other_user_id}@x.com"))
    db_session.commit()

    replace_tags(db_session, test_user_id, [Tag(tag_id="trip", name="Trip")], prune=False)
    db_session.commit()

    theirs = load_store(db_session, user_id=other_user_id)
    assert "trip" not in theirs.tags


def test_load_overrides_with_none_yet_is_empty(db_session: Session, test_user_id: uuid.UUID) -> None:
    assert load_overrides(db_session, user_id=test_user_id) == {}


def test_save_then_load_overrides_round_trips(db_session: Session, test_user_id: uuid.UUID) -> None:
    load_store(db_session, user_id=test_user_id)  # seeds the default categories an override can point at
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    save_overrides({"p1": ManualOverride(category_id="expense:food-drink")}, db_session, user_id=test_user_id)
    reloaded = load_overrides(db_session, user_id=test_user_id)
    assert reloaded["p1"].category_id == "expense:food-drink"


def test_save_then_load_overrides_round_trips_a_tag_override_and_pending_fields(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    load_store(db_session, user_id=test_user_id)  # seeds the default categories an override can point at
    _seed_tags(db_session, test_user_id, ("a", "A"), ("b", "B"))
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    override = ManualOverride(
        category_id="expense:food-drink",
        tag_ids=["a", "b"],
        pending_source="ai",
        pending_selected=False,
        pending_previous_category_id="expense:travel",
    )
    save_overrides({"p1": override}, db_session, user_id=test_user_id)
    reloaded = load_overrides(db_session, user_id=test_user_id)["p1"]
    assert set(reloaded.tag_ids) == {"a", "b"}
    assert reloaded.pending_source == "ai"
    assert reloaded.pending_selected is False
    assert reloaded.pending_previous_category_id == "expense:travel"


def test_save_then_load_overrides_distinguishes_no_tag_override_from_cleared_to_no_tags(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    _seed_posting(db_session, test_user_id, transaction_id="t2", posting_id="p2")
    save_overrides(
        {
            "p1": ManualOverride(category_id=None, tag_ids=None),  # no tag override at all
            "p2": ManualOverride(category_id=None, tag_ids=[]),  # explicitly overridden to no tags
        },
        db_session,
        user_id=test_user_id,
    )
    reloaded = load_overrides(db_session, user_id=test_user_id)
    assert "p1" not in reloaded  # no field set at all, so no row was written in the first place
    assert reloaded["p2"].tag_ids == []


def test_save_overrides_for_postings_does_not_clobber_a_concurrently_saved_different_posting(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """Reproduces the actual bug `save_overrides_for_postings` exists to fix: two requests racing on

    *different* postings must not have one silently erase the other. `save_overrides` (the old whole-table
    path) always rewrote every posting's override from whatever in-memory dict it was handed — a second
    caller working off a snapshot taken before the first caller's write would resave that stale snapshot
    and wipe the first caller's change out. `save_overrides_for_postings` takes an explicit `posting_ids`
    scope instead, so it only ever touches the postings a given call is actually about.
    """
    load_store(db_session, user_id=test_user_id)
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    _seed_posting(db_session, test_user_id, transaction_id="t2", posting_id="p2")

    # Both "requests" read their own snapshot before either one writes.
    snapshot_a = load_overrides_for_postings(db_session, test_user_id, ["p1"])
    snapshot_b = load_overrides_for_postings(db_session, test_user_id, ["p2"])
    assert snapshot_a == {}
    assert snapshot_b == {}

    # "Request A" commits its change to p1 first.
    save_overrides_for_postings(
        ["p1"], {"p1": ManualOverride(category_id="expense:food-drink")}, db_session, test_user_id
    )

    # "Request B" saves its own change to p2, from a snapshot that never saw p1's write.
    save_overrides_for_postings(["p2"], {"p2": ManualOverride(category_id="expense:travel")}, db_session, test_user_id)

    final = load_overrides(db_session, user_id=test_user_id)
    assert final["p1"].category_id == "expense:food-drink"  # would be silently wiped by the old save_overrides
    assert final["p2"].category_id == "expense:travel"


def test_save_posting_split_does_not_clobber_a_concurrently_saved_different_postings_split(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """Same scoping guarantee as the overrides test above, for `save_posting_split`: splitting one posting

    must never touch another posting's already-saved split (the old whole-store path blanket-reinserted
    the entire `posting_splits`/`posting_split_leg` tables on every save).
    """
    load_store(db_session, user_id=test_user_id)
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    _seed_posting(db_session, test_user_id, transaction_id="t2", posting_id="p2")

    save_posting_split(
        PostingSplit(
            posting_id="p1",
            legs=[PostingSplitLeg(amount=6.0, category_id="expense:food-drink"), PostingSplitLeg(amount=4.0)],
        ),
        db_session,
        test_user_id,
    )
    save_posting_split(
        PostingSplit(
            posting_id="p2",
            legs=[PostingSplitLeg(amount=7.0, category_id="expense:travel"), PostingSplitLeg(amount=3.0)],
        ),
        db_session,
        test_user_id,
    )

    store = load_store(db_session, user_id=test_user_id)
    assert set(store.posting_splits.keys()) == {"p1", "p2"}
    assert store.posting_splits["p1"].legs[0].amount == pytest.approx(6.0)
    assert store.posting_splits["p2"].legs[0].amount == pytest.approx(7.0)

    assert delete_posting_split(db_session, test_user_id, "p1") is True
    assert delete_posting_split(db_session, test_user_id, "p1") is False  # idempotent
    store = load_store(db_session, user_id=test_user_id)
    assert set(store.posting_splits.keys()) == {"p2"}  # p2 untouched by p1's delete


def test_save_then_load_store_round_trips_every_entity_type(db_session: Session, test_user_id: uuid.UUID) -> None:
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    _seed_posting(db_session, test_user_id, transaction_id="t2", posting_id="p2")

    store = load_store(db_session, user_id=test_user_id)

    # The accounts aggregate. A child account goes in the same call as its
    # parent on purpose: `replace_accounts` two-passes so the self-referencing
    # FK resolves. Opening balances and manual transfers FK into `accounts`, so
    # they can only be written once those rows exist.
    replace_accounts(
        db_session,
        test_user_id,
        [
            *store.accounts.values(),
            Account(account_id="savings:vault-parent", name="Savings", kind="savings", institution="x", currency="USD"),
            Account(
                account_id="savings:vault-parent:trip",
                name="Trip vault",
                kind="vault",
                institution="x",
                currency="USD",
                parent_account_id="savings:vault-parent",
            ),
        ],
    )
    replace_opening_balances(
        db_session,
        test_user_id,
        [OpeningBalance(account_id="checking:test", amount=100, as_of_date=datetime(2026, 1, 1))],
    )
    replace_manual_transfers(
        db_session,
        test_user_id,
        [
            ManualTransfer(
                transfer_id="mt1",
                date=datetime(2026, 1, 2),
                from_account_id="checking:test",
                to_account_id="savings:vault-parent",
                from_amount=50,
                to_amount=50,
            )
        ],
    )

    # The taxonomy aggregate.
    replace_tags(db_session, test_user_id, [Tag(tag_id="trip", name="Trip")])
    replace_other_assets(db_session, test_user_id, [OtherAsset(asset_id="oa1", name="Car", value=5000)])
    replace_simulator_scenarios(
        db_session,
        test_user_id,
        [
            SimulatorScenario(
                scenario_id="s1",
                name="Retirement",
                initial_capital=1000,
                monthly_contribution=100,
                horizon_years=10,
                annual_rate_pct=5,
            )
        ],
    )

    # The interpretation aggregate.
    replace_transfer_rules(
        db_session, test_user_id, [TransferRule(rule_id="r1", description_contains="uber", priority=1)]
    )
    replace_category_patterns(
        db_session,
        test_user_id,
        [CategoryPattern(pattern_id="cp1", description_contains="uber", category_id="expense:transport")],
    )
    replace_posting_splits(
        db_session,
        test_user_id,
        [
            PostingSplit(
                posting_id="p1",
                legs=[
                    PostingSplitLeg(amount=6, category_id="expense:food-drink", description="groceries"),
                    PostingSplitLeg(amount=4, category_id="expense:transport", description="cab"),
                ],
            )
        ],
    )
    replace_posting_merges(
        db_session,
        test_user_id,
        [PostingMerge(merge_id="m1", kept_transaction_id="t1", duplicate_transaction_ids=["t2"])],
    )

    # The planning aggregate.
    replace_budgets(
        db_session,
        test_user_id,
        [Budget(budget_id="b1", month="2026-01", category_id="expense:food-drink", amount=300)],
    )
    replace_general_budgets(db_session, test_user_id, [GeneralBudget(category_id="expense:food-drink", amount=250)])
    insert_goal(
        db_session,
        test_user_id,
        Goal(
            goal_id="g1",
            name="Emergency fund",
            target_amount=1000,
            target_date=datetime(2027, 1, 1),
            color="#abcdef",
            created_at=datetime(2026, 1, 1),
        ),
    )
    replace_goal_contributions(
        db_session,
        test_user_id,
        [GoalContribution(contribution_id="gc1", goal_id="g1", date=datetime(2026, 1, 5), amount=100)],
    )
    replace_recurring_additions(
        db_session,
        test_user_id,
        [
            RecurringAddition(
                addition_id="ra1",
                goal_id="g1",
                start_date=datetime(2026, 1, 1).date(),
                frequency="monthly",
                mode="fixed_amount",
                value=50,
            )
        ],
    )
    replace_withdrawal_priorities(db_session, test_user_id, [WithdrawalPriorityEntry(goal_id="g1", priority=1)])
    db_session.commit()

    reloaded = load_store(db_session, user_id=test_user_id)

    assert reloaded.accounts["savings:vault-parent:trip"].parent_account_id == "savings:vault-parent"
    assert reloaded.tags["trip"].name == "Trip"
    assert reloaded.rules[0].description_contains == "uber"
    assert reloaded.category_patterns["cp1"].category_id == "expense:transport"
    assert reloaded.other_assets[0].name == "Car"
    assert reloaded.opening_balances["checking:test"].amount == 100
    assert reloaded.manual_transfers[0].to_account_id == "savings:vault-parent"
    assert reloaded.budgets[0].amount == 300
    assert reloaded.general_budgets["expense:food-drink"].amount == 250
    assert reloaded.simulator_scenarios[0].name == "Retirement"
    split = reloaded.posting_splits["p1"]
    assert [leg.amount for leg in split.legs] == [6, 4]
    assert [leg.description for leg in split.legs] == ["groceries", "cab"]
    assert reloaded.posting_merges["m1"].duplicate_transaction_ids == ["t2"]
    assert reloaded.goals["g1"].name == "Emergency fund"
    assert reloaded.goal_contributions["gc1"].amount == 100
    assert reloaded.recurring_additions[0].value == 50
    assert reloaded.withdrawal_priorities[0].goal_id == "g1"


def test_transfer_rule_round_trips_a_real_account_reference(db_session: Session, test_user_id: uuid.UUID) -> None:
    load_store(db_session, user_id=test_user_id)
    replace_accounts(
        db_session,
        test_user_id,
        [
            Account(account_id="checking:test", name="Test", kind="checking", institution="x", currency="USD"),
            Account(account_id="employer:eqore", name="Eqore", kind="income_source", institution="x", currency="USD"),
        ],
        prune=False,
    )
    db_session.commit()
    replace_transfer_rules(
        db_session,
        test_user_id,
        [
            TransferRule(
                rule_id="r1",
                description_contains="payroll",
                account_id="checking:test",
                counterparty_account_id="employer:eqore",
                priority=1,
            )
        ],
    )
    db_session.commit()
    reloaded = load_store(db_session, user_id=test_user_id)

    rule = reloaded.rules[0]
    assert rule.account_id == "checking:test"
    assert rule.counterparty_account_id == "employer:eqore"


def test_transfer_rule_referencing_a_nonexistent_account_raises(db_session: Session, test_user_id: uuid.UUID) -> None:
    load_store(db_session, user_id=test_user_id)
    rule = TransferRule(rule_id="r1", description_contains="payroll", counterparty_account_id="does-not-exist")
    with pytest.raises(IntegrityError):
        replace_transfer_rules(db_session, test_user_id, [rule])


def test_transfer_rule_round_trips_an_excluded_transaction(db_session: Session, test_user_id: uuid.UUID) -> None:
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    rule = TransferRule(rule_id="r1", description_contains="payroll", excluded_transaction_ids=["t1"])
    replace_transfer_rules(db_session, test_user_id, [rule])
    replace_rule_exclusions(db_session, test_user_id, [rule])
    db_session.commit()
    reloaded = load_store(db_session, user_id=test_user_id)

    assert reloaded.rules[0].excluded_transaction_ids == ["t1"]


def test_transfer_rule_excluded_transaction_referencing_a_nonexistent_transaction_raises(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    load_store(db_session, user_id=test_user_id)
    rule = TransferRule(rule_id="r1", description_contains="payroll", excluded_transaction_ids=["does-not-exist"])
    replace_transfer_rules(db_session, test_user_id, [rule])
    with pytest.raises(IntegrityError):
        replace_rule_exclusions(db_session, test_user_id, [rule])


def test_upsert_transfer_rule_leaves_every_other_rule_alone(db_session: Session, test_user_id: uuid.UUID) -> None:
    """The scoped seam that replaced routing `POST /transfer-rules` through the whole-store save.

    That path rewrote every one of this user's rules from the caller's snapshot, so creating one rule
    from a snapshot taken before someone else's create would silently delete the other rule again.
    `upsert_transfer_rule` only ever writes the one `rule_id` it's given.
    """
    load_store(db_session, user_id=test_user_id)
    upsert_transfer_rule(TransferRule(rule_id="r1", description_contains="uber"), db_session, test_user_id)
    upsert_transfer_rule(TransferRule(rule_id="r2", description_contains="lyft"), db_session, test_user_id)

    reloaded = load_store(db_session, user_id=test_user_id)
    assert {rule.rule_id for rule in reloaded.rules} == {"r1", "r2"}

    # Re-posting r1 replaces only r1, and leaves r2 exactly where it was.
    upsert_transfer_rule(TransferRule(rule_id="r1", description_contains="uber", priority=5), db_session, test_user_id)
    reloaded = load_store(db_session, user_id=test_user_id)
    assert {rule.rule_id: rule.priority for rule in reloaded.rules} == {"r1": 5, "r2": 0}


def test_upsert_transfer_rule_round_trips_its_own_exclusions(db_session: Session, test_user_id: uuid.UUID) -> None:
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    upsert_transfer_rule(
        TransferRule(rule_id="r1", description_contains="payroll", excluded_transaction_ids=["t1"]),
        db_session,
        test_user_id,
    )

    assert load_store(db_session, user_id=test_user_id).rules[0].excluded_transaction_ids == ["t1"]

    upsert_transfer_rule(
        TransferRule(rule_id="r1", description_contains="payroll", excluded_transaction_ids=[]),
        db_session,
        test_user_id,
    )
    assert load_store(db_session, user_id=test_user_id).rules[0].excluded_transaction_ids == []


def test_upsert_posting_merge_leaves_every_other_merge_alone(db_session: Session, test_user_id: uuid.UUID) -> None:
    """The scoped seam that replaced routing `POST /posting-merges` through the whole-store save.

    That path blanket-deleted and reinserted every merge from the caller's snapshot, so recording one
    merge could resurrect a merge another request had just undone. `upsert_posting_merge` only ever
    touches the one `merge_id` it's given.
    """
    for index in range(1, 6):
        _seed_posting(db_session, test_user_id, transaction_id=f"t{index}", posting_id=f"p{index}")

    upsert_posting_merge(
        PostingMerge(merge_id="m1", kept_transaction_id="t1", duplicate_transaction_ids=["t2", "t5"]),
        db_session,
        test_user_id,
    )
    upsert_posting_merge(
        PostingMerge(merge_id="m2", kept_transaction_id="t3", duplicate_transaction_ids=["t4"]),
        db_session,
        test_user_id,
    )

    reloaded = load_store(db_session, user_id=test_user_id)
    assert set(reloaded.posting_merges) == {"m1", "m2"}

    # Re-upserting m1 with a narrower duplicate set replaces only m1's rows.
    upsert_posting_merge(
        PostingMerge(merge_id="m1", kept_transaction_id="t1", duplicate_transaction_ids=["t2"], description="redone"),
        db_session,
        test_user_id,
    )
    reloaded = load_store(db_session, user_id=test_user_id)
    assert set(reloaded.posting_merges) == {"m1", "m2"}
    assert reloaded.posting_merges["m1"].duplicate_transaction_ids == ["t2"]
    assert reloaded.posting_merges["m1"].description == "redone"
    assert reloaded.posting_merges["m2"].duplicate_transaction_ids == ["t4"]


def test_transfer_link_round_trips(db_session: Session, test_user_id: uuid.UUID) -> None:
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    _seed_posting(db_session, test_user_id, transaction_id="t2", posting_id="p2")
    link = TransferLink(
        link_id="transfer-link:t1:t2",
        transaction_id_a="t1",
        transaction_id_b="t2",
        source="rule",
        rule_id="chase-card-payoff",
    )
    insert_transfer_links(db_session, test_user_id, [link])
    db_session.commit()
    reloaded = load_store(db_session, user_id=test_user_id)

    assert reloaded.transfer_links == [link]
    assert reloaded.transfer_links[0].rule_id == "chase-card-payoff"


def test_transfer_link_naming_an_already_linked_transaction_raises(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    _seed_posting(db_session, test_user_id, transaction_id="t2", posting_id="p2")
    _seed_posting(db_session, test_user_id, transaction_id="t3", posting_id="p3")
    with pytest.raises(IntegrityError):
        insert_transfer_links(
            db_session,
            test_user_id,
            [
                TransferLink(link_id="transfer-link:t1:t2", transaction_id_a="t1", transaction_id_b="t2"),
                TransferLink(link_id="transfer-link:t1:t3", transaction_id_a="t1", transaction_id_b="t3"),
            ],
        )


def test_writing_accounts_and_taxonomy_never_touches_any_interpretation_row(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """The whole point of the extraction: a stale accounts/taxonomy write can't clobber interpretation data.

    Replaces the old round-trip assertions, which persisted rules/patterns/splits/merges/links *through*
    the whole-store save and so only held because it blanket-reinserted them. Now a caller holding a
    snapshot taken before any of this data existed can write its own aggregate back without erasing a
    single row — which is exactly what every unrelated request (adding an account, renaming a tag) does.
    """
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    _seed_posting(db_session, test_user_id, transaction_id="t2", posting_id="p2")
    stale = load_store(db_session, user_id=test_user_id)
    assert stale.rules == []

    rule = TransferRule(rule_id="r1", description_contains="uber", excluded_transaction_ids=["t1"])
    replace_transfer_rules(db_session, test_user_id, [rule])
    replace_rule_exclusions(db_session, test_user_id, [rule])
    replace_category_patterns(
        db_session,
        test_user_id,
        [CategoryPattern(pattern_id="cp1", description_contains="uber", category_id="expense:transport")],
    )
    replace_posting_splits(
        db_session,
        test_user_id,
        [PostingSplit(posting_id="p1", legs=[PostingSplitLeg(amount=6), PostingSplitLeg(amount=4)])],
    )
    replace_posting_merges(
        db_session,
        test_user_id,
        [PostingMerge(merge_id="m1", kept_transaction_id="t1", duplicate_transaction_ids=["t2"])],
    )
    insert_transfer_links(
        db_session,
        test_user_id,
        [TransferLink(link_id="transfer-link:t1:t2", transaction_id_a="t1", transaction_id_b="t2")],
    )
    db_session.commit()

    # The snapshot taken above still carries none of it — the old whole-store save would wipe all five.
    replace_accounts(db_session, test_user_id, stale.accounts.values())
    replace_categories(db_session, test_user_id, stale.categories.values())
    replace_tags(db_session, test_user_id, stale.tags.values())
    replace_other_assets(db_session, test_user_id, stale.other_assets)
    replace_simulator_scenarios(db_session, test_user_id, stale.simulator_scenarios)
    db_session.commit()

    reloaded = load_store(db_session, user_id=test_user_id)
    assert [r.rule_id for r in reloaded.rules] == ["r1"]
    assert reloaded.rules[0].excluded_transaction_ids == ["t1"]
    assert set(reloaded.category_patterns) == {"cp1"}
    assert set(reloaded.posting_splits) == {"p1"}
    assert set(reloaded.posting_merges) == {"m1"}
    assert [link.link_id for link in reloaded.transfer_links] == ["transfer-link:t1:t2"]


def test_goal_contribution_referencing_a_nonexistent_goal_raises(db_session: Session, test_user_id: uuid.UUID) -> None:
    contribution = GoalContribution(
        contribution_id="gc1", goal_id="does-not-exist", date=datetime(2026, 1, 5), amount=100
    )
    with pytest.raises(IntegrityError):
        replace_goal_contributions(db_session, test_user_id, [contribution])


def test_recurring_addition_referencing_a_nonexistent_goal_raises(db_session: Session, test_user_id: uuid.UUID) -> None:
    addition = RecurringAddition(
        addition_id="ra1",
        goal_id="does-not-exist",
        start_date=datetime(2026, 1, 1).date(),
        frequency="monthly",
        mode="fixed_amount",
        value=50,
    )
    with pytest.raises(IntegrityError):
        replace_recurring_additions(db_session, test_user_id, [addition])


def test_withdrawal_priority_entry_referencing_a_nonexistent_goal_raises(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    with pytest.raises(IntegrityError):
        replace_withdrawal_priorities(db_session, test_user_id, [WithdrawalPriorityEntry(goal_id="does-not-exist")])


def test_posting_budget_id_round_trips_a_real_budget_reference(db_session: Session, test_user_id: uuid.UUID) -> None:
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    replace_budgets(
        db_session,
        test_user_id,
        [Budget(budget_id="b1", month="2026-01", category_id="expense:food-drink", amount=300)],
    )
    db_session.commit()

    posting_row = db_session.query(adb.Posting).filter_by(user_id=test_user_id, natural_key="p1").one()
    posting_row.budget_id = derive_id(test_user_id, "budgets", "b1")
    db_session.commit()

    reloaded_row = db_session.query(adb.Posting).filter_by(user_id=test_user_id, natural_key="p1").one()
    assert reloaded_row.budget_id == derive_id(test_user_id, "budgets", "b1")


def test_posting_budget_id_referencing_a_nonexistent_budget_raises(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    posting_row = db_session.query(adb.Posting).filter_by(user_id=test_user_id, natural_key="p1").one()
    posting_row.budget_id = derive_id(test_user_id, "budgets", "does-not-exist")
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_get_store_version_is_zero_now_that_nothing_bumps_the_whole_store_counter(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """`GET /store` still reports a version; no write path bumps it any more (see `get_store_version`)."""
    load_store(db_session, user_id=test_user_id)
    assert get_store_version(db_session, user_id=test_user_id) == 0


def test_dismissed_suggestion_ids_is_empty_for_a_user_who_never_dismissed_anything(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    assert dismissed_suggestion_ids(db_session, test_user_id, {"transfer:a", "duplicate:b"}) == set()


def test_dismiss_then_check_ids_finds_only_the_dismissed_ones(db_session: Session, test_user_id: uuid.UUID) -> None:
    dismiss_suggestion(
        db_session,
        test_user_id,
        DismissedSuggestion(
            suggestion_id="transfer:a", kind="transfer", description="a", dismissed_at=datetime.now(UTC)
        ),
    )

    found = dismissed_suggestion_ids(db_session, test_user_id, {"transfer:a", "duplicate:b"})

    assert found == {"transfer:a"}


def test_dismiss_suggestion_is_scoped_per_user(db_session: Session, test_user_id: uuid.UUID) -> None:
    other_user_id = uuid.uuid4()
    db_session.add(db.models.User(id=other_user_id, email=f"{other_user_id}@x.com"))
    db_session.commit()
    dismiss_suggestion(
        db_session,
        other_user_id,
        DismissedSuggestion(
            suggestion_id="transfer:a", kind="transfer", description="a", dismissed_at=datetime.now(UTC)
        ),
    )

    assert dismissed_suggestion_ids(db_session, test_user_id, {"transfer:a"}) == set()


def test_list_dismissed_suggestions_is_empty_when_none_dismissed(db_session: Session, test_user_id: uuid.UUID) -> None:
    assert list_dismissed_suggestions(db_session, test_user_id) == []


def test_list_dismissed_suggestions_orders_most_recent_first(db_session: Session, test_user_id: uuid.UUID) -> None:
    dismiss_suggestion(
        db_session,
        test_user_id,
        DismissedSuggestion(
            suggestion_id="transfer:a", kind="transfer", description="a", dismissed_at=datetime(2026, 1, 1, tzinfo=UTC)
        ),
    )
    dismiss_suggestion(
        db_session,
        test_user_id,
        DismissedSuggestion(
            suggestion_id="duplicate:b",
            kind="duplicate",
            description="b",
            dismissed_at=datetime(2026, 1, 5, tzinfo=UTC),
        ),
    )

    listed = list_dismissed_suggestions(db_session, test_user_id)

    assert [entry.suggestion_id for entry in listed] == ["duplicate:b", "transfer:a"]


def test_dismiss_suggestion_twice_replaces_rather_than_duplicates(db_session: Session, test_user_id: uuid.UUID) -> None:
    dismiss_suggestion(
        db_session,
        test_user_id,
        DismissedSuggestion(
            suggestion_id="transfer:a",
            kind="transfer",
            description="first",
            dismissed_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    dismiss_suggestion(
        db_session,
        test_user_id,
        DismissedSuggestion(
            suggestion_id="transfer:a",
            kind="transfer",
            description="second",
            dismissed_at=datetime(2026, 1, 2, tzinfo=UTC),
        ),
    )

    listed = list_dismissed_suggestions(db_session, test_user_id)

    assert len(listed) == 1
    assert listed[0].description == "second"


def test_undismiss_suggestion_removes_it_and_reports_it_existed(db_session: Session, test_user_id: uuid.UUID) -> None:
    dismiss_suggestion(
        db_session,
        test_user_id,
        DismissedSuggestion(
            suggestion_id="transfer:a", kind="transfer", description="a", dismissed_at=datetime.now(UTC)
        ),
    )

    removed = undismiss_suggestion(db_session, test_user_id, "transfer:a")

    assert removed is True
    assert list_dismissed_suggestions(db_session, test_user_id) == []


def test_undismiss_suggestion_reports_false_when_nothing_to_remove(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    assert undismiss_suggestion(db_session, test_user_id, "transfer:nope") is False
