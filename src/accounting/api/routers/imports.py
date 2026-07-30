"""Bank-statement import endpoints — mirrors `accounting.importers.*`."""

from __future__ import annotations

import json
import uuid
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from accounting.api.api_models import (
    CanonicalCategoryOverridesRequest,
    CanonicalImportPreview,
    CanonicalImportResult,
    CategorizationMatch,
    CategorizeFromFileApplyResult,
    CategorizeFromFilePreview,
    DepositMatch,
    DetectedAccount,
    DetectRequest,
    ImportResult,
    PaystubReconciliationResult,
    ProposedSplit,
    RebuildResult,
    SkippedRowsInfo,
    SupportedImportKind,
    SyncStatus,
)
from accounting.api.dependencies import _resolved_postings, state
from accounting.api.entities import Category, PostingSplitLeg
from accounting.dashboard.paystub import propose_posting_splits, reconcile_earnings_statement
from accounting.importers.canonical.csv import (
    CanonicalCsvError,
    CategoryOverrides,
    DateOrder,
    standardize_canonical_csv,
    standardize_canonical_excel,
)
from accounting.importers.categorize_from_file import (
    CategorizationMatch as CategorizationMatchData,
)
from accounting.importers.categorize_from_file import (
    ConfirmedCategorization,
    apply_categorize_from_file,
    preview_categorize_from_file,
)
from accounting.importers.detect import detect_bank_account
from accounting.importers.ingest import (
    UnsupportedImportError,
    ingest_canonical_csv,
    ingest_canonical_excel,
    ingest_csv,
    last_import_at,
    load_ledger,
    rebuild_from_raw_statements,
    supported_import_kinds,
)
from accounting.importers.paystub import extract_paystub_pdf_text, parse_earnings_statement_text
from accounting.models import CurrencyCode
from accounting.repositories.taxonomy import replace_categories
from accounting.taxonomy import seeded_accounts, seeded_categories
from db.current_user import get_current_user_id
from db.session import allow_background_runtime, get_db

router = APIRouter()


@router.post("/detect")
def post_detect(request: DetectRequest) -> DetectedAccount | None:
    """Guess the institution, account kind, and account id a CSV's header, filename, and first row describe.

    Returns
    -------
    DetectedAccount or None
        The best guess, or `None` if nothing matched.
    """
    detected = detect_bank_account(request.header, request.filename, request.first_data_row)
    return None if detected is None else DetectedAccount(**vars(detected))


@router.get("/supported-import-kinds")
def get_supported_import_kinds() -> list[SupportedImportKind]:
    """List every `(institution, account_kind)` pair with a registered CSV standardizer.

    Returns
    -------
    list[SupportedImportKind]
        Lets the UI flag any registered account that has no importer able
        to actually parse a statement for it.
    """
    return [
        SupportedImportKind(institution=institution, account_kind=account_kind)
        for institution, account_kind in sorted(supported_import_kinds())
    ]


@router.get("/sync-status")
def get_sync_status(user_id: Annotated[uuid.UUID, Depends(get_current_user_id)]) -> SyncStatus:
    """When a bank statement was most recently imported, across every institution and account.

    Returns
    -------
    SyncStatus
        `last_import_at` is `None` if nothing has ever been imported.
    """
    return SyncStatus(last_import_at=last_import_at(state.config, user_id))


@router.post("/import")
async def post_import(
    file: UploadFile,
    account_id: Annotated[str, Form()],
    *,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> ImportResult:
    """Archive and import the uploaded CSV against an already-registered account.

    Every account this endpoint is called with must already exist (see
    `AccountCreate`/`POST /accounts`), so the account's own
    `institution`/`kind`/`parent_account_id` are the source of truth for
    picking the importer — a form field that disagreed with the registered
    account would pick the wrong one.

    Returns
    -------
    ImportResult

    Raises
    ------
    HTTPException
        422 if `account_id` doesn't already exist; 400 if no importer exists
        for this institution/account-kind combination.
    """
    allow_background_runtime(session, user_id)
    account = seeded_accounts(session, user_id).get(account_id)
    if account is None:
        message = f"Account {account_id!r} does not exist — create this account first, then import."
        raise HTTPException(status_code=422, detail=message)

    # Try multiple encodings to handle files from different sources
    # (e.g., Excel exports on different systems use different encodings).
    # "utf-8-sig" strips a leading byte-order mark when the file has one
    # (common in CSVs exported by Excel).
    file_bytes = await file.read()
    csv_text: str | None = None

    # Try encodings in order: most common first (Excel UTF-8-sig or UTF-8),
    # then fallback to Windows/Mac formats (cp1252, iso-8859-1)
    for encoding in ["utf-8-sig", "utf-8", "cp1252", "iso-8859-1"]:
        try:
            csv_text = file_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue

    if csv_text is None:
        message = (
            f"Could not decode file '{file.filename}' with any supported encoding "
            "(utf-8-sig, utf-8, cp1252, iso-8859-1). "
            "Please ensure the file is a valid CSV or Excel export."
        )
        raise HTTPException(status_code=400, detail=message)

    try:
        result = ingest_csv(
            csv_text,
            # The registered account is the source of truth for which importer to
            # use — a form value that differs from it (e.g. the detector's own
            # institution spelling) would otherwise pick the wrong importer.
            account.institution,
            account.kind,
            account_id,
            state.config,
            session,
            user_id,
        )
    except UnsupportedImportError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return ImportResult(
        account_id=result.account_id,
        new_posting_count=result.new_posting_count,
        total_posting_count=result.total_posting_count,
        skipped_rows=SkippedRowsInfo(**vars(result.skipped_rows)) if result.skipped_rows else None,
    )


def _read_category_overrides(raw: str | None) -> CategoryOverrides | None:
    """Parse a JSON-encoded `CanonicalCategoryOverridesRequest` form field, or `None` if it wasn't sent.

    Returns
    -------
    CategoryOverrides or None
    """
    if not raw:
        return None
    parsed = CanonicalCategoryOverridesRequest.model_validate_json(raw)
    return CategoryOverrides(categories=parsed.categories, subcategories=parsed.subcategories)


def _read_confirmed_row_numbers(raw: str) -> set[int]:
    """Parse the JSON-encoded list of row numbers the client confirmed.

    Parsed before anything is written, not after. It used to be read *below*
    the commit that persists the file's new categories, so a malformed value
    left those categories in the database and then raised
    `json.JSONDecodeError` into a 500 — a write the caller was told had
    failed.

    Returns
    -------
    set[int]

    Raises
    ------
    HTTPException
        422 if the value is not a JSON array of integers.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        message = f"confirmed_row_numbers must be a JSON array of row numbers: {error}"
        raise HTTPException(status_code=422, detail=message) from error
    if not isinstance(parsed, list):
        message = "confirmed_row_numbers must be a JSON array of integers."
        raise HTTPException(status_code=422, detail=message)
    # `bool` is a subclass of `int`, so `[true]` would otherwise pass as `[1]`.
    if any(not isinstance(number, int) or isinstance(number, bool) for number in parsed):
        message = "confirmed_row_numbers must be a JSON array of integers."
        raise HTTPException(status_code=422, detail=message)
    return set(parsed)


@router.post("/import/canonical/preview")
async def post_canonical_import_preview(
    file: UploadFile,
    account_id: Annotated[str, Form()],
    currency: Annotated[str, Form()] = "USD",
    separator: Annotated[str | None, Form()] = None,
    date_order: Annotated[str, Form()] = "MDY",
    *,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CanonicalImportPreview:
    """Parse a canonical CSV/Excel file without persisting anything, to preview which categories it would create.

    Meant to run before `post_canonical_import`: the caller shows the
    returned categories/subcategories for the user to rename or merge, then
    submits the real import with `category_overrides` built from whatever
    they changed. Never touches the store or the ledger — every account
    field except `account_id`/`currency` is irrelevant here.

    Returns
    -------
    CanonicalImportPreview
        `new_categories` — every category/subcategory this file would
        create, for the caller to render as an editable list.

    Raises
    ------
    HTTPException
        422 if the file couldn't be parsed.
    """
    categories = seeded_categories(session, user_id)
    date_order_literal = cast("DateOrder", date_order)
    is_excel = (file.filename or "").lower().endswith((".xlsx", ".xls"))
    try:
        if is_excel:
            outcome = standardize_canonical_excel(
                await file.read(), account_id, cast("CurrencyCode", currency), categories, date_order_literal
            )
        else:
            csv_text = (await file.read()).decode("utf-8-sig")
            outcome = standardize_canonical_csv(
                csv_text, account_id, cast("CurrencyCode", currency), categories, separator, date_order_literal
            )
    except CanonicalCsvError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return CanonicalImportPreview(
        new_categories=[Category.from_domain(category) for category in outcome.new_categories.values()]
    )


@router.post("/import/canonical")
async def post_canonical_import(
    file: UploadFile,
    account_id: Annotated[str, Form()],
    separator: Annotated[str | None, Form()] = None,
    date_order: Annotated[str, Form()] = "MDY",
    category_overrides: Annotated[str | None, Form()] = None,
    *,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CanonicalImportResult:
    """Import the file, against an already-registered account, through the canonical fallback parser.

    Used when no dedicated standardizer exists for the account's own
    institution/kind (see `supported_import_kinds`) — the canonical parser guesses column
    names and date/amount formats instead of expecting an exact shape (see
    `importers.canonical.csv`). Both `.csv` and `.xlsx` files are accepted
    (dispatched on `file.filename`'s extension); an Excel workbook has every
    sheet checked for the expected columns, not just the first. Any category
    or subcategory named in a `Category`/`Subcategory` column is created
    automatically, unless `category_overrides` (a JSON-encoded
    `CanonicalCategoryOverridesRequest`) renames or merges it — typically
    collected via `post_canonical_import_preview` first.

    Returns
    -------
    CanonicalImportResult
        `new_categories` (each a full category, for a summary table).

    Raises
    ------
    HTTPException
        422 if `account_id` doesn't already exist, or if the file couldn't
        be parsed — the message explains what columns are supported, and
        (when the column separator couldn't be guessed) asks the user to
        pick one and retry with `separator` set. 400 if `account_id`'s kind
        has no independent importer (see `IMPORTABLE_ACCOUNT_KINDS`) — a
        `cash`/`loan`/`other_asset`/`income_source`/`expense_payee` account
        can never be canonically imported into, the same guarantee the
        bank-specific `/import` route already has by construction.
    """
    if account_id not in seeded_accounts(session, user_id):
        message = f"Account {account_id!r} does not exist — create this account first, then import."
        raise HTTPException(status_code=422, detail=message)

    date_order_literal = cast("DateOrder", date_order)
    overrides = _read_category_overrides(category_overrides)
    is_excel = (file.filename or "").lower().endswith((".xlsx", ".xls"))
    try:
        if is_excel:
            result = ingest_canonical_excel(
                await file.read(),
                account_id,
                state.config,
                session,
                user_id,
                date_order=date_order_literal,
                category_overrides=overrides,
            )
        else:
            # "utf-8-sig" strips a leading byte-order mark when the file has
            # one (common in CSVs exported by Excel) and is otherwise
            # identical to plain "utf-8" — without this, a BOM'd file's own
            # first header cell silently reads as "﻿Date" instead of
            # "Date", which then never matches any known column alias.
            csv_text = (await file.read()).decode("utf-8-sig")
            result = ingest_canonical_csv(
                csv_text,
                account_id,
                state.config,
                session,
                user_id,
                separator=separator,
                date_order=date_order_literal,
                category_overrides=overrides,
            )
    except CanonicalCsvError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except UnsupportedImportError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    return CanonicalImportResult(
        account_id=result.account_id,
        new_posting_count=result.new_posting_count,
        total_posting_count=result.total_posting_count,
        new_categories=[Category.from_domain(category) for category in result.new_categories.values()],
        skipped_rows=SkippedRowsInfo(**vars(result.skipped_rows)) if result.skipped_rows else None,
    )


def _match_response(match: CategorizationMatchData) -> CategorizationMatch:
    """Convert one internal `CategorizationMatch` dataclass into its API response model.

    Returns
    -------
    CategorizationMatch
    """
    return CategorizationMatch(**vars(match))


@router.post("/import/categorize-from-file/preview")
async def post_categorize_from_file_preview(
    file: UploadFile,
    account_ids: Annotated[str | None, Form()] = None,
    separator: Annotated[str | None, Form()] = None,
    date_order: Annotated[str, Form()] = "MDY",
    window_days: Annotated[int, Form()] = 5,
    *,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CategorizeFromFilePreview:
    """Match a categorized personal file against the ledger without persisting anything.

    Unlike `post_canonical_import_preview`, this never creates postings —
    see `importers.categorize_from_file`'s docstring for why. Meant to run
    before `post_categorize_from_file_apply`: the caller reviews each
    row's proposed match (and can drop any it disagrees with) before
    confirming.

    Parameters
    ----------
    file
        The CSV/Excel file to match — same column rules as a normal import (see `REQUIRED_COLUMNS_HELP`).
    account_ids
        Comma-separated account ids to restrict candidate postings to. Omit to search every account.
    separator
        The CSV column separator to use, overriding auto-detection. Ignored for Excel.
    date_order
        Whether an ambiguous, all-numeric date reads month-first (`"MDY"`) or day-first (`"DMY"`).
    window_days
        How many days apart a file row's date and a candidate posting's date can be and still match.

    Returns
    -------
    CategorizeFromFilePreview
        `matches` (one entry per file row; `posting_id` is `None` when nothing matched),
        `new_categories`, and `skipped_rows` (same shape as `post_canonical_import_preview`).

    Raises
    ------
    HTTPException
        422 if the file couldn't be parsed.
    """
    categories = seeded_categories(session, user_id)
    ledger = load_ledger(session, user_id)
    date_order_literal = cast("DateOrder", date_order)
    is_excel = (file.filename or "").lower().endswith((".xlsx", ".xls"))
    ids = [account_id.strip() for account_id in account_ids.split(",") if account_id.strip()] if account_ids else None
    try:
        preview = preview_categorize_from_file(
            await file.read(),
            is_excel=is_excel,
            existing_categories=categories,
            ledger=ledger,
            account_ids=ids,
            separator=separator,
            date_order=date_order_literal,
            window_days=window_days,
        )
    except CanonicalCsvError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return CategorizeFromFilePreview(
        matches=[_match_response(match) for match in preview.matches],
        new_categories=[Category.from_domain(category) for category in preview.new_categories.values()],
        skipped_rows=SkippedRowsInfo(**vars(preview.skipped_rows)) if preview.skipped_rows else None,
    )


@router.post("/import/categorize-from-file/apply")
async def post_categorize_from_file_apply(  # noqa: PLR0913
    file: UploadFile,
    confirmed_row_numbers: Annotated[str, Form()],
    account_ids: Annotated[str | None, Form()] = None,
    separator: Annotated[str | None, Form()] = None,
    date_order: Annotated[str, Form()] = "MDY",
    window_days: Annotated[int, Form()] = 5,
    category_overrides: Annotated[str | None, Form()] = None,
    *,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> CategorizeFromFileApplyResult:
    """Re-match the file (stateless, same as `post_canonical_import`'s preview/confirm split) and apply confirmed rows.

    Nothing from the preview call is cached server-side — the file is
    re-read and re-matched exactly the same way, then narrowed down to
    just `confirmed_row_numbers`. The category/subcategory actually
    applied always comes from this server-side re-match, never from the
    caller — a row's checkbox means "yes, apply what I was shown for this
    row," not "trust whatever category id I send." This only ever calls
    `put_posting_override`'s same underlying mechanism (see
    `apply_categorize_from_file`) — it can never create a posting, no
    matter what the file contains.

    Parameters
    ----------
    file
        The same file `post_categorize_from_file_preview` was called with.
    confirmed_row_numbers
        A JSON-encoded list of `row_number`s (from the preview response) the caller has reviewed and wants applied
        — typically every matched row the caller didn't uncheck.
    account_ids, separator, date_order, window_days
        Same as `post_categorize_from_file_preview` — must match, so the same rows resolve to the same postings.
    category_overrides
        A JSON-encoded `CanonicalCategoryOverridesRequest`, same as `post_canonical_import`'s.

    Returns
    -------
    CategorizeFromFileApplyResult
        `updated_posting_count` and `new_categories` (each newly-created category, if the file's own
        Category/Subcategory columns introduced any not already in the store).

    Raises
    ------
    HTTPException
        422 if the file couldn't be parsed.
    """
    categories = seeded_categories(session, user_id)
    ledger = load_ledger(session, user_id)
    date_order_literal = cast("DateOrder", date_order)
    is_excel = (file.filename or "").lower().endswith((".xlsx", ".xls"))
    ids = [account_id.strip() for account_id in account_ids.split(",") if account_id.strip()] if account_ids else None
    overrides = _read_category_overrides(category_overrides)
    # Validated before the category commit below — see `_read_confirmed_row_numbers`.
    wanted_row_numbers = _read_confirmed_row_numbers(confirmed_row_numbers)
    try:
        preview = preview_categorize_from_file(
            await file.read(),
            is_excel=is_excel,
            existing_categories=categories,
            ledger=ledger,
            account_ids=ids,
            separator=separator,
            date_order=date_order_literal,
            category_overrides=overrides,
            window_days=window_days,
        )
    except CanonicalCsvError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    if preview.new_categories:
        # Additive only: these are categories the uploaded file introduced, so
        # nothing already in the tree is rewritten and nothing is pruned.
        replace_categories(session, user_id, preview.new_categories.values(), prune=False)
        session.commit()

    to_apply = [
        ConfirmedCategorization(
            posting_id=match.posting_id,
            category_id=match.proposed_category_id,
            subcategory_id=match.proposed_subcategory_id,
        )
        for match in preview.matches
        if match.row_number in wanted_row_numbers and match.posting_id is not None
    ]
    updated_count = apply_categorize_from_file(session, to_apply, user_id)

    return CategorizeFromFileApplyResult(
        updated_posting_count=updated_count,
        new_categories=[Category.from_domain(category) for category in preview.new_categories.values()],
    )


@router.post("/import/paystub")
async def post_paystub_reconciliation(
    file: UploadFile,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> PaystubReconciliationResult:
    """Parse a paystub PDF and reconcile its deposits against real bank postings near pay day.

    Read-only — never applies anything automatically. `proposed_splits`
    suggests how to categorize each matched deposit (salary vs.
    reimbursement legs), for the user to review, edit, and confirm; use
    `PUT /postings/{posting_id}/split` (or `/override` for a single-leg
    proposal) to actually apply one.

    Returns
    -------
    PaystubReconciliationResult
        `matches` — one entry per deposit, `posting_id`/`account_id`
        `None` if unmatched; `proposed_splits` — one per matched deposit
        (see `dashboard.paystub.ProposedSplit`).

    Raises
    ------
    HTTPException
        400 if the PDF's text doesn't match this module's expected paystub layout — see `importers.paystub`'s
        docstring: its patterns are a generic starting point, not tuned against every payroll provider.
    """
    pdf_bytes = await file.read()
    text = extract_paystub_pdf_text(pdf_bytes)
    try:
        statement = parse_earnings_statement_text(text)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    postings = _resolved_postings(session, user_id)
    result = reconcile_earnings_statement(statement, postings, seeded_accounts(session, user_id))
    proposed_splits = propose_posting_splits(statement, result.matches)
    return PaystubReconciliationResult(
        statement=statement,
        matches=[
            DepositMatch(
                label=match.deposit.label,
                amount=match.deposit.amount,
                account_last4=match.deposit.account_last4,
                posting_id=match.posting_id,
                account_id=match.account_id,
            )
            for match in result.matches
        ],
        is_fully_matched=result.is_fully_matched,
        proposed_splits=[
            ProposedSplit(
                posting_id=proposal.posting_id,
                account_id=proposal.account_id,
                legs=[PostingSplitLeg(**vars(leg)) for leg in proposal.legs],
            )
            for proposal in proposed_splits
        ],
    )


@router.post("/rebuild")
def post_rebuild(
    session: Annotated[Session, Depends(get_db)], user_id: Annotated[uuid.UUID, Depends(get_current_user_id)]
) -> RebuildResult:
    """Recompute the whole posting ledger from every archived raw CSV.

    Returns
    -------
    RebuildResult

    Raises
    ------
    HTTPException
        404 if nothing has ever been imported.
    """
    allow_background_runtime(session, user_id)
    try:
        ledger = rebuild_from_raw_statements(state.config, session, user_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return RebuildResult(total_posting_count=len(ledger))
