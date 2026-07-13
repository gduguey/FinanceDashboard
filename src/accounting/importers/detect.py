"""Guess which bank and account kind a CSV export belongs to.

Every known export shape is structurally distinct, so a header match alone
identifies the bank and account kind for most formats; a genuine export
also embeds a last-4-shaped account number somewhere (the filename, for
most formats), which is checked here purely as a "does this really look
like a real export" signal — never used to build an id, since accounts are
now always looked up (or created) by the caller, never invented here. A
guess is only ever a starting point for the Import page's dropdowns —
never final, always overridable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from accounting.importers.sofi.csv import HEADER as _SOFI_CSV_HEADER
from accounting.importers.sofi.csv import parse_account_name

if TYPE_CHECKING:
    from accounting.models import AccountKind

_CHASE_CHECKING_HEADER = ("Details", "Posting Date", "Description", "Amount", "Type", "Balance", "Check or Slip #")
_CHASE_CREDIT_CARD_HEADER = ("Transaction Date", "Post Date", "Description", "Category", "Type", "Amount", "Memo")
_SOFI_HEADER = ("Date", "Description", "Type", "Amount", "Current balance", "Status")

_CHASE_ACCOUNT_NUMBER = re.compile(r"Chase(\d{4})", re.IGNORECASE)
_SOFI_ACCOUNT_NUMBER = re.compile(r"(\d{4})-\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class DetectedAccount:
    """A best-guess institution and account kind for one uploaded CSV."""

    institution: str
    account_kind: AccountKind


def _sofi_kind(filename: str) -> Literal["checking", "savings"] | None:
    """Guess whether a SoFi export filename is for checking or savings.

    Returns
    -------
    "checking", "savings", or None
    """
    lower = filename.lower()
    if "saving" in lower:
        return "savings"
    if "checking" in lower:
        return "checking"
    return None


def _detect_chase(header_tuple: tuple[str, ...], filename: str) -> DetectedAccount | None:
    """Detect a Chase checking or credit-card export from its exact header and account number in the filename.

    Returns
    -------
    DetectedAccount or None
    """
    kind: Literal["credit_card", "checking"]
    if header_tuple == _CHASE_CREDIT_CARD_HEADER:
        kind = "credit_card"
    elif header_tuple == _CHASE_CHECKING_HEADER:
        kind = "checking"
    else:
        return None
    if _CHASE_ACCOUNT_NUMBER.search(filename) is None:
        return None
    return DetectedAccount("Chase", kind)


def _detect_sofi(header_tuple: tuple[str, ...], filename: str) -> DetectedAccount | None:
    """Detect a SoFi checking or savings export from its exact header and account number in the filename.

    Returns
    -------
    DetectedAccount or None
    """
    if header_tuple != _SOFI_HEADER:
        return None
    kind = _sofi_kind(filename)
    match = _SOFI_ACCOUNT_NUMBER.search(filename)
    if kind is None or match is None:
        return None
    return DetectedAccount("SoFi", kind)


def _detect_sofi_csv(header_tuple: tuple[str, ...], first_data_row: dict[str, str] | None) -> DetectedAccount | None:
    """Detect SoFi's newer, wider CSV export, whose account name lives in a data column, not the filename.

    Returns
    -------
    DetectedAccount or None
    """
    if header_tuple != _SOFI_CSV_HEADER or first_data_row is None:
        return None
    account_name = first_data_row.get("Account Name", "")
    parsed = parse_account_name(account_name)
    if parsed is None:
        return None
    if parsed.kind is not None:
        return DetectedAccount("SoFi", parsed.kind)
    return DetectedAccount("SoFi", "vault")


def detect_bank_account(
    header: list[str], filename: str, first_data_row: dict[str, str] | None = None
) -> DetectedAccount | None:
    """Guess which institution and account kind a CSV's header, filename, and first row describe.

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
