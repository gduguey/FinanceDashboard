import polars as pl

from accounting.ledger.patterns import match_patterns_bulk, matching_pattern
from accounting.models import CategoryPattern

PATTERNS = {
    "p1": CategoryPattern(pattern_id="p1", description_contains="TRADER JOE", category_id="expense:food-drink"),
    "p2": CategoryPattern(
        pattern_id="p2", description_contains="TRADER JOE", category_id="expense:shopping", priority=-1
    ),
}


def test_matching_pattern_returns_none_when_nothing_matches() -> None:
    assert matching_pattern(PATTERNS, "STARBUCKS COFFEE") is None


def test_matching_pattern_is_case_insensitive() -> None:
    match = matching_pattern(PATTERNS, "trader joe's #123")
    assert match is not None
    assert match.pattern_id in {"p1", "p2"}


def test_matching_pattern_breaks_ties_by_lowest_priority() -> None:
    match = matching_pattern(PATTERNS, "TRADER JOE'S #123")
    assert match is not None
    assert match.pattern_id == "p2"


def test_match_patterns_bulk_matches_every_posting_in_one_pass() -> None:
    descriptions = pl.DataFrame({
        "posting_id": ["a", "b", "c"],
        "description": ["trader joe's #123", "STARBUCKS COFFEE", "TRADER JOE'S #456"],
    })
    matches = match_patterns_bulk(PATTERNS, descriptions)
    by_posting = {row["posting_id"]: row for row in matches.to_dicts()}
    assert set(by_posting) == {"a", "c"}
    assert by_posting["a"]["category_id"] == "expense:shopping"
    assert by_posting["c"]["category_id"] == "expense:shopping"


def test_matching_pattern_skips_an_inactive_pattern() -> None:
    patterns = {**PATTERNS, "p2": PATTERNS["p2"].model_copy(update={"active": False})}
    match = matching_pattern(patterns, "TRADER JOE'S #123")
    assert match is not None
    assert match.pattern_id == "p1"


def test_match_patterns_bulk_skips_an_inactive_pattern() -> None:
    patterns = {**PATTERNS, "p2": PATTERNS["p2"].model_copy(update={"active": False})}
    descriptions = pl.DataFrame({"posting_id": ["a"], "description": ["TRADER JOE'S #123"]})
    matches = match_patterns_bulk(patterns, descriptions)
    assert matches.to_dicts()[0]["category_id"] == "expense:food-drink"


def test_match_patterns_bulk_returns_empty_frame_when_no_patterns() -> None:
    descriptions = pl.DataFrame({"posting_id": ["a"], "description": ["anything"]})
    matches = match_patterns_bulk({}, descriptions)
    assert matches.is_empty()


def test_match_patterns_bulk_returns_empty_frame_when_no_descriptions() -> None:
    descriptions = pl.DataFrame(schema={"posting_id": pl.Utf8, "description": pl.Utf8})
    matches = match_patterns_bulk(PATTERNS, descriptions)
    assert matches.is_empty()
