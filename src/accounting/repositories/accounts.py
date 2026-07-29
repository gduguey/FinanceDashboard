"""The accounts aggregate: the accounts money sits in, their opening balances, and the manual transfers between them.

Three tables, one root. `opening_balances` and `manual_transfers` both
foreign-key straight into `accounts` and have no independent existence —
an opening balance is a property of one account, a manual transfer is a
pair of them — so they belong to the same aggregate rather than to
repositories of their own.

`accounts` itself is upsert-and-pruned rather than wiped and reinserted
(see `db.base.upsert_and_prune`): the ledger's `postings.account_id` is a
real foreign key into it, so a write that would orphan real transaction
history has to fail loudly instead of silently dropping it. That's also
why `replace_accounts` runs in two passes — `accounts.parent_account_id`
references the same table, so a child row can only be written once its
parent exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

import accounting.db as adb
from accounting.models import Account, ManualTransfer, OpeningBalance
from db.base import derive_id, natural_keys_by_id, upsert_and_prune

if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterable

    from sqlalchemy.orm import Session


def _account_id(user_id: uuid.UUID, account_id: str) -> uuid.UUID:
    """Derive this user's stable internal id for the account natural-keyed `account_id`.

    Returns
    -------
    uuid.UUID
    """
    return derive_id(user_id, "accounts", account_id)


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
        external_ref=row.external_ref,
        meta=row.meta,
        closed=row.closed,
    )


def _account_row(user_id: uuid.UUID, account: Account) -> adb.Account:
    """Build the ORM row for one account.

    Returns
    -------
    accounting.db.Account
    """
    return adb.Account(
        id=_account_id(user_id, account.account_id),
        user_id=user_id,
        natural_key=account.account_id,
        name=account.name,
        kind=account.kind,
        institution=account.institution,
        currency=account.currency,
        last_four=account.last_four,
        parent_account_id=_account_id(user_id, account.parent_account_id)
        if account.parent_account_id is not None
        else None,
        external_ref=account.external_ref,
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

    Two passes, parentless accounts first: `accounts.parent_account_id`
    references this same table, so a child row inserted before its parent
    exists would violate that foreign key. Both passes prune against the
    *complete* desired set, so a child written in the second pass is never
    swept up by the first pass's prune.

    A caller that prunes must have already cleared or repointed everything
    referencing the accounts being dropped — `opening_balances`,
    `manual_transfers`, and the ledger's own `postings` all foreign-key
    into `accounts`, and a still-referenced delete fails loudly here rather
    than silently orphaning history.

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
    for has_parent in (False, True):
        rows = [
            _account_row(user_id, account)
            for account in accounts
            if (account.parent_account_id is not None) is has_parent
        ]
        if prune:
            upsert_and_prune(session, adb.Account, user_id, rows, keep_natural_keys)
        else:
            for row in rows:
                session.merge(row)
            session.flush()


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
    row = session.get(adb.Account, _account_id(user_id, account.account_id))
    if row is None or row.user_id != user_id:
        return False
    row.name = account.name
    row.kind = account.kind
    row.institution = account.institution
    row.currency = account.currency
    row.last_four = account.last_four
    row.external_ref = account.external_ref
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
    row = session.get(adb.Account, _account_id(user_id, account_id))
    if row is None or row.user_id != user_id:
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
    deleted = session.query(adb.Account).filter_by(id=_account_id(user_id, account_id), user_id=user_id).delete()
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

    Wipe-and-reinsert: nothing foreign-keys into `opening_balances`, and
    every row's id is derived from its account's natural key, so a row that
    survives the rewrite comes back with the id it already had. The
    accounts these reference must already exist — call `replace_accounts`
    first.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose opening balances these are.
    opening_balances
        The complete desired set.
    """
    session.query(adb.OpeningBalance).filter_by(user_id=user_id).delete()
    session.flush()
    session.add_all(
        adb.OpeningBalance(
            id=derive_id(user_id, "opening_balances", opening_balance.account_id),
            user_id=user_id,
            account_id=_account_id(user_id, opening_balance.account_id),
            amount=opening_balance.amount,
            as_of_date=opening_balance.as_of_date,
        )
        for opening_balance in opening_balances
    )
    session.flush()


def upsert_opening_balance(opening_balance: OpeningBalance, session: Session, user_id: uuid.UUID) -> None:
    """Insert-or-update one account's opening balance, touching no other. Scoped like `planning.upsert_budget`.

    Parameters
    ----------
    opening_balance
        The opening balance to persist; its `account_id` names the account.
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose opening balance this is.
    """
    session.execute(
        text(
            """
            INSERT INTO accounting.opening_balances (id, user_id, account_id, amount, as_of_date)
            VALUES (:id, :user_id, :account_id, :amount, :as_of_date)
            ON CONFLICT (id) DO UPDATE SET
                amount = EXCLUDED.amount,
                as_of_date = EXCLUDED.as_of_date
            """
        ),
        {
            "id": str(derive_id(user_id, "opening_balances", opening_balance.account_id)),
            "user_id": str(user_id),
            "account_id": str(_account_id(user_id, opening_balance.account_id)),
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
        `True` if a row was actually deleted, `False` if none existed.
    """
    row_id = derive_id(user_id, "opening_balances", account_id)
    deleted = session.query(adb.OpeningBalance).filter_by(id=row_id, user_id=user_id).delete()
    session.flush()
    return deleted > 0


def load_manual_transfers(session: Session, user_id: uuid.UUID) -> list[ManualTransfer]:
    """Read every hand-recorded transfer between two accounts.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose transfers to read.

    Returns
    -------
    list[ManualTransfer]
    """
    rows = list(session.query(adb.ManualTransfer).filter_by(user_id=user_id))
    account_natural_key_by_id = natural_keys_by_id(
        session, adb.Account, user_id, [row.from_account_id for row in rows] + [row.to_account_id for row in rows]
    )
    return [
        ManualTransfer(
            transfer_id=row.natural_key,
            date=row.date,
            from_account_id=account_natural_key_by_id[row.from_account_id],
            to_account_id=account_natural_key_by_id[row.to_account_id],
            from_amount=row.from_amount,
            to_amount=row.to_amount,
            description=row.description,
        )
        for row in rows
    ]


def replace_manual_transfers(session: Session, user_id: uuid.UUID, transfers: Iterable[ManualTransfer]) -> None:
    """Replace this user's whole manual-transfer list, touching no other table.

    Wipe-and-reinsert, same reasoning as `replace_opening_balances`; the
    accounts on both ends must already exist.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose transfers these are.
    transfers
        The complete desired set.
    """
    session.query(adb.ManualTransfer).filter_by(user_id=user_id).delete()
    session.flush()
    session.add_all(
        adb.ManualTransfer(
            id=derive_id(user_id, "manual_transfers", transfer.transfer_id),
            user_id=user_id,
            natural_key=transfer.transfer_id,
            date=transfer.date,
            from_account_id=_account_id(user_id, transfer.from_account_id),
            to_account_id=_account_id(user_id, transfer.to_account_id),
            from_amount=transfer.from_amount,
            to_amount=transfer.to_amount,
            description=transfer.description,
        )
        for transfer in transfers
    )
    session.flush()


def insert_manual_transfers(transfers: Iterable[ManualTransfer], session: Session, user_id: uuid.UUID) -> None:
    """Insert manual-transfer rows additively, touching no existing transfer.

    Scoped counterpart to routing these through the whole-store save, which
    blanket-deleted and reinserted every manual transfer from the caller's
    snapshot — so recording a transfer from a stale snapshot could drop a
    concurrently-added one. Each row is keyed by its own derived id, so
    re-recording the same transfer is a harmless upsert rather than a
    duplicate.

    Parameters
    ----------
    transfers
        The transfers to record.
    session
        An open database session; the caller commits.
    user_id
        Whose transfers these are.
    """
    for transfer in transfers:
        session.execute(
            text(
                """
                INSERT INTO accounting.manual_transfers
                    (id, user_id, natural_key, stage, date, from_account_id, to_account_id,
                     from_amount, to_amount, description)
                VALUES
                    (:id, :user_id, :natural_key, 'manual_transfer', :date, :from_account_id,
                     :to_account_id, :from_amount, :to_amount, :description)
                ON CONFLICT (id) DO UPDATE SET
                    date = EXCLUDED.date,
                    from_account_id = EXCLUDED.from_account_id,
                    to_account_id = EXCLUDED.to_account_id,
                    from_amount = EXCLUDED.from_amount,
                    to_amount = EXCLUDED.to_amount,
                    description = EXCLUDED.description
                """
            ),
            {
                "id": str(derive_id(user_id, "manual_transfers", transfer.transfer_id)),
                "user_id": str(user_id),
                "natural_key": transfer.transfer_id,
                "date": transfer.date,
                "from_account_id": str(_account_id(user_id, transfer.from_account_id)),
                "to_account_id": str(_account_id(user_id, transfer.to_account_id)),
                "from_amount": transfer.from_amount,
                "to_amount": transfer.to_amount,
                "description": transfer.description,
            },
        )
    session.flush()
