"""Canonical CSV: the fallback importer for any bank with no dedicated standardizer.

Unlike every other importer here (see `importers.chase`/`importers.sofi`),
this one doesn't know its source's exact export shape ahead of time — it
looks for a handful of expected concepts (date, description, an amount) by
fuzzy column-name matching, parses whatever date/number format it finds
(see `canonical.parsing`), and auto-creates any category/subcategory it's
told about. When it can't make sense of a file, it raises a clear,
user-facing error explaining exactly what columns and formats are
supported, rather than guessing further.
"""

from __future__ import annotations

import csv as csv_module
import io
import operator
from dataclasses import dataclass
from datetime import datetime, time
from typing import TYPE_CHECKING

import polars as pl

from accounting.importers.canonical.parsing import find_column, parse_amount_flexible, parse_date_flexible
from accounting.importers.common import row_hash
from accounting.models import Category, Posting
from accounting.store import UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID, slugify

if TYPE_CHECKING:
    from datetime import date as date_type

    from accounting.models import CategoryClassification, CurrencyCode

_DATE_ALIASES = {"date", "transaction date", "posting date", "posted date", "trans date"}
_DESCRIPTION_ALIASES = {"description", "memo", "narrative", "payee", "details", "transaction description"}
_AMOUNT_ALIASES = {"amount", "transaction amount", "value"}
_DEBIT_ALIASES = {"debit", "withdrawal", "money out", "debit amount"}
_CREDIT_ALIASES = {"credit", "deposit", "money in", "credit amount"}
_CATEGORY_ALIASES = {"category"}
_SUBCATEGORY_ALIASES = {"subcategory", "sub category", "sub-category"}
_CANDIDATE_SEPARATORS = [",", ";", "\t", "|"]
_NEW_CATEGORY_COLOR = "#9ca3af"
_MAX_UNPARSEABLE_ROW_FRACTION = 0.2

REQUIRED_COLUMNS_HELP = (
    "Expected a Date column, a Description column, and either an Amount column "
    "(negative for money out, positive for money in) or separate Debit and Credit columns. "
    "An optional Category column and an optional Subcategory column are also read, if present. "
    "Dates can be written in almost any common format (e.g. 2026-06-30, 06/30/2026, Jun 30 2026). "
    "Amounts can include a currency symbol and either US (1,234.56) or European (1.234,56) "
    "thousands separators."
)


class CanonicalCsvError(ValueError):
    """The canonical CSV importer couldn't make sense of a file — the message is shown to the user as-is."""


class CanonicalCsvSeparatorUnknownError(CanonicalCsvError):
    """The file's column separator couldn't be auto-detected — ask the user which one to use."""


@dataclass(frozen=True)
class CanonicalImportResult:
    """What one canonical CSV import produced."""

    postings: pl.DataFrame
    new_categories: dict[str, Category]


@dataclass(frozen=True)
class _Columns:
    """Which header cell (if any) covers each concept this importer looks for."""

    date: str
    description: str
    amount: str | None
    debit: str | None
    credit: str | None
    category: str | None
    subcategory: str | None


@dataclass(frozen=True)
class _ParsedRow:
    posted_at: datetime
    amount: float
    description: str
    category_name: str | None
    subcategory_name: str | None
    row_number: int


def _detect_separator(csv_text: str) -> str:
    sample = csv_text[:8192]
    try:
        return csv_module.Sniffer().sniff(sample, delimiters="".join(_CANDIDATE_SEPARATORS)).delimiter
    except csv_module.Error:
        header_line = sample.splitlines()[0] if sample.splitlines() else ""
        counts = {sep: header_line.count(sep) for sep in _CANDIDATE_SEPARATORS}
        best_sep, best_count = max(counts.items(), key=operator.itemgetter(1))
        if best_count == 0:
            raise CanonicalCsvSeparatorUnknownError(
                "Couldn't tell what separates columns in this file. Please specify the separator "
                "used (comma, semicolon, tab, or pipe) and try again."
            ) from None
        return best_sep


def _resolve_columns(header: list[str]) -> _Columns:
    date_col = find_column(header, _DATE_ALIASES)
    description_col = find_column(header, _DESCRIPTION_ALIASES)
    amount_col = find_column(header, _AMOUNT_ALIASES)
    debit_col = find_column(header, _DEBIT_ALIASES)
    credit_col = find_column(header, _CREDIT_ALIASES)
    has_amount = amount_col is not None or (debit_col is not None and credit_col is not None)
    if date_col is None or description_col is None or not has_amount:
        message = (
            f"Couldn't find the expected columns in this file. {REQUIRED_COLUMNS_HELP} "
            f"Columns found: {', '.join(header) if header else '(none)'}."
        )
        raise CanonicalCsvError(message)
    return _Columns(
        date=date_col,
        description=description_col,
        amount=amount_col,
        debit=debit_col,
        credit=credit_col,
        category=find_column(header, _CATEGORY_ALIASES),
        subcategory=find_column(header, _SUBCATEGORY_ALIASES),
    )


def _row_amount(row_cell: dict[str, str], columns: _Columns) -> float | None:
    if columns.amount is not None:
        return parse_amount_flexible(row_cell[columns.amount])
    debit = parse_amount_flexible(row_cell[columns.debit]) if columns.debit else None
    credit = parse_amount_flexible(row_cell[columns.credit]) if columns.credit else None
    if debit is None and credit is None:
        return None
    return (credit or 0.0) - abs(debit or 0.0)


def _parse_rows(data_rows: list[list[str]], header: list[str], columns: _Columns) -> list[_ParsedRow]:
    index = {name: position for position, name in enumerate(header)}

    def cell(row: list[str], column: str) -> str:
        position = index[column]
        return row[position] if position < len(row) else ""

    parsed_rows: list[_ParsedRow] = []
    bad_dates = 0
    bad_amounts = 0
    total_rows = 0
    for row_number, raw_row in enumerate(data_rows, start=2):
        if not raw_row or all(not value.strip() for value in raw_row):
            continue
        total_rows += 1
        row_cell = {name: cell(raw_row, name) for name in index}

        parsed_date: date_type | None = parse_date_flexible(row_cell[columns.date])
        if parsed_date is None:
            bad_dates += 1
            continue

        amount = _row_amount(row_cell, columns)
        if amount is None:
            bad_amounts += 1
            continue

        raw_category = row_cell[columns.category].strip() if columns.category else ""
        raw_subcategory = row_cell[columns.subcategory].strip() if columns.subcategory else ""
        parsed_rows.append(
            _ParsedRow(
                posted_at=datetime.combine(parsed_date, time()),
                amount=amount,
                description=row_cell[columns.description].strip(),
                category_name=raw_category or None,
                subcategory_name=raw_subcategory or None,
                row_number=row_number,
            )
        )

    if total_rows == 0 or len(parsed_rows) < total_rows * (1 - _MAX_UNPARSEABLE_ROW_FRACTION):
        message = (
            f"Couldn't parse most of this file's rows — {bad_dates} row(s) had an unrecognized date and "
            f"{bad_amounts} row(s) had an unrecognized amount, out of {total_rows} data row(s). "
            f"{REQUIRED_COLUMNS_HELP}"
        )
        raise CanonicalCsvError(message)
    return parsed_rows


def _match_existing_category(name: str, parent_id: str | None, categories: dict[str, Category]) -> Category | None:
    lowered = name.strip().lower()
    for category in categories.values():
        if category.parent_category_id == parent_id and category.name.strip().lower() == lowered:
            return category
    return None


def _unique_category_id(base_id: str, existing: dict[str, Category], created: dict[str, Category]) -> str:
    if base_id not in existing and base_id not in created:
        return base_id
    suffix = 2
    while f"{base_id}-{suffix}" in existing or f"{base_id}-{suffix}" in created:
        suffix += 1
    return f"{base_id}-{suffix}"


def _classification_for(amounts: list[float]) -> CategoryClassification:
    negative = sum(1 for amount in amounts if amount < 0)
    return "expense" if negative >= len(amounts) - negative else "income"


def _resolve_top_categories(
    parsed_rows: list[_ParsedRow], existing_categories: dict[str, Category], new_categories: dict[str, Category]
) -> dict[str, str]:
    amounts_by_name: dict[str, list[float]] = {}
    for row in parsed_rows:
        if row.category_name:
            amounts_by_name.setdefault(row.category_name.strip().lower(), []).append(row.amount)

    category_ids: dict[str, str] = {}
    for key, amounts in amounts_by_name.items():
        existing = _match_existing_category(key, None, existing_categories)
        if existing is not None:
            category_ids[key] = existing.category_id
            continue
        display_name = next(
            row.category_name for row in parsed_rows if row.category_name and row.category_name.strip().lower() == key
        )
        classification = _classification_for(amounts)
        base_id = f"{classification}:{slugify(display_name)}"
        category_id = _unique_category_id(base_id, existing_categories, new_categories)
        new_categories[category_id] = Category(
            category_id=category_id, name=display_name, classification=classification, color=_NEW_CATEGORY_COLOR
        )
        category_ids[key] = category_id
    return category_ids


def _resolve_subcategories(
    parsed_rows: list[_ParsedRow],
    top_category_ids: dict[str, str],
    existing_categories: dict[str, Category],
    new_categories: dict[str, Category],
) -> dict[tuple[str, str], str]:
    amounts_by_key: dict[tuple[str, str], list[float]] = {}
    for row in parsed_rows:
        if row.category_name and row.subcategory_name:
            top_key = row.category_name.strip().lower()
            sub_key = row.subcategory_name.strip().lower()
            amounts_by_key.setdefault((top_key, sub_key), []).append(row.amount)

    subcategory_ids: dict[tuple[str, str], str] = {}
    for top_key, sub_key in amounts_by_key.keys():  # noqa: SIM118
        parent_id = top_category_ids[top_key]
        existing = _match_existing_category(sub_key, parent_id, existing_categories)
        if existing is not None:
            subcategory_ids[top_key, sub_key] = existing.category_id
            continue
        display_name = next(
            row.subcategory_name
            for row in parsed_rows
            if row.category_name
            and row.subcategory_name
            and row.category_name.strip().lower() == top_key
            and row.subcategory_name.strip().lower() == sub_key
        )
        parent = existing_categories.get(parent_id) or new_categories[parent_id]
        sub_id = _unique_category_id(f"{parent_id}:{slugify(display_name)}", existing_categories, new_categories)
        new_categories[sub_id] = Category(
            category_id=sub_id,
            name=display_name,
            classification=parent.classification,
            parent_category_id=parent_id,
            color=parent.color,
        )
        subcategory_ids[top_key, sub_key] = sub_id
    return subcategory_ids


def _build_postings(
    parsed_rows: list[_ParsedRow],
    account_id: str,
    account_currency: CurrencyCode,
    top_category_ids: dict[str, str],
    subcategory_ids: dict[tuple[str, str], str],
) -> list[Posting]:
    postings: list[Posting] = []
    for row in parsed_rows:
        top_key = row.category_name.strip().lower() if row.category_name else None
        category_id = top_category_ids.get(top_key) if top_key else None
        subcategory_id = (
            subcategory_ids.get((top_key, row.subcategory_name.strip().lower()))
            if top_key and row.subcategory_name
            else None
        )
        transaction_hash = row_hash(
            account_id, row.posted_at.isoformat(), f"{row.amount:.4f}", row.description, str(row.row_number)
        )
        transaction_id = f"canonical:{account_id}:{transaction_hash}"
        counterparty = UNCATEGORIZED_INCOME_ACCOUNT_ID if row.amount >= 0 else UNCATEGORIZED_EXPENSE_ACCOUNT_ID
        postings.extend([
            Posting(
                posting_id=f"{transaction_id}:0",
                transaction_id=transaction_id,
                account_id=account_id,
                posted_at=row.posted_at,
                amount=row.amount,
                currency=account_currency,
                description=row.description,
                category_id=category_id,
                subcategory_id=subcategory_id,
                meta={"source": "canonical_csv"},
            ),
            Posting(
                posting_id=f"{transaction_id}:1",
                transaction_id=transaction_id,
                account_id=counterparty,
                posted_at=row.posted_at,
                amount=-row.amount,
                currency=account_currency,
                description=row.description,
            ),
        ])
    return postings


def standardize_canonical_csv(
    csv_text: str,
    account_id: str,
    account_currency: CurrencyCode,
    existing_categories: dict[str, Category],
    separator: str | None = None,
) -> CanonicalImportResult:
    """Map an arbitrary bank's CSV export onto canonical postings, guessing its column names and formats.

    Parameters
    ----------
    csv_text
        The raw CSV file contents, exactly as uploaded.
    account_id
        The real account these rows belong to.
    account_currency
        The account's own currency — every posting is recorded in it, since
        this importer has no per-row currency concept of its own.
    existing_categories
        Every category already in the store, keyed by `category_id` — a
        `Category`/`Subcategory` column value matching one by name (case-
        and trailing-whitespace-insensitively) reuses its id rather than
        creating a duplicate.
    separator
        The column separator to use, overriding auto-detection — set this
        after asking the user, once auto-detection has failed once.

    Returns
    -------
    CanonicalImportResult
        The postings, plus any newly-encountered categories/subcategories.

    Raises
    ------
    CanonicalCsvError
        If the separator can't be auto-detected (raises the narrower
        `CanonicalCsvSeparatorUnknownError`), the required columns aren't
        found, or too many rows fail to parse.
    """
    delimiter = separator or _detect_separator(csv_text)
    rows = list(csv_module.reader(io.StringIO(csv_text), delimiter=delimiter))
    if not rows:
        raise CanonicalCsvError("This file has no rows.")
    header, data_rows = rows[0], rows[1:]

    columns = _resolve_columns(header)
    parsed_rows = _parse_rows(data_rows, header, columns)

    new_categories: dict[str, Category] = {}
    top_category_ids = _resolve_top_categories(parsed_rows, existing_categories, new_categories)
    subcategory_ids = _resolve_subcategories(parsed_rows, top_category_ids, existing_categories, new_categories)
    postings = _build_postings(parsed_rows, account_id, account_currency, top_category_ids, subcategory_ids)

    frame = pl.DataFrame([posting.model_dump() for posting in postings], schema=Posting.polars_schema)
    return CanonicalImportResult(postings=frame.sort("posted_at", "posting_id"), new_categories=new_categories)
