"""`load_store`/`save_store`/`load_overrides`/`save_overrides` against real Postgres.

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
    TransferRule,
    WithdrawalPriorityEntry,
)
from accounting.store import (
    UNCATEGORIZED_EXPENSE_ACCOUNT_ID,
    UNCATEGORIZED_INCOME_ACCOUNT_ID,
    StoreVersionConflictError,
    get_store_version,
    load_overrides,
    load_store,
    remap_tag_ids,
    save_overrides,
    save_store,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_posting(session: Session, user_id: uuid.UUID, transaction_id: str, posting_id: str) -> None:
    """Insert the minimal Account/Transaction/Posting rows a PostingSplit/PostingMerge/GoalContribution can FK to.

    The account is registered *through* `load_store`/`save_store`, not by
    inserting an `adb.Account` row directly — in real usage, every account a
    posting can reference was already created that way (that's the only way
    an account comes to exist at all), and `load_store` relies on that to
    tell "brand new user" apart from "existing user with no accounts left"
    (see its own docstring). Bypassing the store here would make this an
    unrealistic scenario the app itself can never produce. Safe to call more
    than once per test (e.g. one posting per transaction) — the shared
    "checking:test" account is only added to the store the first time.
    """
    store = load_store(session, user_id=user_id)
    if "checking:test" not in store.accounts:
        store = store.model_copy(
            update={
                "accounts": {
                    **store.accounts,
                    "checking:test": Account(
                        account_id="checking:test", name="Test", kind="checking", institution="x", currency="USD"
                    ),
                }
            }
        )
        save_store(store, session, user_id=user_id)
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
    store = load_store(session, user_id=user_id)
    store = store.model_copy(
        update={"tags": {**store.tags, **{tag_id: Tag(tag_id=tag_id, name=name) for tag_id, name in tag_ids_and_names}}}
    )
    save_store(store, session, user_id=user_id)


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
    _seed_tags(db_session, test_user_id, ("tag:trip", "Trip"), ("tag:vacation", "Vacation"))
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    save_overrides({"p1": ManualOverride(tag_ids=["tag:trip", "tag:other"])}, db_session, user_id=test_user_id)

    remap_tag_ids({"tag:trip": "tag:vacation"}, db_session, user_id=test_user_id)

    reloaded = load_overrides(db_session, user_id=test_user_id)["p1"]
    assert reloaded.tag_ids == ["tag:vacation", "tag:other"]


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


def test_save_then_load_store_round_trips_a_custom_category(db_session: Session, test_user_id: uuid.UUID) -> None:
    store = load_store(db_session, user_id=test_user_id)
    updated = store.model_copy(update={"categories": {}})
    save_store(updated, db_session, user_id=test_user_id)
    reloaded = load_store(db_session, user_id=test_user_id)
    assert reloaded.categories == {}


def test_load_store_backfills_a_missing_placeholder_account(db_session: Session, test_user_id: uuid.UUID) -> None:
    store = load_store(db_session, user_id=test_user_id)
    stripped = store.model_copy(update={"accounts": {}})
    save_store(stripped, db_session, user_id=test_user_id)

    reloaded = load_store(db_session, user_id=test_user_id)
    assert UNCATEGORIZED_EXPENSE_ACCOUNT_ID in reloaded.accounts
    assert UNCATEGORIZED_INCOME_ACCOUNT_ID in reloaded.accounts


def test_store_data_is_scoped_per_user(db_session: Session, test_user_id: uuid.UUID) -> None:
    other_user_id = uuid.uuid4()
    db_session.add(db.models.User(id=other_user_id, email=f"{other_user_id}@x.com", hashed_password="unset"))  # noqa: S106
    db_session.commit()

    mine = load_store(db_session, user_id=test_user_id)
    mine = mine.model_copy(update={"tags": {"trip": Tag(tag_id="trip", name="Trip")}})
    save_store(mine, db_session, user_id=test_user_id)

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
    assert reloaded.tag_ids == ["a", "b"]
    assert reloaded.pending_source == "ai"
    assert reloaded.pending_selected is False
    assert reloaded.pending_previous_category_id == "expense:travel"


def test_save_then_load_store_round_trips_every_entity_type(db_session: Session, test_user_id: uuid.UUID) -> None:
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    _seed_posting(db_session, test_user_id, transaction_id="t2", posting_id="p2")

    store = load_store(db_session, user_id=test_user_id)
    store = store.model_copy(
        update={
            "accounts": {
                **store.accounts,
                "savings:vault-parent": Account(
                    account_id="savings:vault-parent", name="Savings", kind="savings", institution="x", currency="USD"
                ),
                "savings:vault-parent:trip": Account(
                    account_id="savings:vault-parent:trip",
                    name="Trip vault",
                    kind="vault",
                    institution="x",
                    currency="USD",
                    parent_account_id="savings:vault-parent",
                ),
            },
            "tags": {"trip": Tag(tag_id="trip", name="Trip")},
            "rules": [TransferRule(rule_id="r1", description_contains="uber", priority=1)],
            "category_patterns": {
                "cp1": CategoryPattern(pattern_id="cp1", description_contains="uber", category_id="expense:transport")
            },
            "other_assets": [OtherAsset(asset_id="oa1", name="Car", value=5000)],
            "opening_balances": {
                "checking:test": OpeningBalance(account_id="checking:test", amount=100, as_of_date=datetime(2026, 1, 1))
            },
            "manual_transfers": [
                ManualTransfer(
                    transfer_id="mt1",
                    date=datetime(2026, 1, 2),
                    from_account_id="checking:test",
                    to_account_id="savings:vault-parent",
                    from_amount=50,
                    to_amount=50,
                )
            ],
            "budgets": [Budget(budget_id="b1", month="2026-01", category_id="expense:food-drink", amount=300)],
            "general_budgets": {
                "expense:food-drink": GeneralBudget(category_id="expense:food-drink", amount=250),
            },
            "simulator_scenarios": [
                SimulatorScenario(
                    scenario_id="s1",
                    name="Retirement",
                    initial_capital=1000,
                    monthly_contribution=100,
                    horizon_years=10,
                    annual_rate_pct=5,
                )
            ],
            "posting_splits": {
                "p1": PostingSplit(
                    posting_id="p1",
                    legs=[
                        PostingSplitLeg(amount=6, category_id="expense:food-drink", description="groceries"),
                        PostingSplitLeg(amount=4, category_id="expense:transport", description="cab"),
                    ],
                )
            },
            "posting_merges": {
                "m1": PostingMerge(merge_id="m1", kept_transaction_id="t1", duplicate_transaction_ids=["t2"])
            },
            "goals": {
                "g1": Goal(
                    goal_id="g1",
                    name="Emergency fund",
                    target_amount=1000,
                    target_date=datetime(2027, 1, 1),
                    color="#abcdef",
                    created_at=datetime(2026, 1, 1),
                )
            },
            "goal_contributions": {
                "gc1": GoalContribution(contribution_id="gc1", goal_id="g1", date=datetime(2026, 1, 5), amount=100)
            },
            "recurring_additions": [
                RecurringAddition(
                    addition_id="ra1",
                    goal_id="g1",
                    start_date=datetime(2026, 1, 1).date(),
                    frequency="monthly",
                    mode="fixed_amount",
                    value=50,
                )
            ],
            "withdrawal_priorities": [WithdrawalPriorityEntry(goal_id="g1", priority=1)],
            "dismissed_suggestions": {
                "ds1": DismissedSuggestion(
                    suggestion_id="ds1", kind="duplicate", description="dup", dismissed_at=datetime(2026, 1, 6)
                )
            },
        }
    )

    save_store(store, db_session, user_id=test_user_id)
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
    assert reloaded.dismissed_suggestions["ds1"].kind == "duplicate"


def test_transfer_rule_round_trips_a_real_account_reference(db_session: Session, test_user_id: uuid.UUID) -> None:
    store = load_store(db_session, user_id=test_user_id)
    store = store.model_copy(
        update={
            "accounts": {
                **store.accounts,
                "checking:test": Account(
                    account_id="checking:test", name="Test", kind="checking", institution="x", currency="USD"
                ),
                "employer:eqore": Account(
                    account_id="employer:eqore", name="Eqore", kind="income_source", institution="x", currency="USD"
                ),
            },
            "rules": [
                TransferRule(
                    rule_id="r1",
                    description_contains="payroll",
                    account_id="checking:test",
                    counterparty_account_id="employer:eqore",
                    priority=1,
                )
            ],
        }
    )
    save_store(store, db_session, user_id=test_user_id)
    reloaded = load_store(db_session, user_id=test_user_id)

    rule = reloaded.rules[0]
    assert rule.account_id == "checking:test"
    assert rule.counterparty_account_id == "employer:eqore"


def test_transfer_rule_referencing_a_nonexistent_account_raises(db_session: Session, test_user_id: uuid.UUID) -> None:
    store = load_store(db_session, user_id=test_user_id)
    store = store.model_copy(
        update={
            "rules": [
                TransferRule(
                    rule_id="r1",
                    description_contains="payroll",
                    counterparty_account_id="does-not-exist",
                )
            ],
        }
    )
    with pytest.raises(IntegrityError):
        save_store(store, db_session, user_id=test_user_id)


def test_goal_contribution_referencing_a_nonexistent_goal_raises(db_session: Session, test_user_id: uuid.UUID) -> None:
    store = load_store(db_session, user_id=test_user_id)
    store = store.model_copy(
        update={
            "goal_contributions": {
                "gc1": GoalContribution(
                    contribution_id="gc1", goal_id="does-not-exist", date=datetime(2026, 1, 5), amount=100
                )
            }
        }
    )
    with pytest.raises(IntegrityError):
        save_store(store, db_session, user_id=test_user_id)


def test_recurring_addition_referencing_a_nonexistent_goal_raises(db_session: Session, test_user_id: uuid.UUID) -> None:
    store = load_store(db_session, user_id=test_user_id)
    store = store.model_copy(
        update={
            "recurring_additions": [
                RecurringAddition(
                    addition_id="ra1",
                    goal_id="does-not-exist",
                    start_date=datetime(2026, 1, 1).date(),
                    frequency="monthly",
                    mode="fixed_amount",
                    value=50,
                )
            ]
        }
    )
    with pytest.raises(IntegrityError):
        save_store(store, db_session, user_id=test_user_id)


def test_withdrawal_priority_entry_referencing_a_nonexistent_goal_raises(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    store = load_store(db_session, user_id=test_user_id)
    store = store.model_copy(update={"withdrawal_priorities": [WithdrawalPriorityEntry(goal_id="does-not-exist")]})
    with pytest.raises(IntegrityError):
        save_store(store, db_session, user_id=test_user_id)


def test_posting_budget_id_round_trips_a_real_budget_reference(db_session: Session, test_user_id: uuid.UUID) -> None:
    _seed_posting(db_session, test_user_id, transaction_id="t1", posting_id="p1")
    store = load_store(db_session, user_id=test_user_id)
    store = store.model_copy(
        update={"budgets": [Budget(budget_id="b1", month="2026-01", category_id="expense:food-drink", amount=300)]}
    )
    save_store(store, db_session, user_id=test_user_id)

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


def test_get_store_version_is_zero_for_a_user_who_has_never_saved(db_session: Session, test_user_id: uuid.UUID) -> None:
    assert get_store_version(db_session, user_id=test_user_id) == 0


def test_save_store_bumps_the_version_by_one_each_time(db_session: Session, test_user_id: uuid.UUID) -> None:
    # `load_store` itself does one internal save to seed a brand-new user's
    # defaults, so the baseline after it is already 1, not 0.
    store = load_store(db_session, user_id=test_user_id)
    baseline = get_store_version(db_session, user_id=test_user_id)
    save_store(store, db_session, user_id=test_user_id)
    assert get_store_version(db_session, user_id=test_user_id) == baseline + 1
    save_store(store, db_session, user_id=test_user_id)
    assert get_store_version(db_session, user_id=test_user_id) == baseline + 2


def test_save_store_with_no_expected_version_set_skips_the_check(db_session: Session, test_user_id: uuid.UUID) -> None:
    store = load_store(db_session, user_id=test_user_id)
    save_store(store, db_session, user_id=test_user_id)
    before = get_store_version(db_session, user_id=test_user_id)
    db_session.info.pop("expected_store_version", None)
    save_store(store, db_session, user_id=test_user_id)
    assert get_store_version(db_session, user_id=test_user_id) == before + 1


def test_save_store_with_the_current_expected_version_succeeds_and_bumps(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    store = load_store(db_session, user_id=test_user_id)
    save_store(store, db_session, user_id=test_user_id)
    current = get_store_version(db_session, user_id=test_user_id)
    db_session.info["expected_store_version"] = current
    save_store(store, db_session, user_id=test_user_id)
    assert get_store_version(db_session, user_id=test_user_id) == current + 1


def test_save_store_with_a_stale_expected_version_raises_and_does_not_bump(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    store = load_store(db_session, user_id=test_user_id)
    save_store(store, db_session, user_id=test_user_id)
    stale = get_store_version(db_session, user_id=test_user_id)
    db_session.info["expected_store_version"] = stale
    save_store(store, db_session, user_id=test_user_id)
    current = get_store_version(db_session, user_id=test_user_id)
    assert current == stale + 1

    db_session.info["expected_store_version"] = stale
    with pytest.raises(StoreVersionConflictError):
        save_store(store, db_session, user_id=test_user_id)
    assert get_store_version(db_session, user_id=test_user_id) == current
