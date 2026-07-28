"""Canonical CSV/Excel: the fallback importer for any bank with no dedicated standardizer.

Unlike every other importer here (see `importers.chase`/`importers.sofi`),
this one doesn't know its source's exact export shape ahead of time — it
looks for a handful of expected concepts (date, description, an amount) by
column-name matching (case- and whitespace-insensitive, but otherwise
exact — see `canonical.parsing.find_column`), parses whatever date/number
format it finds (see `canonical.parsing`), and auto-creates any
category/subcategory it's told about. When it can't make sense of a file,
it raises a clear, user-facing error, rather than guessing further.
"""

from __future__ import annotations

import csv as csv_module
import io
import operator
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import TYPE_CHECKING, Literal

import polars as pl

from accounting.importers.canonical.parsing import find_column, parse_amount_flexible, parse_date_flexible
from accounting.importers.common import row_hash
from accounting.ledger.frame import LEDGER_FRAME_SCHEMA
from accounting.models import Category, Posting
from accounting.store import (
    UNCATEGORIZED_EXPENSE_ACCOUNT_ID,
    UNCATEGORIZED_INCOME_ACCOUNT_ID,
    next_available_color,
    slugify,
)
from db.money import ZERO, Money

if TYPE_CHECKING:
    from datetime import date as date_type

    from accounting.models import CategoryClassification, CurrencyCode

# "MDY" (month before day, e.g. US 01/12 = Jan 12) or "DMY" (day before
# month, e.g. European 01/12 = 1 Dec) — only affects an all-numeric date
# that's genuinely ambiguous (both parts could be a valid month); a named
# month or a day > 12 is read the same either way.
DateOrder = Literal["MDY", "DMY"]

_DATE_ALIASES = {"date", "transaction date", "posting date", "posted date", "trans date"}
_DESCRIPTION_ALIASES = {"description", "memo", "narrative", "payee", "details", "transaction description"}
_AMOUNT_ALIASES = {"amount", "transaction amount", "value"}
_DEBIT_ALIASES = {"debit", "withdrawal", "money out", "debit amount"}
_CREDIT_ALIASES = {"credit", "deposit", "money in", "credit amount"}
_CATEGORY_ALIASES = {"category"}
_SUBCATEGORY_ALIASES = {"subcategory", "sub category", "sub-category"}
_CANDIDATE_SEPARATORS = [",", ";", "\t", "|"]
_MAX_UNPARSEABLE_ROW_FRACTION = 0.2

# Deliberately says nothing about date/amount formats — this is shown when
# the columns themselves can't be found at all, so the fix is a column
# rename, never a value format; format guidance belongs in the row-parsing
# failure message below instead, where it's actually the relevant fix.
REQUIRED_COLUMNS_HELP = (
    "Column names must match exactly. Expected a Date column, a Description column, and either "
    "an Amount column (negative for money out, positive for money in) or separate Debit and "
    "Credit columns. An optional Category column and an optional Subcategory column are also "
    "read, if present."
)

DATE_AMOUNT_FORMAT_HELP = (
    "Dates can be written in almost any common format (e.g. 2026-06-30, 06/30/2026, Jun 30 2026). "
    "Amounts can include a currency symbol and either US (1,234.56) or European (1.234,56) "
    "thousands separators."
)


class CanonicalCsvError(ValueError):
    """The canonical CSV importer couldn't make sense of a file — the message is shown to the user as-is."""


class CanonicalCsvSeparatorUnknownError(CanonicalCsvError):
    """The file's column separator couldn't be auto-detected — ask the user which one to use."""


@dataclass(frozen=True)
class SkippedRowsInfo:
    """Information about rows that couldn't be parsed."""

    total_rows: int
    skipped_count: int
    bad_dates: int  # Rows with unrecognized dates
    bad_amounts: int  # Rows with unrecognized amounts
    skipped_row_numbers: list[int]  # Which row numbers were skipped


@dataclass(frozen=True)
class CanonicalImportResult:
    """What one canonical CSV import produced."""

    postings: pl.DataFrame
    new_categories: dict[str, Category]
    skipped_rows: SkippedRowsInfo | None = None  # None if all rows parsed successfully


@dataclass(frozen=True)
class CategoryOverrides:
    """User-provided renames for the categories/subcategories a canonical import would otherwise auto-create.

    Two raw category names renamed to the same final name merge into one
    category; two raw subcategory names renamed to the same final name
    merge only when they resolve under the same parent category (see
    `_resolve_subcategories`) — the same subcategory name under two
    different categories is never merged with itself.
    """

    categories: dict[str, str] = field(default_factory=dict)
    subcategories: dict[str, dict[str, str]] = field(default_factory=dict)


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
    """One successfully parsed input row, before its category names are resolved against the store."""

    posted_at: datetime
    amount: Money
    description: str
    category_name: str | None
    subcategory_name: str | None
    row_number: int


def _detect_separator(csv_text: str) -> str:
    """Guess `csv_text`'s column separator via `csv.Sniffer`, falling back to counting candidates in the header.

    Returns
    -------
    str

    Raises
    ------
    CanonicalCsvSeparatorUnknownError
        If no candidate separator appears in the header at all.
    """
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
    """Match `header` against every known column alias, raising if a required column can't be found.

    Returns
    -------
    _Columns

    Raises
    ------
    CanonicalCsvError
        If a date, description, or amount column can't be found.
    """
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


def _row_amount(row_cell: dict[str, str], columns: _Columns) -> Money | None:
    """Read one row's signed amount from either its Amount column or its Debit/Credit pair.

    Returns
    -------
    Money or None
    """
    if columns.amount is not None:
        return parse_amount_flexible(row_cell[columns.amount])
    debit = parse_amount_flexible(row_cell[columns.debit]) if columns.debit else None
    credit = parse_amount_flexible(row_cell[columns.credit]) if columns.credit else None
    if debit is None and credit is None:
        return None
    return (credit or ZERO) - abs(debit or ZERO)


def _parse_rows(
    data_rows: list[list[str]], header: list[str], columns: _Columns, date_order: DateOrder = "MDY"
) -> tuple[list[_ParsedRow], SkippedRowsInfo | None]:
    """Parse every data row into a `_ParsedRow`, reporting which (if any) were skipped and why.

    Returns
    -------
    tuple[list[_ParsedRow], SkippedRowsInfo or None]

    Raises
    ------
    CanonicalCsvError
        If too large a fraction of rows fail to parse.
    """
    index = {name: position for position, name in enumerate(header)}
    dayfirst = date_order == "DMY"

    def cell(row: list[str], column: str) -> str:
        """Read `column`'s value from `row`, or `""` if the row is short that column.

        Returns
        -------
        str
        """
        position = index[column]
        return row[position] if position < len(row) else ""

    parsed_rows: list[_ParsedRow] = []
    bad_dates = 0
    bad_amounts = 0
    total_rows = 0
    skipped_row_numbers: list[int] = []
    for row_number, raw_row in enumerate(data_rows, start=2):
        if not raw_row or all(not value.strip() for value in raw_row):
            continue
        total_rows += 1
        row_cell = {name: cell(raw_row, name) for name in index}

        parsed_date: date_type | None = parse_date_flexible(row_cell[columns.date], dayfirst=dayfirst)
        if parsed_date is None:
            bad_dates += 1
            skipped_row_numbers.append(row_number)
            continue

        amount = _row_amount(row_cell, columns)
        if amount is None:
            bad_amounts += 1
            skipped_row_numbers.append(row_number)
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

    skipped_count = bad_dates + bad_amounts
    skip_info: SkippedRowsInfo | None = None
    if skipped_count > 0:
        skip_info = SkippedRowsInfo(
            total_rows=total_rows,
            skipped_count=skipped_count,
            bad_dates=bad_dates,
            bad_amounts=bad_amounts,
            skipped_row_numbers=skipped_row_numbers,
        )

    if total_rows == 0 or len(parsed_rows) < total_rows * (1 - _MAX_UNPARSEABLE_ROW_FRACTION):
        message = (
            f"Couldn't parse most of this file's rows — {bad_dates} row(s) had an unrecognized date and "
            f"{bad_amounts} row(s) had an unrecognized amount, out of {total_rows} data row(s). "
            f"{DATE_AMOUNT_FORMAT_HELP}"
        )
        raise CanonicalCsvError(message)
    return parsed_rows, skip_info


def _match_existing_category(name: str, parent_id: str | None, categories: dict[str, Category]) -> Category | None:
    """Find an existing category under `parent_id` whose name matches `name`, case/whitespace-insensitively.

    Returns
    -------
    Category or None
    """
    lowered = name.strip().lower()
    for category in categories.values():
        if category.parent_category_id == parent_id and category.name.strip().lower() == lowered:
            return category
    return None


def _unique_category_id(base_id: str, existing: dict[str, Category], created: dict[str, Category]) -> str:
    """Return `base_id`, or `base_id-2`/`base_id-3`/... if it already collides with an existing or new category.

    Returns
    -------
    str
    """
    if base_id not in existing and base_id not in created:
        return base_id
    suffix = 2
    while f"{base_id}-{suffix}" in existing or f"{base_id}-{suffix}" in created:
        suffix += 1
    return f"{base_id}-{suffix}"


def _classification_for(amounts: list[Money]) -> CategoryClassification:
    """Infer a new category's classification from the majority sign of the amounts filed under it.

    Returns
    -------
    CategoryClassification
    """
    negative = sum(1 for amount in amounts if amount < 0)
    return "expense" if negative >= len(amounts) - negative else "income"


def _resolve_top_categories(
    parsed_rows: list[_ParsedRow],
    existing_categories: dict[str, Category],
    new_categories: dict[str, Category],
    category_overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Assign every row's raw category name a category id, applying any user rename first.

    Returns
    -------
    dict[str, str]
        Every *raw* (as the file spelled it) category name, lowered, mapped
        to its resolved id — two raw names renamed to the same final name
        share one id here, which is what makes that rename a merge.
    """
    overrides = category_overrides or {}
    amounts_by_effective_key: dict[str, list[Money]] = {}
    display_name_by_effective_key: dict[str, str] = {}
    raw_keys_by_effective_key: dict[str, set[str]] = {}
    for row in parsed_rows:
        if not row.category_name:
            continue
        raw_name = row.category_name.strip()
        effective_name = overrides.get(raw_name, raw_name)
        effective_key = effective_name.lower()
        amounts_by_effective_key.setdefault(effective_key, []).append(row.amount)
        display_name_by_effective_key[effective_key] = effective_name
        raw_keys_by_effective_key.setdefault(effective_key, set()).add(raw_name.lower())

    category_ids_by_raw_key: dict[str, str] = {}
    for effective_key, amounts in amounts_by_effective_key.items():
        existing = _match_existing_category(effective_key, None, existing_categories)
        if existing is not None:
            category_id = existing.category_id
        else:
            display_name = display_name_by_effective_key[effective_key]
            classification = _classification_for(amounts)
            base_id = f"{classification}:{slugify(display_name)}"
            category_id = _unique_category_id(base_id, existing_categories, new_categories)
            used_colors = [c.color for c in existing_categories.values()] + [c.color for c in new_categories.values()]
            new_categories[category_id] = Category(
                category_id=category_id,
                name=display_name,
                classification=classification,
                color=next_available_color(used_colors),
            )
        for raw_key in raw_keys_by_effective_key[effective_key]:
            category_ids_by_raw_key[raw_key] = category_id
    return category_ids_by_raw_key


@dataclass(frozen=True)
class _SubcategoryGroups:
    """One row per resolved `(parent_id, effective_subcategory_key)` group, ready to create or match."""

    amounts_by_resolved_key: dict[tuple[str, str], list[Money]]
    display_name_by_resolved_key: dict[tuple[str, str], str]
    raw_keys_by_resolved_key: dict[tuple[str, str], set[tuple[str, str]]]


def _group_rows_by_resolved_subcategory(
    parsed_rows: list[_ParsedRow], top_category_ids: dict[str, str], overrides: dict[str, dict[str, str]]
) -> _SubcategoryGroups:
    """Bucket every row's raw subcategory name by its resolved (parent id, effective name) key, applying renames.

    Returns
    -------
    _SubcategoryGroups
    """
    groups = _SubcategoryGroups({}, {}, {})
    for row in parsed_rows:
        if not (row.category_name and row.subcategory_name):
            continue
        raw_top_name = row.category_name.strip()
        raw_sub_name = row.subcategory_name.strip()
        parent_id = top_category_ids[raw_top_name.lower()]
        effective_sub_name = overrides.get(raw_top_name, {}).get(raw_sub_name, raw_sub_name)
        resolved_key = (parent_id, effective_sub_name.lower())
        groups.amounts_by_resolved_key.setdefault(resolved_key, []).append(row.amount)
        groups.display_name_by_resolved_key[resolved_key] = effective_sub_name
        raw_key = (raw_top_name.lower(), raw_sub_name.lower())
        groups.raw_keys_by_resolved_key.setdefault(resolved_key, set()).add(raw_key)
    return groups


def _resolve_subcategories(
    parsed_rows: list[_ParsedRow],
    top_category_ids: dict[str, str],
    existing_categories: dict[str, Category],
    new_categories: dict[str, Category],
    subcategory_overrides: dict[str, dict[str, str]] | None = None,
) -> dict[tuple[str, str], str]:
    """Assign every row's raw (category, subcategory) name pair a subcategory id, applying any user rename first.

    A rename is scoped to one *resolved* parent category (after any
    category-level merge already folded several raw category names into
    one) — renaming two subcategories to the same name only merges them
    when they end up under the same parent; the same subcategory name
    under two different categories is never merged.

    Returns
    -------
    dict[tuple[str, str], str]
        Every *raw* `(category name, subcategory name)` pair, both lowered,
        mapped to its resolved subcategory id.
    """
    groups = _group_rows_by_resolved_subcategory(parsed_rows, top_category_ids, subcategory_overrides or {})

    subcategory_ids: dict[tuple[str, str], str] = {}
    for resolved_key in groups.amounts_by_resolved_key:
        parent_id, effective_sub_key = resolved_key
        existing = _match_existing_category(effective_sub_key, parent_id, existing_categories)
        if existing is not None:
            sub_id = existing.category_id
        else:
            parent = existing_categories.get(parent_id) or new_categories[parent_id]
            display_name = groups.display_name_by_resolved_key[resolved_key]
            sub_id = _unique_category_id(f"{parent_id}:{slugify(display_name)}", existing_categories, new_categories)
            used_colors = [c.color for c in existing_categories.values()] + [c.color for c in new_categories.values()]
            new_categories[sub_id] = Category(
                category_id=sub_id,
                name=display_name,
                classification=parent.classification,
                parent_category_id=parent_id,
                color=next_available_color(used_colors),
            )
        for raw_key in groups.raw_keys_by_resolved_key[resolved_key]:
            subcategory_ids[raw_key] = sub_id
    return subcategory_ids


@dataclass(frozen=True)
class ResolvedCategorizationRow:
    """One file row, fully parsed, with its Category/Subcategory columns already resolved to real ids.

    Used by the "categorize from file" flow (see `importers.categorize_from_file`),
    which stops here rather than going on to `_build_postings` — those rows
    are meant to be matched against transactions already in the ledger,
    never turned into new postings.
    """

    posted_at: datetime
    amount: Money
    description: str
    category_id: str | None
    subcategory_id: str | None
    row_number: int


def resolve_categorization_rows(
    header: list[str],
    data_rows: list[list[str]],
    existing_categories: dict[str, Category],
    date_order: DateOrder = "MDY",
    category_overrides: CategoryOverrides | None = None,
) -> tuple[list[ResolvedCategorizationRow], dict[str, Category], SkippedRowsInfo | None]:
    """Parse rows and resolve their Category/Subcategory columns, without building any postings.

    Shares every parsing and category matching-or-creation rule
    `_standardize_rows` uses (see `_resolve_columns`, `_parse_rows`,
    `_resolve_top_categories`, `_resolve_subcategories`) — the only
    difference is this stops one step short of `_build_postings`.

    Parameters
    ----------
    header
        The file's header row.
    data_rows
        Every other row, in file order.
    existing_categories
        Every category already in the store, keyed by `category_id`.
    date_order
        Whether an ambiguous, all-numeric date reads month-first or day-first.
    category_overrides
        User-provided renames for categories/subcategories this file would otherwise auto-create.

    Returns
    -------
    tuple[list[ResolvedCategorizationRow], dict[str, Category], SkippedRowsInfo | None]
        Every row ready to match against the ledger, any newly-encountered
        categories/subcategories, and info about any rows that failed to parse.
    """
    columns = _resolve_columns(header)
    parsed_rows, skip_info = _parse_rows(data_rows, header, columns, date_order)
    overrides = category_overrides or CategoryOverrides()

    new_categories: dict[str, Category] = {}
    top_category_ids = _resolve_top_categories(parsed_rows, existing_categories, new_categories, overrides.categories)
    subcategory_ids = _resolve_subcategories(
        parsed_rows, top_category_ids, existing_categories, new_categories, overrides.subcategories
    )

    resolved = [
        ResolvedCategorizationRow(
            posted_at=row.posted_at,
            amount=row.amount,
            description=row.description,
            category_id=(top_category_ids.get(row.category_name.strip().lower()) if row.category_name else None),
            subcategory_id=(
                subcategory_ids.get((row.category_name.strip().lower(), row.subcategory_name.strip().lower()))
                if row.category_name and row.subcategory_name
                else None
            ),
            row_number=row.row_number,
        )
        for row in parsed_rows
    ]
    return resolved, new_categories, skip_info


def read_tabular_rows(
    file_bytes: bytes, *, is_excel: bool, separator: str | None = None
) -> tuple[list[str], list[list[str]]]:
    """Read a CSV or Excel file into a header row and data rows, the shared first step of every canonical-shaped import.

    An Excel workbook often carries more than one sheet — the real
    transaction data plus, say, a pivot-table summary — so every sheet is
    checked in turn and the first whose header has the expected columns
    (see `_resolve_columns`) is used; the rest are assumed to be something
    else entirely.

    Parameters
    ----------
    file_bytes
        The raw file contents, exactly as uploaded.
    is_excel
        Whether to read this as an `.xlsx`/`.xls` workbook rather than CSV text.
    separator
        The CSV column separator to use, overriding auto-detection. Ignored for Excel.

    Returns
    -------
    tuple[list[str], list[list[str]]]
        The header row, and every data row.

    Raises
    ------
    CanonicalCsvError
        If no sheet (or the file itself) has the expected columns, or the separator can't be guessed.
    """
    if is_excel:
        sheets = _read_excel_sheets(file_bytes)
        if not sheets:
            raise CanonicalCsvError("This workbook has no sheets.")
        for header, data_rows in sheets.values():
            try:
                _resolve_columns(header)
            except CanonicalCsvError:
                continue
            return header, data_rows
        sheet_names = ", ".join(sheets)
        message = (
            f"Couldn't find the expected columns in any sheet of this workbook ({sheet_names}). {REQUIRED_COLUMNS_HELP}"
        )
        raise CanonicalCsvError(message)

    csv_text = file_bytes.decode("utf-8-sig")
    delimiter = separator or _detect_separator(csv_text)
    rows = list(csv_module.reader(io.StringIO(csv_text), delimiter=delimiter))
    if not rows:
        raise CanonicalCsvError("This file has no rows.")
    return rows[0], rows[1:]


def _build_postings(
    parsed_rows: list[_ParsedRow],
    account_id: str,
    account_currency: CurrencyCode,
    top_category_ids: dict[str, str],
    subcategory_ids: dict[tuple[str, str], str],
) -> list[Posting]:
    """Build each parsed row's two-posting pair against `account_id` and its resolved category/subcategory.

    Returns
    -------
    list[Posting]
    """
    postings: list[Posting] = []
    for row in parsed_rows:
        top_key = row.category_name.strip().lower() if row.category_name else None
        category_id = top_category_ids.get(top_key) if top_key else None
        subcategory_id = (
            subcategory_ids.get((top_key, row.subcategory_name.strip().lower()))
            if top_key and row.subcategory_name
            else None
        )
        # No row_number here on purpose — it's this file's line number, not a fact about the
        # transaction, so it changed on every re-export and made re-imports mint duplicate IDs.
        # Two rows that look identical on every real field are now told apart by
        # `_reassign_colliding_transaction_ids` at merge time instead. See its docstring in ingest.py.
        transaction_hash = row_hash(account_id, row.posted_at.isoformat(), f"{row.amount:.4f}", row.description)
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


def _standardize_rows(
    header: list[str],
    data_rows: list[list[str]],
    account_id: str,
    account_currency: CurrencyCode,
    existing_categories: dict[str, Category],
    date_order: DateOrder,
    category_overrides: CategoryOverrides | None = None,
) -> CanonicalImportResult:
    """Shared standardization pipeline behind `standardize_canonical_csv`/`_excel`, given already-split rows.

    Returns
    -------
    CanonicalImportResult
    """
    columns = _resolve_columns(header)
    parsed_rows, skip_info = _parse_rows(data_rows, header, columns, date_order)
    overrides = category_overrides or CategoryOverrides()

    new_categories: dict[str, Category] = {}
    top_category_ids = _resolve_top_categories(parsed_rows, existing_categories, new_categories, overrides.categories)
    subcategory_ids = _resolve_subcategories(
        parsed_rows, top_category_ids, existing_categories, new_categories, overrides.subcategories
    )
    postings = _build_postings(parsed_rows, account_id, account_currency, top_category_ids, subcategory_ids)

    frame = pl.DataFrame([posting.model_dump() for posting in postings], schema=LEDGER_FRAME_SCHEMA)
    return CanonicalImportResult(
        postings=frame.sort("posted_at", "posting_id"), new_categories=new_categories, skipped_rows=skip_info
    )


def standardize_canonical_csv(
    csv_text: str,
    account_id: str,
    account_currency: CurrencyCode,
    existing_categories: dict[str, Category],
    separator: str | None = None,
    date_order: DateOrder = "MDY",
    category_overrides: CategoryOverrides | None = None,
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
    date_order
        Whether an ambiguous, all-numeric date reads month-first (`"MDY"`,
        the default) or day-first (`"DMY"`) — see `parsing.parse_date_flexible`.
    category_overrides
        User-provided renames (and, implicitly, merges) for the categories
        and subcategories this file would otherwise auto-create — see
        `CategoryOverrides`. Typically collected via a preview/validate step
        before the real import (see `api.post_canonical_import_preview`).

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
    return _standardize_rows(
        header, data_rows, account_id, account_currency, existing_categories, date_order, category_overrides
    )


def _read_excel_sheets(file_bytes: bytes) -> dict[str, tuple[list[str], list[list[str]]]]:
    """Read every sheet of an Excel workbook into a plain header row + string data rows, like a parsed CSV.

    `infer_schema_length=0` keeps every cell as text (no numeric/date
    inference) — a date cell would otherwise arrive as a `datetime` and an
    amount as a `float`, bypassing `parsing.parse_date_flexible`/
    `parse_amount_flexible` entirely and losing the same forgiving parsing
    a CSV import gets.

    Parameters
    ----------
    file_bytes
        The raw `.xlsx` file contents, exactly as uploaded.

    Returns
    -------
    dict[str, tuple[list[str], list[list[str]]]]
        Each sheet's own header row and data rows, keyed by sheet name.
    """
    sheets = pl.read_excel(io.BytesIO(file_bytes), sheet_id=0, infer_schema_length=0)

    def rows_as_strings(frame: pl.DataFrame) -> list[list[str]]:
        """Render every cell as a string (`""` for null), matching the CSV path's own row shape.

        Returns
        -------
        list[list[str]]
        """
        return [["" if value is None else str(value) for value in row] for row in frame.iter_rows()]

    return {name: (list(frame.columns), rows_as_strings(frame)) for name, frame in sheets.items()}


def standardize_canonical_excel(
    file_bytes: bytes,
    account_id: str,
    account_currency: CurrencyCode,
    existing_categories: dict[str, Category],
    date_order: DateOrder = "MDY",
    category_overrides: CategoryOverrides | None = None,
) -> CanonicalImportResult:
    """Map an arbitrary bank's Excel export onto canonical postings, checking every sheet for the expected columns.

    A workbook often carries more than one sheet — the real transaction
    data plus, say, a pivot-table summary — so every sheet is checked in
    turn and the first whose header has the expected columns (see
    `_resolve_columns`) is used; the rest are assumed to be something else
    entirely and are silently skipped.

    Parameters
    ----------
    file_bytes
        The raw `.xlsx` file contents, exactly as uploaded.
    account_id
        The real account these rows belong to.
    account_currency
        The account's own currency — every posting is recorded in it.
    existing_categories
        Every category already in the store, keyed by `category_id`.
    date_order
        Whether an ambiguous, all-numeric date reads month-first or day-first.
    category_overrides
        User-provided renames (and, implicitly, merges) for the categories
        and subcategories this file would otherwise auto-create.

    Returns
    -------
    CanonicalImportResult
        The postings, plus any newly-encountered categories/subcategories.

    Raises
    ------
    CanonicalCsvError
        If no sheet's header has the expected columns, or the matching
        sheet's rows mostly fail to parse.
    """
    sheets = _read_excel_sheets(file_bytes)
    if not sheets:
        raise CanonicalCsvError("This workbook has no sheets.")
    for header, data_rows in sheets.values():
        try:
            _resolve_columns(header)
        except CanonicalCsvError:
            continue
        return _standardize_rows(
            header, data_rows, account_id, account_currency, existing_categories, date_order, category_overrides
        )

    sheet_names = ", ".join(sheets)
    message = (
        f"Couldn't find the expected columns in any sheet of this workbook ({sheet_names}). {REQUIRED_COLUMNS_HELP}"
    )
    raise CanonicalCsvError(message)
