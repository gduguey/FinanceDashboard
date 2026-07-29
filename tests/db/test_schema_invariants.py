"""Every invariant this schema moved out of Python and into the engine, proved to actually reject the bad row.

A constraint nobody has watched fail is a comment. Each test here writes
the exact row the constraint exists to refuse and asserts Postgres refuses
it — so "we made it structural" is a claim with evidence, and a future
change that quietly drops one of these fails loudly.

The deferred zero-sum trigger needs one extra step. `tests/conftest`'s
`db_session` wraps every test in a transaction it always rolls back, and
`session.commit()` inside it only releases a SAVEPOINT — the top-level
`COMMIT` that a `DEFERRABLE INITIALLY DEFERRED` trigger fires on never
happens. `SET CONSTRAINTS ALL IMMEDIATE` is how a test reaches it: it
drains the queued trigger events right there, which is precisely the check
`COMMIT` would have run.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, get_args

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import accounting.db as adb
import trades.db as tdb
from accounting.db.core import Account, Category
from trades.config import LedgerEventType
from trades.ledger.signs import CASH_EFFECT_SIGN

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

_JUNCTION_TABLES = (
    adb.PostingTag,
    adb.TransferLinkedTransaction,
    adb.PostingOverrideTag,
    adb.CategorizationRuleExclusion,
    adb.PostingMergeDuplicate,
)
"""The five pure association tables D9 stripped a surrogate `id` from."""


def _category(session: Session, user_id: uuid.UUID, key: str, parent: uuid.UUID | None = None) -> uuid.UUID:
    """Insert one category and return its id.

    Returns
    -------
    uuid.UUID
    """
    category_id = uuid.uuid4()
    session.add(
        Category(
            id=category_id,
            user_id=user_id,
            natural_key=key,
            name=key,
            classification="expense",
            parent_category_id=parent,
            color="#000000",
        )
    )
    session.flush()
    return category_id


def _account(session: Session, user_id: uuid.UUID, key: str, parent: uuid.UUID | None = None, **columns) -> uuid.UUID:
    """Insert one account and return its id.

    Returns
    -------
    uuid.UUID
    """
    account_id = uuid.uuid4()
    session.add(
        Account(
            id=account_id,
            user_id=user_id,
            natural_key=key,
            name=key,
            kind=columns.pop("kind", "savings"),
            institution="Test",
            currency="USD",
            parent_account_id=parent,
            **columns,
        )
    )
    session.flush()
    return account_id


def _transaction(session: Session, user_id: uuid.UUID, key: str) -> uuid.UUID:
    """Insert one transaction and return its id.

    Returns
    -------
    uuid.UUID
    """
    transaction_id = uuid.uuid4()
    session.add(adb.Transaction(id=transaction_id, user_id=user_id, natural_key=key))
    session.flush()
    return transaction_id


def _posting(
    session: Session,
    user_id: uuid.UUID,
    key: str,
    transaction_id: uuid.UUID,
    account_id: uuid.UUID,
    amount: str,
    currency: str = "USD",
) -> uuid.UUID:
    """Insert one posting and return its id.

    Returns
    -------
    uuid.UUID
    """
    posting_id = uuid.uuid4()
    session.add(
        adb.Posting(
            id=posting_id,
            user_id=user_id,
            natural_key=key,
            transaction_id=transaction_id,
            account_id=account_id,
            posted_at=datetime(2026, 1, 1, tzinfo=UTC),
            amount=Decimal(amount),
            currency=currency,
        )
    )
    session.flush()
    return posting_id


def _check_deferred_constraints(session: Session) -> None:
    """Force every deferred constraint trigger queued so far to run now. See this module's docstring."""
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


# --- B. D8: the two naive trees are exactly two levels deep -------------------


def test_a_subcategory_of_a_subcategory_is_rejected(db_session: Session, test_user_id: uuid.UUID) -> None:
    """Correctness finding E1: the category tree is two levels, and nothing said so."""
    top = _category(db_session, test_user_id, "expense:food")
    child = _category(db_session, test_user_id, "expense:food:groceries", parent=top)
    with pytest.raises(IntegrityError):
        _category(db_session, test_user_id, "expense:food:groceries:milk", parent=child)


def test_a_vault_of_a_vault_is_rejected(db_session: Session, test_user_id: uuid.UUID) -> None:
    """The same bound on the account tree: a vault names a savings account, never another vault."""
    savings = _account(db_session, test_user_id, "sofi:savings")
    vault = _account(db_session, test_user_id, "sofi:vault", parent=savings, kind="vault")
    with pytest.raises(IntegrityError):
        _account(db_session, test_user_id, "sofi:vault:nested", parent=vault, kind="vault")


def test_giving_a_parent_to_a_category_that_already_has_children_is_rejected(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """The other direction the composite key closes, which a naive insert-time check would miss."""
    top = _category(db_session, test_user_id, "expense:food")
    other_top = _category(db_session, test_user_id, "expense:home")
    _category(db_session, test_user_id, "expense:food:groceries", parent=top)
    with pytest.raises(IntegrityError):
        db_session.query(Category).filter_by(id=top).update({"parent_category_id": other_top})


# --- C. D10: `category_id` and `subcategory_id` are one coherent pair ---------


def test_a_posting_cannot_name_a_subcategory_of_a_different_category(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """Correctness finding E3: the two slots were independent foreign keys with nothing relating them."""
    food = _category(db_session, test_user_id, "expense:food")
    home = _category(db_session, test_user_id, "expense:home")
    rent = _category(db_session, test_user_id, "expense:home:rent", parent=home)
    account_id = _account(db_session, test_user_id, "chase:checking", kind="checking")
    transaction_id = _transaction(db_session, test_user_id, "t1")
    db_session.add(
        adb.Posting(
            id=uuid.uuid4(),
            user_id=test_user_id,
            natural_key="p1",
            transaction_id=transaction_id,
            account_id=account_id,
            posted_at=datetime(2026, 1, 1, tzinfo=UTC),
            amount=Decimal(-10),
            currency="USD",
            category_id=food,
            subcategory_id=rent,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_a_posting_cannot_carry_a_subcategory_with_no_category(db_session: Session, test_user_id: uuid.UUID) -> None:
    """The half a composite foreign key cannot cover: `MATCH SIMPLE` skips a pair with any `NULL` in it."""
    home = _category(db_session, test_user_id, "expense:home")
    rent = _category(db_session, test_user_id, "expense:home:rent", parent=home)
    account_id = _account(db_session, test_user_id, "chase:checking", kind="checking")
    transaction_id = _transaction(db_session, test_user_id, "t1")
    db_session.add(
        adb.Posting(
            id=uuid.uuid4(),
            user_id=test_user_id,
            natural_key="p1",
            transaction_id=transaction_id,
            account_id=account_id,
            posted_at=datetime(2026, 1, 1, tzinfo=UTC),
            amount=Decimal(-10),
            currency="USD",
            category_id=None,
            subcategory_id=rent,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_a_budget_cannot_name_a_subcategory_of_a_different_category(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """The same pair, on one of the five other tables that carry it."""
    food = _category(db_session, test_user_id, "expense:food")
    home = _category(db_session, test_user_id, "expense:home")
    rent = _category(db_session, test_user_id, "expense:home:rent", parent=home)
    db_session.add(
        adb.Budget(
            id=uuid.uuid4(),
            user_id=test_user_id,
            natural_key="b1",
            category_id=food,
            subcategory_id=rent,
            amount=Decimal(100),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_every_table_carrying_the_category_pair_constrains_it() -> None:
    """Coverage, not behaviour: D10 named six tables, and a seventh must not be able to appear unguarded."""
    for model, columns in (
        (adb.Posting, ("category_id", "subcategory_id")),
        (adb.Budget, ("category_id", "subcategory_id")),
        (adb.CategorizationRule, ("category_id", "subcategory_id")),
        (adb.PostingOverride, ("category_id", "subcategory_id")),
        (adb.PostingSplitLeg, ("category_id", "subcategory_id")),
        (adb.Suggestion, ("previous_category_id", "previous_subcategory_id")),
    ):
        category_column, subcategory_column = columns
        table = model.__table__
        composite = [
            constraint
            for constraint in table.foreign_key_constraints
            if [column.name for column in constraint.columns] == [subcategory_column, category_column]
        ]
        assert composite, f"{table.name} has no composite ({subcategory_column}, {category_column}) foreign key"
        checks = [
            constraint.name
            for constraint in table.constraints
            if "subcategory_needs_category" in (constraint.name or "")
        ]
        assert checks, f"{table.name} does not require a category alongside its subcategory"


# --- D. D9: junction tables carry no surrogate id ----------------------------


def test_no_junction_table_carries_a_surrogate_id() -> None:
    """Karwin's "ID Required": the association's own columns are its primary key, and nothing else is needed."""
    for model in _JUNCTION_TABLES:
        table = model.__table__
        assert "id" not in table.columns, f"{table.name} still carries a surrogate id"
        primary_key = [column.name for column in table.primary_key.columns]
        assert len(primary_key) > 1, f"{table.name}'s primary key is not composite: {primary_key}"
        assert primary_key[0] == "user_id", f"{table.name}'s primary key does not lead with user_id: {primary_key}"


def test_the_same_posting_tag_pairing_cannot_be_recorded_twice(db_session: Session, test_user_id: uuid.UUID) -> None:
    """What the dropped `UNIQUE` used to say, now said by the primary key itself."""
    account_id = _account(db_session, test_user_id, "chase:checking", kind="checking")
    transaction_id = _transaction(db_session, test_user_id, "t1")
    posting_id = _posting(db_session, test_user_id, "p1", transaction_id, account_id, "0")
    tag_id = uuid.uuid4()
    db_session.add(adb.Tag(id=tag_id, user_id=test_user_id, natural_key="trip", name="Trip"))
    db_session.flush()
    db_session.add(adb.PostingTag(user_id=test_user_id, posting_id=posting_id, tag_id=tag_id))
    db_session.flush()
    db_session.add(adb.PostingTag(user_id=test_user_id, posting_id=posting_id, tag_id=tag_id))
    with pytest.raises(IntegrityError):
        db_session.flush()


# --- E. Per-transaction zero-sum ---------------------------------------------


def test_an_unbalanced_transaction_is_rejected_when_the_check_runs(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """Double-entry's defining rule, enforced by the engine rather than only by the importer."""
    account_id = _account(db_session, test_user_id, "chase:checking", kind="checking")
    transaction_id = _transaction(db_session, test_user_id, "t1")
    _posting(db_session, test_user_id, "p1", transaction_id, account_id, "-100")
    with pytest.raises(IntegrityError):
        _check_deferred_constraints(db_session)


def test_a_balanced_transaction_passes(db_session: Session, test_user_id: uuid.UUID) -> None:
    """And the guard is deferred, so the first leg being unbalanced on its own is not an error."""
    checking = _account(db_session, test_user_id, "chase:checking", kind="checking")
    payee = _account(db_session, test_user_id, "uncategorized:expense", kind="expense_payee")
    transaction_id = _transaction(db_session, test_user_id, "t1")
    _posting(db_session, test_user_id, "p1", transaction_id, checking, "-100")
    _posting(db_session, test_user_id, "p2", transaction_id, payee, "100")
    _check_deferred_constraints(db_session)


def test_a_cross_currency_transaction_is_not_checked(db_session: Session, test_user_id: uuid.UUID) -> None:
    """A manual transfer between currencies balances only after a rate this schema deliberately does not store."""
    usd = _account(db_session, test_user_id, "chase:checking", kind="checking")
    eur = _account(db_session, test_user_id, "n26:checking", kind="checking")
    transaction_id = _transaction(db_session, test_user_id, "t1")
    _posting(db_session, test_user_id, "p1", transaction_id, usd, "-100", currency="USD")
    _posting(db_session, test_user_id, "p2", transaction_id, eur, "92", currency="EUR")
    _check_deferred_constraints(db_session)


# --- E. The remaining structural invariants ----------------------------------


def test_two_split_legs_cannot_share_an_ordinal(db_session: Session, test_user_id: uuid.UUID) -> None:
    """`posting_split_legs` shipped with no unique constraint at all, so its "ordering" could have duplicates."""
    account_id = _account(db_session, test_user_id, "chase:checking", kind="checking")
    transaction_id = _transaction(db_session, test_user_id, "t1")
    posting_id = _posting(db_session, test_user_id, "p1", transaction_id, account_id, "0")
    split_id = uuid.uuid4()
    db_session.add(adb.PostingSplit(id=split_id, user_id=test_user_id, posting_id=posting_id))
    db_session.flush()
    db_session.add(adb.PostingSplitLeg(user_id=test_user_id, posting_split_id=split_id, ordinal=1, amount=Decimal(5)))
    db_session.flush()
    db_session.add(adb.PostingSplitLeg(user_id=test_user_id, posting_split_id=split_id, ordinal=1, amount=Decimal(5)))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_a_second_remainder_automation_is_rejected(db_session: Session, test_user_id: uuid.UUID) -> None:
    """ "Fund this goal with whatever is left" is only meaningful once, and the API's 400 was the only guard."""
    goals = []
    for key in ("g1", "g2"):
        goal_id = uuid.uuid4()
        db_session.add(
            adb.Goal(
                id=goal_id,
                user_id=test_user_id,
                natural_key=key,
                name=key,
                target_amount=Decimal(1000),
                target_date=datetime(2027, 1, 1, tzinfo=UTC),
                color="#000000",
            )
        )
        goals.append(goal_id)
    db_session.flush()

    def _remainder(goal_id: uuid.UUID, key: str) -> adb.GoalAutomation:
        return adb.GoalAutomation(
            id=uuid.uuid4(),
            user_id=test_user_id,
            natural_key=key,
            goal_id=goal_id,
            direction="contribution",
            start_date=datetime(2026, 1, 1, tzinfo=UTC).date(),
            frequency="monthly",
            mode="remainder",
            value=Decimal(0),
            currency="USD",
        )

    db_session.add(_remainder(goals[0], "a1"))
    db_session.flush()
    db_session.add(_remainder(goals[1], "a2"))
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(
            lambda user_id: adb.OtherAsset(
                id=uuid.uuid4(), user_id=user_id, natural_key="car", name="Car", value=Decimal(-1)
            ),
            id="other_assets.value",
        ),
        pytest.param(
            lambda user_id: adb.Goal(
                id=uuid.uuid4(),
                user_id=user_id,
                natural_key="g",
                name="g",
                target_amount=Decimal(0),
                target_date=datetime(2027, 1, 1, tzinfo=UTC),
                color="#000",
            ),
            id="goals.target_amount",
        ),
    ],
)
def test_money_that_cannot_be_negative_is_checked(
    db_session: Session, test_user_id: uuid.UUID, build: Callable[[uuid.UUID], object]
) -> None:
    """Targeted, not blanket — `postings.amount` is signed by design and carries no such check."""
    db_session.add(build(test_user_id))
    with pytest.raises(IntegrityError, match="violates check constraint"):
        db_session.flush()


def test_a_budget_amount_cannot_be_negative(db_session: Session, test_user_id: uuid.UUID) -> None:
    """A spending target of minus fifty dollars is not something to plan for."""
    category_id = _category(db_session, test_user_id, "expense:food")
    db_session.add(
        adb.Budget(
            id=uuid.uuid4(),
            user_id=test_user_id,
            natural_key="b1",
            category_id=category_id,
            amount=Decimal(-50),
        )
    )
    with pytest.raises(IntegrityError, match="violates check constraint"):
        db_session.flush()


def test_postings_amount_is_deliberately_unconstrained(db_session: Session, test_user_id: uuid.UUID) -> None:
    """The scoping the positivity checks above depend on: a debit is a negative amount, always."""
    checking = _account(db_session, test_user_id, "chase:checking", kind="checking")
    payee = _account(db_session, test_user_id, "uncategorized:expense", kind="expense_payee")
    transaction_id = _transaction(db_session, test_user_id, "t1")
    _posting(db_session, test_user_id, "p1", transaction_id, checking, "-100")
    _posting(db_session, test_user_id, "p2", transaction_id, payee, "100")
    _check_deferred_constraints(db_session)


# --- A. The ledger seam ------------------------------------------------------


def test_an_account_cannot_name_a_broker_connection_that_does_not_exist(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """DB-audit move #1: the seam was a bare string, so orphaning was one typo away."""
    with pytest.raises(IntegrityError):
        _account(
            db_session,
            test_user_id,
            "external:ibkr",
            kind="external_investment",
            broker_connection_id=uuid.uuid4(),
        )


def test_only_an_investment_account_can_name_a_broker_connection(db_session: Session, test_user_id: uuid.UUID) -> None:
    """The `CHECK` that makes `kind` redundant in `dashboard.net_worth`'s "is this trades-linked" test."""
    connection_id = uuid.uuid4()
    db_session.add(tdb.BrokerConnection(id=connection_id, user_id=test_user_id, natural_key="ibkr", broker="ibkr"))
    db_session.flush()
    with pytest.raises(IntegrityError, match="violates check constraint"):
        _account(db_session, test_user_id, "chase:checking", kind="checking", broker_connection_id=connection_id)


def test_deleting_a_broker_connection_unlinks_the_account_rather_than_orphaning_it(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    """`ON DELETE SET NULL`: the account degrades to manually-valued, which is a real state, not a dangling one."""
    connection_id = uuid.uuid4()
    db_session.add(tdb.BrokerConnection(id=connection_id, user_id=test_user_id, natural_key="ibkr", broker="ibkr"))
    db_session.flush()
    account_id = _account(
        db_session, test_user_id, "external:ibkr", kind="external_investment", broker_connection_id=connection_id
    )

    db_session.query(tdb.BrokerConnection).filter_by(id=connection_id).delete()
    db_session.flush()
    db_session.expire_all()

    assert db_session.get(Account, account_id).broker_connection_id is None


# --- A. The unified sign convention ------------------------------------------


def test_every_ledger_event_type_has_exactly_one_declared_cash_direction() -> None:
    """The single translation point only works if it covers everything — a gap would silently read as zero."""
    assert set(CASH_EFFECT_SIGN) == set(get_args(LedgerEventType))
