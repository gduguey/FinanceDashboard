"""Match a posting's description against user-maintained `CategoryPattern`s — a suggestion, never applied automatically.

Mirrors `ledger.categorization`'s substring-match/priority-tiebreak logic,
but for `CategoryPattern` instead of `TransferRule` — kept separate since a `TransferRule`
match also needs an `account_id` and repoints a counterparty, neither of
which a `CategoryPattern` match does; this only ever proposes a category.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

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


_MATCH_SCHEMA = {"posting_id": pl.Utf8, "category_id": pl.Utf8, "subcategory_id": pl.Utf8}


def match_patterns_bulk(patterns: dict[str, CategoryPattern], descriptions: pl.DataFrame) -> pl.DataFrame:
    """Match many postings' descriptions against every pattern at once, in a single polars pass.

    The vectorized counterpart to calling `matching_pattern` once per
    posting — cross-joins every posting against every pattern instead of
    looping over postings in Python, so a bulk suggestion action over
    hundreds of postings costs one pass instead of one Python-level match
    per row.

    Parameters
    ----------
    patterns
        Every user-maintained pattern, keyed by `pattern_id`.
    descriptions
        A `posting_id`/`description` frame — the postings to match.

    Returns
    -------
    polars.DataFrame
        Columns `posting_id`, `category_id`, `subcategory_id` — one row
        per posting_id in `descriptions` that matched a pattern (the
        lowest-`priority` one, same tiebreak as `matching_pattern`).
        Postings with no match are simply absent.
    """
    if not patterns or descriptions.is_empty():
        return pl.DataFrame(schema=_MATCH_SCHEMA)

    patterns_df = pl.DataFrame({
        "pattern_description": [pattern.description_contains.lower() for pattern in patterns.values()],
        "pattern_category_id": [pattern.category_id for pattern in patterns.values()],
        "pattern_subcategory_id": [pattern.subcategory_id for pattern in patterns.values()],
        "pattern_priority": [pattern.priority for pattern in patterns.values()],
    })
    return (
        descriptions
        .lazy()
        .with_columns(description_lower=pl.col("description").str.to_lowercase())
        .join(patterns_df.lazy(), how="cross")
        .filter(pl.col("description_lower").str.contains(pl.col("pattern_description"), literal=True))
        .sort("pattern_priority")
        .group_by("posting_id", maintain_order=True)
        .first()
        .select(
            "posting_id",
            pl.col("pattern_category_id").alias("category_id"),
            pl.col("pattern_subcategory_id").alias("subcategory_id"),
        )
        .collect()
    )
