from datetime import datetime

import polars as pl
import pytest

from accounting.ledger.duplicates import description_similarity, find_duplicate_candidates
from accounting.ledger.frame import LEDGER_FRAME_SCHEMA

SCHEMA = LEDGER_FRAME_SCHEMA


def _posting(
    posting_id: str,
    transaction_id: str,
    account_id: str,
    amount: float,
    posted_at: str,
    description: str,
) -> dict:
    return {
        "posting_id": posting_id,
        "transaction_id": transaction_id,
        "account_id": account_id,
        "posted_at": datetime.fromisoformat(posted_at),
        "amount": amount,
        "currency": "USD",
        "category_id": None,
        "subcategory_id": None,
        "budget_id": None,
        "tag_ids": [],
        "description": description,
        "meta": {},
    }


def _postings(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=SCHEMA)


def test_description_similarity_is_high_when_one_is_a_near_superset_of_the_other() -> None:
    similarity = description_similarity("JP Morgan Chase", "JP Morgan Chase Transfer Out")
    assert similarity == pytest.approx(0.6)


def test_description_similarity_is_zero_for_unrelated_descriptions() -> None:
    assert description_similarity("Grocery Store", "Netflix Subscription") == pytest.approx(0.0)


def test_description_similarity_is_one_for_identical_descriptions() -> None:
    assert description_similarity("Whole Foods Market", "Whole Foods Market") == pytest.approx(1.0)


def test_finds_no_candidates_when_the_ledger_is_empty() -> None:
    assert find_duplicate_candidates(_postings(), window_days=3) == []


def test_finds_a_duplicate_pair_on_the_same_account_with_the_same_amount_and_similar_description() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", -42.50, "2026-06-30T00:00:00", "WHOLE FOODS MARKET #123"),
        _posting("p1c", "t1", "uncategorized:expense", 42.50, "2026-06-30T00:00:00", "WHOLE FOODS MARKET #123"),
        _posting("p2", "t2", "chase:checking:9579", -42.50, "2026-06-30T12:00:00", "Whole Foods Market"),
        _posting("p2c", "t2", "uncategorized:expense", 42.50, "2026-06-30T12:00:00", "Whole Foods Market"),
    )
    groups = find_duplicate_candidates(postings, window_days=3)
    assert len(groups) == 1
    group = groups[0]
    assert group.account_id == "chase:checking:9579"
    assert {posting.transaction_id for posting in group.postings} == {"t1", "t2"}


def test_does_not_match_postings_on_different_accounts() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", -42.50, "2026-06-30T00:00:00", "Whole Foods Market"),
        _posting("p2", "t2", "ally:savings:1111", -42.50, "2026-06-30T00:00:00", "Whole Foods Market"),
    )
    assert find_duplicate_candidates(postings, window_days=3) == []


def test_does_not_match_postings_with_different_amounts() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", -42.50, "2026-06-30T00:00:00", "Whole Foods Market"),
        _posting("p2", "t2", "chase:checking:9579", -19.99, "2026-06-30T00:00:00", "Whole Foods Market"),
    )
    assert find_duplicate_candidates(postings, window_days=3) == []


def test_does_not_match_postings_outside_the_window() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", -42.50, "2026-06-30T00:00:00", "Whole Foods Market"),
        _posting("p2", "t2", "chase:checking:9579", -42.50, "2026-07-10T00:00:00", "Whole Foods Market"),
    )
    assert find_duplicate_candidates(postings, window_days=3) == []


def test_does_not_match_dissimilar_descriptions_even_with_matching_amount_and_date() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", -42.50, "2026-06-30T00:00:00", "Whole Foods Market"),
        _posting("p2", "t2", "chase:checking:9579", -42.50, "2026-06-30T00:00:00", "Netflix Subscription"),
    )
    assert find_duplicate_candidates(postings, window_days=3) == []


def test_groups_are_sorted_least_certain_first() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", -42.50, "2026-06-30T00:00:00", "Whole Foods Market"),
        _posting("p2", "t2", "chase:checking:9579", -42.50, "2026-06-30T00:00:00", "Whole Foods Market"),
        _posting("p3", "t3", "ally:savings:1111", -19.99, "2026-06-15T00:00:00", "Coffee Shop Downtown"),
        _posting("p4", "t4", "ally:savings:1111", -19.99, "2026-06-18T00:00:00", "Coffee Shop"),
    )
    groups = find_duplicate_candidates(postings, window_days=3)
    assert len(groups) == 2
    assert groups[0].certainty <= groups[1].certainty


def test_clusters_three_or_more_duplicate_postings_into_one_group() -> None:
    postings = _postings(
        _posting("p1", "t1", "chase:checking:9579", -42.50, "2026-06-30T00:00:00", "Whole Foods Market"),
        _posting("p2", "t2", "chase:checking:9579", -42.50, "2026-06-30T06:00:00", "Whole Foods Market"),
        _posting("p3", "t3", "chase:checking:9579", -42.50, "2026-06-30T12:00:00", "Whole Foods Market"),
    )
    groups = find_duplicate_candidates(postings, window_days=3)
    assert len(groups) == 1
    assert {posting.transaction_id for posting in groups[0].postings} == {"t1", "t2", "t3"}
