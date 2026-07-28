"""Forgiving date/amount/column parsing for the canonical CSV fallback importer.

Every other importer in this package knows its bank's exact export shape
ahead of time; this one doesn't — it's the fallback for a bank with no
dedicated standardizer, so it has to cope with whatever date format,
number format, and column naming a user's bank happens to use, guessing
the same way a general-purpose spreadsheet import would.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from dateutil import parser as dateutil_parser

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date

    from db.money import Money


def find_column(header: Iterable[str], aliases: set[str]) -> str | None:
    """Find the first header cell matching one of `aliases`, ignoring case and surrounding whitespace.

    Parameters
    ----------
    header
        The CSV's own column names, exactly as written.
    aliases
        Lowercase, whitespace-normalized names this concept is known to go by.

    Returns
    -------
    str | None
        The original header cell (untouched) if one matches, else `None`.
    """
    for cell in header:
        normalized = re.sub(r"\s+", " ", cell.strip().lower())
        if normalized in aliases:
            return cell
    return None


def parse_date_flexible(text: str, *, dayfirst: bool = False) -> date | None:
    """Parse a date written in pretty much any common format.

    Delegates to `dateutil`'s general parser, which already covers ISO
    (`2026-06-30`), US (`06/30/2026`), and named-month (`Jan 5, 2026`,
    `05-Jan-2026`) forms far more robustly than a hand-rolled list of
    `strptime` formats would.

    Parameters
    ----------
    text
        A single date cell's text.
    dayfirst
        Whether an ambiguous, all-numeric date (`01/12/2026`) should read
        day-before-month (European, 1 Dec) rather than the default
        month-before-day (US, 12 Jan) — has no effect on a date that isn't
        ambiguous in the first place (a named month, or day > 12).

    Returns
    -------
    datetime.date | None
        The parsed date, or `None` if nothing recognizable was found.
    """
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        return dateutil_parser.parse(cleaned, dayfirst=dayfirst).date()
    except dateutil_parser.ParserError, ValueError, OverflowError, TypeError:
        return None


_NEGATIVE_PARENS = re.compile(r"^\((.*)\)$")
_NOT_NUMERIC = re.compile(r"[^0-9.,]")
_THOUSANDS_GROUP_SIZE = 3


def parse_amount_flexible(text: str) -> Money | None:
    """Parse a money amount written in pretty much any common format.

    Handles what a general-purpose parser has to: a currency symbol
    anywhere in the string, thousands separators in either the US
    (`1,234.56`) or European (`1.234,56`) convention, and a negative
    amount written with a leading or trailing minus sign, or wrapped in
    parentheses (the standard accounting convention).

    Parameters
    ----------
    text
        A single amount cell's text.

    Returns
    -------
    Money | None
        The parsed amount as an exact `Decimal`, or `None` if nothing
        recognizable was found. Exact because everything above this point
        is string manipulation: by the time we get here `normalized` is a
        clean decimal literal, and `Decimal` preserves exactly the digits
        the statement actually said.
    """
    cleaned = text.strip()
    if not cleaned:
        return None

    negative = False
    parens_match = _NEGATIVE_PARENS.match(cleaned)
    if parens_match:
        negative = True
        cleaned = parens_match.group(1)
    if cleaned.startswith("-"):
        negative = True
        cleaned = cleaned[1:]
    if cleaned.endswith("-"):
        negative = True
        cleaned = cleaned[:-1]

    digits_only = _NOT_NUMERIC.sub("", cleaned)
    if not digits_only:
        return None

    has_dot = "." in digits_only
    has_comma = "," in digits_only
    if has_dot and has_comma:
        decimal_sep = "," if digits_only.rindex(",") > digits_only.rindex(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        normalized = digits_only.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif has_comma or has_dot:
        sep = "," if has_comma else "."
        # A single separator followed by exactly three digits, with
        # nothing else after it, reads as a thousands grouping (e.g.
        # "1,234" or the European "1.234") rather than a two-decimal
        # amount — real currency amounts essentially never carry three
        # decimal digits, so this is the safer default of the two. More
        # than one occurrence can only ever be a thousands grouping,
        # since a number has at most one decimal separator.
        if digits_only.count(sep) > 1:
            normalized = digits_only.replace(sep, "")
        else:
            tail = digits_only.split(sep)[-1]
            is_thousands_group = len(tail) == _THOUSANDS_GROUP_SIZE
            normalized = digits_only.replace(sep, "") if is_thousands_group else digits_only.replace(sep, ".")
    else:
        normalized = digits_only

    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        return None
    return -amount if negative else amount
