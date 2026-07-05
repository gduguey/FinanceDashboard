"""JSON-over-HTTP view of the accounting module: this file serializes, `dashboard.*`/`ledger.*` compute.

Mounted as a router onto the same FastAPI app `trades.api` already runs
(see that module's own `app.include_router` call), so the web dashboard's
one dev server serves both. State lives on this module rather than on the
shared `app` object, so accounting stays importable — and testable —
without ever importing `trades.api` itself, keeping the one-directional
coupling (`accounting` reads `trades`, never the reverse) intact at the
API layer too.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Annotated, Any, cast

import polars as pl
from fastapi import APIRouter, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from accounting.config import AccountingConfig
from accounting.dashboard import budgets, income_statement, interest, simulator
from accounting.dashboard.net_worth import net_worth_summary
from accounting.dashboard.paystub import propose_posting_splits, reconcile_earnings_statement
from accounting.importers.detect import detect_bank_account
from accounting.importers.ingest import (
    UnsupportedImportError,
    ingest_csv,
    ingest_sofi_statement_pdf,
    load_ledger,
    rebuild_from_raw_statements,
    supported_import_kinds,
)
from accounting.importers.paystub import extract_paystub_pdf_text, parse_earnings_statement_text
from accounting.ledger.categorization import apply_manual_overrides, apply_posting_splits, apply_rules
from accounting.ledger.currency import DisplayCurrency, convert
from accounting.ledger.replay import account_balances_over_time
from accounting.ledger.transfers import find_unmatched_transfer_candidates
from accounting.llm import categorize
from accounting.llm.gemini import GeminiProvider
from accounting.llm.mistral import MistralProvider
from accounting.llm.provider import LLMProvider, LLMProviderError, complete_with_fallback
from accounting.llm.settings import LLMCredentials
from accounting.llm.usage import RESET_PERIOD, TrackedProvider, load_usage
from accounting.market_data import exchange_rates
from accounting.models import (
    BASE_CURRENCY,
    SUPPORTED_CURRENCIES,
    Account,
    AccountKind,
    Budget,
    Category,
    CategoryClassification,
    CurrencyCode,
    GeneralBudget,
    ManualOverride,
    OpeningBalance,
    OtherAsset,
    PostingSplit,
    PostingSplitLeg,
    Rule,
    SimulatorScenario,
    Tag,
)
from accounting.store import (
    AccountingStore,
    load_overrides,
    load_store,
    normalize_categories,
    save_overrides,
    save_store,
)


class _State:
    """Everything a running server needs, held off the shared `app` object so tests can swap it per-test."""

    def __init__(self) -> None:
        self.config = AccountingConfig()


state = _State()
router = APIRouter(prefix="/api/accounting")


def _resolved_postings_and_store(config: AccountingConfig) -> tuple[Any, Any]:
    """Load the raw ledger and resolve it against the current rules and manual overrides.

    A rule only ever repoints a posting at an account that already exists
    in the store, never creates one — so unlike importing a statement
    (which does register a new account), this is a pure read with no side
    effect to persist. Manual overrides are never written back here
    either; they already live in their own file and are only ever applied
    on top.

    Returns
    -------
    tuple[polars.DataFrame, accounting.store.AccountingStore]
        The fully resolved postings, and the current store.
    """
    raw = load_ledger(config)
    store = load_store(config)
    resolved = apply_rules(raw, store.rules, store.accounts)
    resolved = apply_posting_splits(resolved, store.posting_splits)
    overrides = load_overrides(config)
    resolved = apply_manual_overrides(resolved, overrides)
    return resolved, store


def _account_has_postings(account_id: str, config: AccountingConfig) -> bool:
    """Check whether any imported posting has ever been assigned to this account.

    Used to enforce the accounts-CRUD rule: an account's institution,
    kind, and currency (and the account itself) may only be edited or
    deleted before any real transaction has landed on it — afterward,
    only its display name may change.

    Returns
    -------
    bool
        `True` if at least one posting in the raw ledger references this account.
    """
    ledger = load_ledger(config)
    if ledger.is_empty():
        return False
    return bool(ledger.filter(pl.col("account_id") == account_id).height > 0)


@router.get("/store")
def get_store() -> dict[str, Any]:
    """Return every persisted accounting entity: accounts, categories, tags, rules, other assets.

    Returns
    -------
    dict[str, Any]
        `accounts`, `categories`, `tags`, `opening_balances` (each a dict
        keyed by id), `rules`, `other_assets`, `budgets` (each a list).
    """
    _postings, store = _resolved_postings_and_store(state.config)
    return {
        "accounts": {account_id: account.model_dump(mode="json") for account_id, account in store.accounts.items()},
        "categories": {cat_id: category.model_dump(mode="json") for cat_id, category in store.categories.items()},
        "tags": {tag_id: tag.model_dump(mode="json") for tag_id, tag in store.tags.items()},
        "rules": [rule.model_dump(mode="json") for rule in store.rules],
        "other_assets": [asset.model_dump(mode="json") for asset in store.other_assets],
        "opening_balances": {
            account_id: balance.model_dump(mode="json") for account_id, balance in store.opening_balances.items()
        },
        "budgets": [budget.model_dump(mode="json") for budget in store.budgets],
        "simulator_scenarios": [scenario.model_dump(mode="json") for scenario in store.simulator_scenarios],
        "posting_splits": {pid: split.model_dump(mode="json") for pid, split in store.posting_splits.items()},
        "general_budgets": {cat_id: budget.model_dump(mode="json") for cat_id, budget in store.general_budgets.items()},
    }


@router.get("/currencies")
def get_currencies() -> list[dict[str, Any]]:
    """List every currency this app knows how to hold money in.

    Returns
    -------
    list[dict[str, Any]]
        One entry per supported currency, `{"code", "symbol", "decimal_places"}`.
    """
    return [currency.model_dump(mode="json") for currency in SUPPORTED_CURRENCIES.values()]


def _display_currency(
    code: CurrencyCode, store: AccountingStore | None = None, as_of: date | None = None
) -> DisplayCurrency:
    """Build a `DisplayCurrency` from the cached exchange-rate history's smoothed rate as of a date.

    Only ever requires history for the currencies actually in play —
    `code` itself, plus every account's and other-asset's own currency
    when `store` is given — never every `CurrencyCode` this app could
    theoretically support, so a store with no EUR accounts yet isn't
    blocked from a USD-only net worth just because EUR was never synced.

    Parameters
    ----------
    code
        The currency to display aggregates in.
    store
        The accounting store, to find every currency actually in use;
        `None` (e.g. a standalone rate lookup) only requires `code` itself.
    as_of
        The date to compute the smoothed rate as of; defaults to today.

    Returns
    -------
    DisplayCurrency

    Raises
    ------
    HTTPException
        400 if exchange rates have never been synced (or lack history for
        a needed currency) — sync first, rather than silently guessing a rate.
    """
    needed = {code}
    if store is not None:
        needed.update(account.currency for account in store.accounts.values())
        needed.update(asset.currency for asset in store.other_assets)
    history = exchange_rates.load_rate_history(state.config)
    try:
        rates = exchange_rates.current_rates_to_base(history, as_of or datetime.now(tz=UTC).date(), needed)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return DisplayCurrency(code, rates)


@router.post("/sync-exchange-rates")
def post_sync_exchange_rates() -> dict[str, Any]:
    """Re-fetch exchange-rate history from Frankfurter and overwrite the cache.

    Returns
    -------
    dict[str, Any]
        `as_of`, and every non-base currency's freshly smoothed rate into `accounting.models.BASE_CURRENCY`.
    """
    history = exchange_rates.update_rate_history_cache(state.config)
    as_of = datetime.now(tz=UTC).date()
    rates = exchange_rates.current_rates_to_base(history, as_of)
    return {"as_of": as_of.isoformat(), "base_currency": BASE_CURRENCY, "rates_to_base": rates}


@router.get("/exchange-rates/current")
def get_current_exchange_rate(currency: CurrencyCode) -> dict[str, Any]:
    """Return the smoothed rate this app currently uses for one currency, and how it's computed.

    Returns
    -------
    dict[str, Any]
        `currency`, `base_currency`, `rate_to_base`, `as_of`, `window_days`.
    """
    as_of = datetime.now(tz=UTC).date()
    display = _display_currency(currency, as_of=as_of)
    return {
        "currency": currency,
        "base_currency": BASE_CURRENCY,
        "rate_to_base": display.rates_to_base[currency],
        "as_of": as_of.isoformat(),
        "window_days": exchange_rates.DEFAULT_SMOOTHING_WINDOW_DAYS,
    }


@router.get("/exchange-rates/history")
def get_exchange_rate_history(currency: CurrencyCode) -> list[dict[str, Any]]:
    """Return the cached daily rate history for one currency, alongside the smoothed value at each point.

    Returns
    -------
    list[dict[str, Any]]
        One `{"date", "rate", "smoothed_rate"}` per cached day, oldest first.

    Raises
    ------
    HTTPException
        400 if exchange rates have never been synced.
    """
    history = exchange_rates.load_rate_history(state.config)
    series = history.filter(pl.col("currency") == currency).sort("date")
    if series.is_empty():
        raise HTTPException(
            status_code=400, detail=f"No exchange-rate history for {currency!r} — sync exchange rates first."
        )
    return [
        {
            "date": row["date"].isoformat(),
            "rate": row["rate_to_base"],
            "smoothed_rate": exchange_rates.smoothed_rate_as_of(history, currency, row["date"]),
        }
        for row in series.iter_rows(named=True)
    ]


@router.put("/categories")
def put_categories(categories: dict[str, Category]) -> dict[str, Any]:
    """Replace the whole category tree, enforcing the "Other" catch-all subcategory invariant.

    Returns
    -------
    dict[str, Any]
        The categories just persisted, keyed by `category_id` — may
        include an "Other" subcategory the caller didn't submit, or omit
        one it did (see `store.normalize_categories`).
    """
    store = load_store(state.config)
    store = store.model_copy(update={"categories": normalize_categories(categories)})
    save_store(store, state.config)
    return {cat_id: category.model_dump(mode="json") for cat_id, category in store.categories.items()}


@router.put("/tags")
def put_tags(tags: dict[str, Tag]) -> dict[str, Any]:
    """Replace the whole tag list.

    Returns
    -------
    dict[str, Any]
        The tags just persisted, keyed by `tag_id`.
    """
    store = load_store(state.config)
    store = store.model_copy(update={"tags": tags})
    save_store(store, state.config)
    return {tag_id: tag.model_dump(mode="json") for tag_id, tag in store.tags.items()}


@router.put("/rules")
def put_rules(rules: list[Rule]) -> list[dict[str, Any]]:
    """Replace the whole rule list.

    Returns
    -------
    list[dict[str, Any]]
        The rules just persisted.
    """
    store = load_store(state.config)
    store = store.model_copy(update={"rules": rules})
    save_store(store, state.config)
    return [rule.model_dump(mode="json") for rule in store.rules]


@router.put("/other-assets")
def put_other_assets(other_assets: list[OtherAsset]) -> list[dict[str, Any]]:
    """Replace the whole manually-entered-asset list.

    Returns
    -------
    list[dict[str, Any]]
        The assets just persisted.
    """
    store = load_store(state.config)
    store = store.model_copy(update={"other_assets": other_assets})
    save_store(store, state.config)
    return [asset.model_dump(mode="json") for asset in store.other_assets]


@router.put("/budgets")
def put_budgets(budgets: list[Budget]) -> list[dict[str, Any]]:
    """Replace the whole budget list, across every month.

    Returns
    -------
    list[dict[str, Any]]
        The budgets just persisted.
    """
    store = load_store(state.config)
    store = store.model_copy(update={"budgets": budgets})
    save_store(store, state.config)
    return [budget.model_dump(mode="json") for budget in store.budgets]


@router.put("/general-budgets")
def put_general_budgets(general_budgets: dict[str, GeneralBudget]) -> dict[str, Any]:
    """Replace the whole general-budget map, keyed by `category_id` — the same amount applies to every month.

    Stored, edited, and displayed completely separately from `Budget`'s
    per-month rows (see `models.GeneralBudget`); this never falls back to
    or overwrites a per-month budget, or vice versa.

    Returns
    -------
    dict[str, Any]
        The general budgets just persisted.
    """
    store = load_store(state.config)
    store = store.model_copy(update={"general_budgets": general_budgets})
    save_store(store, state.config)
    return {cat_id: budget.model_dump(mode="json") for cat_id, budget in store.general_budgets.items()}


@router.put("/simulator/scenarios")
def put_simulator_scenarios(scenarios: list[SimulatorScenario]) -> list[dict[str, Any]]:
    """Replace the whole saved-scenario list.

    Returns
    -------
    list[dict[str, Any]]
        The scenarios just persisted.
    """
    store = load_store(state.config)
    store = store.model_copy(update={"simulator_scenarios": scenarios})
    save_store(store, state.config)
    return [scenario.model_dump(mode="json") for scenario in store.simulator_scenarios]


@router.get("/simulator/project")
def get_simulator_projection(
    initial_capital: float,
    monthly_contribution: float,
    horizon_years: float,
    annual_rate_pct: float,
    compounding_frequency: simulator.CompoundingFrequency = "monthly",
) -> list[dict[str, Any]]:
    """Project a compound-interest scenario forward, month by month.

    Returns
    -------
    list[dict[str, Any]]
        See `dashboard.simulator.ProjectionPoint`.
    """
    points = simulator.project(
        initial_capital, monthly_contribution, horizon_years, annual_rate_pct, compounding_frequency
    )
    return [vars(point) for point in points]


@router.post("/accounts")
def post_account(account: Account) -> dict[str, Any]:
    """Register a new account.

    Returns
    -------
    dict[str, Any]
        The account just persisted.

    Raises
    ------
    HTTPException
        409 if an account with this id already exists.
    """
    store = load_store(state.config)
    if account.account_id in store.accounts:
        raise HTTPException(status_code=409, detail=f"Account {account.account_id!r} already exists")
    store = store.model_copy(update={"accounts": {**store.accounts, account.account_id: account}})
    save_store(store, state.config)
    return account.model_dump(mode="json")


class AccountUpdate(BaseModel):
    """Request body for `PUT /api/accounting/accounts/{account_id}`.

    `institution`, `kind`, and `currency` may only differ from the
    account's current values while it has no postings yet — enforced in
    `put_account`, not here, since that check needs the ledger.
    """

    name: str
    institution: str
    kind: AccountKind
    currency: CurrencyCode
    meta: dict[str, str] = Field(default_factory=dict)


@router.put("/accounts/{account_id}")
def put_account(account_id: str, update: AccountUpdate) -> dict[str, Any]:
    """Update an account — full edit if it has no postings yet, name/meta-only afterward.

    Returns
    -------
    dict[str, Any]
        The account after the update.

    Raises
    ------
    HTTPException
        404 if the account doesn't exist; 400 if institution/kind/currency
        changed on an account that already has postings.
    """
    store = load_store(state.config)
    existing = store.accounts.get(account_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")

    locked_fields_changed = (
        update.institution != existing.institution
        or update.kind != existing.kind
        or update.currency != existing.currency
    )
    if locked_fields_changed and _account_has_postings(account_id, state.config):
        raise HTTPException(
            status_code=400,
            detail="This account already has transactions — only its display name and meta can be edited",
        )

    updated = existing.model_copy(
        update={
            "name": update.name,
            "institution": update.institution,
            "kind": update.kind,
            "currency": update.currency,
            "meta": update.meta,
        }
    )
    store = store.model_copy(update={"accounts": {**store.accounts, account_id: updated}})
    save_store(store, state.config)
    return updated.model_dump(mode="json")


@router.delete("/accounts/{account_id}")
def delete_account(account_id: str) -> dict[str, str]:
    """Delete an account, as long as it has no postings yet.

    Returns
    -------
    dict[str, str]
        `{"account_id": ...}` of the account just deleted.

    Raises
    ------
    HTTPException
        404 if the account doesn't exist; 400 if it already has postings.
    """
    store = load_store(state.config)
    if account_id not in store.accounts:
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")
    if _account_has_postings(account_id, state.config):
        raise HTTPException(status_code=400, detail="This account already has transactions and can't be deleted")
    remaining = {aid: account for aid, account in store.accounts.items() if aid != account_id}
    store = store.model_copy(update={"accounts": remaining})
    save_store(store, state.config)
    return {"account_id": account_id}


@router.put("/accounts/{account_id}/opening-balance")
def put_opening_balance(account_id: str, opening_balance: OpeningBalance) -> dict[str, Any]:
    """Set the balance an account already held the day before its first posting.

    Returns
    -------
    dict[str, Any]
        The opening balance just persisted.

    Raises
    ------
    HTTPException
        404 if the account doesn't exist; 400 if `opening_balance.account_id` doesn't match the path.
    """
    store = load_store(state.config)
    if account_id not in store.accounts:
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")
    if opening_balance.account_id != account_id:
        raise HTTPException(status_code=400, detail="account_id in the body must match the URL")
    store = store.model_copy(update={"opening_balances": {**store.opening_balances, account_id: opening_balance}})
    save_store(store, state.config)
    return opening_balance.model_dump(mode="json")


@router.delete("/accounts/{account_id}/opening-balance")
def delete_opening_balance(account_id: str) -> dict[str, str]:
    """Remove an account's opening balance, if it has one.

    Returns
    -------
    dict[str, str]
        `{"account_id": ...}` of the account whose opening balance was cleared.
    """
    store = load_store(state.config)
    remaining = {aid: value for aid, value in store.opening_balances.items() if aid != account_id}
    store = store.model_copy(update={"opening_balances": remaining})
    save_store(store, state.config)
    return {"account_id": account_id}


class DetectRequest(BaseModel):
    """Request body for `POST /api/accounting/detect`."""

    header: list[str]
    filename: str
    first_data_row: dict[str, str] | None = None


@router.post("/detect")
def post_detect(request: DetectRequest) -> dict[str, Any] | None:
    """Guess the institution, account kind, and account id a CSV's header, filename, and first row describe.

    Returns
    -------
    dict[str, Any] or None
        The best guess, or `None` if nothing matched.
    """
    detected = detect_bank_account(request.header, request.filename, request.first_data_row)
    return None if detected is None else vars(detected)


@router.get("/supported-import-kinds")
def get_supported_import_kinds() -> list[dict[str, str]]:
    """List every `(institution, account_kind)` pair with a registered CSV standardizer.

    Returns
    -------
    list[dict[str, str]]
        One `{"institution": ..., "account_kind": ...}` dict per supported pair — lets the UI flag any
        registered account that has no importer able to actually parse a statement for it.
    """
    return [
        {"institution": institution, "account_kind": account_kind}
        for institution, account_kind in sorted(supported_import_kinds())
    ]


@router.post("/import")
async def post_import(
    file: UploadFile,
    institution: Annotated[str, Form()],
    account_kind: Annotated[str, Form()],
    account_id: Annotated[str, Form()],
    account_name: Annotated[str, Form()],
    currency: Annotated[str, Form()] = "USD",
    parent_account_id: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """Register the account if it's new, then archive and import the uploaded CSV.

    Returns
    -------
    dict[str, Any]
        `account_id`, `new_posting_count`, `total_posting_count`.

    Raises
    ------
    HTTPException
        400 if no importer exists for this institution/account-kind combination.
    """
    store = load_store(state.config)
    if account_id not in store.accounts:
        account_kind_literal: Any = account_kind
        new_account = Account(
            account_id=account_id,
            name=account_name,
            kind=account_kind_literal,
            institution=institution,
            currency=currency,  # type: ignore[arg-type]
            parent_account_id=parent_account_id,
        )
        store = store.model_copy(update={"accounts": {**store.accounts, account_id: new_account}})
        save_store(store, state.config)

    csv_text = (await file.read()).decode("utf-8")
    try:
        result = ingest_csv(csv_text, institution, account_kind, account_id, state.config)
    except UnsupportedImportError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {
        "account_id": result.account_id,
        "new_posting_count": result.new_posting_count,
        "total_posting_count": result.total_posting_count,
    }


@router.post("/import/sofi-statement-pdf")
async def post_sofi_statement_pdf(file: UploadFile) -> dict[str, Any]:
    """Archive and import a SoFi monthly statement PDF — checking, savings, and every vault in one file.

    SoFi Vaults have no CSV export; their interest and transfers only ever
    show up here. Registers or refreshes every account the statement
    describes itself — unlike `post_import`, no institution/account-kind/
    account-id form fields are needed from the caller.

    Returns
    -------
    dict[str, Any]
        `account_ids`, `new_posting_count`, `total_posting_count`.

    Raises
    ------
    HTTPException
        400 if the PDF has no recognizable SoFi savings account section.
    """
    pdf_bytes = await file.read()
    try:
        result = ingest_sofi_statement_pdf(pdf_bytes, state.config)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {
        "account_ids": result.account_ids,
        "new_posting_count": result.new_posting_count,
        "total_posting_count": result.total_posting_count,
    }


@router.post("/import/paystub")
async def post_paystub_reconciliation(file: UploadFile) -> dict[str, Any]:
    """Parse a paystub PDF and reconcile its deposits against real bank postings near pay day.

    Read-only — never applies anything automatically. `proposed_splits`
    suggests how to categorize each matched deposit (salary vs.
    reimbursement legs), for the user to review, edit, and confirm; use
    `PUT /postings/{posting_id}/split` (or `/override` for a single-leg
    proposal) to actually apply one.

    Returns
    -------
    dict[str, Any]
        `statement` (the parsed `EarningsStatement`), `matches` (one
        `{label, amount, account_last4, posting_id, account_id}` per
        deposit — `posting_id`/`account_id` `None` if unmatched),
        `is_fully_matched`, and `proposed_splits` (one per matched
        deposit — see `dashboard.paystub.ProposedSplit`).

    Raises
    ------
    HTTPException
        400 if the PDF's text doesn't match this module's expected paystub layout — see `importers.paystub`'s
        docstring: its patterns are a generic starting point, not tuned against every payroll provider.
    """
    pdf_bytes = await file.read()
    text = extract_paystub_pdf_text(pdf_bytes)
    try:
        statement = parse_earnings_statement_text(text)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    postings, store = _resolved_postings_and_store(state.config)
    result = reconcile_earnings_statement(statement, postings, store.accounts)
    proposed_splits = propose_posting_splits(statement, result.matches)
    return {
        "statement": statement.model_dump(mode="json"),
        "matches": [
            {
                "label": match.deposit.label,
                "amount": match.deposit.amount,
                "account_last4": match.deposit.account_last4,
                "posting_id": match.posting_id,
                "account_id": match.account_id,
            }
            for match in result.matches
        ],
        "is_fully_matched": result.is_fully_matched,
        "proposed_splits": [
            {
                "posting_id": proposal.posting_id,
                "account_id": proposal.account_id,
                "legs": [vars(leg) for leg in proposal.legs],
            }
            for proposal in proposed_splits
        ],
    }


@router.post("/rebuild")
def post_rebuild() -> dict[str, Any]:
    """Recompute the whole posting ledger from every archived raw CSV.

    Returns
    -------
    dict[str, Any]
        `total_posting_count`.

    Raises
    ------
    HTTPException
        404 if nothing has ever been imported.
    """
    try:
        ledger = rebuild_from_raw_statements(state.config)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"total_posting_count": len(ledger)}


@router.get("/postings")
def get_postings() -> list[dict[str, Any]]:
    """Return every posting, resolved against the current rules and manual overrides.

    Returns
    -------
    list[dict[str, Any]]
        One dict per posting.
    """
    postings, _store = _resolved_postings_and_store(state.config)
    return postings.to_dicts()


@router.put("/postings/{posting_id}/override")
def put_posting_override(posting_id: str, override: ManualOverride) -> dict[str, Any]:
    """Upsert one posting's manual override, merging into any override already stored for it.

    A request only ever carries the fields the caller actually means to
    change (e.g. setting a subcategory sends just `subcategory_id`) — so a
    field absent from this request must fall back to whatever was already
    stored, never reset to `None`, or an earlier edit (like a manually-set
    category) would be wiped out by a later, unrelated one (like picking a
    subcategory). `model_fields_set` is what distinguishes "the caller sent
    this field, possibly as null, to clear it" from "the caller didn't
    mention this field at all".

    Returns
    -------
    dict[str, Any]
        The override just persisted, merged with any prior one.
    """
    overrides = load_overrides(state.config)
    existing = overrides.get(posting_id)
    if existing is not None:
        merged = existing.model_dump()
        merged.update(override.model_dump(include=override.model_fields_set))
        override = ManualOverride(**merged)
    overrides[posting_id] = override
    save_overrides(overrides, state.config)
    return override.model_dump(mode="json")


_SPLIT_ZERO_SUM_TOLERANCE = 1e-6


def _current_amount_for_split(postings: pl.DataFrame, posting_id: str) -> float | None:
    """Return the amount a split of `posting_id` must sum to — its own amount, or (if already split) its legs' total.

    Returns
    -------
    float or None
        `None` if no posting or split leg with this id exists at all.
    """
    direct = postings.filter(pl.col("posting_id") == posting_id)
    if not direct.is_empty():
        return float(direct["amount"][0])
    legs = postings.filter(pl.col("posting_id").str.starts_with(f"{posting_id}:split:"))
    if legs.is_empty():
        return None
    return float(legs["amount"].sum())


@router.put("/postings/{posting_id}/split")
def put_posting_split(posting_id: str, legs: list[PostingSplitLeg]) -> dict[str, Any]:
    """Split one posting into several independently-categorized legs, e.g. a paycheck into wage + reimbursement.

    Overwrites any split already stored for this posting — unlike
    `put_posting_override`'s field-level merge, a split is one coherent
    set of legs, not independently-settable fields, so there's nothing
    meaningful to merge.

    Returns
    -------
    dict[str, Any]
        The split just persisted.

    Raises
    ------
    HTTPException
        404 if the posting doesn't exist; 400 if the legs don't sum to the posting's own amount.
    """
    postings, store = _resolved_postings_and_store(state.config)
    current_amount = _current_amount_for_split(postings, posting_id)
    if current_amount is None:
        raise HTTPException(status_code=404, detail=f"Posting {posting_id!r} not found")
    total = sum(leg.amount for leg in legs)
    if abs(total - current_amount) > _SPLIT_ZERO_SUM_TOLERANCE:
        raise HTTPException(
            status_code=400, detail=f"Legs sum to {total}, not the posting's own amount of {current_amount}"
        )
    split = PostingSplit(posting_id=posting_id, legs=legs)
    store = store.model_copy(update={"posting_splits": {**store.posting_splits, posting_id: split}})
    save_store(store, state.config)
    return split.model_dump(mode="json")


@router.delete("/postings/{posting_id}/split")
def delete_posting_split(posting_id: str) -> dict[str, str]:
    """Undo a posting split, restoring the single original posting.

    Returns
    -------
    dict[str, str]
        `{"posting_id": ...}`.
    """
    store = load_store(state.config)
    remaining = {pid: split for pid, split in store.posting_splits.items() if pid != posting_id}
    store = store.model_copy(update={"posting_splits": remaining})
    save_store(store, state.config)
    return {"posting_id": posting_id}


_MAX_FEW_SHOT_EXAMPLES = 20


def _llm_providers() -> list[LLMProvider]:
    """Build the default-Gemini-then-Mistral fallback chain from whichever API keys `.env` actually has set.

    Every provider is wrapped in `TrackedProvider` so each call's outcome
    is recorded to `llm_usage.json` regardless of which provider in the
    chain ends up being tried (see `get_llm_usage`).

    Returns
    -------
    list[LLMProvider]
        Gemini first (if `GEMINI_API_KEY` is set), then Mistral (if `MISTRAL_API_KEY` is set) — empty if neither is.
    """
    credentials = LLMCredentials()
    providers: list[LLMProvider] = []
    if credentials.gemini_api_key is not None:
        providers.append(
            TrackedProvider(
                GeminiProvider(credentials.gemini_api_key.get_secret_value()), "gemini", state.config.llm_usage_path
            )
        )
    if credentials.mistral_api_key is not None:
        providers.append(
            TrackedProvider(
                MistralProvider(credentials.mistral_api_key.get_secret_value()),
                "mistral",
                state.config.llm_usage_path,
            )
        )
    return providers


@router.get("/llm-usage")
def get_llm_usage() -> dict[str, Any]:
    """Return each LLM provider's self-tracked call count this period, and whether it's currently rate-limited.

    Returns
    -------
    dict[str, Any]
        Keyed by provider name (`"gemini"`, `"mistral"`). Each entry has
        `configured` (whether an API key is set for it at all),
        `used_count`, `period` (`"daily"` or `"monthly"` — see
        `llm.usage.RESET_PERIOD`), `is_limited`, and `last_error` (the
        provider's own error text from the last refused call, `None` if
        it hasn't been refused since its count last reset).
    """
    credentials = LLMCredentials()
    configured = {
        "gemini": credentials.gemini_api_key is not None,
        "mistral": credentials.mistral_api_key is not None,
    }
    usage = load_usage(state.config.llm_usage_path)
    return {
        provider: {
            "configured": configured[provider],
            "used_count": entry.used_count,
            "period": RESET_PERIOD[provider],
            "is_limited": entry.is_limited,
            "last_error": entry.last_error,
        }
        for provider, entry in usage.items()
    }


Example = tuple[str, str, str | None]


def _few_shot_examples(postings: pl.DataFrame, classification: CategoryClassification) -> list[Example]:
    """Return already-categorized postings on `classification`'s side, as `(description, category_id, subcategory_id)`.

    Returns
    -------
    list[tuple[str, str, str | None]]
        Up to `_MAX_FEW_SHOT_EXAMPLES` examples.
    """
    categorized = postings.filter(pl.col("category_id").is_not_null()).select(
        "description", "amount", "category_id", "subcategory_id"
    )
    return [
        (row["description"], row["category_id"], row["subcategory_id"])
        for row in categorized.to_dicts()
        if (row["amount"] >= 0) == (classification == "income")
    ][:_MAX_FEW_SHOT_EXAMPLES]


@router.post("/postings/{posting_id}/ai-suggest-category")
def post_ai_suggest_category(posting_id: str, lock_category_id: str | None = None) -> dict[str, Any]:
    """Ask an LLM to suggest a category for one posting, from already-categorized examples — never automatic.

    Only ever called when the user clicks the "AI suggestion" button, or
    by the bulk-suggest action over a filtered set of postings — nothing
    in this module calls it on its own. The suggestion is validated
    against real category/subcategory ids before being applied (see
    `llm.categorize.parse_and_validate_suggestion`) and, if valid,
    persisted as a manual override exactly as if the user had picked it
    from the dropdown themselves.

    Parameters
    ----------
    posting_id
        The posting to suggest a category for.
    lock_category_id
        If given, the posting already has this category and only its
        subcategory is missing — the suggestion is discarded (treated as
        unapplied) unless the LLM's own top-level guess agrees with it, so
        this call can never change a category the user (or an earlier
        rule) already assigned.

    Returns
    -------
    dict[str, Any]
        `{"category_id", "subcategory_id", "applied"}` — `applied` is `False` if no provider returned a
        usable suggestion, in which case nothing about the posting is changed.

    Raises
    ------
    HTTPException
        404 if the posting doesn't exist; 503 if no LLM provider is configured or every configured one failed.
    """
    postings, store = _resolved_postings_and_store(state.config)
    target = postings.filter(pl.col("posting_id") == posting_id)
    if target.is_empty():
        raise HTTPException(status_code=404, detail=f"Posting {posting_id!r} not found")
    target_row = target.row(0, named=True)
    classification: CategoryClassification = "income" if target_row["amount"] >= 0 else "expense"

    system_prompt, user_prompt = categorize.build_prompt(
        target_row["description"], classification, store.categories, _few_shot_examples(postings, classification)
    )
    try:
        raw_response = complete_with_fallback(_llm_providers(), system_prompt, user_prompt)
    except LLMProviderError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    category_id, subcategory_id = categorize.parse_and_validate_suggestion(
        raw_response, store.categories, classification
    )
    if category_id is None:
        return {"category_id": None, "subcategory_id": None, "applied": False}
    if lock_category_id is not None and category_id != lock_category_id:
        return {"category_id": None, "subcategory_id": None, "applied": False}

    overrides = load_overrides(state.config)
    existing = overrides.get(posting_id)
    suggested = {"category_id": category_id, "subcategory_id": subcategory_id}
    merged = {**(existing.model_dump() if existing else {}), **suggested}
    overrides[posting_id] = ManualOverride(**merged)
    save_overrides(overrides, state.config)
    return {"category_id": category_id, "subcategory_id": subcategory_id, "applied": True}


@router.get("/transfer-suggestions")
def get_transfer_suggestions() -> list[dict[str, Any]]:
    """Suggest likely internal transfers no rule has already resolved.

    Returns
    -------
    list[dict[str, Any]]
        One dict per candidate pair — see `ledger.transfers.find_unmatched_transfer_candidates`.
    """
    postings, _store = _resolved_postings_and_store(state.config)
    return find_unmatched_transfer_candidates(postings).to_dicts()


def _external_investment_values_usd(dates: list[date]) -> dict[date, float] | None:
    """Look up the tracked investment portfolio's value as of each requested date, from the live `trades` server.

    Imported lazily, and reads the *running* `trades.api` app's own
    `app.state.config` (the same one its own endpoints use, so this
    reflects whatever cache directory that server is actually configured
    for) rather than a disconnected default — this is the one place
    accounting code reaches into trades at all.

    Uses `trades.dashboard.valuation.daily_portfolio_values` (one batched,
    O(unique event dates) computation) rather than calling `overview_cards`
    once per date — that would replay the whole ledger per point, and net
    worth history can ask for a year of daily points. The valuation range
    is widened back to the ledger's own first event so a request whose
    window starts after investing began still sees that day's real value,
    not 0 (`daily_portfolio_values` only considers events inside the range
    it's given).

    Parameters
    ----------
    dates
        Every date a value is needed for.

    Returns
    -------
    dict[date, float] or None
        Portfolio value for each requested date (0.0 for dates before the
        first investment event), or `None` if trades has never been synced.
    """
    from trades import api as trades_api  # noqa: PLC0415
    from trades.brokers.ibkr import main as trades_main  # noqa: PLC0415
    from trades.dashboard.valuation import daily_portfolio_values, make_price_lookup  # noqa: PLC0415

    trades_config = trades_api.app.state.config
    ledger = trades_main.load_ledger(trades_config)
    if ledger.is_empty():
        return None
    first_event_date = cast("date", ledger["event_datetime"].dt.date().min())
    start = min(first_event_date, *dates)
    end = max(dates)
    price_lookup = make_price_lookup(trades_config)
    daily = cast("pl.DataFrame", daily_portfolio_values(ledger, price_lookup, start, end, trades_config))
    return dict(zip(daily["date"].to_list(), daily["value"].to_list(), strict=True))


def _benchmark_apy_pct(as_of: date) -> float | None:
    """Look up `trades`'s published HYSA rate as of a date, as a percent, to compare vault/savings APYs against.

    Imported lazily, reading the *running* `trades.api` app's own
    `app.state.config` — the same reasoning as `_external_investment_values_usd`.

    Returns
    -------
    float or None
        The benchmark rate as a percent (e.g. `4.2`), or `None` if `trades` has no rate configured.
    """
    from trades import api as trades_api  # noqa: PLC0415
    from trades.dashboard.settings import hysa_rate_lookup  # noqa: PLC0415

    trades_config = trades_api.app.state.config
    try:
        rate = hysa_rate_lookup(trades_config)(as_of)
    except Exception:  # noqa: BLE001 - a missing/misconfigured HYSA rate shouldn't block the rest of the view
        return None
    return rate * 100


@router.get("/interest-summary")
def get_interest_summary(as_of: date | None = None) -> list[dict[str, Any]]:
    """Every savings/vault account's year-to-date interest, current APY, balance, and a one-year projection.

    Returns
    -------
    list[dict[str, Any]]
        See `dashboard.interest.InterestAccountRow`.
    """
    postings, store = _resolved_postings_and_store(state.config)
    resolved_as_of = as_of or datetime.now(tz=UTC).date()
    rows = interest.interest_summary(postings, store.accounts, resolved_as_of, _benchmark_apy_pct(resolved_as_of))
    return [vars(row) for row in rows]


@router.get("/net-worth")
def get_net_worth(as_of: date | None = None, display_currency: CurrencyCode = "USD") -> dict[str, Any]:
    """Return the full net-worth view: every account's balance, grouped, plus manually-added assets.

    Returns
    -------
    dict[str, Any]
        See `dashboard.net_worth.NetWorthSummary`.
    """
    postings, store = _resolved_postings_and_store(state.config)
    has_external_investment = any(account.kind == "external_investment" for account in store.accounts.values())
    resolved_as_of = as_of or datetime.now(tz=UTC).date()
    external_values = _external_investment_values_usd([resolved_as_of]) if has_external_investment else None
    summary = net_worth_summary(
        postings,
        store.accounts,
        store.other_assets,
        resolved_as_of,
        _display_currency(display_currency, store, resolved_as_of),
        external_investment_value_usd=(external_values or {}).get(resolved_as_of) if external_values else None,
        opening_balances=store.opening_balances,
    )
    return {
        "as_of": summary.as_of.isoformat(),
        "display_currency": summary.display_currency,
        "assets": summary.assets,
        "liabilities": summary.liabilities,
        "other_assets_total": summary.other_assets_total,
        "net_worth": summary.net_worth,
        "accounts": [vars(row) for row in summary.accounts],
        "other_assets": [asset.model_dump(mode="json") for asset in summary.other_assets],
    }


@router.get("/net-worth/history")
def get_net_worth_history(
    start: date, end: date, interval_days: int = 1, display_currency: CurrencyCode = "USD"
) -> list[dict[str, Any]]:
    """Return net worth as of a regularly-spaced series of dates, for a history chart.

    Each point uses that date's own smoothed exchange rate (see
    `market_data.exchange_rates`), not today's — a EUR account's value ten
    months ago is converted at what the rate actually was ten months ago,
    not backdated with today's rate.

    Returns
    -------
    list[dict[str, Any]]
        One `{"date": ..., "net_worth": ...}` per point, oldest first.
    """
    postings, store = _resolved_postings_and_store(state.config)
    has_external_investment = any(account.kind == "external_investment" for account in store.accounts.values())
    dates = pl.date_range(start, end, interval=f"{interval_days}d", eager=True).to_list()
    external_values = _external_investment_values_usd(dates) if has_external_investment else None
    return [
        {
            "date": day.isoformat(),
            "net_worth": net_worth_summary(
                postings,
                store.accounts,
                store.other_assets,
                day,
                _display_currency(display_currency, store, day),
                external_investment_value_usd=(external_values or {}).get(day, 0.0) if external_values else None,
                opening_balances=store.opening_balances,
            ).net_worth,
        }
        for day in dates
    ]


_VIRTUAL_ACCOUNT_KINDS = {"income_source", "expense_payee"}


@router.get("/net-worth/history/by-account")
def get_net_worth_history_by_account(
    start: date, end: date, interval_days: int = 1, display_currency: CurrencyCode = "USD"
) -> list[dict[str, Any]]:
    """Return every real account's own balance as of a regularly-spaced series of dates.

    The per-account counterpart to `get_net_worth_history` — same dates,
    same as-of-date exchange rate handling, but one row per (date,
    account) instead of one aggregate net-worth figure per date, for the
    net worth chart's "detailed" per-account view.

    Returns
    -------
    list[dict[str, Any]]
        One `{"date", "account_id", "account_name", "balance"}` per point
        per account, `balance` already converted into `display_currency`.
    """
    postings, store = _resolved_postings_and_store(state.config)
    dates = pl.date_range(start, end, interval=f"{interval_days}d", eager=True).to_list()
    real_accounts = {
        account_id: account
        for account_id, account in store.accounts.items()
        if account.kind not in _VIRTUAL_ACCOUNT_KINDS
    }
    has_external_investment = any(account.kind == "external_investment" for account in real_accounts.values())
    external_values = _external_investment_values_usd(dates) if has_external_investment else None

    balances = cast("pl.DataFrame", account_balances_over_time(postings, dates))
    balance_lookup = {(row["account_id"], row["date"]): row["balance"] for row in balances.to_dicts()}

    rows: list[dict[str, Any]] = []
    for day in dates:
        display = _display_currency(display_currency, store, day)
        for account_id, account in real_accounts.items():
            if account.kind == "external_investment":
                native = (external_values or {}).get(day, 0.0)
            else:
                native = balance_lookup.get((account_id, day), 0.0)
                opening = store.opening_balances.get(account_id)
                if opening is not None and day >= opening.as_of_date.date():
                    native += opening.amount
            rows.append({
                "date": day.isoformat(),
                "account_id": account_id,
                "account_name": account.name,
                "balance": convert(native, account.currency, display.code, display.rates_to_base),
            })
    return rows


@router.get("/income-statement/category-totals")
def get_category_totals(
    start: date,
    end: date,
    account_ids: str | None = None,
    tag_id: str | None = None,
    display_currency: CurrencyCode = "USD",
) -> list[dict[str, Any]]:
    """Sum real income/expense postings by classification, category, and subcategory.

    Returns
    -------
    list[dict[str, Any]]
        See `dashboard.income_statement.category_totals`.
    """
    postings, store = _resolved_postings_and_store(state.config)
    parsed_account_ids = account_ids.split(",") if account_ids else None
    totals = income_statement.category_totals(
        postings,
        store.accounts,
        store.categories,
        start,
        end,
        income_statement.Scope(parsed_account_ids, tag_id),
        _display_currency(display_currency, store),
    )
    return totals.to_dicts()


@router.get("/income-statement/monthly")
def get_monthly_income_expense(start: date, end: date, display_currency: CurrencyCode = "USD") -> list[dict[str, Any]]:
    """Sum real income and real expense per calendar month.

    Returns
    -------
    list[dict[str, Any]]
        See `dashboard.income_statement.monthly_income_expense`.
    """
    postings, store = _resolved_postings_and_store(state.config)
    return income_statement.monthly_income_expense(
        postings, store.accounts, start, end, _display_currency(display_currency, store)
    ).to_dicts()


@router.get("/income-statement/spend-curve")
def get_spend_curve(
    month: date, lookback_months: int = 3, display_currency: CurrencyCode = "USD"
) -> list[dict[str, Any]]:
    """Cumulative daily spend through one month, next to the average of the prior months.

    Returns
    -------
    list[dict[str, Any]]
        See `dashboard.income_statement.spend_curve_vs_average`.
    """
    postings, store = _resolved_postings_and_store(state.config)
    return income_statement.spend_curve_vs_average(
        postings, store.accounts, month, lookback_months, _display_currency(display_currency, store)
    ).to_dicts()


@router.get("/budgets/comparison")
def get_budget_comparison(month: str, display_currency: CurrencyCode = "USD") -> list[dict[str, Any]]:
    """Every category budgeted for one month, actual spend next to the target.

    Returns
    -------
    list[dict[str, Any]]
        See `dashboard.budgets.BudgetComparisonRow`.

    Raises
    ------
    HTTPException
        400 if `month` isn't `"YYYY-MM"`.
    """
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(status_code=400, detail="month must be in YYYY-MM form")
    postings, store = _resolved_postings_and_store(state.config)
    rows = budgets.budget_comparison(
        postings, store.accounts, store.categories, store.budgets, month, _display_currency(display_currency, store)
    )
    return [vars(row) for row in rows]


@router.get("/budgets/suggested-amount")
def get_suggested_budget_amount(
    category_id: str, month: str, lookback_months: int = 3, display_currency: CurrencyCode = "USD"
) -> dict[str, float]:
    """Suggest a budget for a category from its trailing months' actual spend.

    Returns
    -------
    dict[str, float]
        `{"suggested_amount": ...}`.

    Raises
    ------
    HTTPException
        400 if `month` isn't `"YYYY-MM"`.
    """
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(status_code=400, detail="month must be in YYYY-MM form")
    postings, store = _resolved_postings_and_store(state.config)
    amount = budgets.suggested_budget_amount(
        postings, store.accounts, category_id, month, lookback_months, _display_currency(display_currency, store)
    )
    return {"suggested_amount": amount}
