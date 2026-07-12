from datetime import datetime

from accounting.importers.common import RawLeg, posting_pair, postings_to_frame
from accounting.ledger.transfers import find_unmatched_transfer_candidates
from accounting.store import UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID


def _leg(amount: float, posted_at: str, description: str = "x") -> RawLeg:
    return RawLeg(
        posted_at=datetime.fromisoformat(posted_at), amount=amount, currency="USD", description=description, meta={}
    )


def _placeholder_pair(source: str, row_id: str, account_id: str, leg: RawLeg) -> list:
    counterparty = UNCATEGORIZED_INCOME_ACCOUNT_ID if leg.amount >= 0 else UNCATEGORIZED_EXPENSE_ACCOUNT_ID
    return posting_pair(
        source=source, row_id=row_id, account_id=account_id, counterparty_account_id=counterparty, leg=leg
    )


def test_finds_a_same_amount_opposite_sign_pair_within_the_window() -> None:
    postings = postings_to_frame([
        *_placeholder_pair("chase-checking", "1", "chase:checking:9579", _leg(-70.0, "2026-06-29T00:00:00")),
        *_placeholder_pair("chase-credit-card", "1", "chase:credit_card:8235", _leg(70.0, "2026-06-30T00:00:00")),
    ])
    matches = find_unmatched_transfer_candidates(postings, window_days=3)
    assert len(matches) == 1
    row = matches.row(0, named=True)
    assert {row["account_id"], row["other_account_id"]} == {"chase:checking:9579", "chase:credit_card:8235"}


def test_ignores_a_pair_outside_the_window() -> None:
    postings = postings_to_frame([
        *_placeholder_pair("chase-checking", "1", "chase:checking:9579", _leg(-70.0, "2026-06-01T00:00:00")),
        *_placeholder_pair("chase-credit-card", "1", "chase:credit_card:8235", _leg(70.0, "2026-06-30T00:00:00")),
    ])
    assert find_unmatched_transfer_candidates(postings, window_days=3).is_empty()


def test_ignores_a_pair_on_the_same_account() -> None:
    postings = postings_to_frame([
        *_placeholder_pair("chase-checking", "1", "chase:checking:9579", _leg(-70.0, "2026-06-29T00:00:00")),
        *_placeholder_pair("chase-checking", "2", "chase:checking:9579", _leg(70.0, "2026-06-29T00:00:00")),
    ])
    assert find_unmatched_transfer_candidates(postings, window_days=3).is_empty()


def test_no_unresolved_postings_returns_empty() -> None:
    postings = postings_to_frame(
        _placeholder_pair("chase-checking", "1", "chase:checking:9579", _leg(-70.0, "2026-06-29T00:00:00"))
    )
    result = find_unmatched_transfer_candidates(postings.filter(postings["account_id"] == "chase:checking:9579"))
    assert result.is_empty()
