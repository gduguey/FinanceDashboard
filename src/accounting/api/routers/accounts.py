"""Account endpoints — register, edit, close, reopen and delete accounts, plus set their opening balances."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from accounting.api.api_models import (
    AccountCloseRequest,
    AccountCloseResponse,
    AccountCreate,
    AccountUpdate,
)
from accounting.api.dependencies import _account_has_postings
from accounting.api.locations import CREATED_WITH_LOCATION, location_of
from accounting.models import Account, AccountKind, OpeningBalance
from accounting.repositories.accounts import (
    broker_connection_exists,
    insert_manual_transfers,
    load_manual_transfers,
    remove_account,
    remove_opening_balance,
    replace_accounts,
    set_account_closed,
    update_account_fields,
    upsert_opening_balance,
)
from accounting.taxonomy import seeded_accounts
from db.current_user import get_current_user_id
from db.session import get_db

router = APIRouter()


def _check_broker_link(
    session: Session, user_id: uuid.UUID, kind: AccountKind, broker_connection_id: uuid.UUID | None
) -> None:
    """Reject a broker link the database itself would reject, with a status code that says which way it is wrong.

    Both rules are enforced structurally — a foreign key into
    `trades.broker_connections` and a `CHECK` pinning the link to the
    `external_investment` kind (see `db.core.Account`). Both are also
    checked here, because a foreign-key or check violation reaches a
    client as a 500, and neither of these is a server error: one is a
    reference to something that isn't there (404) and the other is a
    malformed request (400).

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose account this is.
    kind
        The account kind being created or updated to.
    broker_connection_id
        The connection being linked, or `None` for a locally-valued account.

    Raises
    ------
    HTTPException
        404 if the connection doesn't exist; 400 if `kind` cannot carry one.
    """
    if broker_connection_id is None:
        return
    if kind != "external_investment":
        raise HTTPException(
            status_code=400,
            detail="Only an external_investment account can pull its value from a broker connection",
        )
    if not broker_connection_exists(session, user_id, broker_connection_id):
        raise HTTPException(status_code=404, detail=f"Broker connection {broker_connection_id} does not exist")


@router.get("/accounts/{account_id}")
def get_account(
    account_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Account:
    """Return one account by id — the address `post_account` advertises.

    Returns
    -------
    Account

    Raises
    ------
    HTTPException
        404 if no account has this id.
    """
    account = seeded_accounts(session, user_id).get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")
    return account


@router.post("/accounts", status_code=201, responses=CREATED_WITH_LOCATION)
def post_account(
    account: AccountCreate,
    http_request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Account:
    """Register a new account, generating its id.

    A genuine `201`: the id is a fresh `uuid4`, so this route cannot
    replace an existing account no matter what the body says.

    Returns
    -------
    Account
        The account just persisted, including its newly-generated `account_id`.

    Raises
    ------
    HTTPException
        404 if `parent_account_id` is set but names an account that doesn't
        exist, or if `broker_connection_id` names a connection that doesn't;
        400 if a broker connection is named on a non-investment account.
    """
    accounts = seeded_accounts(session, user_id)
    if account.parent_account_id is not None and account.parent_account_id not in accounts:
        raise HTTPException(status_code=404, detail=f"Parent account {account.parent_account_id!r} does not exist")
    _check_broker_link(session, user_id, account.kind, account.broker_connection_id)
    new_account = Account(
        account_id=uuid.uuid4().hex,
        name=account.name,
        kind=account.kind,
        institution=account.institution,
        currency=account.currency,
        last_four=account.last_four,
        parent_account_id=account.parent_account_id,
        broker_connection_id=account.broker_connection_id,
        meta=account.meta,
    )
    replace_accounts(session, user_id, [new_account], prune=False)
    session.commit()
    location_of(http_request, response, "get_account", account_id=new_account.account_id)
    return new_account


@router.put("/accounts/{account_id}")
def put_account(
    account_id: str,
    update: AccountUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Account:
    """Update an account — full edit if it has no postings yet, name/meta-only afterward.

    Returns
    -------
    Account
        The account after the update.

    Raises
    ------
    HTTPException
        404 if the account doesn't exist or `broker_connection_id` names a
        connection that doesn't; 400 if institution/kind/currency changed on
        an account that already has postings, or a broker connection is
        named on a non-investment account.
    """
    existing = seeded_accounts(session, user_id).get(account_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")

    locked_fields_changed = (
        update.institution != existing.institution
        or update.kind != existing.kind
        or update.currency != existing.currency
    )
    if locked_fields_changed and _account_has_postings(account_id, session, user_id):
        raise HTTPException(
            status_code=400,
            detail="This account already has transactions — only its display name and meta can be edited",
        )

    _check_broker_link(session, user_id, update.kind, update.broker_connection_id)
    updated = existing.model_copy(
        update={
            "name": update.name,
            "institution": update.institution,
            "kind": update.kind,
            "currency": update.currency,
            "last_four": update.last_four,
            "broker_connection_id": update.broker_connection_id,
            "meta": update.meta,
        }
    )
    if not update_account_fields(session, user_id, updated):
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")
    session.commit()
    return updated


@router.delete("/accounts/{account_id}", status_code=204)
def delete_account(
    account_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> None:
    """Delete an account, as long as it has no postings yet.

    Raises
    ------
    HTTPException
        404 if the account doesn't exist; 400 if it already has postings.
    """
    if account_id not in seeded_accounts(session, user_id):
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")
    if _account_has_postings(account_id, session, user_id):
        raise HTTPException(status_code=400, detail="This account already has transactions and can't be deleted")
    remove_account(session, user_id, account_id)
    session.commit()


@router.post("/accounts/{account_id}/close")
def close_account(
    account_id: str,
    request: AccountCloseRequest,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> AccountCloseResponse:
    """Mark an account closed, recording any transfers that moved its remaining balance out first.

    An account no longer open at its institution still keeps its full
    transaction history (see `models.Account.closed`) — closing never
    deletes anything. `request.transfers` (each a `models.ManualTransfer`)
    become real postings on both the closed account and wherever its
    balance went, the one case in this ledger where a posting doesn't
    trace back to an imported statement.

    Returns
    -------
    AccountCloseResponse
        The account and every manual transfer after the update.

    Raises
    ------
    HTTPException
        404 if the account doesn't exist; 400 if a transfer doesn't move
        money out of `account_id`, or names an unknown `to_account_id`.
    """
    accounts = seeded_accounts(session, user_id)
    # Read before the insert below, so the response lists each transfer once.
    existing_transfers = load_manual_transfers(session, user_id)
    account = accounts.get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")
    for transfer in request.transfers:
        if transfer.from_account_id != account_id:
            raise HTTPException(status_code=400, detail="Each transfer must move money out of the account being closed")
        if transfer.to_account_id not in accounts:
            raise HTTPException(status_code=400, detail=f"Account {transfer.to_account_id!r} not found")

    updated_account = account.model_copy(update={"closed": True})
    if not set_account_closed(session, user_id, account_id, closed=True):
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")
    insert_manual_transfers(request.transfers, session, user_id)
    session.commit()
    return AccountCloseResponse(account=updated_account, manual_transfers=[*existing_transfers, *request.transfers])


@router.post("/accounts/{account_id}/reopen")
def reopen_account(
    account_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> Account:
    """Clear an account's `closed` flag, without touching any transfers recorded when it was closed.

    Returns
    -------
    Account
        The account after the update.

    Raises
    ------
    HTTPException
        404 if the account doesn't exist.
    """
    account = seeded_accounts(session, user_id).get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")
    updated_account = account.model_copy(update={"closed": False})
    if not set_account_closed(session, user_id, account_id, closed=False):
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")
    session.commit()
    return updated_account


@router.put("/accounts/{account_id}/opening-balance")
def put_opening_balance(
    account_id: str,
    opening_balance: OpeningBalance,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> OpeningBalance:
    """Set the balance an account already held the day before its first posting.

    Returns
    -------
    OpeningBalance
        The opening balance just persisted.

    Raises
    ------
    HTTPException
        404 if the account doesn't exist; 400 if `opening_balance.account_id` doesn't match the path.
    """
    if account_id not in seeded_accounts(session, user_id):
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")
    if opening_balance.account_id != account_id:
        raise HTTPException(status_code=400, detail="account_id in the body must match the URL")
    upsert_opening_balance(opening_balance, session, user_id)
    return opening_balance


@router.delete("/accounts/{account_id}/opening-balance", status_code=204)
def delete_opening_balance(
    account_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> None:
    """Remove an account's opening balance, if it has one."""
    remove_opening_balance(session, user_id, account_id)
    session.commit()
