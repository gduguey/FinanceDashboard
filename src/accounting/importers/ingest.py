"""Import orchestration: archive a CSV verbatim, standardize it, and merge the result into the posting ledger.

Mirrors `trades.brokers.ibkr.main`'s sync/rebuild split: `ingest_csv` is the
everyday path (archive, then merge just the new file), `rebuild_from_raw_statements`
recomputes the whole ledger from every archive on disk, for when the derived
cache needs to be thrown away and regenerated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import polars as pl

from accounting.importers.chase.checking import standardize_chase_checking
from accounting.importers.chase.credit_card import standardize_chase_credit_card
from accounting.importers.sofi.checking import standardize_sofi_checking
from accounting.importers.sofi.savings import standardize_sofi_savings
from accounting.models import Posting
from trades.utils.io_utils import write_csv_atomic

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from accounting.config import AccountingConfig

_STANDARDIZERS: dict[tuple[str, str], Callable[[str, str], pl.DataFrame]] = {
    ("Chase", "checking"): standardize_chase_checking,
    ("Chase", "credit_card"): standardize_chase_credit_card,
    ("SoFi", "checking"): standardize_sofi_checking,
    ("SoFi", "savings"): standardize_sofi_savings,
}


class UnsupportedImportError(ValueError):
    """No standardizer exists for the given institution/account-kind combination."""


@dataclass(frozen=True)
class IngestResult:
    """What happened when one CSV was ingested."""

    account_id: str
    new_posting_count: int
    total_posting_count: int


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


def _merge_ledger(existing: pl.DataFrame, new: pl.DataFrame) -> pl.DataFrame:
    return (
        pl
        .concat([existing, new], how="vertical")
        .unique(subset="posting_id", keep="last")
        .sort("posted_at", "posting_id")
    )


def _raw_statement_path(institution: str, account_id: str, config: AccountingConfig) -> Path:
    directory = config.raw_statement_dir / institution / account_id
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    return directory / f"{timestamp}.csv"


def ingest_csv(
    csv_text: str, institution: str, account_kind: str, account_id: str, config: AccountingConfig
) -> IngestResult:
    """Archive one uploaded CSV verbatim, standardize it, and merge the result into the ledger.

    The raw file is saved before parsing even starts, so a parse failure
    never loses the upload — per `docs/architecture.md`'s "cache raw,
    derive everything else" rule, applied here the same way it already is
    for IBKR statements.

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
    """
    standardizer = _STANDARDIZERS.get((institution, account_kind))
    if standardizer is None:
        message = f"No importer for institution={institution!r}, account_kind={account_kind!r}."
        raise UnsupportedImportError(message)

    _raw_statement_path(institution, account_id, config).write_text(csv_text, encoding="utf-8")
    new_postings = standardizer(csv_text, account_id)

    existing = load_ledger(config)
    merged = _merge_ledger(existing, new_postings)
    _write_ledger(merged, config)

    return IngestResult(
        account_id=account_id, new_posting_count=len(merged) - len(existing), total_posting_count=len(merged)
    )


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
    raw_paths = sorted(config.raw_statement_dir.glob("*/*/*.csv"))
    if not raw_paths:
        message = f"No archived raw statements under {config.raw_statement_dir}"
        raise FileNotFoundError(message)

    frames = [pl.DataFrame(schema=Posting.polars_schema)]
    for path in raw_paths:
        institution = path.parent.parent.name
        account_id = path.parent.name
        account_kind = account_id.split(":")[1]
        standardizer = _STANDARDIZERS.get((institution, account_kind))
        if standardizer is None:
            message = f"No importer for institution={institution!r}, account_kind={account_kind!r}."
            raise UnsupportedImportError(message)
        frames.append(standardizer(path.read_text(encoding="utf-8"), account_id))

    ledger = _merge_ledger(frames[0], pl.concat(frames[1:], how="vertical"))
    _write_ledger(ledger, config)
    return ledger
