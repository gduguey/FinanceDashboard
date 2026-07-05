"""Guess which bank, account, and account number a CSV export belongs to, from its header and filename alone.

Every known export shape is structurally distinct (see `ACCOUNTING_PLAN.md`
Part 5, Phase 1), so a header match alone identifies the bank and account
kind; the account number comes from the filename, which every one of these
exports embeds somewhere. A guess is only ever a starting point for the
Import page's dropdowns — never final, always overridable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from accounting.models import AccountKind

_CHASE_CHECKING_HEADER = ("Details", "Posting Date", "Description", "Amount", "Type", "Balance", "Check or Slip #")
_CHASE_CREDIT_CARD_HEADER = ("Transaction Date", "Post Date", "Description", "Category", "Type", "Amount", "Memo")
_SOFI_HEADER = ("Date", "Description", "Type", "Amount", "Current balance", "Status")

_CHASE_ACCOUNT_NUMBER = re.compile(r"Chase(\d{4})", re.IGNORECASE)
_SOFI_ACCOUNT_NUMBER = re.compile(r"(\d{4})-\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class DetectedAccount:
    """A best-guess bank, account kind, and stable account id for one uploaded CSV."""

    institution: str
    account_kind: AccountKind
    account_id: str
    account_name: str


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


def detect_bank_account(header: list[str], filename: str) -> DetectedAccount | None:
    """Guess the institution, account kind, and account id a CSV's header and filename describe.

    Parameters
    ----------
    header
        The CSV's column names, in order, exactly as written in the file.
    filename
        The uploaded file's name, used only to extract an account number.

    Returns
    -------
    DetectedAccount or None
        The best guess, or `None` if the header matches no known shape or
        no account number could be found in the filename.
    """
    header_tuple = tuple(header)
    return _detect_chase(header_tuple, filename) or _detect_sofi(header_tuple, filename)
