from datetime import datetime

from accounting.importers.common import RawLeg, posting_pair, postings_to_frame
from accounting.ledger.transfers import (
    apply_transfer_links,
    find_unmatched_transfer_candidates,
    make_transfer_link,
    reconcile_rule_links,
)
from accounting.models import Account, PostingSplit, PostingSplitLeg, TransferRule
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


def test_finds_a_same_amount_opposite_sign_pair_within_the_window_excludes_already_linked_transactions() -> None:
    checking_pair = _placeholder_pair("chase-checking", "1", "chase:checking:9579", _leg(-70.0, "2026-06-29T00:00:00"))
    card_pair = _placeholder_pair("chase-credit-card", "1", "chase:credit_card:8235", _leg(70.0, "2026-06-30T00:00:00"))
    postings = postings_to_frame([*checking_pair, *card_pair])
    link = make_transfer_link(checking_pair[0].transaction_id, card_pair[0].transaction_id, source="manual")

    assert find_unmatched_transfer_candidates(postings, window_days=3, existing_links=[link]).is_empty()


CHASE_CHECKING = Account(
    account_id="chase:checking:9579", name="Chase Checking", kind="checking", institution="Chase", currency="USD"
)
CHASE_CREDIT_CARD = Account(
    account_id="chase:credit_card:8235",
    name="Chase Credit Card",
    kind="credit_card",
    institution="Chase",
    currency="USD",
)
CHASE_CARD_PAYOFF_RULE = TransferRule(
    rule_id="chase-card-payoff",
    description_contains="Payment to Chase card ending in 8235",
    account_id="chase:checking:9579",
    counterparty_account_id="chase:credit_card:8235",
)
_ACCOUNTS = {"chase:checking:9579": CHASE_CHECKING, "chase:credit_card:8235": CHASE_CREDIT_CARD}


def test_make_transfer_link_is_order_independent() -> None:
    forward = make_transfer_link("txn-a", "txn-b", source="manual")
    backward = make_transfer_link("txn-b", "txn-a", source="manual")
    assert forward == backward


def test_reconcile_rule_links_proposes_a_link_when_both_sides_have_a_unique_match() -> None:
    checking_pair = _placeholder_pair(
        "chase-checking",
        "1",
        "chase:checking:9579",
        _leg(-70.0, "2026-06-29T00:00:00", "Payment to Chase card ending in 8235"),
    )
    card_pair = _placeholder_pair(
        "chase-credit-card", "1", "chase:credit_card:8235", _leg(70.0, "2026-06-30T00:00:00", "Payment Thank You")
    )
    postings = postings_to_frame([*checking_pair, *card_pair])

    links = reconcile_rule_links(postings, [CHASE_CARD_PAYOFF_RULE], _ACCOUNTS, posting_splits={}, existing_links=[])

    assert len(links) == 1
    assert links[0].source == "rule"
    assert links[0].rule_id == CHASE_CARD_PAYOFF_RULE.rule_id
    assert {links[0].transaction_id_a, links[0].transaction_id_b} == {
        checking_pair[0].transaction_id,
        card_pair[0].transaction_id,
    }


def test_reconcile_rule_links_proposes_nothing_when_the_other_side_was_never_imported() -> None:
    checking_pair = _placeholder_pair(
        "chase-checking",
        "1",
        "chase:checking:9579",
        _leg(-70.0, "2026-06-29T00:00:00", "Payment to Chase card ending in 8235"),
    )
    postings = postings_to_frame(checking_pair)

    links = reconcile_rule_links(postings, [CHASE_CARD_PAYOFF_RULE], _ACCOUNTS, posting_splits={}, existing_links=[])

    assert links == []


def test_reconcile_rule_links_proposes_nothing_when_the_match_is_ambiguous() -> None:
    checking_pair = _placeholder_pair(
        "chase-checking",
        "1",
        "chase:checking:9579",
        _leg(-70.0, "2026-06-29T00:00:00", "Payment to Chase card ending in 8235"),
    )
    card_pair_1 = _placeholder_pair(
        "chase-credit-card", "1", "chase:credit_card:8235", _leg(70.0, "2026-06-29T00:00:00", "Payment Thank You")
    )
    card_pair_2 = _placeholder_pair(
        "chase-credit-card", "2", "chase:credit_card:8235", _leg(70.0, "2026-06-30T00:00:00", "Payment Thank You")
    )
    postings = postings_to_frame([*checking_pair, *card_pair_1, *card_pair_2])

    links = reconcile_rule_links(postings, [CHASE_CARD_PAYOFF_RULE], _ACCOUNTS, posting_splits={}, existing_links=[])

    assert links == []


def test_reconcile_rule_links_skips_a_transaction_that_already_has_a_split() -> None:
    checking_pair = _placeholder_pair(
        "chase-checking",
        "1",
        "chase:checking:9579",
        _leg(-70.0, "2026-06-29T00:00:00", "Payment to Chase card ending in 8235"),
    )
    card_pair = _placeholder_pair(
        "chase-credit-card", "1", "chase:credit_card:8235", _leg(70.0, "2026-06-30T00:00:00", "Payment Thank You")
    )
    postings = postings_to_frame([*checking_pair, *card_pair])
    real_posting_id = checking_pair[0].posting_id
    split = PostingSplit(
        posting_id=real_posting_id, legs=[PostingSplitLeg(amount=-40.0), PostingSplitLeg(amount=-30.0)]
    )

    links = reconcile_rule_links(
        postings, [CHASE_CARD_PAYOFF_RULE], _ACCOUNTS, posting_splits={real_posting_id: split}, existing_links=[]
    )

    assert links == []


def test_reconcile_rule_links_skips_a_transaction_that_is_already_linked() -> None:
    checking_pair = _placeholder_pair(
        "chase-checking",
        "1",
        "chase:checking:9579",
        _leg(-70.0, "2026-06-29T00:00:00", "Payment to Chase card ending in 8235"),
    )
    card_pair = _placeholder_pair(
        "chase-credit-card", "1", "chase:credit_card:8235", _leg(70.0, "2026-06-30T00:00:00", "Payment Thank You")
    )
    postings = postings_to_frame([*checking_pair, *card_pair])
    existing_link = make_transfer_link(checking_pair[0].transaction_id, "some:other:transaction", source="manual")

    links = reconcile_rule_links(
        postings, [CHASE_CARD_PAYOFF_RULE], _ACCOUNTS, posting_splits={}, existing_links=[existing_link]
    )

    assert links == []


def test_reconcile_rule_links_never_proposes_a_link_for_a_safe_kind_counterparty() -> None:
    """A virtual (or non-importable) counterparty is `apply_rules`'s job to repoint directly — never this function's."""
    employer = Account(
        account_id="employer:eqore", name="EQORE Inc.", kind="income_source", institution="external", currency="USD"
    )
    rule = TransferRule(
        rule_id="eqore-payroll", description_contains="EQORE Inc.", counterparty_account_id="employer:eqore"
    )
    postings = postings_to_frame(
        _placeholder_pair("sofi-savings", "1", "sofi:savings:3680", _leg(2000.0, "2026-06-29T00:00:00", "EQORE Inc."))
    )

    links = reconcile_rule_links(postings, [rule], {"employer:eqore": employer}, posting_splits={}, existing_links=[])

    assert links == []


def test_apply_transfer_links_with_no_links_adds_false_and_null_columns() -> None:
    postings = postings_to_frame(
        _placeholder_pair("chase-checking", "1", "chase:checking:9579", _leg(-70.0, "2026-06-29T00:00:00"))
    )
    result = apply_transfer_links(postings, [])
    assert result["is_linked_transfer"].to_list() == [False, False]
    assert result["linked_transaction_id"].to_list() == [None, None]


def test_apply_transfer_links_marks_both_sides_regardless_of_which_account_the_placeholder_is_on() -> None:
    checking_pair = _placeholder_pair("chase-checking", "1", "chase:checking:9579", _leg(-70.0, "2026-06-29T00:00:00"))
    card_pair = _placeholder_pair("chase-credit-card", "1", "chase:credit_card:8235", _leg(70.0, "2026-06-30T00:00:00"))
    postings = postings_to_frame([*checking_pair, *card_pair])
    link = make_transfer_link(checking_pair[0].transaction_id, card_pair[0].transaction_id, source="rule")

    result = apply_transfer_links(postings, [link])

    assert result["is_linked_transfer"].to_list() == [True, True, True, True]
    linked_ids = dict(zip(result["transaction_id"].to_list(), result["linked_transaction_id"].to_list(), strict=True))
    assert linked_ids[checking_pair[0].transaction_id] == card_pair[0].transaction_id
    assert linked_ids[card_pair[0].transaction_id] == checking_pair[0].transaction_id
    assert set(result["transfer_link_source"].to_list()) == {"rule"}
