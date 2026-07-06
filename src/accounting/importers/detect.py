"""Guess which bank, account, and account number a CSV export belongs to.

Every known export shape is structurally distinct (see `ACCOUNTING_PLAN.md`
Part 5, Phase 1), so a header match alone identifies the bank and account
kind for most formats; the account number then comes from the filename,
which every one of those exports embeds somewhere. SoFi's newer, wider CSV
shape (`importers.sofi.csv`) is the one exception — it names its account in
the `Account Name` *column*, not the filename, so detecting it needs a
peek at the first data row too. A guess is only ever a starting point for
the Import page's dropdowns — never final, always overridable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from accounting.importers.sofi.csv import HEADER as _SOFI_CSV_HEADER
from accounting.importers.sofi.csv import parse_account_name
from accounting.store import slugify

if TYPE_CHECKING:
    from accounting.models import AccountKind

_CHASE_CHECKING_HEADER = ("Details", "Posting Date", "Description", "Amount", "Type", "Balance", "Check or Slip #")
_CHASE_CREDIT_CARD_HEADER = ("Transaction Date", "Post Date", "Description", "Category", "Type", "Amount", "Memo")
_SOFI_HEADER = ("Date", "Description", "Type", "Amount", "Current balance", "Status")

_CHASE_ACCOUNT_NUMBER = re.compile(r"Chase(\d{4})", re.IGNORECASE)
_SOFI_ACCOUNT_NUMBER = re.compile(r"(\d{4})-\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class DetectedAccount:
    """A best-guess bank, account kind, and stable account id for one uploaded CSV.

    `parent_account_id` is only ever set when `account_kind` is `"vault"`
    — the savings account this vault belongs to, so the Import page can
    register a brand-new vault with its parent already wired up instead
    of leaving it `None`.
    """

    institution: str
    account_kind: AccountKind
    account_id: str
    account_name: str
    parent_account_id: str | None = None


def _sofi_kind(filename: str) -> Literal["checking", "savings"] | None:
    lower = filename.lower()
    if "saving" in lower:
        return "savings"
    if "checking" in lower:
        return "checking"
    return None


def _detect_chase(header_tuple: tuple[str, ...], filename: str) -> DetectedAccount | None:
    kind: Literal["credit_card", "checking"]
    label: str
    if header_tuple == _CHASE_CREDIT_CARD_HEADER:
        kind, label = "credit_card", "Credit Card"
    elif header_tuple == _CHASE_CHECKING_HEADER:
        kind, label = "checking", "Checking"
    else:
        return None
    match = _CHASE_ACCOUNT_NUMBER.search(filename)
    if match is None:
        return None
    last4 = match.group(1)
    return DetectedAccount("Chase", kind, f"chase:{kind}:{last4}", f"Chase {label} (...{last4})")


def _detect_sofi(header_tuple: tuple[str, ...], filename: str) -> DetectedAccount | None:
    if header_tuple != _SOFI_HEADER:
        return None
    kind = _sofi_kind(filename)
    match = _SOFI_ACCOUNT_NUMBER.search(filename)
    if kind is None or match is None:
        return None
    last4 = match.group(1)
    return DetectedAccount("SoFi", kind, f"sofi:{kind}:{last4}", f"SoFi {kind.title()} (...{last4})")


def _detect_sofi_csv(header_tuple: tuple[str, ...], first_data_row: dict[str, str] | None) -> DetectedAccount | None:
    if header_tuple != _SOFI_CSV_HEADER or first_data_row is None:
        return None
    account_name = first_data_row.get("Account Name", "")
    parsed = parse_account_name(account_name)
    if parsed is None:
        return None
    if parsed.kind is not None:
        return DetectedAccount(
            "SoFi", parsed.kind, f"sofi:{parsed.kind}:{parsed.last4}", f"SoFi {parsed.kind.title()} (...{parsed.last4})"
        )
    parent_id = f"sofi:savings:{parsed.last4}"
    vault_id = f"{parent_id}:vault:{slugify(parsed.label)}"
    return DetectedAccount("SoFi", "vault", vault_id, f"{parsed.label} Vault", parent_account_id=parent_id)


def detect_bank_account(
    header: list[str], filename: str, first_data_row: dict[str, str] | None = None
) -> DetectedAccount | None:
    """Guess the institution, account kind, and account id a CSV's header, filename, and first row describe.

    Parameters
    ----------
    header
        The CSV's column names, in order, exactly as written in the file.
    filename
        The uploaded file's name, used to extract an account number for most formats.
    first_data_row
        The first data row, keyed by column name — only needed for SoFi's
        newer CSV shape, which names its account in a column rather than the filename.

    Returns
    -------
    DetectedAccount or None
        The best guess, or `None` if nothing matched.
    """
    header_tuple = tuple(header)
    return (
        _detect_chase(header_tuple, filename)
        or _detect_sofi(header_tuple, filename)
        or _detect_sofi_csv(header_tuple, first_data_row)
    )
