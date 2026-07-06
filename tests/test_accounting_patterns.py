from accounting.ledger.patterns import matching_pattern
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
