"""Parse a paystub PDF's extracted text into an `EarningsStatement`.

Tuned against one real paystub (a Gusto-style "Earnings Statement" —
`Pay Day: Jun 15, 2026`, deposit lines like `SoFi HYSA ( . . . 3680):
$2,000.00`, a `Summary` section with `Gross Earnings`/`Taxes`/`Net Pay`,
and reimbursement line items between `Total Reimbursements` and `Check
Amount`). Every payroll provider (ADP, Justworks, Rippling, ...) formats
this differently, so if this fails on a different provider's PDF,
`parse_earnings_statement_text` raises `ValueError` naming exactly what it
couldn't find — loud, not a silent guess — and the patterns below are
where to add that provider's own phrasing.
"""

from __future__ import annotations

import io
import re
from datetime import datetime

import pdfplumber

from accounting.models import EarningsDeposit, EarningsLineItem, EarningsStatement

_GROSS_PAY = re.compile(r"Gross (?:Pay|Earnings)[:\s]+\$?([\d,]+\.\d{2})", re.IGNORECASE)
_NET_PAY = re.compile(r"Net Pay[:\s]+\$?([\d,]+\.\d{2})", re.IGNORECASE)
_TAXES_WITHHELD = re.compile(r"(?:Total Tax(?:es)?|\bTaxes)[:\s]+\$?([\d,]+\.\d{2})", re.IGNORECASE)

_PAY_DATE_NUMERIC = re.compile(r"Pay Date[:\s]+(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE)
_PAY_DATE_TEXTUAL = re.compile(r"Pay Day[:\s]+([A-Za-z]{3,9}\s+\d{1,2},\s*\d{4})", re.IGNORECASE)

# "Checking ending in 1234: $X" / "SoFi HYSA ( . . . 3680): $2,000.00" — two
# distinct phrasings seen across providers for the same fact.
_DEPOSIT_LINE_LABELED = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z .]*?)\s*(?:ending in|acct\.?|account)\s*(?P<last4>\d{4})"
    r".*?\$(?P<amount>[\d,]+\.\d{2})",
    re.IGNORECASE,
)
_DEPOSIT_LINE_DOTTED = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z ]*?)\s*\(\s*\.\s*\.\s*\.\s*(?P<last4>\d{4})\s*\)\s*:\s*\$(?P<amount>[\d,]+\.\d{2})"
)

_TOTAL_REIMBURSEMENTS = re.compile(r"Total Reimbursements\s+\$[\d,]+\.\d{2}\s+\$[\d,]+\.\d{2}", re.IGNORECASE)
_CHECK_AMOUNT = re.compile(r"Check Amount\s+\$[\d,]+\.\d{2}", re.IGNORECASE)
# Every row of a paystub's line-item tables (earnings, taxes,
# reimbursements) is "label, then this period's $ amount, then a YTD $
# amount" — scoped to just the reimbursements section (see
# `_parse_reimbursement_lines`) rather than matched document-wide, since
# every other table on the page has this exact same shape too.
_LINE_ITEM = re.compile(r"^(?P<label>.+?)\s+\$(?P<current>[\d,]+\.\d{2})\s+\$(?P<ytd>[\d,]+\.\d{2})\s*$", re.MULTILINE)


def _to_float(text: str) -> float:
    """Parse a comma-grouped dollar amount string (e.g. `"2,000.00"`) into a float.

    Returns
    -------
    float
    """
    return float(text.replace(",", ""))


def extract_paystub_pdf_text(pdf_bytes: bytes) -> str:
    """Extract every page's text from a paystub PDF, via `pdfplumber`.

    Split out from `parse_earnings_statement_text` purely so a caller (or
    a test) can swap in already-extracted text without needing a real PDF.

    Parameters
    ----------
    pdf_bytes
        The raw PDF file contents.

    Returns
    -------
    str
        Every page's extracted text, joined with newlines.
    """
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _parse_pay_date(text: str) -> datetime | None:
    """Find the pay date in either its numeric (`Pay Date: 6/15/2026`) or textual (`Pay Day: Jun 15, 2026`) form.

    Returns
    -------
    datetime.datetime or None
    """
    numeric = _PAY_DATE_NUMERIC.search(text)
    if numeric is not None:
        return datetime.strptime(numeric[1], "%m/%d/%Y")  # noqa: DTZ007  (a paystub's pay date has no timezone)
    textual = _PAY_DATE_TEXTUAL.search(text)
    if textual is not None:
        normalized = " ".join(textual[1].split())
        return datetime.strptime(normalized, "%b %d, %Y")  # noqa: DTZ007
    return None


def _parse_deposits(text: str) -> list[EarningsDeposit]:
    """Find every deposit line, trying the dotted account-number phrasing before the "ending in" one.

    Returns
    -------
    list[EarningsDeposit]
    """
    matches = list(_DEPOSIT_LINE_DOTTED.finditer(text)) or list(_DEPOSIT_LINE_LABELED.finditer(text))
    return [
        EarningsDeposit(label=match["label"].strip(), account_last4=match["last4"], amount=_to_float(match["amount"]))
        for match in matches
    ]


def _parse_reimbursement_lines(text: str) -> list[EarningsLineItem]:
    """Every reimbursement line item with a nonzero *this-period* amount.

    A $0.00-this-period row (its dollar total is YTD-only, from an
    earlier pay period) contributed nothing to this paycheck's actual
    deposit, so it's excluded — matching what "Total Reimbursements"
    itself sums to.

    Returns
    -------
    list[EarningsLineItem]
        Empty if the reimbursements section itself can't be found.
    """
    start_match = _TOTAL_REIMBURSEMENTS.search(text)
    end_match = _CHECK_AMOUNT.search(text)
    if start_match is None or end_match is None:
        return []
    section = text[start_match.end() : end_match.start()]
    return [
        EarningsLineItem(label=match["label"].strip(), amount=_to_float(match["current"]))
        for match in _LINE_ITEM.finditer(section)
        if _to_float(match["current"]) > 0
    ]


def parse_earnings_statement_text(text: str) -> EarningsStatement:
    """Parse a paystub's already-extracted text into an `EarningsStatement`.

    Parameters
    ----------
    text
        The paystub's full text, e.g. from `pdfplumber`'s `page.extract_text()`.

    Returns
    -------
    EarningsStatement

    Raises
    ------
    ValueError
        If gross pay, net pay, pay date, or at least one deposit line can't be found — see this module's own
        docstring for why a layout mismatch raises instead of guessing.
    """
    gross_match = _GROSS_PAY.search(text)
    net_match = _NET_PAY.search(text)
    pay_date = _parse_pay_date(text)
    found = (
        ("gross pay", gross_match is not None),
        ("net pay", net_match is not None),
        ("pay date", pay_date is not None),
    )
    missing = [name for name, is_found in found if not is_found]
    if missing or gross_match is None or net_match is None or pay_date is None:
        message = f"Could not find {', '.join(missing)} in this paystub's text"
        raise ValueError(message)

    deposits = _parse_deposits(text)
    if not deposits:
        raise ValueError("Could not find any per-account deposit line in this paystub's text")

    taxes_match = _TAXES_WITHHELD.search(text)
    return EarningsStatement(
        pay_date=pay_date,
        gross_pay=_to_float(gross_match[1]),
        taxes_withheld=_to_float(taxes_match[1]) if taxes_match else 0.0,
        net_pay=_to_float(net_match[1]),
        deposits=deposits,
        reimbursement_lines=_parse_reimbursement_lines(text),
    )
