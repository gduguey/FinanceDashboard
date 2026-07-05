"""Parse a paystub PDF's extracted text into an `EarningsStatement`.

Paystub layouts vary enormously by payroll provider (ADP, Gusto,
Justworks, and every employer's own custom template) — unlike
`importers.sofi.statement_pdf`'s regexes, which were tuned against an
actual SoFi statement, the patterns below are a best-effort starting
point written against commonly-seen label text ("Gross Pay", "Net Pay",
"Pay Date", a deposit line naming an account's last 4 digits). Treat this
as scaffolding to adjust against your own paystub's real text, not a
finished parser for every provider — `parse_earnings_statement_text`
raises `ValueError` naming exactly what it couldn't find, rather than
guessing, so a layout mismatch is loud rather than silently wrong.
"""

from __future__ import annotations

import io
import re
from datetime import datetime

from accounting.models import EarningsDeposit, EarningsStatement

_GROSS_PAY = re.compile(r"Gross Pay[:\s]+\$?([\d,]+\.\d{2})", re.IGNORECASE)
_NET_PAY = re.compile(r"Net Pay[:\s]+\$?([\d,]+\.\d{2})", re.IGNORECASE)
_TOTAL_TAXES = re.compile(r"Total Tax(?:es)?[:\s]+\$?([\d,]+\.\d{2})", re.IGNORECASE)
_PAY_DATE = re.compile(r"Pay Date[:\s]+(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE)
_DEPOSIT_LINE = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z .]*?)\s*(?:ending in|acct\.?|account)\s*(?P<last4>\d{4})"
    r".*?\$?(?P<amount>[\d,]+\.\d{2})",
    re.IGNORECASE,
)


def _to_float(text: str) -> float:
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
    import pdfplumber  # noqa: PLC0415

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


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
    date_match = _PAY_DATE.search(text)
    missing = [
        name
        for name, match in (("gross pay", gross_match), ("net pay", net_match), ("pay date", date_match))
        if match is None
    ]
    if missing or gross_match is None or net_match is None or date_match is None:
        message = f"Could not find {', '.join(missing)} in this paystub's text"
        raise ValueError(message)

    deposits = [
        EarningsDeposit(label=match["label"].strip(), account_last4=match["last4"], amount=_to_float(match["amount"]))
        for match in _DEPOSIT_LINE.finditer(text)
    ]
    if not deposits:
        raise ValueError("Could not find any per-account deposit line in this paystub's text")

    taxes_match = _TOTAL_TAXES.search(text)
    return EarningsStatement(
        pay_date=datetime.strptime(date_match[1], "%m/%d/%Y"),  # noqa: DTZ007  (a paystub's pay date has no timezone)
        gross_pay=_to_float(gross_match[1]),
        taxes_withheld=_to_float(taxes_match[1]) if taxes_match else 0.0,
        net_pay=_to_float(net_match[1]),
        deposits=deposits,
    )
