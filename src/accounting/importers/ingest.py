"""Import orchestration: archive a CSV verbatim, standardize it, and merge the result into the posting ledger.

Mirrors `trades.brokers.ibkr.main`'s sync/rebuild split: `ingest_csv` is the
everyday path (archive, then merge just the new file), `rebuild_from_raw_statements`
recomputes the whole ledger from every archive on disk, for when the derived
cache needs to be thrown away and regenerated.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import polars as pl
from sqlalchemy import select

import accounting.db as adb
from accounting.importers.canonical.csv import (
    CanonicalCsvError,
    SkippedRowsInfo,
    standardize_canonical_csv,
    standardize_canonical_excel,
)
from accounting.importers.chase.checking import standardize_chase_checking
from accounting.importers.chase.credit_card import standardize_chase_credit_card
from accounting.importers.sofi.csv import standardize_sofi_checking, standardize_sofi_savings
from accounting.ledger.frame import LEDGER_FRAME_SCHEMA
from accounting.ledger.replay import validate_balanced
from accounting.ledger.transfers import reconcile_and_persist_rule_links
from accounting.models import IMPORTABLE_ACCOUNT_KINDS
from accounting.repositories.ledger import ledger_rows_to_frame, ledger_statement
from accounting.repositories.taxonomy import replace_categories
from accounting.taxonomy import normalize_categories, seeded_accounts, seeded_categories
from accounting.utils.statement_archive import StatementArchive
from db.base import ids_by_natural_key
from db.money import quantize_money

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable, Sequence
    from datetime import date
    from typing import Any

    from sqlalchemy.orm import Session

    from accounting.config import AccountingConfig
    from accounting.importers.canonical.csv import CanonicalImportResult, CategoryOverrides, DateOrder
    from accounting.models import Account, Category, TransactionOrigin

_Fingerprint = tuple[str, datetime, float, str]
"""`(account_id, posted_at, amount, description)` — two transactions with the same fingerprint look identical."""

_STANDARDIZERS: dict[tuple[str, str], Callable[[str, str], pl.DataFrame]] = {
    ("Chase", "checking"): standardize_chase_checking,
    ("Chase", "credit_card"): standardize_chase_credit_card,
    ("SoFi", "checking"): standardize_sofi_checking,
    ("SoFi", "savings"): standardize_sofi_savings,
    # A vault's raw CSV is structurally identical to the newer SoFi
    # savings/checking export (see `importers.sofi.csv`) — registered
    # separately only because `account_kind` for a vault account is
    # itself `"vault"`, not `"savings"`.
    ("SoFi", "vault"): standardize_sofi_savings,
}


def supported_import_kinds() -> set[tuple[str, str]]:
    """Every `(institution, account_kind)` pair with a registered CSV standardizer.

    Returns
    -------
    set[tuple[str, str]]
        Exactly the keys of `_STANDARDIZERS` — used to flag accounts in the
        UI that were created (manually, or discovered from an older import)
        without any code path able to actually parse a statement for them.
    """
    return set(_STANDARDIZERS.keys())


class UnsupportedImportError(ValueError):
    """No standardizer exists for the given institution/account-kind combination."""


@dataclass(frozen=True)
class IngestResult:
    """What happened when one CSV was ingested."""

    account_id: str
    new_posting_count: int
    total_posting_count: int
    skipped_rows: SkippedRowsInfo | None = None  # Rows that couldn't be parsed (bank-specific or canonical fallback)


def load_ledger(
    session: Session,
    user_id: uuid.UUID,
    *,
    since: date | None = None,
    until: date | None = None,
    origin: TransactionOrigin | None = None,
    transaction_ids: Sequence[uuid.UUID] | None = None,
) -> pl.DataFrame:
    """Load the posting ledger, optionally restricted to a date range, an origin, or a named set of transactions.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose ledger to load.
    since
        First day to include, inclusive. `None` (the default) means no
        lower bound — every caller that needs the true full history
        (import/merge logic diffing against everything, `GET /ledger/export`)
        must leave this unset; only callers that already scope their own
        result to a date range (dashboard aggregations) should pass it.
    until
        Last day to include, inclusive. Same defaulting reasoning as `since`.
    origin
        Restrict to postings of `imported` or `manual` transactions (see
        `models.TransactionOrigin`). `None` (the default) is the whole
        ledger, which is what every *read* wants: a manual transfer is a
        real transaction and shows up in balances, net worth and the
        transaction list exactly like any other. Only the import machinery
        passes `"imported"`, and for two reasons — the merge it does is a
        diff against what the archives produce, which manual rows are no
        part of, and `ledger.replay.validate_balanced` (run over the merged
        result) would flag a legitimate cross-currency manual transfer,
        whose two legs are equal-and-opposite only after a conversion it
        deliberately doesn't store.
    transaction_ids
        Restrict to the postings of these transactions, which is how a page
        of `repositories.ledger.visible_transaction_page` becomes a frame.
        `None` (the default) is every transaction.

    Returns
    -------
    polars.DataFrame
        Shaped exactly like `LEDGER_FRAME_SCHEMA` — every other ledger
        and dashboard module depends on that shape, not on how it's
        actually stored, so nothing downstream of this function needed to
        change when its own storage moved from `ledger.csv` to Postgres,
        nor when `posted_at`/`description` moved off `postings` onto
        `transactions` and became a join. An empty frame if nothing
        matches.

        The statement that produces these rows, and why it selects columns
        rather than entities, is `repositories.ledger`.
    """
    statement = ledger_statement(user_id, since=since, until=until, origin=origin, transaction_ids=transaction_ids)
    rows = session.execute(statement).all()
    if not rows:
        return pl.DataFrame(schema=LEDGER_FRAME_SCHEMA)
    return ledger_rows_to_frame(rows)


def _write_ledger(ledger: pl.DataFrame, session: Session, user_id: uuid.UUID) -> None:
    """Persist the imported half of the posting ledger, overwriting whatever was saved before.

    Only the imported half. Every row written here is `origin =
    "imported"` (see `models.TransactionOrigin`), and the prune below is
    scoped to imported transactions and their postings, so a rebuild that
    replays every archived statement cannot delete a `manual` transaction
    — a manual transfer is reproducible from nothing, so nothing this
    function replays would ever put it back. That scoping *is* the
    discriminator's reason for existing; without it, retiring the old
    `manual_transfers` table into ordinary transactions would have made
    `rebuild_from_raw_statements` silently destructive.

    `transactions`/`postings` are upserted and pruned rather than deleted
    wholesale and reinserted — `manual_overrides`, `posting_splits`,
    `posting_merges`, `transfer_links`, `categorization_rule_exclusions`, and
    `goal_contributions.source_posting_id` all foreign-key into them (see
    `db.base.upsert_and_prune` for the same reasoning applied to accounts,
    categories, and tags). Pruning a
    transaction still referenced by one of these needs `transfer_links`
    handled explicitly (see below);
    `posting_merges`/`categorization_rule_exclusions`
    lean on `ondelete="CASCADE"` instead, since each references its
    transaction directly rather than through a separate join table.
    `posting_tags` has nothing foreign-keying into it, so it's safe to
    delete in full and rebuild from `ledger`'s own `tag_ids` column every
    time.

    Parameters
    ----------
    ledger
        The whole imported ledger to persist, shaped like `LEDGER_FRAME_SCHEMA`.
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose ledger this is.
    """
    rows = ledger.to_dicts()

    # The frame carries one `posted_at`/`description` pair per *leg*; the
    # storage holds one per event (see `db.core.Transaction`). Collapsing by
    # `transaction_id` here is where the frame's denormalized copies become
    # the one row again — and since every path that builds a frame gives
    # each leg of a transaction the same date and description, "last leg
    # wins" is not a choice being made, it is the same value written once.
    transaction_facts = {row["transaction_id"]: (row["posted_at"], row["description"]) for row in rows}

    # Every prune below is scoped to these. A `manual` transaction and its
    # postings are outside the set this function is the source of truth for,
    # so a rebuild that no longer produces them must not read that as "the
    # user deleted them" — see this function's own docstring.
    imported_transaction_ids = select(adb.Transaction.id).where(
        adb.Transaction.user_id == user_id, adb.Transaction.origin == "imported"
    )
    imported_posting_ids = select(adb.Posting.id).where(
        adb.Posting.user_id == user_id, adb.Posting.transaction_id.in_(imported_transaction_ids)
    )

    # One read of the imported half's `(natural_key, id)` pairs serves both
    # jobs below: telling `merge()` which rows already exist (so it updates
    # rather than duplicating on `UNIQUE (user_id, natural_key)`), and giving
    # the prune the set of ids to diff against. Deliberately *not* an
    # `ids_by_natural_key` call keyed on the frame's own natural keys: a
    # rebuild carries tens of thousands of them, and one `IN` list that long
    # is the 65,535-parameter cliff DB-audit D4 is about. Reading the user's
    # imported rows wholesale binds two parameters regardless of size.
    existing_transaction_ids = {
        row.natural_key: row.id
        for row in session.query(adb.Transaction.natural_key, adb.Transaction.id).filter_by(
            user_id=user_id, origin="imported"
        )
    }
    existing_posting_ids = {
        row.natural_key: row.id
        for row in session
        .query(adb.Posting.natural_key, adb.Posting.id)
        .filter_by(user_id=user_id)
        .filter(adb.Posting.transaction_id.in_(imported_transaction_ids))
    }

    transaction_rows: dict[str, adb.Transaction] = {}
    for natural_key, (posted_at, description) in transaction_facts.items():
        transaction_rows[natural_key] = session.merge(
            adb.Transaction(
                id=existing_transaction_ids.get(natural_key),
                user_id=user_id,
                natural_key=natural_key,
                posted_at=posted_at,
                description=description,
                origin="imported",
            )
        )
    # Flushed before the postings below, because a posting's `transaction_id`
    # is the id `uuid7()` mints for its transaction — read off the flushed
    # instance rather than recomputed from the natural key.
    session.flush()

    # The reference tables a posting points at. These are small per user (a
    # handful of accounts, dozens of categories and tags) and the frame names
    # only a subset, so a keyed lookup is the right shape here — unlike the
    # ledger's own two tables above.
    account_ids = ids_by_natural_key(session, adb.Account, user_id, [row["account_id"] for row in rows])
    category_ids = ids_by_natural_key(
        session,
        adb.Category,
        user_id,
        [row["category_id"] for row in rows] + [row["subcategory_id"] for row in rows],
    )
    budget_ids = ids_by_natural_key(session, adb.Budget, user_id, [row["budget_id"] for row in rows])

    posting_rows: dict[str, adb.Posting] = {}
    for row in rows:
        posting_rows[row["posting_id"]] = session.merge(
            adb.Posting(
                id=existing_posting_ids.get(row["posting_id"]),
                user_id=user_id,
                natural_key=row["posting_id"],
                transaction_id=transaction_rows[row["transaction_id"]].id,
                account_id=account_ids[row["account_id"]],
                # Back out of the float projection before Postgres sees this.
                # `rows` comes off a `LEDGER_FRAME_SCHEMA` frame, where `amount` is
                # `Float64`, and `postings.amount` is `MONEY` = `NUMERIC(18, 4)`:
                # handing psycopg a Python float makes Postgres cast
                # `float8 -> numeric`, which truncates at 15 significant digits, so
                # `12345678901234.5678` persisted as `12345678901234.6000`. Worse
                # here than in `trades`, because these amounts arrive genuinely
                # exact — Chase, SoFi and canonical CSV all parse to `Money` — so
                # the cast destroyed precision that really existed. `quantize_money`
                # routes the float through `str()` for its shortest round-trip
                # literal; see `ledger.frame`, "Exact again on the way out".
                amount=quantize_money(row["amount"]),
                currency=row["currency"],
                category_id=category_ids[row["category_id"]] if row["category_id"] is not None else None,
                subcategory_id=category_ids[row["subcategory_id"]] if row["subcategory_id"] is not None else None,
                budget_id=budget_ids[row["budget_id"]] if row["budget_id"] is not None else None,
                meta=row["meta"],
            )
        )
    session.flush()

    # Postings first, then transactions — a transaction that lost every one of
    # its postings would otherwise still be referenced by the very rows this
    # step is trying to delete first.
    removed_posting_ids = set(existing_posting_ids.values()) - {row.id for row in posting_rows.values()}
    if removed_posting_ids:
        session.query(adb.Posting).filter_by(user_id=user_id).filter(adb.Posting.id.in_(removed_posting_ids)).delete(
            synchronize_session=False
        )

    removed_transaction_ids = set(existing_transaction_ids.values()) - {row.id for row in transaction_rows.values()}
    if removed_transaction_ids:
        # A `TransferLink` has no FK of its own into `transactions` — only
        # its `TransferLinkedTransaction` children do — so cascading that
        # child row alone would leave the link's other side referencing a
        # link with only one member. Deleting the whole link here instead
        # (its children cascade off `link_id`) removes both sides together.
        # `PostingMerge`/`PostingMergeDuplicate`/`CategorizationRuleExclusion`
        # don't need the same handling: each references its transaction
        # directly, so `ondelete="CASCADE"` on those columns is enough.
        stale_link_ids = {
            row.link_id
            for row in session
            .query(adb.TransferLinkedTransaction.link_id)
            .filter_by(user_id=user_id)
            .filter(adb.TransferLinkedTransaction.transaction_id.in_(removed_transaction_ids))
        }
        if stale_link_ids:
            session.query(adb.TransferLink).filter_by(user_id=user_id).filter(
                adb.TransferLink.id.in_(stale_link_ids)
            ).delete(synchronize_session=False)
        session.query(adb.Transaction).filter_by(user_id=user_id).filter(
            adb.Transaction.id.in_(removed_transaction_ids)
        ).delete(synchronize_session=False)

    session.query(adb.PostingTag).filter_by(user_id=user_id).filter(
        adb.PostingTag.posting_id.in_(imported_posting_ids)
    ).delete(synchronize_session=False)
    tag_ids = ids_by_natural_key(session, adb.Tag, user_id, [tag_id for row in rows for tag_id in row["tag_ids"]])
    session.add_all(
        adb.PostingTag(
            user_id=user_id,
            posting_id=posting_rows[row["posting_id"]].id,
            tag_id=tag_ids[tag_id],
        )
        for row in rows
        for tag_id in row["tag_ids"]
    )
    session.commit()


def _fingerprint(row: dict[str, Any]) -> _Fingerprint:
    """Build the (account, date, amount, description) tuple that makes two transactions look identical.

    Parameters
    ----------
    row
        One posting, as a plain dict (e.g. from `DataFrame.to_dicts()`).

    Returns
    -------
    _Fingerprint
        Two transactions with the same fingerprint look the same on paper, whether or not they're the same one.
    """
    return (row["account_id"], row["posted_at"], row["amount"], row["description"])


def _occurrence_suffix(transaction_id: str) -> int:
    """Read back which copy of a duplicate a transaction_id is, so duplicates sort oldest-first.

    Parameters
    ----------
    transaction_id
        A plain id, or one already reassigned to `...#N` by `_reassign_colliding_transaction_ids`.

    Returns
    -------
    in
        0 for a plain id (the first copy ever seen), N for one ending in `#N`.
    """
    if "#" not in transaction_id:
        return 0
    return int(transaction_id.rsplit("#", 1)[1])


def _existing_ids_by_fingerprint(existing: pl.DataFrame) -> dict[_Fingerprint, list[str]]:
    """Map every fingerprint already in the ledger to the transaction_ids that share it, oldest first.

    Parameters
    ----------
    existing
        The ledger as it stands before this import.

    Returns
    -------
    dict[_Fingerprint, list[str]]
        Fingerprint (see `_fingerprint`) to the transaction_ids of every existing transaction tha
        looks like it, ordered oldest-first so new duplicates match the longest-standing one first.
    """
    if existing.is_empty():
        return {}
    grouped: dict[_Fingerprint, list[str]] = defaultdict(list)
    for row in existing.filter(pl.col("posting_id").str.ends_with(":0")).to_dicts():
        grouped[_fingerprint(row)].append(row["transaction_id"])
    for transaction_ids in grouped.values():
        transaction_ids.sort(key=_occurrence_suffix)
    return grouped


def _reassign_colliding_transaction_ids(existing: pl.DataFrame, new: pl.DataFrame) -> pl.DataFrame:
    """Give same-day duplicate transactions their own id instead of letting them overwrite each other.

    Every importer builds a transaction's id by hashing the facts tha
    describe it — account, date, amount, description (see `row_hash`'s
    docstring for why it's only ever those facts, never something like
    which line of the file the row was on). That's the right call, but i
    has one side effect: two transactions that genuinely look identical —
    two coffees bought at the same place on the same morning — hash to the
    exact same id. Without this step, the second one would silently
    overwrite the first in the ledger instead of being added alongside it.

    The fix is to count instead of just hash. Say the ledger already has
    one "Starbucks $5, July 3rd", and the statement you just imported has
    two rows that also look like "Starbucks $5, July 3rd" (a real second
    coffee that day, not a duplicate upload). The first of those two new
    rows matches the one already in the ledger, so it simply reuses tha
    same id — nothing changes there. The second new row has nothing left to
    match, so it's treated as genuinely new: it gets its own id (the same
    id with `#2` appended) and is added as a second transaction. The ledger
    ends up with two coffees, not one.

    This also makes re-imports safe no matter what order the bank lists
    rows in. Re-importing that same statement next week, even if the bank
    happens to print those two rows in the opposite order this time, still
    produces the same result — because rows are matched by how many share a
    fingerprint, not by their position in the file.

    Parameters
    ----------
    existing
        The ledger as it stands before this import.
    new
        Freshly parsed postings from the file just uploaded, straight out of the importer.

    Returns
    -------
    polars.DataFrame
        `new`, with any colliding transaction_id/posting_id reassigned so distinct
        same-day transactions never collide, and re-imports of the same transaction stay stable.
    """
    if new.is_empty():
        return new

    existing_ids_by_fingerprint = _existing_ids_by_fingerprint(existing)

    rows = new.to_dicts()
    real_leg_indexes_by_id: dict[str, list[int]] = defaultdict(list)
    counterparty_leg_indexes_by_id: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        if row["posting_id"].endswith(":0"):
            real_leg_indexes_by_id[row["transaction_id"]].append(index)
        else:
            counterparty_leg_indexes_by_id[row["transaction_id"]].append(index)

    for original_id, real_indexes in real_leg_indexes_by_id.items():
        existing_ids = existing_ids_by_fingerprint.get(_fingerprint(rows[real_indexes[0]]), [])
        counterparty_indexes = counterparty_leg_indexes_by_id.get(original_id, [])

        for occurrence, real_index in enumerate(real_indexes):
            if occurrence < len(existing_ids):
                final_id = existing_ids[occurrence]
            elif occurrence == 0:
                final_id = original_id
            else:
                final_id = f"{original_id}#{occurrence + 1}"

            rows[real_index]["transaction_id"] = final_id
            rows[real_index]["posting_id"] = f"{final_id}:0"
            if occurrence < len(counterparty_indexes):
                counterparty_index = counterparty_indexes[occurrence]
                rows[counterparty_index]["transaction_id"] = final_id
                rows[counterparty_index]["posting_id"] = f"{final_id}:1"

    return pl.DataFrame(rows, schema=LEDGER_FRAME_SCHEMA)


def _merge_ledger(existing: pl.DataFrame, new: pl.DataFrame) -> pl.DataFrame:
    """Combine `existing` and `new` postings, deduped by `posting_id`, sorted chronologically.

    Returns
    -------
    polars.DataFrame
    """
    reconciled = _reassign_colliding_transaction_ids(existing, new)
    return (
        pl
        .concat([existing, reconciled], how="vertical")
        .unique(subset="posting_id", keep="last")
        .sort("posted_at", "posting_id")
    )


def _archive_raw_statement(
    institution: str, account_id: str, data: bytes, config: AccountingConfig, user_id: uuid.UUID, suffix: str = "csv"
) -> None:
    """Save one raw uploaded statement verbatim, timestamped, under this user's own archive prefix."""
    archive = StatementArchive(config.raw_statement_dir, f"statements/{user_id}")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    archive.write(f"{institution}/{account_id}/{timestamp}.{suffix}", data)


def _fallback_to_canonical_csv(
    csv_text: str,
    account_id: str,
    session: Session,
    user_id: uuid.UUID,
) -> tuple[pl.DataFrame, SkippedRowsInfo | None]:
    """Standardize via the canonical CSV importer, persisting any category it had to create along the way.

    Parameters
    ----------
    csv_text
        The raw CSV file contents.
    account_id
        The account these rows belong to.
    session
        An open database session.
    user_id
        Whose category tree the newly-created categories are merged into.

    Returns
    -------
    tuple[pl.DataFrame, SkippedRowsInfo | None]
        The standardized postings, and info about any skipped rows.
    """
    categories = seeded_categories(session, user_id)
    canonical_result = standardize_canonical_csv(csv_text, account_id, "USD", categories)
    if canonical_result.new_categories:
        merged_categories = normalize_categories({**categories, **canonical_result.new_categories})
        # Additive only — an import can mint a category it met in a file, never
        # remove one — so the merged tree is upserted without a prune.
        replace_categories(session, user_id, merged_categories.values(), prune=False)
        session.commit()
    return canonical_result.postings, canonical_result.skipped_rows


def ingest_csv(
    csv_text: str,
    institution: str,
    account_kind: str,
    account_id: str,
    config: AccountingConfig,
    session: Session,
    user_id: uuid.UUID,
) -> IngestResult:
    """Archive one uploaded CSV verbatim, standardize it, and merge the result into the ledger.

    The raw file is saved before parsing even starts, so a parse failure
    never loses the upload — per `docs/trades/architecture.md`'s "cache raw,
    derive everything else" rule, applied here the same way it already is
    for IBKR statements.

    If the bank-specific standardizer fails (validation error, parsing error),
    automatically falls back to the canonical CSV importer, which is more
    forgiving about column names and formats. This handles cases where the
    user's file doesn't match the expected bank export format exactly.

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
        Application configuration; `config.raw_statement_dir` is used (the
        raw file archive stays on disk/R2, not Postgres).
    session
        An open database session.
    user_id
        Whose ledger this is.

    Returns
    -------
    IngestResult
        How many postings were newly added.

    Raises
    ------
    UnsupportedImportError
        If no standardizer is registered for this institution/account-kind pair.
    ValueError
        If both the bank-specific and canonical standardizers fail to parse the file.
    """
    standardizer = _STANDARDIZERS.get((institution, account_kind))
    if standardizer is None:
        message = f"No importer for institution={institution!r}, account_kind={account_kind!r}."
        raise UnsupportedImportError(message)

    _archive_raw_statement(institution, account_id, csv_text.encode("utf-8"), config, user_id)

    skip_info: SkippedRowsInfo | None = None
    # Try the bank-specific standardizer first; if it fails, fall back to canonical CSV
    try:
        new_postings = standardizer(csv_text, account_id)
    except (ValueError, RuntimeError) as error:
        # Bank-specific standardizer failed (likely validation, parsing, or format mismatch).
        # Fall back to canonical CSV importer, which is more forgiving about column names/formats.
        try:
            new_postings, skip_info = _fallback_to_canonical_csv(csv_text, account_id, session, user_id=user_id)
        except (CanonicalCsvError, ValueError, KeyError, RuntimeError) as canonical_error:
            # Both standardizers failed; raise the original bank error with fallback note
            message = (
                f"Could not parse file with {institution} {account_kind} format: {error}\n\n"
                f"Also tried canonical CSV importer but it failed: {canonical_error}\n\n"
                f"Please check your file format and try again."
            )
            raise ValueError(message) from error

    existing = load_ledger(session, user_id=user_id, origin="imported")
    merged = _merge_ledger(existing, new_postings)
    validate_balanced(merged)
    _write_ledger(merged, session, user_id=user_id)
    reconcile_and_persist_rule_links(merged, session, user_id=user_id)

    return IngestResult(
        account_id=account_id,
        new_posting_count=len(merged) - len(existing),
        total_posting_count=len(merged),
        skipped_rows=skip_info,
    )


def _reject_non_importable_kind(account: Account, account_id: str) -> None:
    """Refuse to canonically import into an account kind nothing should ever independently import into.

    Unlike `ingest_csv`, this path takes an arbitrary already-registered
    `account_id` rather than being restricted by `_STANDARDIZERS` to a
    fixed set of (institution, kind) pairs — so it needs its own gate to
    keep the same guarantee `IMPORTABLE_ACCOUNT_KINDS` exists for: a
    `TransferRule` counterparty outside that set is trusted to never have
    its own independently-imported statement, and direct-repoint safety
    (see `ledger.categorization.apply_rules`) depends on that staying true.

    Raises
    ------
    UnsupportedImportError
        If `account.kind` isn't one of `IMPORTABLE_ACCOUNT_KINDS`.
    """
    if account.kind not in IMPORTABLE_ACCOUNT_KINDS:
        message = (
            f"Cannot import a statement into {account_id!r} — {account.kind!r} accounts have no independent "
            "importer; only checking, savings, credit_card, and vault accounts can be canonically imported."
        )
        raise UnsupportedImportError(message)


@dataclass(frozen=True)
class CanonicalIngestResult:
    """What happened when one CSV was ingested through the canonical fallback importer."""

    account_id: str
    new_posting_count: int
    total_posting_count: int
    new_categories: dict[str, Category]
    skipped_rows: SkippedRowsInfo | None = None  # Rows that couldn't be parsed


def ingest_canonical_csv(  # noqa: PLR0913, PLR0917 (config+session+user_id, on top of the CSV-parsing options, push this one over)
    csv_text: str,
    account_id: str,
    config: AccountingConfig,
    session: Session,
    user_id: uuid.UUID,
    separator: str | None = None,
    date_order: DateOrder = "MDY",
    category_overrides: CategoryOverrides | None = None,
) -> CanonicalIngestResult:
    """Archive one uploaded CSV verbatim, standardize it with the canonical fallback parser, and merge the result.

    Unlike `ingest_csv`, this doesn't need a registered institution/account-
    kind standardizer — see `importers.canonical.csv.standardize_canonical_csv`
    for how it guesses column names and formats instead. Any category or
    subcategory named in the file that doesn't already exist is created and
    persisted here, the same way a rule creates a new counterparty account
    the first time it matches. Still refuses to import into a non-`IMPORTABLE_ACCOUNT_KINDS`
    account (see `_reject_non_importable_kind`) — this path takes an
    arbitrary registered account, so it needs that same gate explicitly.

    Parameters
    ----------
    csv_text
        The raw CSV file contents, exactly as uploaded.
    account_id
        The account these rows belong to — must already be registered.
    config
        Application configuration; `config.raw_statement_dir` is used (the
        raw file archive stays on disk/R2, not Postgres).
    session
        An open database session.
    user_id
        Whose ledger this is.
    separator
        The column separator to use, overriding auto-detection.
    date_order
        Whether an ambiguous, all-numeric date reads month-first or day-first.
    category_overrides
        User-provided renames (and, implicitly, merges) for the categories
        and subcategories this file would otherwise auto-create.

    Returns
    -------
    CanonicalIngestResult
        How many postings were newly added, and any categories created.
    """
    account = seeded_accounts(session, user_id)[account_id]
    _reject_non_importable_kind(account, account_id)
    categories = seeded_categories(session, user_id)

    _archive_raw_statement(account.institution, account_id, csv_text.encode("utf-8"), config, user_id)
    outcome = standardize_canonical_csv(
        csv_text, account_id, account.currency, categories, separator, date_order, category_overrides
    )
    return _apply_canonical_outcome(outcome, account_id, categories, session, user_id=user_id)


def ingest_canonical_excel(
    file_bytes: bytes,
    account_id: str,
    config: AccountingConfig,
    session: Session,
    user_id: uuid.UUID,
    date_order: DateOrder = "MDY",
    category_overrides: CategoryOverrides | None = None,
) -> CanonicalIngestResult:
    """Archive one uploaded Excel workbook verbatim, standardize it, and merge the result.

    Mirrors `ingest_canonical_csv` — see `importers.canonical.csv.standardize_canonical_excel`
    for how it picks which sheet holds the transaction data.

    Parameters
    ----------
    file_bytes
        The raw `.xlsx` file contents, exactly as uploaded.
    account_id
        The account these rows belong to — must already be registered.
    config
        Application configuration; `config.raw_statement_dir` is used (the
        raw file archive stays on disk/R2, not Postgres).
    session
        An open database session.
    user_id
        Whose ledger this is.
    date_order
        Whether an ambiguous, all-numeric date reads month-first or day-first.
    category_overrides
        User-provided renames (and, implicitly, merges) for the categories
        and subcategories this file would otherwise auto-create.

    Returns
    -------
    CanonicalIngestResult
        How many postings were newly added, and any categories created.
    """
    account = seeded_accounts(session, user_id)[account_id]
    _reject_non_importable_kind(account, account_id)
    categories = seeded_categories(session, user_id)

    _archive_raw_statement(account.institution, account_id, file_bytes, config, user_id, suffix="xlsx")
    outcome = standardize_canonical_excel(
        file_bytes, account_id, account.currency, categories, date_order, category_overrides
    )
    return _apply_canonical_outcome(outcome, account_id, categories, session, user_id=user_id)


def _apply_canonical_outcome(
    outcome: CanonicalImportResult,
    account_id: str,
    categories: dict[str, Category],
    session: Session,
    user_id: uuid.UUID,
) -> CanonicalIngestResult:
    """Persist a canonical parse's new categories and merge its postings into the ledger — shared by CSV and Excel.

    Parameters
    ----------
    outcome
        What the canonical parser produced.
    account_id
        The account these rows belong to.
    categories
        The category tree as it stood before the parse, for the new
        categories to be merged into.
    session, user_id
        An open database session, and whose ledger this is.

    Returns
    -------
    CanonicalIngestResult
    """
    if outcome.new_categories:
        merged_categories = normalize_categories({**categories, **outcome.new_categories})
        # Additive only, same as `_standardize_canonical`'s own new categories.
        replace_categories(session, user_id, merged_categories.values(), prune=False)
        session.commit()

    existing = load_ledger(session, user_id=user_id, origin="imported")
    merged = _merge_ledger(existing, outcome.postings)
    validate_balanced(merged)
    _write_ledger(merged, session, user_id=user_id)
    reconcile_and_persist_rule_links(merged, session, user_id=user_id)

    return CanonicalIngestResult(
        account_id=account_id,
        new_posting_count=len(merged) - len(existing),
        total_posting_count=len(merged),
        new_categories=outcome.new_categories,
        skipped_rows=outcome.skipped_rows,
    )


def last_import_at(config: AccountingConfig, user_id: uuid.UUID) -> datetime | None:
    """Find the most recent moment any statement was archived, across every institution and account.

    Reads the timestamp encoded in each archive's own filename (see
    `_raw_statement_path`) rather than the file's on-disk mtime, so a
    copy/rsync of `data/` onto another machine can't make an old import
    look freshly done.

    Parameters
    ----------
    config
        Application configuration; `config.raw_statement_dir` is read.
    user_id
        Whose archived statements to check.

    Returns
    -------
    datetime.datetime or None
        Timezone-aware (UTC), or `None` if nothing has ever been imported.
    """
    archive = StatementArchive(config.raw_statement_dir, f"statements/{user_id}")
    relative_paths = archive.list_relative_paths("*/*/*.csv")
    timestamps: list[datetime] = []
    for relative_path in relative_paths:
        filename = relative_path.rsplit("/", 1)[-1]
        stem = filename.rsplit(".", 1)[0]
        try:
            timestamps.append(datetime.strptime(stem, "%Y%m%dT%H%M%S%f").replace(tzinfo=UTC))
        except ValueError:
            continue
    return max(timestamps) if timestamps else None


def rebuild_from_raw_statements(config: AccountingConfig, session: Session, user_id: uuid.UUID) -> pl.DataFrame:
    """Recompute the whole ledger from every archived raw CSV.

    Discards whatever ledger is currently persisted. The account id and kind
    for each archive are recovered from its own directory name
    (`raw_statement_dir/{institution}/{account_id}/...`) — no separate
    registry of "which files belong to which account" is needed.

    Only archived CSVs are replayed. PDF-based import was retired and its
    parser deleted, so any posting that was only ever derived from an old
    archived PDF is dropped by a rebuild rather than re-parsed.

    Only the `imported` half of the ledger is discarded and recomputed.
    A `manual` transaction (see `models.TransactionOrigin`) is not derived
    from any archive — no replay could put one back — so `_write_ledger`'s
    prune deliberately cannot see it, and a rebuild leaves every manual
    transfer exactly where it was. That is what the discriminator is for.

    Parameters
    ----------
    config
        Application configuration; `config.raw_statement_dir` is read.
    session
        An open database session.
    user_id
        Whose ledger this is.

    Returns
    -------
    polars.DataFrame
        The rebuilt ledger.

    Raises
    ------
    FileNotFoundError
        If no raw CSV statements have ever been archived.
    UnsupportedImportError
        If an archived directory's institution/account-kind has no registered standardizer.
    """
    archive = StatementArchive(config.raw_statement_dir, f"statements/{user_id}")
    csv_relative_paths = archive.list_relative_paths("*/*/*.csv")
    if not csv_relative_paths:
        message = f"No archived raw statements under {config.raw_statement_dir}"
        raise FileNotFoundError(message)

    accounts = seeded_accounts(session, user_id)
    frames = [pl.DataFrame(schema=LEDGER_FRAME_SCHEMA)]
    for relative_path in csv_relative_paths:
        institution, account_id, _filename = relative_path.split("/")
        # An archived-but-now-missing account is an invariant violation, not a
        # case to degrade gracefully for — `delete_account` already refuses to
        # delete any account with postings, and every archived raw statement
        # implies postings were ingested from it, so indexing directly is safe.
        account = accounts[account_id]
        standardizer = _STANDARDIZERS.get((institution, account.kind))
        if standardizer is None:
            message = f"No importer for institution={institution!r}, account_kind={account.kind!r}."
            raise UnsupportedImportError(message)
        frames.append(standardizer(archive.read(relative_path).decode("utf-8"), account_id))

    ledger = _merge_ledger(frames[0], pl.concat(frames[1:], how="vertical"))
    validate_balanced(ledger)
    _write_ledger(ledger, session, user_id=user_id)
    reconcile_and_persist_rule_links(ledger, session, user_id=user_id)
    return ledger
