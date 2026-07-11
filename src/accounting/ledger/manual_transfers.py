"""Manual transfers: the one deliberate exception to "every posting traces back to a real import".

See `models.ManualTransfer` for why this exists — closing an account needs
a way to record where its remaining balance went that no future bank
statement will ever describe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from accounting.importers.common import postings_to_frame
from accounting.models import Posting

if TYPE_CHECKING:
    import polars as pl

    from accounting.models import Account, ManualTransfer


def postings_for_manual_transfers(transfers: list[ManualTransfer], accounts: dict[str, Account]) -> pl.DataFrame:
    """Turn each manual transfer into its two legs, ready to fold into the resolved posting ledger.

    Both legs share one `transaction_id`, so `dashboard.income_statement`'s
    real-income/expense filter (which looks for a virtual-counterparty
    sibling leg) sees a transaction between two real accounts and correctly
    excludes it from income and expense, the same as any other transfer.

    Parameters
    ----------
    transfers
        Every manually-recorded transfer, e.g. moving a closed account's remaining balance out.
    accounts
        Every known account, keyed by `account_id` — each leg's currency
        always follows its own account's, never the other side's.

    Returns
    -------
    polars.DataFrame
        Two `Posting`-shaped rows per transfer.
    """
    postings = []
    for transfer in transfers:
        transaction_id = f"manual-transfer:{transfer.transfer_id}"
        postings.extend([
            Posting(
                posting_id=f"{transaction_id}:from",
                transaction_id=transaction_id,
                account_id=transfer.from_account_id,
                posted_at=transfer.date,
                amount=-transfer.from_amount,
                currency=accounts[transfer.from_account_id].currency,
                description=transfer.description,
                meta={"source": "manual_transfer"},
            ),
            Posting(
                posting_id=f"{transaction_id}:to",
                transaction_id=transaction_id,
                account_id=transfer.to_account_id,
                posted_at=transfer.date,
                amount=transfer.to_amount,
                currency=accounts[transfer.to_account_id].currency,
                description=transfer.description,
                meta={"source": "manual_transfer"},
            ),
        ])
    return postings_to_frame(postings)
