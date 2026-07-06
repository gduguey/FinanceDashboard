"""Match a posting's description against user-maintained `CategoryPattern`s — a suggestion, never applied automatically.

Mirrors `ledger.categorization`'s substring-match/priority-tiebreak logic,
but for `CategoryPattern` instead of `Rule` — kept separate since a `Rule`
match also needs an `account_id` and repoints a counterparty, neither of
which a `CategoryPattern` match does; this only ever proposes a category.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from accounting.models import CategoryPattern


def matching_pattern(patterns: dict[str, CategoryPattern], description: str) -> CategoryPattern | None:
    """Return the highest-priority pattern whose `description_contains` matches, or `None`.

    Parameters
    ----------
    patterns
        Every user-maintained pattern, keyed by `pattern_id`.
    description
        The posting's own description text.

    Returns
    -------
    CategoryPattern or None
    """
    lowered = description.lower()
    candidates = [pattern for pattern in patterns.values() if pattern.description_contains.lower() in lowered]
    if not candidates:
        return None
    return min(candidates, key=lambda pattern: pattern.priority)
