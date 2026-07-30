"""Transfer-rule endpoints — create, edit and delete the rules that propose transfer links."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from accounting.api.api_models import TransferRuleCreate, TransferRuleUpdate
from accounting.api.entities import TransferRule
from accounting.api.locations import created_or_replaced, location_of
from accounting.importers.common import row_hash
from accounting.importers.ingest import load_ledger
from accounting.ledger.transfers import reconcile_and_persist_rule_links
from accounting.models import TransferRule as DomainTransferRule
from accounting.repositories.interpretation import (
    delete_transfer_rule,
    load_transfer_rules,
    remove_rule_transfer_links,
    update_transfer_rule,
    upsert_transfer_rule,
)
from accounting.taxonomy import seeded_accounts
from db.current_user import get_current_user_id
from db.session import get_db

router = APIRouter()


def _transfer_rule_id(description_contains: str, account_id: str | None, counterparty_account_id: str | None) -> str:
    """Derive a transfer rule's natural key from its own matching criteria.

    Returns
    -------
    str
    """
    return f"rule:{row_hash(description_contains, account_id or '', counterparty_account_id or '')}"


@router.get("/transfer-rules/{rule_id}")
def get_transfer_rule(
    rule_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TransferRule:
    """Return one transfer rule by id — the address `post_transfer_rule` advertises on a create.

    Returns
    -------
    TransferRule

    Raises
    ------
    HTTPException
        404 if no rule has this id.
    """
    rule = next((r for r in load_transfer_rules(session, user_id) if r.rule_id == rule_id), None)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Transfer rule {rule_id!r} not found")
    return TransferRule.from_domain(rule)


@router.post("/transfer-rules", status_code=201, responses=created_or_replaced(TransferRule))
def post_transfer_rule(
    request: TransferRuleCreate,
    http_request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TransferRule:
    """Create one new transfer rule, without touching any other rule already saved.

    Posting this again for the same
    `(description_contains, account_id, counterparty_account_id)` replaces
    that rule (its `priority`/`description` update in place, while its
    `active` toggle and accumulated exclusions are preserved) rather than
    creating a duplicate — see `PATCH /transfer-rules/{rule_id}` instead
    for editing an existing rule by id, which never risks that ambiguity.
    The status reports which of the two this call did: `201` with a
    `Location` on a create, `200` on a replace. The answer costs nothing
    extra — it is the same `existing` lookup the carry-forward below
    already needs, in the same transaction as the write.

    Stays a `POST` on the collection rather than becoming
    `PUT /transfer-rules/{rule_id}`: the id is derived from the request's
    content, but through `importers.common.row_hash`, which no client can
    compute, so there is no address a caller could name up front.

    Returns
    -------
    TransferRule
        The rule just persisted.

    Raises
    ------
    HTTPException
        404 if `account_id` or `counterparty_account_id` names an account that doesn't exist.
    """
    accounts = seeded_accounts(session, user_id)
    # Both reference real accounts (counterparty_account_id is a DB foreign key);
    # validate up front so an unknown id is a clean 404, not an IntegrityError 500
    # from the insert.
    for label, ref in (
        ("account_id", request.account_id),
        ("counterparty_account_id", request.counterparty_account_id),
    ):
        if ref is not None and ref not in accounts:
            raise HTTPException(status_code=404, detail=f"Account {ref!r} referenced by {label} does not exist")
    rule_id = _transfer_rule_id(request.description_contains, request.account_id, request.counterparty_account_id)
    # A create body can't express `active`/`excluded_transaction_ids`/`version`,
    # so when this natural key already exists, carry those forward from the rule
    # being replaced — otherwise re-posting would silently re-enable a disabled
    # rule, drop every exclusion the user built up, and answer 200 with
    # `version: 1` for a row `_upsert_rule` deliberately leaves at whatever
    # `check_and_bump_row_version` last set, so the client's next `PATCH` would
    # 409 on a version it was told to use. Only priority/description come from
    # the request.
    existing = next((r for r in load_transfer_rules(session, user_id) if r.rule_id == rule_id), None)
    rule = DomainTransferRule(
        rule_id=rule_id,
        description_contains=request.description_contains,
        account_id=request.account_id,
        counterparty_account_id=request.counterparty_account_id,
        priority=request.priority,
        description=request.description,
        active=existing.active if existing else True,
        excluded_transaction_ids=list(existing.excluded_transaction_ids) if existing else [],
        version=existing.version if existing else 1,
    )
    raw_ledger = load_ledger(session, user_id)
    upsert_transfer_rule(rule, session, user_id)
    reconcile_and_persist_rule_links(raw_ledger, session, user_id)
    if existing is None:
        location_of(http_request, response, "get_transfer_rule", rule_id=rule_id)
    else:
        response.status_code = 200
    return TransferRule.from_domain(rule)


@router.patch("/transfer-rules/{rule_id}")
def patch_transfer_rule(
    rule_id: str,
    request: TransferRuleUpdate,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> TransferRule:
    """Update one existing transfer rule in place, without touching any other rule already saved.

    A true per-resource write — unlike `POST /transfer-rules`, this
    never round-trips through a whole-store rewrite; see
    `repositories.interpretation.update_transfer_rule`. Guarded by
    `request.expected_version`, this rule's own row version, so an edit
    to this one rule can never spuriously conflict with — or be silently
    overwritten by — an unrelated save elsewhere in the store.

    Returns
    -------
    TransferRule
        The rule as persisted after the update.

    Raises
    ------
    HTTPException
        404 if no rule with `rule_id` exists.
    """
    raw_ledger = load_ledger(session, user_id)
    rule = DomainTransferRule(
        rule_id=rule_id,
        description_contains=request.description_contains,
        account_id=request.account_id,
        counterparty_account_id=request.counterparty_account_id,
        priority=request.priority,
        description=request.description,
        active=request.active,
        excluded_transaction_ids=request.excluded_transaction_ids,
    )
    updated = update_transfer_rule(session, user_id, rule, request.expected_version)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Transfer rule {rule_id!r} not found")
    session.commit()
    reconcile_and_persist_rule_links(raw_ledger, session, user_id)
    return TransferRule.from_domain(updated)


@router.delete("/transfer-rules/{rule_id}", status_code=204)
def delete_transfer_rule_route(
    rule_id: str,
    session: Annotated[Session, Depends(get_db)],
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> None:
    """Delete one transfer rule and every transfer link it created, touching no other rule.

    Deleting a rule cascades to the links it produced: a rule-created link
    (`source == "rule"`) is a consequence of the rule, so it must not outlive
    it. Manually-confirmed links are never swept up (see
    `repositories.interpretation.remove_rule_transfer_links`). The follow-up
    `reconcile_and_persist_rule_links` re-proposes only from the *remaining*
    rules, so the deleted rule's links stay gone rather than being re-derived.

    No version check — see `repositories.interpretation.delete_transfer_rule`'s own
    docstring for why deleting an already-gone rule is a plain 404, not a
    409: there's nothing left to conflict with.


    Raises
    ------
    HTTPException
        404 if no rule with `rule_id` exists.
    """
    raw_ledger = load_ledger(session, user_id)
    # Links first, rule second. `transfer_links.rule_id` is a real foreign
    # key with `ON DELETE SET NULL` now (DB-audit D7): deleting the rule
    # first would clear the column this sweep matches on and strand every
    # link the rule created. A rule that doesn't exist has no links either
    # — that is what the foreign key guarantees — so this is a no-op on the
    # 404 path below.
    remove_rule_transfer_links(session, user_id, rule_id)
    deleted = delete_transfer_rule(session, user_id, rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Transfer rule {rule_id!r} not found")
    session.commit()
    reconcile_and_persist_rule_links(raw_ledger, session, user_id)
