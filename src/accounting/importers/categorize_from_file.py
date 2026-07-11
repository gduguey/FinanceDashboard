"""Match an already-categorized personal file against transactions already in the ledger.

For someone who tracked their own spending by hand before finding this
app — a spreadsheet built from old bank exports, categorized transaction
by transaction — re-importing that file through the normal path would
just create a second copy of every transaction it describes. This reads
the exact same file shape `importers.canonical.csv` does (Date,
Description, Amount or Debit/Credit, optional Category/Subcategory), but
instead of building new postings from it, it finds the posting each row
most likely already *is* somewhere in the ledger, and proposes copying
that row's category onto it. Nothing here is ever persisted by
`preview_categorize_from_file` alone — see `apply_categorize_from_file`,
which only touches the specific postings it's told to.
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

import polars as pl

from accounting.importers.canonical.csv import (
    CategoryOverrides,
    read_tabular_rows,
    resolve_categorization_rows,
)
from accounting.models import ManualOverride
from accounting.store import load_overrides, save_overrides
from db.current_user import DEFAULT_USER_ID

if TYPE_CHECKING:
    import uuid
    from datetime import datetime
    from typing import Any

    from sqlalchemy.orm import Session

    from accounting.importers.canonical.csv import DateOrder, SkippedRowsInfo
    from accounting.models import Category

# Amounts are cents-precision money, not arbitrary floats — an exact match
# (up to float roundoff) is the one cheap, unambiguous filter available
# before description similarity even needs to run.
_AMOUNT_TOLERANCE = 1e-6
# Description containment matters more than exact timing — a hand-entered
# spreadsheet date can be off by a day or two, but two genuinely different
# transactions essentially never describe each other's words.
_DESCRIPTION_WEIGHT = 0.6
_MIN_MATCH_SCORE = 0.3
_DEFAULT_WINDOW_DAYS = 5

_WORD_PATTERN = re.compile(r"[a-z0-9]+")
_WORD_MATCH_THRESHOLD = 0.7


def _tokenize(description: str) -> list[str]:
    return _WORD_PATTERN.findall(description.lower())


def _words_match(word: str, other: str) -> bool:
    return word == other or SequenceMatcher(None, word, other).ratio() >= _WORD_MATCH_THRESHOLD


def _description_containment(file_description: str, posting_description: str) -> float:
    """How much of the shorter description's words show up somewhere in the longer one.

    Deliberately not `ledger.duplicates.description_similarity`'s
    contained-times-coverage score — that's tuned for matching two same-
    format bank exports, each about as long and messy as the other. Here,
    one side is often a short, hand-cleaned label ("Payroll") for what the
    bank recorded as something much longer ("SOME EMPLOYER PAYROLL PPD ID:
    1234567890") — dividing by the *long* side's word count as well would
    score that, and every real match like it, as barely a match at all.
    Containment alone — what fraction of the shorter description's words
    are found in the longer one — is what this asymmetry actually calls for.

    Returns
    -------
    float
        `0.0` (nothing in common) to `1.0` (every word in the shorter description is found in the longer one).
    """
    words_a, words_b = _tokenize(file_description), _tokenize(posting_description)
    if not words_a or not words_b:
        return 0.0
    shorter, longer = (words_a, words_b) if len(words_a) <= len(words_b) else (words_b, words_a)
    matched = sum(1 for word in shorter if any(_words_match(word, other) for other in longer))
    return matched / len(shorter)


def _date_closeness(days_apart: float, window_days: int) -> float:
    return max(0.0, 1 - abs(days_apart) / window_days)


def _category_name(category_id: str | None, categories: dict[str, Category]) -> str | None:
    if category_id is None:
        return None
    category = categories.get(category_id)
    return category.name if category is not None else None


@dataclass(frozen=True)
class CategorizationMatch:
    """One file row, and the existing posting (if any) it most likely already is.

    `posting_id` is `None` when nothing in the ledger matched closely
    enough — the row is still reported, so the caller can show it as
    "couldn't find this one" rather than silently dropping it.
    """

    row_number: int
    posted_at: datetime
    description: str
    amount: float
    proposed_category_id: str | None
    proposed_category_name: str | None
    proposed_subcategory_id: str | None
    proposed_subcategory_name: str | None
    posting_id: str | None
    transaction_id: str | None
    matched_description: str | None
    existing_category_id: str | None
    confidence: float | None


@dataclass(frozen=True)
class CategorizeFromFilePreview:
    """What matching a "categorize from file" upload against the current ledger would do — nothing persisted yet."""

    matches: list[CategorizationMatch]
    new_categories: dict[str, Category]
    skipped_rows: SkippedRowsInfo | None


def preview_categorize_from_file(  # noqa: PLR0913, PLR0914, C901
    file_bytes: bytes,
    *,
    is_excel: bool,
    existing_categories: dict[str, Category],
    ledger: pl.DataFrame | pl.LazyFrame,
    account_ids: list[str] | None = None,
    separator: str | None = None,
    date_order: DateOrder = "MDY",
    category_overrides: CategoryOverrides | None = None,
    window_days: int = _DEFAULT_WINDOW_DAYS,
) -> CategorizeFromFilePreview:
    """Parse a file and propose, for each row, which existing posting it already is and what category to set.

    Every real transaction has exactly one posting whose id ends in
    `":0"` (see `importers.common.posting_pair`/`canonical.csv._build_postings`)
    — its counterparty leg (`":1"`) always points at a placeholder or
    resolved-transfer account and is never what a hand-kept spreadsheet
    row describes, so only `":0"` postings are ever candidates here.

    Matching narrows by exact amount first (a spreadsheet total and a
    bank's own amount for the same real transaction are never off by a
    penny), then scores the surviving candidates mostly on description
    containment (see `_description_containment`), a little on how close
    the two dates are. Every (row, posting) pair scoring above the minimum
    is then matched off greedily, best score first, each row and each
    posting claimed at most once — so two rows that both loosely resemble
    one posting don't both grab it, and a strong match elsewhere isn't
    blocked by a weaker one claimed first.

    Parameters
    ----------
    file_bytes
        The raw file contents, exactly as uploaded.
    is_excel
        Whether to read this as an `.xlsx`/`.xls` workbook rather than CSV text.
    existing_categories
        Every category already in the store, keyed by `category_id`.
    ledger
        The full posting ledger to search for matches.
    account_ids
        Restrict candidate postings to these real accounts. `None` searches every account —
        the whole point of this flow is that a hand-kept sheet may span more than one.
    separator
        The CSV column separator to use, overriding auto-detection. Ignored for Excel.
    date_order
        Whether an ambiguous, all-numeric date reads month-first or day-first.
    category_overrides
        User-provided renames for categories/subcategories this file would otherwise auto-create.
    window_days
        How many days apart a file row's date and a candidate posting's date can be and still match.

    Returns
    -------
    CategorizeFromFilePreview
        One `CategorizationMatch` per file row (matched or not), plus any categories/subcategories
        this file would create and info about any rows that failed to parse. Nothing is persisted.
        Propagates `canonical.csv.CanonicalCsvError` unchanged if the file's columns or separator
        couldn't be figured out — see `read_tabular_rows`/`resolve_categorization_rows`.
    """
    header, data_rows = read_tabular_rows(file_bytes, is_excel=is_excel, separator=separator)
    resolved_rows, new_categories, skip_info = resolve_categorization_rows(
        header, data_rows, existing_categories, date_order, category_overrides
    )
    all_categories = {**existing_categories, **new_categories}

    ledger_df = ledger.lazy() if isinstance(ledger, pl.DataFrame) else ledger
    candidates = ledger_df.filter(pl.col("posting_id").str.ends_with(":0"))
    if account_ids:
        candidates = candidates.filter(pl.col("account_id").is_in(account_ids))
    candidate_rows = (
        candidates
        .select("posting_id", "transaction_id", "posted_at", "description", "amount", "category_id")
        .collect()
        .to_dicts()
    )

    candidates_by_amount: dict[float, list[dict[str, Any]]] = {}
    for candidate in candidate_rows:
        candidates_by_amount.setdefault(round(candidate["amount"], 2), []).append(candidate)

    scored: list[tuple[float, int, str]] = []  # (score, row_index, posting_id)
    for row_index, row in enumerate(resolved_rows):
        for candidate in candidates_by_amount.get(round(row.amount, 2), []):
            if abs(candidate["amount"] - row.amount) > _AMOUNT_TOLERANCE:
                continue
            days_apart = abs((candidate["posted_at"] - row.posted_at).total_seconds()) / 86400
            if days_apart > window_days:
                continue
            similarity = _description_containment(row.description, candidate["description"])
            if similarity < _MIN_MATCH_SCORE:
                continue
            score = _DESCRIPTION_WEIGHT * similarity + (1 - _DESCRIPTION_WEIGHT) * _date_closeness(
                days_apart, window_days
            )
            scored.append((score, row_index, candidate["posting_id"]))

    scored.sort(key=operator.itemgetter(0), reverse=True)
    candidate_by_posting_id = {candidate["posting_id"]: candidate for candidate in candidate_rows}
    claimed_rows: set[int] = set()
    claimed_postings: set[str] = set()
    best_match_for_row: dict[int, tuple[str, float]] = {}
    for score, row_index, posting_id in scored:
        if row_index in claimed_rows or posting_id in claimed_postings:
            continue
        claimed_rows.add(row_index)
        claimed_postings.add(posting_id)
        best_match_for_row[row_index] = (posting_id, score)

    matches: list[CategorizationMatch] = []
    for row_index, row in enumerate(resolved_rows):
        match = best_match_for_row.get(row_index)
        matched_candidate = candidate_by_posting_id[match[0]] if match else None
        matches.append(
            CategorizationMatch(
                row_number=row.row_number,
                posted_at=row.posted_at,
                description=row.description,
                amount=row.amount,
                proposed_category_id=row.category_id,
                proposed_category_name=_category_name(row.category_id, all_categories),
                proposed_subcategory_id=row.subcategory_id,
                proposed_subcategory_name=_category_name(row.subcategory_id, all_categories),
                posting_id=matched_candidate["posting_id"] if matched_candidate else None,
                transaction_id=matched_candidate["transaction_id"] if matched_candidate else None,
                matched_description=matched_candidate["description"] if matched_candidate else None,
                existing_category_id=matched_candidate["category_id"] if matched_candidate else None,
                confidence=match[1] if match else None,
            )
        )

    return CategorizeFromFilePreview(matches=matches, new_categories=new_categories, skipped_rows=skip_info)


@dataclass(frozen=True)
class ConfirmedCategorization:
    """One match the caller has reviewed and wants applied — see `apply_categorize_from_file`."""

    posting_id: str
    category_id: str | None
    subcategory_id: str | None


def apply_categorize_from_file(
    session: Session, confirmed: list[ConfirmedCategorization], user_id: uuid.UUID = DEFAULT_USER_ID
) -> int:
    """Set category/subcategory on every confirmed posting, through the same override a hand edit would make.

    Uses `ManualOverride`'s usual field-level merge (see `api.put_posting_override`) — an entry with only
    `category_id` set never touches a posting's `subcategory_id`, tags, or account override already on file.
    An entry with neither `category_id` nor `subcategory_id` set is skipped; there'd be nothing to apply.

    Parameters
    ----------
    session
        An open database session.
    confirmed
        Every match the caller has reviewed and wants applied — typically a subset of
        `CategorizeFromFilePreview.matches`, with unmatched or rejected rows filtered out first.
    user_id
        Whose overrides these are. See `accounting.store.load_store` for why it defaults.

    Returns
    -------
    int
        How many postings were actually updated.
    """
    applicable = [entry for entry in confirmed if entry.category_id is not None or entry.subcategory_id is not None]
    if not applicable:
        return 0

    overrides = load_overrides(session, user_id=user_id)
    for entry in applicable:
        patch: dict[str, str] = {}
        if entry.category_id is not None:
            patch["category_id"] = entry.category_id
        if entry.subcategory_id is not None:
            patch["subcategory_id"] = entry.subcategory_id
        existing = overrides.get(entry.posting_id)
        if existing is not None:
            overrides[entry.posting_id] = existing.model_copy(update=patch)
        else:
            overrides[entry.posting_id] = ManualOverride(
                category_id=patch.get("category_id"), subcategory_id=patch.get("subcategory_id")
            )
    save_overrides(overrides, session, user_id=user_id)
    return len(applicable)
