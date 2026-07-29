"""The accounts aggregate: the accounts money sits in, their opening balances, and the manual transfers between them.

Two tables and one projection. `opening_balances` foreign-keys straight
into `accounts` and has no independent existence — an opening balance is a
property of one account — so it belongs to this aggregate rather than to a
repository of its own.

Manual transfers used to be a third table, `manual_transfers`, holding a
date, two accounts, two amounts and a description: everything a
`transactions` row plus two `postings` already expressed, expressed a
second and incompatible way. They are ordinary ledger rows now — one
`manual`-origin transaction with two balancing legs — and
`load_manual_transfers`/`insert_manual_transfers` stay here, as the
projection between that storage and the pair-shaped `models.ManualTransfer`
the API speaks, because closing an account is the only thing that creates
one and `close_account` is an accounts-aggregate operation.

`accounts` itself is upsert-and-pruned rather than wiped and reinserted
(see `db.base.upsert_and_prune`): the ledger's `postings.account_id` is a
real foreign key into it, so a write that would orphan real transaction
history has to fail loudly instead of silently dropping it. That's also
why `replace_accounts` runs in two passes — `accounts.parent_account_id`
references the same table, so a child row can only be written once its
parent exists.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import TYPE_CHECKING

from sqlalchemy import text

import accounting.db as adb
from accounting.models import Account, ManualTransfer, OpeningBalance
from db.base import (
    ensure_reference_rows,
    ids_by_natural_key,
    merge_by_natural_key,
    natural_keys_by_id,
    upsert_and_prune,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterable, Mapping

    from sqlalchemy.orm import Session


_BROKER_CONNECTIONS_TABLE = "trades.broker_connections"
"""The other ledger's table, named here because `accounts.broker_connection_id` foreign-keys into it.

The only place this package reads across the seam, and it reads exactly one
bit: does this connection exist for this user. Written as raw SQL against
the qualified table rather than by importing `trades.db`, so the Python-side
dependency stays what the schema-side dependency already is — a name — and
`accounting` still imports nothing from `trades`.
"""


def broker_connection_exists(session: Session, user_id: uuid.UUID, connection_id: uuid.UUID) -> bool:
    """Whether `connection_id` names a broker connection this user actually has.

    The foreign key is the guarantee; this is the *diagnostic*, and the
    reason both exist. Without it a client naming a connection that isn't
    there gets an `IntegrityError` surfaced as a 500; with it,
    `POST`/`PUT /accounts` answers 404 and says which reference was bad.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose connections to look in — a connection belonging to someone
        else is "does not exist" here, exactly as Row-Level Security
        already makes it in the live app.
    connection_id
        The connection to look for.

    Returns
    -------
    bool
    """
    found = session.execute(
        text(f"SELECT 1 FROM {_BROKER_CONNECTIONS_TABLE} WHERE id = :id AND user_id = :user_id"),  # noqa: S608
        {"id": str(connection_id), "user_id": str(user_id)},
    ).first()
    return found is not None


def _account_row_by_natural_key(session: Session, user_id: uuid.UUID, account_id: str) -> adb.Account | None:
    """Fetch one account by the natural key the API addresses it with.

    Replaces `session.get(adb.Account, <derived id>)`: an id is minted by
    the database now, so the only thing a caller holding an `account_id`
    string can do is name it in the `WHERE` clause — which is one query
    either way, not a lookup followed by a fetch.

    Returns
    -------
    accounting.db.Account or None
        `None` if this user has no account with that natural key.
    """
    return session.query(adb.Account).filter_by(user_id=user_id, natural_key=account_id).one_or_none()


def _account_from_row(row: adb.Account, account_natural_key_by_id: dict[uuid.UUID, str]) -> Account:
    """Convert one persisted `Account` row back into its pydantic model, using natural keys.

    Returns
    -------
    Account
    """
    return Account(
        account_id=row.natural_key,
        name=row.name,
        kind=row.kind,  # type: ignore[arg-type]
        institution=row.institution,
        currency=row.currency,  # type: ignore[arg-type]
        last_four=row.last_four,
        parent_account_id=account_natural_key_by_id.get(row.parent_account_id)
        if row.parent_account_id is not None
        else None,
        broker_connection_id=row.broker_connection_id,
        meta=row.meta,
        closed=row.closed,
    )


def _account_row(user_id: uuid.UUID, account: Account, parent_ids: Mapping[str, uuid.UUID]) -> adb.Account:
    """Build the ORM row for one account, resolving its parent reference through `parent_ids`.

    No `id`: the column's own `uuid7()` default mints one on insert, and
    `db.base.merge_by_natural_key` fills it in from `(user_id, natural_key)`
    when this row is an update of one that already exists.

    Parameters
    ----------
    user_id
        Whose account this is.
    account
        The pydantic account to convert.
    parent_ids
        Account natural key to row id, covering at least this account's
        `parent_account_id`. Subscripted rather than `.get`, so naming a
        parent that doesn't exist raises here instead of silently storing a
        `NULL` — which would turn a vault into a top-level account. See
        `db.base.ids_by_natural_key`.

    Returns
    -------
    accounting.db.Account
    """
    return adb.Account(
        user_id=user_id,
        natural_key=account.account_id,
        name=account.name,
        kind=account.kind,
        institution=account.institution,
        currency=account.currency,
        last_four=account.last_four,
        parent_account_id=parent_ids[account.parent_account_id] if account.parent_account_id is not None else None,
        broker_connection_id=account.broker_connection_id,
        meta=account.meta,
        closed=account.closed,
    )


def load_accounts(session: Session, user_id: uuid.UUID) -> dict[str, Account]:
    """Read every account, keyed by its natural key.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose accounts to read.

    Returns
    -------
    dict[str, Account]
    """
    rows = list(session.query(adb.Account).filter_by(user_id=user_id))
    account_natural_key_by_id = {row.id: row.natural_key for row in rows}
    return {row.natural_key: _account_from_row(row, account_natural_key_by_id) for row in rows}


def replace_accounts(session: Session, user_id: uuid.UUID, accounts: Iterable[Account], *, prune: bool = True) -> None:
    """Insert-or-update every one of `accounts`, then delete this user's accounts not among them.

    Two passes, parentless accounts first, and the ordering now carries a
    second load: `accounts.parent_account_id` references this same table, so
    a child row inserted before its parent exists would violate that foreign
    key *and* — since a row's id is minted by the database rather than
    derived from its natural key — there would be no id to point at yet. The
    first pass's flush is what makes the parents' ids knowable, so the second
    pass resolves them (`db.base.ids_by_natural_key`) and writes the children.
    Both passes prune against the *complete* desired set, so a child written
    in the second pass is never swept up by the first pass's prune.

    A caller that prunes must have already cleared or repointed everything
    referencing the accounts being dropped — `opening_balances` and the
    ledger's own `postings` (a manual transfer's two legs included) both
    foreign-key into `accounts`, and a still-referenced delete fails loudly
    here rather than silently orphaning history.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose accounts these are.
    accounts
        The complete desired set when `prune` is true; just the rows to add
        or update when it isn't.
    prune
        `False` makes this purely additive — the single-account create path
        (`POST /accounts`), which must never remove an account it wasn't given.
    """
    accounts = list(accounts)
    keep_natural_keys = {account.account_id for account in accounts}
    # `accounts.institution` is a real reference now, and the vocabulary is
    # open — an importer names one, or the person types their credit union's
    # name into the account form — so the reference is created here rather
    # than requiring it to already exist. See `db.base.ensure_reference_rows`.
    ensure_reference_rows(session, adb.Institution, [account.institution for account in accounts])
    for has_parent in (False, True):
        batch = [account for account in accounts if (account.parent_account_id is not None) is has_parent]
        # Only the second pass has parents to resolve, and by then the first
        # pass has flushed them — including any it just inserted.
        parent_ids = (
            ids_by_natural_key(session, adb.Account, user_id, [account.parent_account_id for account in batch])
            if has_parent
            else {}
        )
        rows = [_account_row(user_id, account, parent_ids) for account in batch]
        if prune:
            upsert_and_prune(session, adb.Account, user_id, rows, keep_natural_keys)
        else:
            merge_by_natural_key(session, adb.Account, user_id, rows)


def update_account_fields(session: Session, user_id: uuid.UUID, account: Account) -> bool:
    """Update one account's editable columns in place, touching no other account.

    Scoped counterpart to routing an account edit through the whole-store
    save, which re-merged *every* account from the caller's (possibly
    stale) snapshot — so editing account A could silently revert a
    concurrent edit to account B. This fetches only `account.account_id`'s
    row and mutates its columns, so an UPDATE is issued for that one row
    alone. `parent_account_id` is intentionally not touched (it isn't part
    of the edit surface).

    Returns
    -------
    bool
        `True` if the account existed and was updated, `False` otherwise.
    """
    row = _account_row_by_natural_key(session, user_id, account.account_id)
    if row is None:
        return False
    ensure_reference_rows(session, adb.Institution, [account.institution])
    row.name = account.name
    row.kind = account.kind
    row.institution = account.institution
    row.currency = account.currency
    row.last_four = account.last_four
    row.broker_connection_id = account.broker_connection_id
    row.meta = account.meta
    row.closed = account.closed
    session.flush()
    return True


def set_account_closed(session: Session, user_id: uuid.UUID, account_id: str, *, closed: bool) -> bool:
    """Flip one account's `closed` flag in place, touching no other account.

    Returns
    -------
    bool
        `True` if the account existed, `False` otherwise.
    """
    row = _account_row_by_natural_key(session, user_id, account_id)
    if row is None:
        return False
    row.closed = closed
    session.flush()
    return True


def remove_account(session: Session, user_id: uuid.UUID, account_id: str) -> bool:
    """Delete one account, touching no other. Idempotent, no version check.

    The caller checks the no-postings precondition first; a delete that
    still violates a foreign key (a posting somehow references it) fails
    loudly, same as through the whole-store path.

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed.
    """
    deleted = session.query(adb.Account).filter_by(user_id=user_id, natural_key=account_id).delete()
    session.flush()
    return deleted > 0


def load_opening_balances(session: Session, user_id: uuid.UUID) -> dict[str, OpeningBalance]:
    """Read every account's opening balance, keyed by the account's natural key.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose opening balances to read.

    Returns
    -------
    dict[str, OpeningBalance]
    """
    rows = list(session.query(adb.OpeningBalance).filter_by(user_id=user_id))
    account_natural_key_by_id = natural_keys_by_id(session, adb.Account, user_id, [row.account_id for row in rows])
    return {
        account_natural_key_by_id[row.account_id]: OpeningBalance(
            account_id=account_natural_key_by_id[row.account_id], amount=row.amount, as_of_date=row.as_of_date
        )
        for row in rows
    }


def replace_opening_balances(session: Session, user_id: uuid.UUID, opening_balances: Iterable[OpeningBalance]) -> None:
    """Replace this user's whole set of opening balances, touching no other table.

    Wipe-and-reinsert: nothing foreign-keys into `opening_balances`, so each
    surviving row coming back with a *fresh* id costs nothing — an opening
    balance is only ever addressed through the account it belongs to (see
    `upsert_opening_balance`/`remove_opening_balance`), never by an id
    anything else holds. The accounts these reference must already exist —
    call `replace_accounts` first.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose opening balances these are.
    opening_balances
        The complete desired set.
    """
    opening_balances = list(opening_balances)
    session.query(adb.OpeningBalance).filter_by(user_id=user_id).delete()
    session.flush()
    account_ids = ids_by_natural_key(
        session, adb.Account, user_id, [opening_balance.account_id for opening_balance in opening_balances]
    )
    session.add_all(
        adb.OpeningBalance(
            user_id=user_id,
            account_id=account_ids[opening_balance.account_id],
            amount=opening_balance.amount,
            as_of_date=opening_balance.as_of_date,
        )
        for opening_balance in opening_balances
    )
    session.flush()


def upsert_opening_balance(opening_balance: OpeningBalance, session: Session, user_id: uuid.UUID) -> None:
    """Insert-or-update one account's opening balance, touching no other. Scoped like `planning.upsert_budget`.

    `ON CONFLICT (user_id, account_id)` — `uq_opening_balances_user_account`,
    this table's real key. `opening_balances` carries no `natural_key` of its
    own because it has no identity of its own: an opening balance *is* a
    property of one account (see this module's docstring), and that
    constraint is the statement of it. Nothing supplies `id`, so a genuinely
    new row gets one from the column's `uuid7()` default.

    Parameters
    ----------
    opening_balance
        The opening balance to persist; its `account_id` names the account.
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose opening balance this is.
    """
    account_ids = ids_by_natural_key(session, adb.Account, user_id, [opening_balance.account_id])
    session.execute(
        text(
            """
            INSERT INTO accounting.opening_balances (user_id, account_id, amount, as_of_date)
            VALUES (:user_id, :account_id, :amount, :as_of_date)
            ON CONFLICT (user_id, account_id) DO UPDATE SET
                amount = EXCLUDED.amount,
                as_of_date = EXCLUDED.as_of_date
            """
        ),
        {
            "user_id": str(user_id),
            "account_id": str(account_ids[opening_balance.account_id]),
            "amount": opening_balance.amount,
            "as_of_date": opening_balance.as_of_date,
        },
    )
    session.commit()


def remove_opening_balance(session: Session, user_id: uuid.UUID, account_id: str) -> bool:
    """Delete one account's opening balance, touching no other. Idempotent, no version check.

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed — an
        account that doesn't exist at all included, since it cannot have one.
    """
    account_ids = ids_by_natural_key(session, adb.Account, user_id, [account_id])
    if account_id not in account_ids:
        return False
    deleted = session.query(adb.OpeningBalance).filter_by(user_id=user_id, account_id=account_ids[account_id]).delete()
    session.flush()
    return deleted > 0


_MANUAL_TRANSACTION_PREFIX = "manual-transfer:"
"""What a manual transfer's `transactions.natural_key` is built from its `transfer_id`.

Unchanged from when `ledger.manual_transfers` synthesized these postings
on every read, so every posting id a manual transfer has ever had
(`manual-transfer:<transfer_id>:from` / `:to`) is the id it still has now
that the rows are real — nothing that referenced one had to be migrated.
"""

_LEG_COUNT = 2
"""A manual transfer is exactly two postings; anything else isn't one."""


def _manual_transfer_from_legs(
    transaction: adb.Transaction, legs: list[adb.Posting], account_natural_key_by_id: dict[uuid.UUID, str]
) -> ManualTransfer:
    """Rebuild the `ManualTransfer` shape from the transaction and the two postings that store it.

    The signed pair `insert_manual_transfers` wrote is read back the way it
    was written: the more-negative leg is the *from* side, the other the
    *to* side, and each magnitude is that side's own amount in its own
    account's currency. Sorting rather than testing each leg's sign is what
    makes that total — the two legs of a real transfer always straddle
    zero, but nothing here has to fall over if one somehow doesn't.

    The date and description come off the `transaction`, not off a leg:
    there is exactly one of each per transfer, which is the whole of what
    moving those columns bought (see `db.core.Transaction`). This function
    used to read them off whichever leg sorted first, with the other leg's
    identical copy silently ignored.

    Returns
    -------
    ManualTransfer
    """
    outgoing, incoming = sorted(legs, key=lambda leg: leg.amount)
    return ManualTransfer(
        transfer_id=transaction.natural_key.removeprefix(_MANUAL_TRANSACTION_PREFIX),
        date=transaction.posted_at,
        from_account_id=account_natural_key_by_id[outgoing.account_id],
        to_account_id=account_natural_key_by_id[incoming.account_id],
        from_amount=-outgoing.amount,
        to_amount=incoming.amount,
        description=transaction.description,
    )


def load_manual_transfers(session: Session, user_id: uuid.UUID) -> list[ManualTransfer]:
    """Read every hand-recorded transfer back out of the ledger it now lives in.

    There is no `manual_transfers` table any more: a manual transfer is a
    `transactions` row with `origin = "manual"` and its two balancing
    postings (see `models.ManualTransfer`), so this is a read *of the
    ledger*, projected back into the pair-shaped model the API still speaks.
    A transaction whose legs have somehow stopped being a pair is skipped
    rather than guessed at.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose transfers to read.

    Returns
    -------
    list[ManualTransfer]
        Ordered by `transfer_id`, so the store this feeds is the same list
        every time rather than whatever order the rows happened to come
        back in.
    """
    transactions = list(session.query(adb.Transaction).filter_by(user_id=user_id, origin="manual"))
    if not transactions:
        return []
    transaction_by_id = {row.id: row for row in transactions}
    legs_by_transaction_id: dict[uuid.UUID, list[adb.Posting]] = defaultdict(list)
    for posting in (
        session.query(adb.Posting).filter_by(user_id=user_id).filter(adb.Posting.transaction_id.in_(transaction_by_id))
    ):
        legs_by_transaction_id[posting.transaction_id].append(posting)
    account_natural_key_by_id = natural_keys_by_id(
        session, adb.Account, user_id, [leg.account_id for legs in legs_by_transaction_id.values() for leg in legs]
    )
    transfers = [
        _manual_transfer_from_legs(transaction, legs_by_transaction_id[transaction_id], account_natural_key_by_id)
        for transaction_id, transaction in transaction_by_id.items()
        if len(legs_by_transaction_id[transaction_id]) == _LEG_COUNT
    ]
    return sorted(transfers, key=lambda transfer: transfer.transfer_id)


def insert_manual_transfers(transfers: Iterable[ManualTransfer], session: Session, user_id: uuid.UUID) -> None:
    """Record each transfer as one `manual`-origin transaction with two balancing postings, additively.

    Scoped counterpart to routing these through the whole-store save, which
    blanket-deleted and reinserted every manual transfer from the caller's
    snapshot — so recording a transfer from a stale snapshot could drop a
    concurrently-added one. Every row is keyed by its own natural key —
    `(user_id, natural_key)`, each table's real unique constraint — so
    re-recording the same transfer is a harmless upsert rather than a
    duplicate.

    The two legs are the signed pair `models.ManualTransfer`'s positivity
    constraints exist to protect: `-from_amount` leaves one account,
    `+to_amount` arrives at the other, each in its *own* account's
    currency, never the other side's — which is why a cross-currency
    transfer is stored as the two magnitudes the user actually entered
    rather than one amount and a rate.

    Parameters
    ----------
    transfers
        The transfers to record.
    session
        An open database session; the caller commits.
    user_id
        Whose transfers these are.
    """
    transfers = list(transfers)
    if not transfers:
        return
    accounts = {
        row.natural_key: (row.id, row.currency)
        for row in session.query(adb.Account.natural_key, adb.Account.id, adb.Account.currency).filter_by(
            user_id=user_id
        )
    }
    for transfer in transfers:
        transaction_natural_key = f"{_MANUAL_TRANSACTION_PREFIX}{transfer.transfer_id}"
        # The date and the description are written once, here, rather than
        # onto each leg below — a transfer happened on one day and says one
        # thing (see `db.core.Transaction`). `DO UPDATE` rather than the
        # `DO NOTHING` this used to be: re-recording the same transfer with
        # an edited date or description has to land, and until those columns
        # moved it landed on the postings' own upsert instead.
        #
        # `RETURNING id` is how the two legs below learn which transaction to
        # point at: on an insert it is the id `uuid7()` just minted, on a
        # conflict the id the existing row already had. Either way it is one
        # statement, with no second round trip to look the row back up.
        transaction_id = session.execute(
            text(
                """
                INSERT INTO accounting.transactions (user_id, natural_key, posted_at, description, origin)
                VALUES (:user_id, :natural_key, :posted_at, :description, 'manual')
                ON CONFLICT (user_id, natural_key) DO UPDATE SET
                    posted_at = EXCLUDED.posted_at,
                    description = EXCLUDED.description
                RETURNING id
                """
            ),
            {
                "user_id": str(user_id),
                "natural_key": transaction_natural_key,
                "posted_at": transfer.date,
                "description": transfer.description,
            },
        ).scalar_one()
        legs = (
            (f"{transaction_natural_key}:from", transfer.from_account_id, -transfer.from_amount),
            (f"{transaction_natural_key}:to", transfer.to_account_id, transfer.to_amount),
        )
        for posting_natural_key, account_id, amount in legs:
            leg_account_id, leg_currency = accounts[account_id]
            session.execute(
                text(
                    """
                    INSERT INTO accounting.postings
                        (user_id, natural_key, transaction_id, account_id, amount, currency, meta)
                    VALUES
                        (:user_id, :natural_key, :transaction_id, :account_id, :amount, :currency, :meta)
                    ON CONFLICT (user_id, natural_key) DO UPDATE SET
                        account_id = EXCLUDED.account_id,
                        amount = EXCLUDED.amount,
                        currency = EXCLUDED.currency
                    """
                ),
                {
                    "user_id": str(user_id),
                    "natural_key": posting_natural_key,
                    "transaction_id": str(transaction_id),
                    "account_id": str(leg_account_id),
                    "amount": amount,
                    "currency": leg_currency,
                    "meta": json.dumps({"source": "manual_transfer"}),
                },
            )
    session.flush()
