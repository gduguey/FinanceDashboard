"""Suggest — never apply — likely duplicate transactions: the same real-world purchase imported twice.

Unlike `ledger.transfers` (two legs of one real event, on two different
accounts), a duplicate is the *same* leg recorded twice on the *same*
account — usually because it reached this ledger through two different
import sources (a CSV export and a statement PDF, say) that each gave it
their own transaction id. A heuristic match like this can be wrong (two
genuinely separate same-amount purchases days apart), so it is only ever
surfaced as a suggestion for a human to confirm via `models.PostingMerge`
(see `apply_posting_merges`), never applied automatically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from datetime import datetime

_PLACEHOLDER_ACCOUNT_IDS = ["uncategorized:expense", "uncategorized:income"]
_AMOUNT_TOLERANCE = 1e-6
_WORD_PATTERN = re.compile(r"[a-z0-9]+")
_WORD_MATCH_THRESHOLD = 0.7
_MIN_DESCRIPTION_SIMILARITY = 0.3
_CERTAINTY_DESCRIPTION_WEIGHT = 0.6


def _tokenize(description: str) -> list[str]:
    return _WORD_PATTERN.findall(description.lower())


def _words_match(word: str, other: str) -> bool:
    return word == other or SequenceMatcher(None, word, other).ratio() >= _WORD_MATCH_THRESHOLD


def description_similarity(a: str, b: str) -> float:
    """How much the shorter description's words are contained in, and cover, the longer one's.

    Two numbers multiplied together, not just one: "containment" (what
    fraction of the shorter description's own words show up in the
    longer one, allowing near-matches like "Morgan"/"Morger" for a typo'd
    re-export) and "coverage" (what fraction of the *longer* description
    those matched words actually account for) — a short description that
    matches only a tiny fragment of a long, unrelated one still needs to
    score low, which containment alone wouldn't catch.

    Parameters
    ----------
    a
        One transaction's description.
    b
        The other transaction's description.

    Returns
    -------
    float
        `0.0` (nothing in common) to `1.0` (identical, word for word).
    """
    words_a, words_b = _tokenize(a), _tokenize(b)
    if not words_a or not words_b:
        return 0.0
    shorter, longer = (words_a, words_b) if len(words_a) <= len(words_b) else (words_b, words_a)
    matched = sum(1 for word in shorter if any(_words_match(word, other) for other in longer))
    contained = matched / len(shorter)
    coverage = matched / len(longer)
    return contained * coverage


def _date_closeness(days_apart: float, window_days: int) -> float:
    return max(0.0, 1 - abs(days_apart) / window_days)


@dataclass(frozen=True)
class DuplicatePosting:
    """One real account's own leg of a transaction that might be a duplicate."""

    posting_id: str
    transaction_id: str
    posted_at: datetime
    description: str
    amount: float


@dataclass(frozen=True)
class DuplicateGroup:
    """Two or more transactions on one account that look like the same real-world event, recorded more than once."""

    group_key: str
    account_id: str
    certainty: float
    postings: list[DuplicatePosting]


class _UnionFind:
    """Standard union-find over string keys, path-compressing on lookup."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, node: str) -> str:
        root = node
        while self._parent.get(root, root) != root:
            root = self._parent[root]
        while self._parent.get(node, node) != root:
            self._parent[node], node = root, self._parent.get(node, node)
        return root

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self._parent[root_a] = root_b


def find_duplicate_candidates(postings: pl.DataFrame, window_days: int = 3) -> list[DuplicateGroup]:
    """Find groups of transactions on the same account that look like the same event, imported more than once.

    Parameters
    ----------
    postings
        The full posting ledger.
    window_days
        How many days apart transactions can be and still count as one duplicate.

    Returns
    -------
    list[DuplicateGroup]
        Every group found, sorted least-certain first — those need the
        closest review, since a confident match is unlikely to be wrong.
    """
    real = postings.filter(~pl.col("account_id").is_in(_PLACEHOLDER_ACCOUNT_IDS)).select(
        "posting_id", "transaction_id", "account_id", "posted_at", "description", "amount"
    )
    if real.is_empty():
        return []

    joined = real.join(real, how="cross", suffix="_other")
    pairs = joined.filter(
        (pl.col("account_id") == pl.col("account_id_other"))
        & (pl.col("transaction_id") != pl.col("transaction_id_other"))
        & (pl.col("posting_id") < pl.col("posting_id_other"))
        & ((pl.col("amount") - pl.col("amount_other")).abs() < _AMOUNT_TOLERANCE)
        & ((pl.col("posted_at") - pl.col("posted_at_other")).abs() <= pl.duration(days=window_days))
    )
    if pairs.is_empty():
        return []

    edges: list[tuple[str, str, str, float]] = []
    for row in pairs.iter_rows(named=True):
        similarity = description_similarity(row["description"], row["description_other"])
        if similarity < _MIN_DESCRIPTION_SIMILARITY:
            continue
        days_apart = abs((row["posted_at"] - row["posted_at_other"]).total_seconds()) / 86400
        certainty = _CERTAINTY_DESCRIPTION_WEIGHT * similarity + (1 - _CERTAINTY_DESCRIPTION_WEIGHT) * _date_closeness(
            days_apart, window_days
        )
        edges.append((row["account_id"], row["transaction_id"], row["transaction_id_other"], certainty))
    if not edges:
        return []

    union_find = _UnionFind()
    for _account_id, transaction_id, other_transaction_id, _certainty in edges:
        union_find.union(transaction_id, other_transaction_id)

    transaction_ids_by_root: dict[str, set[str]] = {}
    min_certainty_by_root: dict[str, float] = {}
    account_by_root: dict[str, str] = {}
    for account_id, transaction_id, other_transaction_id, certainty in edges:
        root = union_find.find(transaction_id)
        transaction_ids_by_root.setdefault(root, set()).update({transaction_id, other_transaction_id})
        min_certainty_by_root[root] = min(certainty, min_certainty_by_root.get(root, certainty))
        account_by_root[root] = account_id

    postings_by_transaction = {
        row["transaction_id"]: DuplicatePosting(
            posting_id=row["posting_id"],
            transaction_id=row["transaction_id"],
            posted_at=row["posted_at"],
            description=row["description"],
            amount=row["amount"],
        )
        for row in real.iter_rows(named=True)
    }

    groups = [
        DuplicateGroup(
            group_key=":".join(sorted(transaction_ids)),
            account_id=account_by_root[root],
            certainty=min_certainty_by_root[root],
            postings=[postings_by_transaction[transaction_id] for transaction_id in sorted(transaction_ids)],
        )
        for root, transaction_ids in transaction_ids_by_root.items()
    ]
    return sorted(groups, key=lambda group: group.certainty)
