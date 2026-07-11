"""Import orchestration: archive a CSV verbatim, standardize it, and merge the result into the posting ledger.

Mirrors `trades.brokers.ibkr.main`'s sync/rebuild split: `ingest_csv` is the
everyday path (archive, then merge just the new file), `rebuild_from_raw_statements`
recomputes the whole ledger from every archive on disk, for when the derived
cache needs to be thrown away and regenerated.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import polars as pl

from accounting.importers.canonical.csv import (
    CanonicalCsvError,
    SkippedRowsInfo,
    standardize_canonical_csv,
    standardize_canonical_excel,
)
from accounting.importers.chase.checking import standardize_chase_checking
from accounting.importers.chase.credit_card import standardize_chase_credit_card
from accounting.importers.sofi.csv import standardize_sofi_checking, standardize_sofi_savings
from accounting.importers.sofi.statement_pdf import standardize_sofi_statement_pdf
from accounting.models import Posting
from accounting.store import load_store, normalize_categories, save_store
from accounting.utils.io_utils import write_csv_atomic
from accounting.utils.statement_archive import DEFAULT_USER_ID, StatementArchive

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from accounting.config import AccountingConfig
    from accounting.importers.canonical.csv import CanonicalImportResult, CategoryOverrides, DateOrder
    from accounting.models import Account, Category
    from accounting.store import AccountingStore

_Fingerprint = tuple[str, datetime, float, str]
"""`(account_id, posted_at, amount, description)` — two transactions with the same fingerprint look identical."""

_STANDARDIZERS: dict[tuple[str, str], Callable[[str, str], pl.DataFrame]] = {
    ("Chase", "checking"): standardize_chase_checking,
    ("Chase", "credit_card"): standardize_chase_credit_card,
    ("SoFi", "checking"): standardize_sofi_checking,
    ("SoFi", "savings"): standardize_sofi_savings,
    # A vault's raw CSV is structurally identical to the newer SoFi
    # savings/checking export (see `importers.sofi.csv`) — registered
    # separately only because `account_kind` for a vault account is
    # itself `"vault"`, not `"savings"`.
    ("SoFi", "vault"): standardize_sofi_savings,
}

_SOFI_STATEMENT_PDF_ACCOUNT_KIND = "statement_pdf"


def supported_import_kinds() -> set[tuple[str, str]]:
    """Every `(institution, account_kind)` pair with a registered CSV standardizer.

    Returns
    -------
    set[tuple[str, str]]
        Exactly the keys of `_STANDARDIZERS` — used to flag accounts in the
        UI that were created (manually, or discovered from an older import)
        without any code path able to actually parse a statement for them.
    """
    return set(_STANDARDIZERS.keys())


class UnsupportedImportError(ValueError):
    """No standardizer exists for the given institution/account-kind combination."""


@dataclass(frozen=True)
class IngestResult:
    """What happened when one CSV was ingested."""

    account_id: str
    new_posting_count: int
    total_posting_count: int
    skipped_rows: SkippedRowsInfo | None = None  # Rows that couldn't be parsed (bank-specific or canonical fallback)


def load_ledger(config: AccountingConfig) -> pl.DataFrame:
    """Load the full posting ledger.

    `meta` round-trips through the CSV as JSON, the same reason
    `trades.brokers.ibkr.main.load_ledger` does it — a dict has no native
    CSV type. `tag_ids` round-trips as a `|`-joined string instead (a
    native polars list expression, not a JSON encode/decode, since
    `map_elements` on an all-empty-list column has a known edge case where
    polars runs the callback on the whole series rather than per-element);
    splitting an empty string back would otherwise produce `[""]` instead
    of `[]`, so that case is filtered out explicitly. `*_id` columns are
    forced to string dtype so an all-numeric id never round-trips as an
    integer.

    Parameters
    ----------
    config
        Application configuration; `config.ledger_csv_path` is read.

    Returns
    -------
    polars.DataFrame
        The ledger, or an empty frame if nothing has been imported yet.
    """
    if not config.ledger_csv_path.exists():
        return pl.DataFrame(schema=Posting.polars_schema)
    ledger = pl.read_csv(
        config.ledger_csv_path,
        schema_overrides={"posting_id": pl.Utf8, "transaction_id": pl.Utf8, "account_id": pl.Utf8},
        try_parse_dates=True,
    )
    return ledger.with_columns(
        pl.col("meta").map_elements(json.loads, return_dtype=pl.Object),
        tag_ids=pl.col("tag_ids").str.split("|").list.eval(pl.element().filter(pl.element() != "")),  # noqa: PLC1901
    )


def _write_ledger(ledger: pl.DataFrame, config: AccountingConfig) -> None:
    serialized = ledger.with_columns(
        pl.col("meta").map_elements(json.dumps, return_dtype=pl.Utf8), tag_ids=pl.col("tag_ids").list.join("|")
    )
    write_csv_atomic(serialized, config.ledger_csv_path)


def remap_ledger_category_ids(id_remap: dict[str, str], config: AccountingConfig) -> None:
    """Repoint every posting's `category_id`/`subcategory_id` after a category merge, in place.

    A canonical import can bake a category straight onto a posting at
    import time (from that file's own Category/Subcategory columns) rather
    than only through a rule or a manual override — so merging two
    categories (see `store.plan_category_rename`) needs to fix the ledger
    cache itself, not just the store's own rules/patterns/budgets (see
    `store.remap_category_ids`). `.replace(...)` leaves any id not in
    `id_remap` (including `null`) unchanged.

    Parameters
    ----------
    id_remap
        `old_id -> new_id`, as returned by `store.plan_category_rename` —
        a no-op when empty.
    config
        Application configuration; `config.ledger_csv_path` is read and rewritten.
    """
    if not id_remap:
        return
    ledger = load_ledger(config)
    ledger = ledger.with_columns(pl.col("category_id").replace(id_remap), pl.col("subcategory_id").replace(id_remap))
    _write_ledger(ledger, config)


def _fingerprint(row: dict[str, Any]) -> _Fingerprint:
    """Build the (account, date, amount, description) tuple that makes two transactions look identical.

    Parameters
    ----------
    row
        One posting, as a plain dict (e.g. from `DataFrame.to_dicts()`).

    Returns
    -------
    _Fingerprint
        Two transactions with the same fingerprint look the same on paper, whether or not they're the same one.
    """
    return (row["account_id"], row["posted_at"], row["amount"], row["description"])


def _occurrence_suffix(transaction_id: str) -> int:
    """Read back which copy of a duplicate a transaction_id is, so duplicates sort oldest-first.

    Parameters
    ----------
    transaction_id
        A plain id, or one already reassigned to `...#N` by `_reassign_colliding_transaction_ids`.

    Returns
    -------
    int
        0 for a plain id (the first copy ever seen), N for one ending in `#N`.
    """
    if "#" not in transaction_id:
        return 0
    return int(transaction_id.rsplit("#", 1)[1])


def _existing_ids_by_fingerprint(existing: pl.DataFrame) -> dict[_Fingerprint, list[str]]:
    """Map every fingerprint already in the ledger to the transaction_ids that share it, oldest first.

    Parameters
    ----------
    existing
        The ledger as it stands before this import.

    Returns
    -------
    dict[_Fingerprint, list[str]]
        Fingerprint (see `_fingerprint`) to the transaction_ids of every existing transaction that
        looks like it, ordered oldest-first so new duplicates match the longest-standing one first.
    """
    if existing.is_empty():
        return {}
    grouped: dict[_Fingerprint, list[str]] = defaultdict(list)
    for row in existing.filter(pl.col("posting_id").str.ends_with(":0")).to_dicts():
        grouped[_fingerprint(row)].append(row["transaction_id"])
    for transaction_ids in grouped.values():
        transaction_ids.sort(key=_occurrence_suffix)
    return grouped


def _reassign_colliding_transaction_ids(existing: pl.DataFrame, new: pl.DataFrame) -> pl.DataFrame:
    """Give same-day duplicate transactions their own id instead of letting them overwrite each other.

    Every importer builds a transaction's id by hashing the facts that
    describe it — account, date, amount, description (see `row_hash`'s
    docstring for why it's only ever those facts, never something like
    which line of the file the row was on). That's the right call, but it
    has one side effect: two transactions that genuinely look identical —
    two coffees bought at the same place on the same morning — hash to the
    exact same id. Without this step, the second one would silently
    overwrite the first in the ledger instead of being added alongside it.

    The fix is to count instead of just hash. Say the ledger already has
    one "Starbucks $5, July 3rd", and the statement you just imported has
    two rows that also look like "Starbucks $5, July 3rd" (a real second
    coffee that day, not a duplicate upload). The first of those two new
    rows matches the one already in the ledger, so it simply reuses that
    same id — nothing changes there. The second new row has nothing left to
    match, so it's treated as genuinely new: it gets its own id (the same
    id with `#2` appended) and is added as a second transaction. The ledger
    ends up with two coffees, not one.

    This also makes re-imports safe no matter what order the bank lists
    rows in. Re-importing that same statement next week, even if the bank
    happens to print those two rows in the opposite order this time, still
    produces the same result — because rows are matched by how many share a
    fingerprint, not by their position in the file.

    Parameters
    ----------
    existing
        The ledger as it stands before this import.
    new
        Freshly parsed postings from the file just uploaded, straight out of the importer.

    Returns
    -------
    polars.DataFrame
        `new`, with any colliding transaction_id/posting_id reassigned so distinct
        same-day transactions never collide, and re-imports of the same transaction stay stable.
    """
    if new.is_empty():
        return new

    existing_ids_by_fingerprint = _existing_ids_by_fingerprint(existing)

    rows = new.to_dicts()
    real_leg_indexes_by_id: dict[str, list[int]] = defaultdict(list)
    counterparty_leg_indexes_by_id: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        if row["posting_id"].endswith(":0"):
            real_leg_indexes_by_id[row["transaction_id"]].append(index)
        else:
            counterparty_leg_indexes_by_id[row["transaction_id"]].append(index)

    for original_id, real_indexes in real_leg_indexes_by_id.items():
        existing_ids = existing_ids_by_fingerprint.get(_fingerprint(rows[real_indexes[0]]), [])
        counterparty_indexes = counterparty_leg_indexes_by_id.get(original_id, [])

        for occurrence, real_index in enumerate(real_indexes):
            if occurrence < len(existing_ids):
                final_id = existing_ids[occurrence]
            elif occurrence == 0:
                final_id = original_id
            else:
                final_id = f"{original_id}#{occurrence + 1}"

            rows[real_index]["transaction_id"] = final_id
            rows[real_index]["posting_id"] = f"{final_id}:0"
            if occurrence < len(counterparty_indexes):
                counterparty_index = counterparty_indexes[occurrence]
                rows[counterparty_index]["transaction_id"] = final_id
                rows[counterparty_index]["posting_id"] = f"{final_id}:1"

    return pl.DataFrame(rows, schema=Posting.polars_schema)


def _merge_ledger(existing: pl.DataFrame, new: pl.DataFrame) -> pl.DataFrame:
    reconciled = _reassign_colliding_transaction_ids(existing, new)
    return (
        pl
        .concat([existing, reconciled], how="vertical")
        .unique(subset="posting_id", keep="last")
        .sort("posted_at", "posting_id")
    )


def _archive_raw_statement(
    institution: str, account_id: str, data: bytes, config: AccountingConfig, suffix: str = "csv"
) -> None:
    archive = StatementArchive(config.raw_statement_dir, f"statements/{DEFAULT_USER_ID}")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    archive.write(f"{institution}/{account_id}/{timestamp}.{suffix}", data)


def _merge_discovered_accounts(discovered: dict[str, Account], config: AccountingConfig) -> None:
    """Add newly-seen accounts to the store, and refresh `meta` on ones already known.

    Unlike a rule's counterparty (only ever created the first time it's
    matched), a statement PDF's checking/savings/vault accounts are
    already fully known every time it's parsed — re-importing a later
    month must keep updating `meta["apy_pct"]` without ever touching a
    user-edited `name`.

    Parameters
    ----------
    discovered
        Every account this statement describes, keyed by `account_id`.
    config
        Application configuration; the store is read and, if anything
        changed, written back.
    """
    store = load_store(config)
    accounts = dict(store.accounts)
    changed = False
    for account_id, discovered_account in discovered.items():
        existing = accounts.get(account_id)
        if existing is None:
            accounts[account_id] = discovered_account
            changed = True
        elif existing.meta != {**existing.meta, **discovered_account.meta}:
            accounts[account_id] = existing.model_copy(update={"meta": {**existing.meta, **discovered_account.meta}})
            changed = True
    if changed:
        save_store(store.model_copy(update={"accounts": accounts}), config)


def _fallback_to_canonical_csv(
    csv_text: str, account_id: str, config: AccountingConfig
) -> tuple[pl.DataFrame, SkippedRowsInfo | None]:
    """Standardize via the canonical CSV importer, merging any newly-created categories into the store.

    Parameters
    ----------
    csv_text
        The raw CSV file contents.
    account_id
        The account these rows belong to.
    config
        Application configuration.

    Returns
    -------
    tuple[pl.DataFrame, SkippedRowsInfo | None]
        The standardized postings, and info about any skipped rows.
    """
    store = load_store(config)
    canonical_result = standardize_canonical_csv(csv_text, account_id, "USD", store.categories)
    if canonical_result.new_categories:
        merged_categories = normalize_categories({**store.categories, **canonical_result.new_categories})
        save_store(store.model_copy(update={"categories": merged_categories}), config)
    return canonical_result.postings, canonical_result.skipped_rows


def ingest_csv(
    csv_text: str, institution: str, account_kind: str, account_id: str, config: AccountingConfig
) -> IngestResult:
    """Archive one uploaded CSV verbatim, standardize it, and merge the result into the ledger.

    The raw file is saved before parsing even starts, so a parse failure
    never loses the upload — per `docs/trades/architecture.md`'s "cache raw,
    derive everything else" rule, applied here the same way it already is
    for IBKR statements.

    If the bank-specific standardizer fails (validation error, parsing error),
    automatically falls back to the canonical CSV importer, which is more
    forgiving about column names and formats. This handles cases where the
    user's file doesn't match the expected bank export format exactly.

    Parameters
    ----------
    csv_text
        The raw CSV file contents, exactly as uploaded.
    institution
        `"Chase"` or `"SoFi"` — must have a registered standardizer.
    account_kind
        `"checking"`, `"credit_card"`, or `"savings"`.
    account_id
        The account these rows belong to.
    config
        Application configuration; `config.raw_statement_dir` and `config.ledger_csv_path` are used.

    Returns
    -------
    IngestResult
        How many postings were newly added.

    Raises
    ------
    UnsupportedImportError
        If no standardizer is registered for this institution/account-kind pair.
    ValueError
        If both the bank-specific and canonical standardizers fail to parse the file.
    """
    standardizer = _STANDARDIZERS.get((institution, account_kind))
    if standardizer is None:
        message = f"No importer for institution={institution!r}, account_kind={account_kind!r}."
        raise UnsupportedImportError(message)

    _archive_raw_statement(institution, account_id, csv_text.encode("utf-8"), config)

    skip_info: SkippedRowsInfo | None = None
    # Try the bank-specific standardizer first; if it fails, fall back to canonical CSV
    try:
        new_postings = standardizer(csv_text, account_id)
    except (ValueError, RuntimeError) as error:
        # Bank-specific standardizer failed (likely validation, parsing, or format mismatch).
        # Fall back to canonical CSV importer, which is more forgiving about column names/formats.
        try:
            new_postings, skip_info = _fallback_to_canonical_csv(csv_text, account_id, config)
        except (CanonicalCsvError, ValueError, KeyError, RuntimeError) as canonical_error:
            # Both standardizers failed; raise the original bank error with fallback note
            message = (
                f"Could not parse file with {institution} {account_kind} format: {error}\n\n"
                f"Also tried canonical CSV importer but it failed: {canonical_error}\n\n"
                f"Please check your file format and try again."
            )
            raise ValueError(message) from error

    existing = load_ledger(config)
    merged = _merge_ledger(existing, new_postings)
    _write_ledger(merged, config)

    return IngestResult(
        account_id=account_id,
        new_posting_count=len(merged) - len(existing),
        total_posting_count=len(merged),
        skipped_rows=skip_info,
    )


@dataclass(frozen=True)
class CanonicalIngestResult:
    """What happened when one CSV was ingested through the canonical fallback importer."""

    account_id: str
    new_posting_count: int
    total_posting_count: int
    new_categories: dict[str, Category]
    skipped_rows: SkippedRowsInfo | None = None  # Rows that couldn't be parsed


def ingest_canonical_csv(
    csv_text: str,
    account_id: str,
    config: AccountingConfig,
    separator: str | None = None,
    date_order: DateOrder = "MDY",
    category_overrides: CategoryOverrides | None = None,
) -> CanonicalIngestResult:
    """Archive one uploaded CSV verbatim, standardize it with the canonical fallback parser, and merge the result.

    Unlike `ingest_csv`, this doesn't need a registered institution/account-
    kind standardizer — see `importers.canonical.csv.standardize_canonical_csv`
    for how it guesses column names and formats instead. Any category or
    subcategory named in the file that doesn't already exist is created and
    persisted here, the same way a rule creates a new counterparty account
    the first time it matches.

    Parameters
    ----------
    csv_text
        The raw CSV file contents, exactly as uploaded.
    account_id
        The account these rows belong to — must already be registered.
    config
        Application configuration; `config.raw_statement_dir` and `config.ledger_csv_path` are used.
    separator
        The column separator to use, overriding auto-detection.
    date_order
        Whether an ambiguous, all-numeric date reads month-first or day-first.
    category_overrides
        User-provided renames (and, implicitly, merges) for the categories
        and subcategories this file would otherwise auto-create.

    Returns
    -------
    CanonicalIngestResult
        How many postings were newly added, and any categories created.
    """
    store = load_store(config)
    account = store.accounts[account_id]

    _archive_raw_statement(account.institution, account_id, csv_text.encode("utf-8"), config)
    outcome = standardize_canonical_csv(
        csv_text, account_id, account.currency, store.categories, separator, date_order, category_overrides
    )
    return _apply_canonical_outcome(outcome, account_id, store, config)


def ingest_canonical_excel(
    file_bytes: bytes,
    account_id: str,
    config: AccountingConfig,
    date_order: DateOrder = "MDY",
    category_overrides: CategoryOverrides | None = None,
) -> CanonicalIngestResult:
    """Archive one uploaded Excel workbook verbatim, standardize it, and merge the result.

    Mirrors `ingest_canonical_csv` — see `importers.canonical.csv.standardize_canonical_excel`
    for how it picks which sheet holds the transaction data.

    Parameters
    ----------
    file_bytes
        The raw `.xlsx` file contents, exactly as uploaded.
    account_id
        The account these rows belong to — must already be registered.
    config
        Application configuration; `config.raw_statement_dir` and `config.ledger_csv_path` are used.
    date_order
        Whether an ambiguous, all-numeric date reads month-first or day-first.
    category_overrides
        User-provided renames (and, implicitly, merges) for the categories
        and subcategories this file would otherwise auto-create.

    Returns
    -------
    CanonicalIngestResult
        How many postings were newly added, and any categories created.
    """
    store = load_store(config)
    account = store.accounts[account_id]

    _archive_raw_statement(account.institution, account_id, file_bytes, config, suffix="xlsx")
    outcome = standardize_canonical_excel(
        file_bytes, account_id, account.currency, store.categories, date_order, category_overrides
    )
    return _apply_canonical_outcome(outcome, account_id, store, config)


def _apply_canonical_outcome(
    outcome: CanonicalImportResult, account_id: str, store: AccountingStore, config: AccountingConfig
) -> CanonicalIngestResult:
    if outcome.new_categories:
        merged_categories = normalize_categories({**store.categories, **outcome.new_categories})
        save_store(store.model_copy(update={"categories": merged_categories}), config)

    existing = load_ledger(config)
    merged = _merge_ledger(existing, outcome.postings)
    _write_ledger(merged, config)

    return CanonicalIngestResult(
        account_id=account_id,
        new_posting_count=len(merged) - len(existing),
        total_posting_count=len(merged),
        new_categories=outcome.new_categories,
        skipped_rows=outcome.skipped_rows,
    )


def last_import_at(config: AccountingConfig) -> datetime | None:
    """Find the most recent moment any statement was archived, across every institution and account.

    Reads the timestamp encoded in each archive's own filename (see
    `_raw_statement_path`) rather than the file's on-disk mtime, so a
    copy/rsync of `data/` onto another machine can't make an old import
    look freshly done.

    Parameters
    ----------
    config
        Application configuration; `config.raw_statement_dir` is read.

    Returns
    -------
    datetime.datetime or None
        Timezone-aware (UTC), or `None` if nothing has ever been imported.
    """
    archive = StatementArchive(config.raw_statement_dir, f"statements/{DEFAULT_USER_ID}")
    relative_paths = [
        *archive.list_relative_paths("*/*/*.csv"),
        *archive.list_relative_paths(f"SoFi/{_SOFI_STATEMENT_PDF_ACCOUNT_KIND}/*.pdf"),
    ]
    timestamps: list[datetime] = []
    for relative_path in relative_paths:
        filename = relative_path.rsplit("/", 1)[-1]
        stem = filename.rsplit(".", 1)[0]
        try:
            timestamps.append(datetime.strptime(stem, "%Y%m%dT%H%M%S%f").replace(tzinfo=UTC))
        except ValueError:
            continue
    return max(timestamps) if timestamps else None


def rebuild_from_raw_statements(config: AccountingConfig) -> pl.DataFrame:
    """Recompute the whole ledger from every archived raw CSV.

    Discards whatever ledger is currently on disk. The account id and kind
    for each archive are recovered from its own directory name
    (`raw_statement_dir/{institution}/{account_id}/...`) and `account_id`'s
    own `{institution}:{kind}:{number}` shape — no separate registry of
    "which files belong to which account" is needed.

    Parameters
    ----------
    config
        Application configuration; `config.raw_statement_dir` is read.

    Returns
    -------
    polars.DataFrame
        The rebuilt ledger.

    Raises
    ------
    FileNotFoundError
        If no raw statements have ever been archived.
    UnsupportedImportError
        If an archived directory's institution/account-kind has no registered standardizer.
    """
    archive = StatementArchive(config.raw_statement_dir, f"statements/{DEFAULT_USER_ID}")
    csv_relative_paths = archive.list_relative_paths("*/*/*.csv")
    pdf_relative_paths = archive.list_relative_paths(f"SoFi/{_SOFI_STATEMENT_PDF_ACCOUNT_KIND}/*.pdf")
    if not csv_relative_paths and not pdf_relative_paths:
        message = f"No archived raw statements under {config.raw_statement_dir}"
        raise FileNotFoundError(message)

    frames = [pl.DataFrame(schema=Posting.polars_schema)]
    for relative_path in csv_relative_paths:
        institution, account_id, _filename = relative_path.split("/")
        account_kind = account_id.split(":")[1]
        standardizer = _STANDARDIZERS.get((institution, account_kind))
        if standardizer is None:
            message = f"No importer for institution={institution!r}, account_kind={account_kind!r}."
            raise UnsupportedImportError(message)
        frames.append(standardizer(archive.read(relative_path).decode("utf-8"), account_id))

    # New statement-PDF imports are retired (SoFi CSV now covers checking,
    # savings, and vaults — see `importers.sofi.csv`) — there is no upload
    # path left that writes into `pdf_relative_paths` going forward. But any
    # PDF archived by a past import still needs to be re-derived here, or a
    # rebuild would silently drop those postings and orphan their
    # categorization (see `models.ManualOverride`, keyed by posting_id).
    discovered_accounts: dict[str, Account] = {}
    for relative_path in pdf_relative_paths:
        pdf_postings, pdf_accounts = standardize_sofi_statement_pdf(archive.read(relative_path))
        frames.append(pdf_postings)
        discovered_accounts.update(pdf_accounts)
    if discovered_accounts:
        _merge_discovered_accounts(discovered_accounts, config)

    ledger = _merge_ledger(frames[0], pl.concat(frames[1:], how="vertical"))
    _write_ledger(ledger, config)
    return ledger
