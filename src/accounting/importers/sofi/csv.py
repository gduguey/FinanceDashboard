"""Map every SoFi CSV export shape (checking, savings, or a vault) onto canonical postings.

Two distinct export shapes exist (see `importers.sofi.models`): an older,
per-account-kind shape (`SofiRow`) with no vault support at all, and a
newer, wider shape (`SofiCsvRow`) that covers checking, savings, and
vaults alike — SoFi previously only ever exposed vault history via the
monthly statement PDF, whose importer has since been retired and
deleted; this format lets a vault be dragged straight in as a CSV
instead. `standardize_sofi_checking`/`standardize_sofi_savings` dispatch
to whichever shape a given file is actually in, so both are safe to keep
registered against their account kind regardless of which shape the user
happens to export.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from accounting.importers.common import RawLeg, posting_pair, postings_to_frame, row_hash
from accounting.importers.sofi.models import SofiCsvRow, SofiRow
from accounting.taxonomy import UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID

if TYPE_CHECKING:
    import polars as pl

    from accounting.models import Posting

HEADER = (
    "Authorized Date",
    "Posted Date",
    "Status",
    "Account Name",
    "Description",
    "Primary Category",
    "Detailed Category",
    "Amount",
)

_ACCOUNT_NAME = re.compile(r"^(?P<label>.+?)\s*\*{2,3}(?P<last4>\d{4})$")
_CHECKING_KEYWORDS = ("checking",)
_SAVINGS_KEYWORDS = ("saving", "hysa")

# Every already-imported posting's `posting_id`/`transaction_id` is derived
# from this tag (see `importers.common.posting_pair`) and persisted
# verbatim in the ledger cache and in `ManualOverride`/`PostingSplit`,
# which are keyed by posting_id in their own files. Changing it would
# silently orphan every categorization ever made on a wide-shape SoFi
# import — so it stays frozen even though the module/function names
# around it have dropped the "v2" they used to carry.
_WIDE_CSV_SOURCE = "sofi-csv-v2"


@dataclass(frozen=True)
class ParsedAccountName:
    """What one row's `Account Name` field says about which account it belongs to.

    `kind` is `None` when `label` names neither "checking" nor "savings"/
    "hysa" — meaning `label` is itself a vault's own name (e.g. "Emergency
    Fund"), not a description of the parent account.
    """

    label: str
    last4: str
    kind: Literal["checking", "savings"] | None


def is_sofi_csv(csv_text: str) -> bool:
    """Check whether `csv_text` is in the newer, wider shape, by its header line.

    Returns
    -------
    bool
    """
    first_line = csv_text.splitlines()[0] if csv_text else ""
    return tuple(column.strip() for column in first_line.split(",")) == HEADER


def parse_account_name(account_name: str) -> ParsedAccountName | None:
    """Parse one row's `Account Name` field into a label, last-4 digits, and a checking/savings guess.

    Parameters
    ----------
    account_name
        e.g. `"Emergency Fund ***3680"`, `"SoFi HYSA ***3680"`, `"Checking ***9169"`.

    Returns
    -------
    ParsedAccountName or None
        `None` if `account_name` doesn't end in `***XXXX`-style digits at all.
    """
    match = _ACCOUNT_NAME.match(account_name.strip())
    if match is None:
        return None
    label = match["label"].strip()
    lowered = label.lower()
    kind: Literal["checking", "savings"] | None = None
    if any(keyword in lowered for keyword in _CHECKING_KEYWORDS):
        kind = "checking"
    elif any(keyword in lowered for keyword in _SAVINGS_KEYWORDS):
        kind = "savings"
    return ParsedAccountName(label=label, last4=match["last4"], kind=kind)


def _standardize_sofi_wide_csv(csv_text: str, account_id: str) -> pl.DataFrame:
    """Map the newer, wider SoFi export's rows onto postings against `account_id`.

    Every row — including an "Interest" row and a vault's own "Transfer
    To/From Savings" row — lands on the usual uncategorized placeholder,
    exactly like every other importer in this module. Earlier revisions
    special-cased both: an "Interest" row's category was stamped on
    immediately, and a vault's savings-transfer row was pointed straight at
    `parent_account_id`. That second one silently double-booked every
    transfer once the parent savings account's own export was also
    imported — its own row for the same transfer independently lands a
    real leg on the savings account, so auto-resolving the vault's side too
    put two real legs on savings for one real-world movement of money.
    Resolving either now takes an explicit `TransferRule`/`CategoryPattern`,
    same as any other row — see `docs/accounting/categorization.md`.

    Parameters
    ----------
    csv_text
        The raw CSV file contents, exactly as uploaded.
    account_id
        The real account (checking, savings, or vault) these rows belong to.

    Returns
    -------
    polars.DataFrame
        Posting-shaped rows, two per input row, validated through `Posting`.
    """
    postings: list[Posting] = []
    for raw in csv.DictReader(io.StringIO(csv_text)):
        row = SofiCsvRow.model_validate(raw)
        posted_at = datetime.combine(row.posted_date, datetime.min.time())
        counterparty = UNCATEGORIZED_INCOME_ACCOUNT_ID if row.amount >= 0 else UNCATEGORIZED_EXPENSE_ACCOUNT_ID

        # `:.4f` at `MONEY_SCALE`, never `str(row.amount)` — see the same line in
        # `importers.chase.checking` for why the `Decimal`'s own scale must not leak
        # into a transaction id.
        transaction_row_id = row_hash(account_id, row.posted_date.isoformat(), f"{row.amount:.4f}", row.description)
        leg = RawLeg(
            posted_at=posted_at,
            amount=row.amount,
            currency="USD",
            description=row.description,
            meta={
                "primary_category": row.primary_category,
                "detailed_category": row.detailed_category,
                "row_hash": transaction_row_id,
            },
        )
        postings.extend(
            posting_pair(
                source=_WIDE_CSV_SOURCE,
                row_id=transaction_row_id,
                account_id=account_id,
                counterparty_account_id=counterparty,
                leg=leg,
            )
        )
    return postings_to_frame(postings)


def _standardize_sofi_legacy_csv(csv_text: str, account_id: str, *, source: str) -> pl.DataFrame:
    """Map the older, per-account-kind SoFi export's rows onto postings against `account_id`.

    Vault transfers ("To Travel Vault", "From House Vault") aren't
    recognized here — they still get a generic placeholder counterparty
    like every other row. Recognizing the vault name and repointing the
    counterparty at a real `kind="vault"` sub-account is
    `ledger.categorization`'s job (Phase 2), since it's a categorization
    decision, not a fact about the file format. This shape has no vault
    export at all, so it's only ever reached for checking/savings.

    Parameters
    ----------
    csv_text
        The raw CSV file contents, exactly as uploaded.
    account_id
        The real SoFi checking or savings account these rows belong to.
    source
        `"sofi-checking"` or `"sofi-savings"` — kept distinct per caller so
        already-imported posting ids never change (see `_WIDE_CSV_SOURCE`).

    Returns
    -------
    polars.DataFrame
        Posting-shaped rows, two per input row, validated through `Posting`.
    """
    postings: list[Posting] = []
    for raw in csv.DictReader(io.StringIO(csv_text)):
        row = SofiRow.model_validate(raw)
        posted_at = datetime.combine(row.transaction_date, datetime.min.time())
        counterparty = UNCATEGORIZED_INCOME_ACCOUNT_ID if row.amount >= 0 else UNCATEGORIZED_EXPENSE_ACCOUNT_ID
        # `:.4f` at `MONEY_SCALE`, never `str(row.amount)` — see the same line in
        # `importers.chase.checking` for why the `Decimal`'s own scale must not leak
        # into a transaction id.
        transaction_row_id = row_hash(
            account_id, row.transaction_date.isoformat(), f"{row.amount:.4f}", row.description
        )
        leg = RawLeg(
            posted_at=posted_at,
            amount=row.amount,
            currency="USD",
            description=row.description,
            meta={"source_type": row.type, "row_hash": transaction_row_id},
        )
        postings.extend(
            posting_pair(
                source=source,
                row_id=transaction_row_id,
                account_id=account_id,
                counterparty_account_id=counterparty,
                leg=leg,
            )
        )
    return postings_to_frame(postings)


def standardize_sofi_checking(csv_text: str, account_id: str) -> pl.DataFrame:
    """Map SoFi checking export rows onto postings against `account_id`.

    Dispatches to the wider CSV shape when `csv_text` is in it — both are
    registered under the same `("SoFi", "checking")` importer key, since
    the file itself (not the account kind) determines which shape a given
    export is in.

    Parameters
    ----------
    csv_text
        The raw CSV file contents, exactly as uploaded.
    account_id
        The real SoFi checking account these rows belong to.

    Returns
    -------
    polars.DataFrame
        Posting-shaped rows, two per input row, validated through `Posting`.
    """
    if is_sofi_csv(csv_text):
        return _standardize_sofi_wide_csv(csv_text, account_id)
    return _standardize_sofi_legacy_csv(csv_text, account_id, source="sofi-checking")


def standardize_sofi_savings(csv_text: str, account_id: str) -> pl.DataFrame:
    """Map SoFi savings (or vault) export rows onto postings against `account_id`.

    Dispatches to the wider CSV shape when `csv_text` is in it — the only
    shape a vault's export can ever be in, since the older shape has no
    vault export at all.

    Parameters
    ----------
    csv_text
        The raw CSV file contents, exactly as uploaded.
    account_id
        The real SoFi savings or vault account these rows belong to.

    Returns
    -------
    polars.DataFrame
        Posting-shaped rows, two per input row, validated through `Posting`.
    """
    if is_sofi_csv(csv_text):
        return _standardize_sofi_wide_csv(csv_text, account_id)
    return _standardize_sofi_legacy_csv(csv_text, account_id, source="sofi-savings")


__all__ = [
    "ParsedAccountName",
    "is_sofi_csv",
    "parse_account_name",
    "standardize_sofi_checking",
    "standardize_sofi_savings",
]
