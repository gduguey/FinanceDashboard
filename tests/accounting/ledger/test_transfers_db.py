"""`reconcile_and_persist_rule_links` against real Postgres — the write-time half of the transfer redesign.

Complements `tests/accounting/ledger/test_transfers.py` (the pure
`reconcile_rule_links`/`apply_transfer_links` logic) — this exercises the
actual persistence boundary: loading the store, proposing links, and
saving them back, including the DB-enforced "a transaction is never in
more than one link" invariant.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import polars as pl

from accounting.importers.ingest import _write_ledger, load_ledger
from accounting.ledger.frame import LEDGER_FRAME_SCHEMA
from accounting.ledger.transfers import reconcile_and_persist_rule_links
from accounting.models import Account, Posting, TransferRule
from accounting.repositories.interpretation import replace_transfer_rules
from accounting.store import load_store, save_store

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session


def _register_account(session: Session, user_id: uuid.UUID, account_id: str, kind: str) -> None:
    store = load_store(session, user_id=user_id)
    if account_id in store.accounts:
        return
    store = store.model_copy(
        update={
            "accounts": {
                **store.accounts,
                account_id: Account(account_id=account_id, name=account_id, kind=kind, institution="x", currency="USD"),  # type: ignore[arg-type]
            }
        }
    )
    save_store(store, session, user_id=user_id)


def _placeholder_posting(
    posting_id: str, transaction_id: str, account_id: str, amount: float, posted_at: datetime, description: str
) -> Posting:
    return Posting(
        posting_id=posting_id,
        transaction_id=transaction_id,
        account_id=account_id,
        posted_at=posted_at,
        amount=amount,
        currency="USD",
        description=description,
    )


def _seed_transfer_pair(session: Session, user_id: uuid.UUID) -> None:
    """Two independently-imported transactions that look like one Chase card payoff."""
    _register_account(session, user_id, "chase:checking:9579", "checking")
    _register_account(session, user_id, "chase:credit_card:8235", "credit_card")
    checking_at = datetime(2026, 6, 29, tzinfo=UTC)
    card_at = datetime(2026, 6, 30, tzinfo=UTC)
    postings = [
        _placeholder_posting(
            "checking:1:0",
            "checking:1",
            "chase:checking:9579",
            -70.0,
            checking_at,
            "Payment to Chase card ending in 8235",
        ),
        _placeholder_posting(
            "checking:1:1",
            "checking:1",
            "uncategorized:expense",
            70.0,
            checking_at,
            "Payment to Chase card ending in 8235",
        ),
        _placeholder_posting("card:1:0", "card:1", "chase:credit_card:8235", 70.0, card_at, "Payment Thank You"),
        _placeholder_posting("card:1:1", "card:1", "uncategorized:income", -70.0, card_at, "Payment Thank You"),
    ]
    ledger = pl.DataFrame([p.model_dump(mode="python") for p in postings], schema=LEDGER_FRAME_SCHEMA)
    _write_ledger(ledger, session, user_id=user_id)


def test_reconcile_and_persist_rule_links_persists_a_new_link(db_session: Session, test_user_id: uuid.UUID) -> None:
    _seed_transfer_pair(db_session, test_user_id)
    rule = TransferRule(
        rule_id="chase-card-payoff",
        description_contains="Payment to Chase card ending in 8235",
        account_id="chase:checking:9579",
        counterparty_account_id="chase:credit_card:8235",
    )
    replace_transfer_rules(db_session, test_user_id, [rule])
    db_session.commit()

    raw = load_ledger(db_session, user_id=test_user_id)
    new_links = reconcile_and_persist_rule_links(raw, db_session, test_user_id)

    assert len(new_links) == 1
    assert new_links[0].source == "rule"
    reloaded = load_store(db_session, user_id=test_user_id)
    assert reloaded.transfer_links == new_links


def test_reconcile_and_persist_rule_links_is_idempotent(db_session: Session, test_user_id: uuid.UUID) -> None:
    _seed_transfer_pair(db_session, test_user_id)
    rule = TransferRule(
        rule_id="chase-card-payoff",
        description_contains="Payment to Chase card ending in 8235",
        account_id="chase:checking:9579",
        counterparty_account_id="chase:credit_card:8235",
    )
    replace_transfer_rules(db_session, test_user_id, [rule])
    db_session.commit()
    raw = load_ledger(db_session, user_id=test_user_id)

    first = reconcile_and_persist_rule_links(raw, db_session, test_user_id)
    assert len(first) == 1
    second = reconcile_and_persist_rule_links(raw, db_session, test_user_id)
    assert second == []
    assert len(load_store(db_session, user_id=test_user_id).transfer_links) == 1


def test_reconcile_and_persist_rule_links_finds_nothing_when_no_rule_matches(
    db_session: Session, test_user_id: uuid.UUID
) -> None:
    _seed_transfer_pair(db_session, test_user_id)
    raw = load_ledger(db_session, user_id=test_user_id)
    new_links = reconcile_and_persist_rule_links(raw, db_session, test_user_id)
    assert new_links == []
    assert load_store(db_session, user_id=test_user_id).transfer_links == []
