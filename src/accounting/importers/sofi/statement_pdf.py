"""Map a SoFi monthly statement PDF onto canonical postings and accounts.

Retired for new imports: SoFi's wider CSV shape (`importers.sofi.csv`) now
covers checking, savings, and vaults alike, so there's no upload path left
that calls `standardize_sofi_statement_pdf` for a *new* statement. It's
kept here, still called, for one reason only — `importers.ingest.
rebuild_from_raw_statements` still re-derives every already-archived PDF
on a rebuild, so postings (and their categorization) from statements
imported before this retirement aren't silently dropped. Nothing should
add a new caller of this function; if SoFi ever changes its CSV export
again in a way that drops vault coverage, revive the PDF importer here
rather than inventing a second one.

SoFi Vaults have no CSV export of their own — historically, the only
place their interest and transfers ever showed up was the monthly
statement PDF, which also covers the linked checking and savings accounts
in one file. This module is the one place that PDF's specific vocabulary
(its `TYPE` column values, its "To/From <Name> Vault" and "To/From
Checking - 1234" transfer phrasing, its per-account APY block) is read;
everything past `standardize_sofi_statement_pdf` sees the same `Posting`/
`Account` shapes every other importer produces.

The statement double-books every internal transfer: a vault's own section
shows "Deposit From savings balance" mirroring the savings account's
"Withdrawal To <Name> Vault", and checking/savings mutually show
"Withdrawal To Savings - 3680" / "Deposit From Checking - 9169" for the
same transfer. Both sides of every pair are kept — this importer never
decides which one to drop, since that's a judgment about the user's own
accounts, not a fact about the file. A `TransferRule` (the Rules page)
repoints each side's placeholder counterparty at the real account on the
other end, the same mechanism `chase.credit_card` relies on for its own
"Payment Thank You" rows — until one exists, both legs land on the usual
uncategorized placeholder like any other row.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import pdfplumber

from accounting.importers.common import RawLeg, posting_pair, postings_to_frame, row_hash
from accounting.models import Account
from accounting.store import UNCATEGORIZED_EXPENSE_ACCOUNT_ID, UNCATEGORIZED_INCOME_ACCOUNT_ID, slugify

if TYPE_CHECKING:
    import polars as pl

    from accounting.models import Posting

_COLUMNS_LINE = "DATE TYPE DESCRIPTION AMOUNT BALANCE"
_ACCOUNT_HEADER = re.compile(r"^(Checking|Savings) Account - (\d+)$")
_APY_BLOCK = re.compile(
    r"^(Checking|Savings) Account - (\d+)\n.*\n\$[\d,]+\.\d{2} \$[\d,]+\.\d{2} (\d+(?:\.\d+)?)%", re.MULTILINE
)
_ROW = re.compile(
    r"^(?P<month>[A-Za-z]{3,9}) (?P<day>\d{1,2}), (?P<year>\d{4}) (?P<body>.+?) "
    r"(?P<amount>-?\$[\d,]+\.\d{2}) (?P<balance>\$[\d,]+\.\d{2})\n"
    r"Transaction ID: (?P<txn_id>[\w-]+)$",
    re.MULTILINE,
)
_TYPES_BY_LENGTH_DESC = sorted(
    ["Interest Earned", "Direct Deposit", "Direct Payment", "Withdrawal", "Deposit"], key=len, reverse=True
)
_INTEREST_EARNED_CATEGORY_ID = "income:interest-earned"


@dataclass(frozen=True)
class ParsedRow:
    """One statement row, still in the PDF's own vocabulary (`source_type`, not a category)."""

    account_id: str
    posted_at: datetime
    amount: float
    description: str
    source_type: str
    transaction_id: str


@dataclass(frozen=True)
class ParsedStatement:
    """Everything one SoFi statement PDF describes: its accounts and every row across all of them."""

    accounts: dict[str, Account]
    rows: list[ParsedRow]


def _extract_page_texts(pdf_bytes: bytes) -> list[str]:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _extract_apys(text: str) -> dict[str, float]:
    return {
        f"sofi:{match.group(1).lower()}:{match.group(2)}": float(match.group(3)) for match in _APY_BLOCK.finditer(text)
    }


def _split_type_and_description(body: str) -> tuple[str, str]:
    for type_value in _TYPES_BY_LENGTH_DESC:
        prefix = f"{type_value} "
        if body.startswith(prefix):
            return type_value, body[len(prefix) :]
    return "Other", body


def _parse_statement_date(month: str, day: str, year: str) -> datetime:
    return datetime.strptime(f"{month} {day} {year}", "%b %d %Y")  # noqa: DTZ007  (a statement date has no timezone)


def _header_indices(lines: list[str]) -> list[int]:
    return [i for i in range(len(lines) - 1) if lines[i + 1].strip() == _COLUMNS_LINE]


def _discover_checking_and_savings_accounts(
    lines: list[str], header_indices: list[int], apys: dict[str, float]
) -> dict[str, Account]:
    accounts: dict[str, Account] = {}
    for i in header_indices:
        match = _ACCOUNT_HEADER.match(lines[i].strip())
        if match is None:
            continue
        kind, last4 = match.group(1).lower(), match.group(2)
        account_id = f"sofi:{kind}:{last4}"
        accounts[account_id] = Account(
            account_id=account_id,
            name=f"SoFi {match.group(1)} (...{last4})",
            kind=kind,  # type: ignore[arg-type]
            institution="SoFi",
            currency="USD",
            meta={"apy_pct": str(apys.get(account_id, 0.0))},
        )
    return accounts


def _section_rows(section_text: str, account_id: str) -> list[ParsedRow]:
    rows: list[ParsedRow] = []
    for row_match in _ROW.finditer(section_text):
        source_type, description = _split_type_and_description(row_match.group("body").strip())
        rows.append(
            ParsedRow(
                account_id=account_id,
                posted_at=_parse_statement_date(
                    row_match.group("month"), row_match.group("day"), row_match.group("year")
                ),
                amount=float(row_match.group("amount").replace("$", "").replace(",", "")),
                description=description,
                source_type=source_type,
                transaction_id=row_match.group("txn_id"),
            )
        )
    return rows


def _section_account_id_and_new_vault(
    header: str, savings_account_id: str, savings_apy: float
) -> tuple[str, Account | None]:
    match = _ACCOUNT_HEADER.match(header)
    if match is not None:
        return f"sofi:{match.group(1).lower()}:{match.group(2)}", None
    vault_name = header.removesuffix(" Vault")
    account_id = f"{savings_account_id}:vault:{slugify(vault_name)}"
    vault = Account(
        account_id=account_id,
        name=header,
        kind="vault",
        institution="SoFi",
        currency="USD",
        parent_account_id=savings_account_id,
        meta={"apy_pct": str(savings_apy)},
    )
    return account_id, vault


def parse_sofi_statement_pdf(pdf_bytes: bytes) -> ParsedStatement:
    """Parse a SoFi monthly statement PDF into accounts and every row across all of them.

    Parameters
    ----------
    pdf_bytes
        The raw PDF file contents, exactly as uploaded.

    Returns
    -------
    ParsedStatement
        See `parse_sofi_statement_text`.
    """
    pages = [page for page in _extract_page_texts(pdf_bytes) if not page.strip().startswith("Important Information")]
    return parse_sofi_statement_text("\n".join(pages))


def parse_sofi_statement_text(text: str) -> ParsedStatement:
    """Parse a SoFi monthly statement's already-extracted text into accounts and every row across all of them.

    Split out from `parse_sofi_statement_pdf` so the actual parsing logic
    can be exercised directly against hand-written text fixtures, without
    needing a real PDF file to extract text from.

    Parameters
    ----------
    text
        Every non-"Important Information" page's `extract_text()` output, joined with newlines.

    Returns
    -------
    ParsedStatement
        Every checking/savings/vault account the statement describes
        (with `meta["apy_pct"]` set), and every row across all of them.

    Raises
    ------
    ValueError
        If the statement has no savings account section — every vault is a
        named sub-balance of one, so parsing can't proceed without it.
    """
    apys = _extract_apys(text)
    lines = text.splitlines()
    header_indices = _header_indices(lines)

    accounts = _discover_checking_and_savings_accounts(lines, header_indices, apys)
    savings_account_id = next((aid for aid, account in accounts.items() if account.kind == "savings"), None)
    if savings_account_id is None:
        message = "SoFi statement PDF has no savings account section"
        raise ValueError(message)
    savings_apy = apys.get(savings_account_id, 0.0)

    rows: list[ParsedRow] = []
    for position, i in enumerate(header_indices):
        body_end = header_indices[position + 1] if position + 1 < len(header_indices) else len(lines)
        account_id, new_vault = _section_account_id_and_new_vault(lines[i].strip(), savings_account_id, savings_apy)
        if new_vault is not None:
            accounts.setdefault(account_id, new_vault)
        rows.extend(_section_rows("\n".join(lines[i + 2 : body_end]), account_id))

    return ParsedStatement(accounts=accounts, rows=rows)


def standardize_sofi_statement_pdf(pdf_bytes: bytes) -> tuple[pl.DataFrame, dict[str, Account]]:
    """Map a SoFi monthly statement PDF onto postings and the accounts it describes.

    Parameters
    ----------
    pdf_bytes
        The raw PDF file contents, exactly as uploaded.

    Returns
    -------
    tuple[polars.DataFrame, dict[str, Account]]
        See `_postings_for_parsed_statement`.
    """
    return _postings_for_parsed_statement(parse_sofi_statement_pdf(pdf_bytes))


def standardize_sofi_statement_text(text: str) -> tuple[pl.DataFrame, dict[str, Account]]:
    """Map a SoFi monthly statement's already-extracted text onto postings and accounts.

    See `parse_sofi_statement_text` for why this text-only entry point exists.

    Parameters
    ----------
    text
        Every non-"Important Information" page's `extract_text()` output, joined with newlines.

    Returns
    -------
    tuple[polars.DataFrame, dict[str, Account]]
        See `_postings_for_parsed_statement`.
    """
    return _postings_for_parsed_statement(parse_sofi_statement_text(text))


def _postings_for_parsed_statement(parsed: ParsedStatement) -> tuple[pl.DataFrame, dict[str, Account]]:
    """Map a parsed statement's rows onto postings, ready to merge into the ledger.

    Returns
    -------
    tuple[polars.DataFrame, dict[str, Account]]
        Posting-shaped rows, two per statement row, and every checking/savings/vault account the statement describes.
    """
    postings: list[Posting] = []
    for row in parsed.rows:
        counterparty = UNCATEGORIZED_INCOME_ACCOUNT_ID if row.amount >= 0 else UNCATEGORIZED_EXPENSE_ACCOUNT_ID
        category_id = _INTEREST_EARNED_CATEGORY_ID if row.source_type == "Interest Earned" else None
        row_id = row_hash(row.account_id, row.transaction_id)
        leg = RawLeg(
            posted_at=row.posted_at,
            amount=row.amount,
            currency="USD",
            description=row.description,
            meta={"source_type": row.source_type, "row_hash": row_id},
        )
        postings.extend(
            posting_pair(
                source="sofi-statement-pdf",
                row_id=row_id,
                account_id=row.account_id,
                counterparty_account_id=counterparty,
                leg=leg,
                category_id=category_id,
            )
        )
    return postings_to_frame(postings), parsed.accounts
