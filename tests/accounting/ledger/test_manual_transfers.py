from datetime import datetime

import pytest

from accounting.ledger.manual_transfers import postings_for_manual_transfers
from accounting.models import Account, ManualTransfer

CHECKING = Account(
    account_id="chase:checking:9579", name="Checking", kind="checking", institution="Chase", currency="USD"
)
SAVINGS = Account(account_id="ally:savings:1111", name="Savings", kind="savings", institution="Ally", currency="USD")
EUR_VAULT = Account(account_id="n26:vault:2222", name="Euro vault", kind="vault", institution="N26", currency="EUR")
ACCOUNTS = {CHECKING.account_id: CHECKING, SAVINGS.account_id: SAVINGS, EUR_VAULT.account_id: EUR_VAULT}


def test_postings_for_manual_transfers_is_empty_for_no_transfers() -> None:
    frame = postings_for_manual_transfers([], ACCOUNTS)
    assert frame.is_empty()


def test_postings_for_manual_transfers_produces_two_balanced_legs_for_a_same_currency_transfer() -> None:
    transfer = ManualTransfer(
        transfer_id="close-chase-checking-1",
        date=datetime.fromisoformat("2026-06-30T00:00:00"),
        from_account_id=CHECKING.account_id,
        to_account_id=SAVINGS.account_id,
        from_amount=250.0,
        to_amount=250.0,
        description="Closing out Chase checking",
    )
    frame = postings_for_manual_transfers([transfer], ACCOUNTS)
    assert frame.height == 2
    rows = {row["account_id"]: row for row in frame.iter_rows(named=True)}
    assert rows[CHECKING.account_id]["amount"] == pytest.approx(-250.0)
    assert rows[CHECKING.account_id]["currency"] == "USD"
    assert rows[SAVINGS.account_id]["amount"] == pytest.approx(250.0)
    assert rows[SAVINGS.account_id]["currency"] == "USD"
    assert rows[CHECKING.account_id]["transaction_id"] == rows[SAVINGS.account_id]["transaction_id"]


def test_postings_for_manual_transfers_uses_each_accounts_own_currency_for_a_cross_currency_transfer() -> None:
    transfer = ManualTransfer(
        transfer_id="close-chase-checking-2",
        date=datetime.fromisoformat("2026-06-30T00:00:00"),
        from_account_id=CHECKING.account_id,
        to_account_id=EUR_VAULT.account_id,
        from_amount=100.0,
        to_amount=92.0,
        description="",
    )
    frame = postings_for_manual_transfers([transfer], ACCOUNTS)
    rows = {row["account_id"]: row for row in frame.iter_rows(named=True)}
    assert rows[CHECKING.account_id]["amount"] == pytest.approx(-100.0)
    assert rows[CHECKING.account_id]["currency"] == "USD"
    assert rows[EUR_VAULT.account_id]["amount"] == pytest.approx(92.0)
    assert rows[EUR_VAULT.account_id]["currency"] == "EUR"
