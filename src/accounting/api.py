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

import io
import re
import zipfile
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal, cast

import polars as pl
from fastapi import APIRouter, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from accounting.config import AccountingConfig
from accounting.dashboard import budgets, income_statement, interest, simulator
from accounting.dashboard.goals import all_goal_balances, contributions_to_frame, unallocated_balance
from accounting.dashboard.net_worth import net_worth_summary
from accounting.dashboard.paystub import propose_posting_splits, reconcile_earnings_statement
from accounting.importers.canonical.csv import (
    CanonicalCsvError,
    CategoryOverrides,
    DateOrder,
    standardize_canonical_csv,
    standardize_canonical_excel,
)
from accounting.importers.detect import detect_bank_account
from accounting.importers.ingest import (
    UnsupportedImportError,
    ingest_canonical_csv,
    ingest_canonical_excel,
    ingest_csv,
    last_import_at,
    load_ledger,
    rebuild_from_raw_statements,
    remap_ledger_category_ids,
    supported_import_kinds,
)
from accounting.importers.paystub import extract_paystub_pdf_text, parse_earnings_statement_text
from accounting.ledger.categorization import (
    apply_manual_overrides,
    apply_posting_merges,
    apply_posting_splits,
    apply_rules,
    resolved_transfer_rule_ids_by_transaction,
)
from accounting.ledger.currency import DisplayCurrency, convert
from accounting.ledger.duplicates import DuplicateGroup, find_duplicate_candidates
from accounting.ledger.goal_automations import (
    next_recurring_occurrence,
    run_recurring_additions,
    run_withdrawal_automation,
)
from accounting.ledger.manual_transfers import postings_for_manual_transfers
from accounting.ledger.patterns import match_patterns_bulk, matching_pattern
from accounting.ledger.pending import resolve_pending_suggestion, stage_pending_suggestion
from accounting.ledger.replay import account_balances_over_time
from accounting.ledger.transfers import find_unmatched_transfer_candidates
from accounting.llm import categorize
from accounting.llm.gemini import GeminiProvider
from accounting.llm.mistral import MistralProvider
from accounting.llm.provider import LLMProvider, LLMProviderError, complete_with_fallback
from accounting.llm.settings import (
    LLMCredentialOverride,
    load_llm_credential_override,
    resolve_llm_credentials,
    save_llm_credential_override,
)
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
    CategoryPattern,
    CurrencyCode,
    DismissedSuggestion,
    GeneralBudget,
    Goal,
    GoalContribution,
    ManualOverride,
    ManualTransfer,
    OpeningBalance,
    OtherAsset,
    PendingSuggestionSource,
    PostingMerge,
    PostingSplit,
    PostingSplitLeg,
    RecurringAddition,
    SimulatorScenario,
    Tag,
    TransferRule,
    WithdrawalPriorityEntry,
)
from accounting.store import (
    AccountingStore,
    load_overrides,
    load_store,
    normalize_categories,
    plan_category_rename,
    remap_category_ids,
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
    resolved = apply_posting_merges(resolved, store.posting_merges)
    if store.manual_transfers:
        manual = postings_for_manual_transfers(store.manual_transfers, store.accounts)
        resolved = pl.concat([resolved, manual], how="vertical")
    return resolved, store


def _resolved_postings_for_aggregation(config: AccountingConfig) -> tuple[Any, Any]:
    """Like `_resolved_postings_and_store`, but clears category/subcategory for unconfirmed suggestions.

    A pending AI/pattern suggestion is applied optimistically everywhere
    else (see `ledger.pending`) so its category shows up immediately in the
    transaction table for review — but a chart, income statement, or
    budget shouldn't count it until it's actually been validated. Without
    this, `category_totals`/`monthly_income_expense`/`spend_curve_vs_average`/
    the budget endpoints would bucket a still-pending posting under its
    suggested category rather than leaving it in "Uncategorized".

    Returns
    -------
    tuple[polars.DataFrame, accounting.store.AccountingStore]
        The resolved postings (with any pending posting's category/subcategory
        nulled out), and the current store.
    """
    postings, store = _resolved_postings_and_store(config)
    overrides = load_overrides(config)
    pending_ids = [posting_id for posting_id, override in overrides.items() if override.pending_source is not None]
    if not pending_ids:
        return postings, store
    cleared = pl.when(pl.col("posting_id").is_in(pending_ids)).then(None).otherwise(pl.col("category_id"))
    cleared_sub = pl.when(pl.col("posting_id").is_in(pending_ids)).then(None).otherwise(pl.col("subcategory_id"))
    return postings.with_columns(category_id=cleared, subcategory_id=cleared_sub), store


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
        "transfer_rules": [rule.model_dump(mode="json") for rule in store.rules],
        "other_assets": [asset.model_dump(mode="json") for asset in store.other_assets],
        "opening_balances": {
            account_id: balance.model_dump(mode="json") for account_id, balance in store.opening_balances.items()
        },
        "manual_transfers": [transfer.model_dump(mode="json") for transfer in store.manual_transfers],
        "budgets": [budget.model_dump(mode="json") for budget in store.budgets],
        "simulator_scenarios": [scenario.model_dump(mode="json") for scenario in store.simulator_scenarios],
        "posting_splits": {pid: split.model_dump(mode="json") for pid, split in store.posting_splits.items()},
        "posting_merges": {mid: merge.model_dump(mode="json") for mid, merge in store.posting_merges.items()},
        "general_budgets": {cat_id: budget.model_dump(mode="json") for cat_id, budget in store.general_budgets.items()},
        "category_patterns": {
            pattern_id: pattern.model_dump(mode="json") for pattern_id, pattern in store.category_patterns.items()
        },
        "goals": {goal_id: goal.model_dump(mode="json") for goal_id, goal in store.goals.items()},
        "goal_contributions": {
            contribution_id: contribution.model_dump(mode="json")
            for contribution_id, contribution in store.goal_contributions.items()
        },
        "recurring_additions": [addition.model_dump(mode="json") for addition in store.recurring_additions],
        "withdrawal_priorities": [entry.model_dump(mode="json") for entry in store.withdrawal_priorities],
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
    `code` itself, plus every account's, other-asset's, and goal
    contribution's own currency when `store` is given — never every
    `CurrencyCode` this app could theoretically support, so a store with
    no EUR accounts yet isn't blocked from a USD-only net worth just
    because EUR was never synced.

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
        needed.update(contribution.currency for contribution in store.goal_contributions.values())
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


class CategoryRenameRequest(BaseModel):
    """Request body for `POST /categories/{category_id}/rename`."""

    name: str


@router.post("/categories/{category_id}/rename")
def post_category_rename(category_id: str, request: CategoryRenameRequest) -> dict[str, Any]:
    """Rename a category or subcategory, merging it into an existing same-named one if there is one.

    A merge repoints every reference to the merged-away id — postings
    already in the ledger cache, manual per-posting overrides, transfer
    rules, category patterns, budgets, and posting splits — onto the
    surviving id, then removes the merged-away category entirely. See
    `store.plan_category_rename` for the exact matching rules: a top-level
    category only merges into another top-level category of the same
    classification; a subcategory only merges into a sibling under the
    same parent.

    Returns
    -------
    dict[str, Any]
        `{"categories": {...}, "merged": bool}` — `categories` is the full
        tree after the change; `merged` is true if this rename actually
        folded into an existing category rather than just changing a name.

    Raises
    ------
    HTTPException
        404 if `category_id` doesn't exist.
    """
    store = load_store(state.config)
    if category_id not in store.categories:
        raise HTTPException(status_code=404, detail=f"Category {category_id!r} not found")

    categories, id_remap = plan_category_rename(store.categories, category_id, request.name)
    store = remap_category_ids(store.model_copy(update={"categories": categories}), id_remap)
    save_store(store, state.config)

    remap_ledger_category_ids(id_remap, state.config)
    if id_remap:

        def remap(category_id: str | None) -> str | None:
            return id_remap.get(category_id, category_id) if category_id is not None else None

        overrides = load_overrides(state.config)
        overrides = {
            posting_id: override.model_copy(
                update={"category_id": remap(override.category_id), "subcategory_id": remap(override.subcategory_id)}
            )
            for posting_id, override in overrides.items()
        }
        save_overrides(overrides, state.config)

    return {
        "categories": {cat_id: category.model_dump(mode="json") for cat_id, category in store.categories.items()},
        "merged": bool(id_remap),
    }


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


@router.put("/transfer-rules")
def put_transfer_rules(rules: list[TransferRule]) -> list[dict[str, Any]]:
    """Replace the whole transfer-rule list.

    Returns
    -------
    list[dict[str, Any]]
        The transfer rules just persisted.
    """
    store = load_store(state.config)
    store = store.model_copy(update={"rules": rules})
    save_store(store, state.config)
    return [rule.model_dump(mode="json") for rule in store.rules]


@router.put("/category-patterns")
def put_category_patterns(category_patterns: dict[str, CategoryPattern]) -> dict[str, Any]:
    """Replace the whole category-pattern list — the description-match suggestion source, distinct from `TransferRule`.

    Returns
    -------
    dict[str, Any]
        The patterns just persisted, keyed by `pattern_id`.
    """
    store = load_store(state.config)
    store = store.model_copy(update={"category_patterns": category_patterns})
    save_store(store, state.config)
    return {pattern_id: pattern.model_dump(mode="json") for pattern_id, pattern in store.category_patterns.items()}


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
    `put_account`, not here, since that check needs the ledger. `closed`
    isn't edited here — see `close_account`/`reopen_account`, which pair it
    with recording where a closed account's remaining balance went.
    `external_ref` is never locked — it only ever changes which value an
    `external_investment` account shows (see `dashboard.net_worth`), never
    what it has already recorded, so it's free to toggle regardless of postings.
    """

    name: str
    institution: str
    kind: AccountKind
    currency: CurrencyCode
    external_ref: str | None = None
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
            "external_ref": update.external_ref,
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


class AccountCloseRequest(BaseModel):
    """Request body for `POST /api/accounting/accounts/{account_id}/close`."""

    transfers: list[ManualTransfer] = Field(default_factory=list)


@router.post("/accounts/{account_id}/close")
def close_account(account_id: str, request: AccountCloseRequest) -> dict[str, Any]:
    """Mark an account closed, recording any transfers that moved its remaining balance out first.

    An account no longer open at its institution still keeps its full
    transaction history (see `models.Account.closed`) — closing never
    deletes anything. `request.transfers` (each a `models.ManualTransfer`)
    become real postings on both the closed account and wherever its
    balance went, the one case in this ledger where a posting doesn't
    trace back to an imported statement.

    Returns
    -------
    dict[str, Any]
        `{"account": ..., "manual_transfers": [...]}` after the update.

    Raises
    ------
    HTTPException
        404 if the account doesn't exist; 400 if a transfer doesn't move
        money out of `account_id`, or names an unknown `to_account_id`.
    """
    store = load_store(state.config)
    account = store.accounts.get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")
    for transfer in request.transfers:
        if transfer.from_account_id != account_id:
            raise HTTPException(status_code=400, detail="Each transfer must move money out of the account being closed")
        if transfer.to_account_id not in store.accounts:
            raise HTTPException(status_code=400, detail=f"Account {transfer.to_account_id!r} not found")

    updated_account = account.model_copy(update={"closed": True})
    store = store.model_copy(
        update={
            "accounts": {**store.accounts, account_id: updated_account},
            "manual_transfers": [*store.manual_transfers, *request.transfers],
        }
    )
    save_store(store, state.config)
    return {
        "account": updated_account.model_dump(mode="json"),
        "manual_transfers": [transfer.model_dump(mode="json") for transfer in store.manual_transfers],
    }


@router.post("/accounts/{account_id}/reopen")
def reopen_account(account_id: str) -> dict[str, Any]:
    """Clear an account's `closed` flag, without touching any transfers recorded when it was closed.

    Returns
    -------
    dict[str, Any]
        The account after the update.

    Raises
    ------
    HTTPException
        404 if the account doesn't exist.
    """
    store = load_store(state.config)
    account = store.accounts.get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"Account {account_id!r} not found")
    updated_account = account.model_copy(update={"closed": False})
    store = store.model_copy(update={"accounts": {**store.accounts, account_id: updated_account}})
    save_store(store, state.config)
    return updated_account.model_dump(mode="json")


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


@router.get("/sync-status")
def get_sync_status() -> dict[str, str | None]:
    """When a bank statement was most recently imported, across every institution and account.

    Returns
    -------
    dict[str, str | None]
        `{"last_import_at": ...}`, an ISO 8601 UTC timestamp, or `None` if nothing has ever been imported.
    """
    imported_at = last_import_at(state.config)
    return {"last_import_at": imported_at.isoformat() if imported_at else None}


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

    # Try multiple encodings to handle files from different sources
    # (e.g., Excel exports on different systems use different encodings).
    # "utf-8-sig" strips a leading byte-order mark when the file has one
    # (common in CSVs exported by Excel).
    file_bytes = await file.read()
    csv_text: str | None = None

    # Try encodings in order: most common first (Excel UTF-8-sig or UTF-8),
    # then fallback to Windows/Mac formats (cp1252, iso-8859-1)
    for encoding in ["utf-8-sig", "utf-8", "cp1252", "iso-8859-1"]:
        try:
            csv_text = file_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue

    if csv_text is None:
        message = (
            f"Could not decode file '{file.filename}' with any supported encoding "
            "(utf-8-sig, utf-8, cp1252, iso-8859-1). "
            "Please ensure the file is a valid CSV or Excel export."
        )
        raise HTTPException(status_code=400, detail=message)

    try:
        result = ingest_csv(csv_text, institution, account_kind, account_id, state.config)
    except UnsupportedImportError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    response_data: dict[str, Any] = {
        "account_id": result.account_id,
        "new_posting_count": result.new_posting_count,
        "total_posting_count": result.total_posting_count,
    }

    # Include skip info if any rows were skipped
    if result.skipped_rows:
        response_data["skipped_rows"] = {
            "total_rows": result.skipped_rows.total_rows,
            "skipped_count": result.skipped_rows.skipped_count,
            "bad_dates": result.skipped_rows.bad_dates,
            "bad_amounts": result.skipped_rows.bad_amounts,
            "skipped_row_numbers": result.skipped_rows.skipped_row_numbers,
        }

    return response_data


class CanonicalCategoryOverridesRequest(BaseModel):
    """The `category_overrides` form field's JSON shape — see `importers.canonical.csv.CategoryOverrides`."""

    categories: dict[str, str] = Field(default_factory=dict)
    subcategories: dict[str, dict[str, str]] = Field(default_factory=dict)


def _read_category_overrides(raw: str | None) -> CategoryOverrides | None:
    if not raw:
        return None
    parsed = CanonicalCategoryOverridesRequest.model_validate_json(raw)
    return CategoryOverrides(categories=parsed.categories, subcategories=parsed.subcategories)


@router.post("/import/canonical/preview")
async def post_canonical_import_preview(
    file: UploadFile,
    account_id: Annotated[str, Form()],
    currency: Annotated[str, Form()] = "USD",
    separator: Annotated[str | None, Form()] = None,
    date_order: Annotated[str, Form()] = "MDY",
) -> dict[str, Any]:
    """Parse a canonical CSV/Excel file without persisting anything, to preview which categories it would create.

    Meant to run before `post_canonical_import`: the caller shows the
    returned categories/subcategories for the user to rename or merge, then
    submits the real import with `category_overrides` built from whatever
    they changed. Never touches the store or the ledger — every account
    field except `account_id`/`currency` is irrelevant here.

    Returns
    -------
    dict[str, Any]
        `new_categories` — every category/subcategory this file would
        create, for the caller to render as an editable list.

    Raises
    ------
    HTTPException
        422 if the file couldn't be parsed.
    """
    store = load_store(state.config)
    date_order_literal = cast("DateOrder", date_order)
    is_excel = (file.filename or "").lower().endswith((".xlsx", ".xls"))
    try:
        if is_excel:
            outcome = standardize_canonical_excel(
                await file.read(), account_id, cast("CurrencyCode", currency), store.categories, date_order_literal
            )
        else:
            csv_text = (await file.read()).decode("utf-8-sig")
            outcome = standardize_canonical_csv(
                csv_text, account_id, cast("CurrencyCode", currency), store.categories, separator, date_order_literal
            )
    except CanonicalCsvError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"new_categories": [category.model_dump(mode="json") for category in outcome.new_categories.values()]}


@router.post("/import/canonical")
async def post_canonical_import(  # noqa: PLR0913, PLR0917
    file: UploadFile,
    institution: Annotated[str, Form()],
    account_kind: Annotated[str, Form()],
    account_id: Annotated[str, Form()],
    account_name: Annotated[str, Form()],
    currency: Annotated[str, Form()] = "USD",
    parent_account_id: Annotated[str | None, Form()] = None,
    separator: Annotated[str | None, Form()] = None,
    date_order: Annotated[str, Form()] = "MDY",
    category_overrides: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """Register the account if it's new, then import the file through the canonical fallback parser.

    Used when no dedicated standardizer exists for `institution`/`account_kind`
    (see `supported_import_kinds`) — the canonical parser guesses column
    names and date/amount formats instead of expecting an exact shape (see
    `importers.canonical.csv`). Both `.csv` and `.xlsx` files are accepted
    (dispatched on `file.filename`'s extension); an Excel workbook has every
    sheet checked for the expected columns, not just the first. Any category
    or subcategory named in a `Category`/`Subcategory` column is created
    automatically, unless `category_overrides` (a JSON-encoded
    `CanonicalCategoryOverridesRequest`) renames or merges it — typically
    collected via `post_canonical_import_preview` first.

    Returns
    -------
    dict[str, Any]
        `account_id`, `new_posting_count`, `total_posting_count`, and
        `new_categories` (each a full category, for a summary table).

    Raises
    ------
    HTTPException
        422 if the file couldn't be parsed — the message explains what
        columns are supported, and (when the column separator couldn't be
        guessed) asks the user to pick one and retry with `separator` set.
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

    date_order_literal = cast("DateOrder", date_order)
    overrides = _read_category_overrides(category_overrides)
    is_excel = (file.filename or "").lower().endswith((".xlsx", ".xls"))
    try:
        if is_excel:
            result = ingest_canonical_excel(await file.read(), account_id, state.config, date_order_literal, overrides)
        else:
            # "utf-8-sig" strips a leading byte-order mark when the file has
            # one (common in CSVs exported by Excel) and is otherwise
            # identical to plain "utf-8" — without this, a BOM'd file's own
            # first header cell silently reads as "﻿Date" instead of
            # "Date", which then never matches any known column alias.
            csv_text = (await file.read()).decode("utf-8-sig")
            result = ingest_canonical_csv(csv_text, account_id, state.config, separator, date_order_literal, overrides)
    except CanonicalCsvError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    response_data: dict[str, Any] = {
        "account_id": result.account_id,
        "new_posting_count": result.new_posting_count,
        "total_posting_count": result.total_posting_count,
        "new_categories": [category.model_dump(mode="json") for category in result.new_categories.values()],
    }

    # Include skip info if any rows were skipped
    if result.skipped_rows:
        response_data["skipped_rows"] = {
            "total_rows": result.skipped_rows.total_rows,
            "skipped_count": result.skipped_rows.skipped_count,
            "bad_dates": result.skipped_rows.bad_dates,
            "bad_amounts": result.skipped_rows.bad_amounts,
            "skipped_row_numbers": result.skipped_rows.skipped_row_numbers,
        }

    return response_data


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

    Each row also carries `pending_source` (`"ai"`, `"pattern"`, or
    `None`) and `pending_selected` — an automated categorizer's
    not-yet-confirmed suggestion, and whether it's currently checked for
    the next "validate selection" action (see `ledger.pending`) — and
    `resolved_by_transfer_rule_id`, naming which `TransferRule` (if any) resolved this
    posting's transaction, purely for display (see
    `ledger.categorization.resolved_transfer_rule_ids_by_transaction`).

    Returns
    -------
    list[dict[str, Any]]
        One dict per posting.
    """
    postings, store = _resolved_postings_and_store(state.config)
    overrides = load_overrides(state.config)
    # Recomputed from the raw ledger rather than threaded through
    # `_resolved_postings_and_store`'s return value — that function's
    # signature is shared by every other endpoint in this module, and this
    # is purely a display concern only `get_postings` needs.
    raw = load_ledger(state.config)
    resolved_by_rule = resolved_transfer_rule_ids_by_transaction(raw, store.rules, store.accounts)
    rows = postings.to_dicts()
    for row in rows:
        override = overrides.get(row["posting_id"])
        row["pending_source"] = override.pending_source if override is not None else None
        row["pending_selected"] = override.pending_selected if override is not None else True
        row["resolved_by_transfer_rule_id"] = resolved_by_rule.get(row["transaction_id"])
    return rows


@router.get("/ledger/export")
def get_ledger_export() -> list[dict[str, Any]]:
    """Export the raw ledger, exactly as imported — before any rule, override, split, or merge is applied.

    Returns
    -------
    list[dict[str, Any]]
        Every posting for the user's own backup. See `GET /postings` for
        the same data after every rule/override/split/merge is applied on
        top — what the Transactions page actually shows.
    """
    return load_ledger(state.config).to_dicts()


@router.get("/statements/export")
def get_statements_export() -> Response:
    """Zip every raw statement archived from an import (CSV or PDF, verbatim as uploaded) for download.

    Returns
    -------
    fastapi.Response
        A `.zip` attachment, one entry per archived file, empty if
        nothing has been imported yet.
    """
    buffer = io.BytesIO()
    root = state.config.raw_statement_dir
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        if root.exists():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    zip_file.write(path, path.relative_to(root))
    filename = f"accounting-statements-{datetime.now(tz=UTC).date().isoformat()}.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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


@router.put("/posting-merges")
def put_posting_merges(merges: dict[str, PostingMerge]) -> dict[str, Any]:
    """Replace the whole posting-merge map, keyed by `merge_id`.

    Returns
    -------
    dict[str, Any]
        The merges just persisted.
    """
    store = load_store(state.config)
    store = store.model_copy(update={"posting_merges": merges})
    save_store(store, state.config)
    return {merge_id: merge.model_dump(mode="json") for merge_id, merge in store.posting_merges.items()}


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
    credentials = resolve_llm_credentials(state.config)
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
    credentials = resolve_llm_credentials(state.config)
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


class LLMSettingsUpdate(BaseModel):
    """Request body for `PUT /api/accounting/settings/llm`.

    Either field left `None` leaves that one exactly as it was — entering
    a Gemini key doesn't clear an existing Mistral one.
    """

    gemini_api_key: str | None = None
    mistral_api_key: str | None = None


def _llm_settings_response(*, gemini_set: bool, mistral_set: bool) -> dict[str, bool]:
    return {"gemini_key_set": gemini_set, "mistral_key_set": mistral_set}


@router.get("/settings/llm")
def get_llm_settings() -> dict[str, bool]:
    """Report whether each LLM provider's Settings-page key override is set, without exposing its value.

    Returns
    -------
    dict[str, bool]
        `gemini_key_set`, `mistral_key_set` — whether the Settings-page
        override itself has each key, regardless of `.env` (see `GET
        /llm-usage`'s own `configured`, which reflects both sources).
    """
    override = load_llm_credential_override(state.config)
    return _llm_settings_response(gemini_set=bool(override.gemini_api_key), mistral_set=bool(override.mistral_api_key))


@router.put("/settings/llm")
def put_llm_settings(update: LLMSettingsUpdate) -> dict[str, bool]:
    """Persist an LLM API key override (merges into the existing one).

    Returns
    -------
    dict[str, bool]
        Same shape as `GET /settings/llm`, reflecting what was just persisted.
    """
    existing = load_llm_credential_override(state.config)
    updated = existing.model_copy(
        update={
            "gemini_api_key": update.gemini_api_key if update.gemini_api_key is not None else existing.gemini_api_key,
            "mistral_api_key": (
                update.mistral_api_key if update.mistral_api_key is not None else existing.mistral_api_key
            ),
        }
    )
    save_llm_credential_override(updated, state.config)
    return _llm_settings_response(gemini_set=bool(updated.gemini_api_key), mistral_set=bool(updated.mistral_api_key))


@router.delete("/settings/llm")
def delete_llm_settings() -> dict[str, bool]:
    """Clear the Settings-page LLM key override, falling back to `.env` (if any) again.

    Returns
    -------
    dict[str, bool]
        Same shape as `GET /settings/llm`.
    """
    save_llm_credential_override(LLMCredentialOverride(), state.config)
    return _llm_settings_response(gemini_set=False, mistral_set=False)


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


def _stage_and_save_pending_suggestion(
    posting_id: str,
    target_row: dict[str, Any],
    category_id: str,
    subcategory_id: str | None,
    source: PendingSuggestionSource,
) -> dict[str, Any]:
    """Stage a not-yet-confirmed suggestion as a pending override, snapshotting the posting's current category.

    Shared by `post_ai_suggest_category` and `post_pattern_suggest_category`
    — the only difference between the two is how `category_id`/
    `subcategory_id` were arrived at.

    Returns
    -------
    dict[str, Any]
        `{"category_id", "subcategory_id", "applied": True}`.
    """
    overrides = load_overrides(state.config)
    staged = stage_pending_suggestion(
        existing=overrides.get(posting_id),
        category_id=category_id,
        subcategory_id=subcategory_id,
        source=source,
        previous_category_id=target_row["category_id"],
        previous_subcategory_id=target_row["subcategory_id"],
    )
    overrides[posting_id] = staged
    save_overrides(overrides, state.config)
    return {"category_id": category_id, "subcategory_id": subcategory_id, "applied": True}


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

    return _stage_and_save_pending_suggestion(posting_id, target_row, category_id, subcategory_id, "ai")


@router.post("/postings/{posting_id}/pattern-suggest-category")
def post_pattern_suggest_category(posting_id: str, lock_category_id: str | None = None) -> dict[str, Any]:
    """Suggest a category for one posting from a user-maintained `CategoryPattern` description match.

    The description-match/suggest-don't-apply counterpart to
    `post_ai_suggest_category` — same staged-pending flow (see
    `ledger.pending`), same `lock_category_id` guarantee, just matched
    against `store.category_patterns` instead of calling an LLM.

    Parameters
    ----------
    posting_id
        The posting to suggest a category for.
    lock_category_id
        If given, the posting already has this category and only its
        subcategory is missing — a pattern match is discarded unless its
        own `category_id` agrees, so this call can never change a
        category the user (or an earlier rule) already assigned.

    Returns
    -------
    dict[str, Any]
        `{"category_id", "subcategory_id", "applied"}` — `applied` is `False` if no pattern matched.

    Raises
    ------
    HTTPException
        404 if the posting doesn't exist.
    """
    postings, store = _resolved_postings_and_store(state.config)
    target = postings.filter(pl.col("posting_id") == posting_id)
    if target.is_empty():
        raise HTTPException(status_code=404, detail=f"Posting {posting_id!r} not found")
    target_row = target.row(0, named=True)

    pattern = matching_pattern(store.category_patterns, str(target_row["description"]))
    if pattern is None:
        return {"category_id": None, "subcategory_id": None, "applied": False}
    if lock_category_id is not None and pattern.category_id != lock_category_id:
        return {"category_id": None, "subcategory_id": None, "applied": False}

    return _stage_and_save_pending_suggestion(
        posting_id, target_row, pattern.category_id, pattern.subcategory_id, "pattern"
    )


class PatternSuggestBulkRequest(BaseModel):
    """Which postings to run category-pattern matching over, in one call."""

    posting_ids: list[str]


@router.post("/postings/pattern-suggest-category/bulk")
def post_pattern_suggest_category_bulk(payload: PatternSuggestBulkRequest) -> dict[str, Any]:
    """Suggest categories for many postings at once from category-pattern matches, in one ledger load.

    The bulk counterpart to `post_pattern_suggest_category` — that
    endpoint reloads and re-resolves the entire ledger on every single
    call, which is fine for one posting but made the "run pattern
    suggestions" bulk action take minutes over a few hundred rows (each
    one its own full reload). This loads everything exactly once and
    matches every posting in a single vectorized pass (see
    `ledger.patterns.match_patterns_bulk`) instead of looping over
    postings to match them one at a time.

    A posting that already has a category only gets a pattern match if
    the pattern's own category agrees with it — the same guarantee
    `post_pattern_suggest_category`'s `lock_category_id` gives, using each
    posting's own current category as its lock.

    Parameters
    ----------
    payload
        The postings to suggest categories for.

    Returns
    -------
    dict[str, Any]
        `{"applied": int}` — how many postings got a staged suggestion.
    """
    postings, store = _resolved_postings_and_store(state.config)
    targets = postings.filter(pl.col("posting_id").is_in(payload.posting_ids))
    if targets.is_empty():
        return {"applied": 0}

    matches = match_patterns_bulk(store.category_patterns, targets.select("posting_id", "description"))
    if matches.is_empty():
        return {"applied": 0}

    target_rows = {row["posting_id"]: row for row in targets.to_dicts()}
    overrides = load_overrides(state.config)
    applied = 0
    for match in matches.iter_rows(named=True):
        target_row = target_rows[match["posting_id"]]
        if target_row["category_id"] is not None and target_row["category_id"] != match["category_id"]:
            continue
        staged = stage_pending_suggestion(
            existing=overrides.get(match["posting_id"]),
            category_id=match["category_id"],
            subcategory_id=match["subcategory_id"],
            source="pattern",
            previous_category_id=target_row["category_id"],
            previous_subcategory_id=target_row["subcategory_id"],
        )
        overrides[match["posting_id"]] = staged
        applied += 1
    save_overrides(overrides, state.config)
    return {"applied": applied}


class ValidatePendingRequest(BaseModel):
    """Which postings' pending suggestions to resolve — always exactly the caller's current filtered view."""

    posting_ids: list[str]


@router.post("/postings/validate-pending")
def post_validate_pending(payload: ValidatePendingRequest) -> dict[str, Any]:
    """Resolve every listed posting's pending suggestion per its own `pending_selected` flag.

    Only ever touches postings named in `payload.posting_ids` — the
    caller's current filtered view — so a pending suggestion sitting
    outside that view is never affected by this call, per the "validate
    selection" button's contract. A posting with no override, or one
    whose override isn't pending, is silently skipped.

    Returns
    -------
    dict[str, Any]
        `{"accepted", "reverted"}` counts.
    """
    overrides = load_overrides(state.config)
    accepted = reverted = 0
    for posting_id in payload.posting_ids:
        existing = overrides.get(posting_id)
        if existing is None or existing.pending_source is None:
            continue
        if existing.pending_selected:
            accepted += 1
        else:
            reverted += 1
        resolved = resolve_pending_suggestion(existing)
        if resolved is None:
            del overrides[posting_id]
        else:
            overrides[posting_id] = resolved
    save_overrides(overrides, state.config)
    return {"accepted": accepted, "reverted": reverted}


def _transfer_suggestion_id(row: dict[str, Any]) -> str:
    """Build a stable key for a transfer-suggestion pair, independent of which side comes first in the row.

    Recomputing the candidate list finds the exact same pair under the
    exact same key every time, so a dismissed suggestion reliably stays
    dismissed (see `models.DismissedSuggestion`).

    Returns
    -------
    str
        The pair's stable dismiss key.
    """
    return "transfer:" + ":".join(sorted([row["posting_id"], row["other_posting_id"]]))


def _duplicate_suggestion_id(group: DuplicateGroup) -> str:
    """Build a stable key for a duplicate group, from its own already-stable `group_key`.

    Returns
    -------
    str
        The group's stable dismiss key.
    """
    return f"duplicate:{group.group_key}"


@router.get("/transfer-suggestions")
def get_transfer_suggestions(window_days: int = 3) -> list[dict[str, Any]]:
    """Suggest likely internal transfers no rule has already resolved.

    Parameters
    ----------
    window_days
        How many days apart the two postings can be and still count as
        one transfer — widen this if a transfer took longer than 3 days
        to land on both sides (e.g. an ACH transfer over a weekend).

    Returns
    -------
    list[dict[str, Any]]
        One dict per candidate pair — see `ledger.transfers.find_unmatched_transfer_candidates`.
        Each carries both sides' own `description` and a `suggestion_id`,
        for the caller to propose a `TransferRule` from or dismiss —
        never applied automatically. Excludes any pair already dismissed
        (see `POST /dismissed-suggestions`).
    """
    postings, store = _resolved_postings_and_store(state.config)
    rows = find_unmatched_transfer_candidates(postings, window_days=window_days).to_dicts()
    return [
        {**row, "suggestion_id": _transfer_suggestion_id(row)}
        for row in rows
        if _transfer_suggestion_id(row) not in store.dismissed_suggestions
    ]


@router.get("/duplicate-suggestions")
def get_duplicate_suggestions(window_days: int = 3) -> list[dict[str, Any]]:
    """Suggest likely duplicate transactions no merge decision has already resolved.

    Parameters
    ----------
    window_days
        How many days apart two transactions can be and still count as one duplicate.

    Returns
    -------
    list[dict[str, Any]]
        One dict per candidate group — see `ledger.duplicates.find_duplicate_candidates`.
        Sorted least-certain first, since those need the closest review.
        Each carries a `suggestion_id` for dismissing it. Excludes any
        group already dismissed (see `POST /dismissed-suggestions`).
    """
    postings, store = _resolved_postings_and_store(state.config)
    groups = find_duplicate_candidates(postings, window_days=window_days)
    return [
        {**asdict(group), "suggestion_id": _duplicate_suggestion_id(group)}
        for group in groups
        if _duplicate_suggestion_id(group) not in store.dismissed_suggestions
    ]


class DismissSuggestionRequest(BaseModel):
    """Request body for `POST /api/accounting/dismissed-suggestions`."""

    suggestion_id: str
    kind: Literal["transfer", "duplicate"]
    description: str


@router.get("/dismissed-suggestions")
def get_dismissed_suggestions() -> list[dict[str, Any]]:
    """List every archived (dismissed) suggestion, most recently dismissed first.

    Returns
    -------
    list[dict[str, Any]]
        See `models.DismissedSuggestion`.
    """
    store = load_store(state.config)
    dismissed = sorted(store.dismissed_suggestions.values(), key=lambda entry: entry.dismissed_at, reverse=True)
    return [entry.model_dump(mode="json") for entry in dismissed]


@router.post("/dismissed-suggestions")
def post_dismissed_suggestion(request: DismissSuggestionRequest) -> dict[str, Any]:
    """Archive a suggestion so it stops being proposed, without discarding it.

    Returns
    -------
    dict[str, Any]
        The archived entry just persisted.
    """
    store = load_store(state.config)
    entry = DismissedSuggestion(
        suggestion_id=request.suggestion_id,
        kind=request.kind,
        description=request.description,
        dismissed_at=datetime.now(tz=UTC),
    )
    store = store.model_copy(
        update={"dismissed_suggestions": {**store.dismissed_suggestions, entry.suggestion_id: entry}}
    )
    save_store(store, state.config)
    return entry.model_dump(mode="json")


@router.delete("/dismissed-suggestions/{suggestion_id}")
def delete_dismissed_suggestion(suggestion_id: str) -> dict[str, str]:
    """Restore a dismissed suggestion so it can be proposed again.

    Returns
    -------
    dict[str, str]
        `{"suggestion_id": ...}` of the entry just restored.

    Raises
    ------
    HTTPException
        404 if no archived entry has this id.
    """
    store = load_store(state.config)
    if suggestion_id not in store.dismissed_suggestions:
        raise HTTPException(status_code=404, detail=f"No dismissed suggestion {suggestion_id!r}")
    remaining = {key: value for key, value in store.dismissed_suggestions.items() if key != suggestion_id}
    store = store.model_copy(update={"dismissed_suggestions": remaining})
    save_store(store, state.config)
    return {"suggestion_id": suggestion_id}


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
    has_external_investment = any(
        account.kind == "external_investment" and account.external_ref == "trades"
        for account in store.accounts.values()
    )
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
    has_external_investment = any(
        account.kind == "external_investment" and account.external_ref == "trades"
        for account in store.accounts.values()
    )
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
    has_external_investment = any(
        account.kind == "external_investment" and account.external_ref == "trades" for account in real_accounts.values()
    )
    external_values = _external_investment_values_usd(dates) if has_external_investment else None

    balances = cast("pl.DataFrame", account_balances_over_time(postings, dates))
    balance_lookup = {(row["account_id"], row["date"]): row["balance"] for row in balances.to_dicts()}

    rows: list[dict[str, Any]] = []
    for day in dates:
        display = _display_currency(display_currency, store, day)
        for account_id, account in real_accounts.items():
            if account.kind == "external_investment" and account.external_ref == "trades":
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
    postings, store = _resolved_postings_for_aggregation(state.config)
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
    postings, store = _resolved_postings_for_aggregation(state.config)
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
    postings, store = _resolved_postings_for_aggregation(state.config)
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
    postings, store = _resolved_postings_for_aggregation(state.config)
    rows = budgets.budget_comparison(
        postings, store.accounts, store.categories, store.budgets, month, _display_currency(display_currency, store)
    )
    return [vars(row) for row in rows]


@router.get("/budgets/suggested-amount")
def get_suggested_budget_amount(
    category_id: str,
    month: str,
    lookback_months: int = 3,
    subcategory_id: str | None = None,
    display_currency: CurrencyCode = "USD",
) -> dict[str, float]:
    """Suggest a budget for a category (or one subcategory of it) from its trailing months' actual spend.

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
    postings, store = _resolved_postings_for_aggregation(state.config)
    amount = budgets.suggested_budget_amount(
        postings,
        store.accounts,
        category_id,
        month,
        lookback_months,
        subcategory_id,
        _display_currency(display_currency, store),
    )
    return {"suggested_amount": amount}


# --- Goals -------------------------------------------------------------


@router.put("/goals")
def put_goals(goals: dict[str, Goal]) -> dict[str, Any]:
    """Replace the whole goal list.

    Returns
    -------
    dict[str, Any]
        The goals just persisted, keyed by `goal_id`.
    """
    store = load_store(state.config)
    store = store.model_copy(update={"goals": goals})
    save_store(store, state.config)
    return {goal_id: goal.model_dump(mode="json") for goal_id, goal in store.goals.items()}


@router.put("/goal-contributions")
def put_goal_contributions(contributions: dict[str, GoalContribution]) -> dict[str, Any]:
    """Replace the whole contribution ledger — every dated allocation into or withdrawal from every goal.

    Returns
    -------
    dict[str, Any]
        The contributions just persisted, keyed by `contribution_id`.
    """
    store = load_store(state.config)
    store = store.model_copy(update={"goal_contributions": contributions})
    save_store(store, state.config)
    return {
        contribution_id: contribution.model_dump(mode="json")
        for contribution_id, contribution in store.goal_contributions.items()
    }


@router.put("/recurring-additions")
def put_recurring_additions(additions: list[RecurringAddition]) -> list[dict[str, Any]]:
    """Replace the whole recurring-addition list — the priority-ordered monthly allocation rules.

    Returns
    -------
    list[dict[str, Any]]
        The additions just persisted.

    Raises
    ------
    HTTPException
        400 if more than one addition uses `mode="remainder"`, or one does but isn't the lowest-priority row.
    """
    remainder_additions = [addition for addition in additions if addition.mode == "remainder"]
    if len(remainder_additions) > 1:
        raise HTTPException(status_code=400, detail="Only one recurring addition may use mode='remainder'")
    if remainder_additions and remainder_additions[0].priority != max((a.priority for a in additions), default=0):
        raise HTTPException(status_code=400, detail="A 'remainder' addition must be the lowest-priority row")
    store = load_store(state.config)
    store = store.model_copy(update={"recurring_additions": additions})
    save_store(store, state.config)
    return [addition.model_dump(mode="json") for addition in store.recurring_additions]


@router.put("/withdrawal-priorities")
def put_withdrawal_priorities(priorities: list[WithdrawalPriorityEntry]) -> list[dict[str, Any]]:
    """Replace the whole withdrawal-priority list — the order goals are drawn down from when unallocated goes negative.

    Returns
    -------
    list[dict[str, Any]]
        The priorities just persisted.
    """
    store = load_store(state.config)
    store = store.model_copy(update={"withdrawal_priorities": priorities})
    save_store(store, state.config)
    return [entry.model_dump(mode="json") for entry in store.withdrawal_priorities]


@router.get("/goals/summary")
def get_goals_summary(as_of: date | None = None, display_currency: CurrencyCode = "USD") -> dict[str, Any]:
    """Every goal's balance, plus unallocated money, as of `as_of` (today if omitted).

    Both are always recomputed fresh from postings and contributions —
    see `dashboard.goals` — never a stored figure.

    Returns
    -------
    dict[str, Any]
        `{"balances": {goal_id: float}, "unallocated": float}`.
    """
    postings, store = _resolved_postings_and_store(state.config)
    as_of_date = as_of or datetime.now(UTC).date()
    display = _display_currency(display_currency, store, as_of_date)
    contributions = contributions_to_frame(store.goal_contributions)
    balances = all_goal_balances(contributions, list(store.goals.keys()), as_of_date, display)
    unallocated = unallocated_balance(postings, store.accounts, contributions, as_of_date, display)
    return {"balances": balances, "unallocated": unallocated}


def _next_contribution_id(existing_ids: set[str], prefix: str) -> str:
    """Build a contribution id that doesn't collide with anything already persisted.

    Returns
    -------
    str
    """
    candidate = prefix
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{prefix}:{suffix}"
        suffix += 1
    return candidate


@router.post("/goals/run-recurring-additions")
def post_run_recurring_additions(as_of: date | None = None) -> list[dict[str, Any]]:
    """Run every recurring addition whose most recent scheduled occurrence hasn't already run.

    Idempotent by construction: each addition's occurrence writes a
    contribution under a deterministic id
    (`f"auto:{addition_id}:{occurrence.isoformat()}"`, see
    `ledger.goal_automations.next_recurring_occurrence`); calling this
    again before the next occurrence is a no-op for any addition that id
    already exists for. There is no background scheduler in this app —
    this is meant to be called when the Goals page loads, which is the
    natural moment a user would notice a change anyway.

    Returns
    -------
    list[dict[str, Any]]
        The new contributions just written (empty if nothing was due).
    """
    postings, store = _resolved_postings_and_store(state.config)
    as_of_date = as_of or datetime.now(UTC).date()
    existing_ids = set(store.goal_contributions.keys())

    occurrences: dict[str, date] = {}
    for addition in store.recurring_additions:
        occurrence = next_recurring_occurrence(addition, as_of_date)
        if occurrence is None:
            continue
        if f"auto:{addition.addition_id}:{occurrence.isoformat()}" in existing_ids:
            continue
        occurrences[addition.addition_id] = occurrence

    due = [addition for addition in store.recurring_additions if addition.addition_id in occurrences]
    if not due:
        return []

    contributions_frame = contributions_to_frame(store.goal_contributions)
    unallocated = unallocated_balance(postings, store.accounts, contributions_frame, as_of_date)
    funded = run_recurring_additions(due, unallocated)

    by_goal_addition = {addition.goal_id: addition for addition in due}
    new_contributions: dict[str, GoalContribution] = {}
    for goal_id, amount in funded:
        addition = by_goal_addition[goal_id]
        occurrence = occurrences[addition.addition_id]
        contribution_id = f"auto:{addition.addition_id}:{occurrence.isoformat()}"
        new_contributions[contribution_id] = GoalContribution(
            contribution_id=contribution_id,
            goal_id=goal_id,
            date=datetime.combine(occurrence, datetime.min.time()),
            amount=amount,
            currency=addition.currency,
            note="Recurring addition",
            origin="automation",
        )

    store = store.model_copy(update={"goal_contributions": {**store.goal_contributions, **new_contributions}})
    save_store(store, state.config)
    return [contribution.model_dump(mode="json") for contribution in new_contributions.values()]


@router.post("/goals/run-withdrawal-automation")
def post_run_withdrawal_automation(as_of: date | None = None) -> dict[str, Any]:
    """If unallocated money is negative as of today, draw down goals (by withdrawal priority) to cover it.

    Idempotent in effect (not by a stored id, unlike the recurring-addition
    case): a withdrawal always brings unallocated back to exactly zero or
    exhausts every goal, so calling this again immediately afterward finds
    nothing left to do — a *new* shortfall only ever appears from new
    postings/contributions arriving, at which point running this again is
    exactly what should happen.

    Returns
    -------
    dict[str, Any]
        `{"withdrawals": [...], "remaining_shortfall": float}` — the
        contributions just written, and however much of the shortfall
        (if any) no goal had enough left to cover.
    """
    postings, store = _resolved_postings_and_store(state.config)
    as_of_date = as_of or datetime.now(UTC).date()
    contributions_frame = contributions_to_frame(store.goal_contributions)
    unallocated = unallocated_balance(postings, store.accounts, contributions_frame, as_of_date)
    if unallocated >= 0:
        return {"withdrawals": [], "remaining_shortfall": 0.0}

    shortfall = -unallocated
    balances = all_goal_balances(contributions_frame, list(store.goals.keys()), as_of_date)
    drawn = run_withdrawal_automation(store.withdrawal_priorities, balances, shortfall)

    existing_ids = set(store.goal_contributions.keys())
    new_contributions: dict[str, GoalContribution] = {}
    for goal_id, amount in drawn:
        contribution_id = _next_contribution_id(existing_ids, f"auto-withdrawal:{goal_id}:{as_of_date.isoformat()}")
        existing_ids.add(contribution_id)
        new_contributions[contribution_id] = GoalContribution(
            contribution_id=contribution_id,
            goal_id=goal_id,
            date=datetime(as_of_date.year, as_of_date.month, as_of_date.day),  # noqa: DTZ001  (ledger dates are naive)
            amount=amount,
            note="Withdrawal automation — unallocated went negative",
            origin="automation",
        )

    store = store.model_copy(update={"goal_contributions": {**store.goal_contributions, **new_contributions}})
    save_store(store, state.config)
    remaining_shortfall = max(0.0, shortfall - sum(-amount for _, amount in drawn))
    return {
        "withdrawals": [contribution.model_dump(mode="json") for contribution in new_contributions.values()],
        "remaining_shortfall": remaining_shortfall,
    }


class SimulateContributionRequest(BaseModel):
    """A proposed manual contribution, checked against unallocated money before the user commits to it."""

    goal_id: str
    date: date
    amount: float


@router.post("/goals/simulate-contribution")
def post_simulate_contribution(payload: SimulateContributionRequest) -> dict[str, Any]:
    """Check a proposed manual contribution against unallocated money, and project the next automation run.

    Validates against the *running total as of `payload.date`* — since
    contributions are dated, not bucketed by month, a contribution backdated
    to a day with less unallocated money available than today can't be
    slipped in just because today's balance would cover it.

    Returns
    -------
    dict[str, Any]
        `{"unallocated_as_of_date", "exceeds_unallocated", "projected_next_run_unallocated", "would_go_negative"}`
        — the last two simulate every configured recurring addition running
        once more, with this contribution already applied, so the user
        can see if it sets up a shortfall soon after (non-blocking).
    """
    postings, store = _resolved_postings_and_store(state.config)
    contributions_frame = contributions_to_frame(store.goal_contributions)
    unallocated_as_of_date = unallocated_balance(postings, store.accounts, contributions_frame, payload.date)
    exceeds_unallocated = payload.amount > unallocated_as_of_date

    today = datetime.now(UTC).date()
    unallocated_today = unallocated_balance(postings, store.accounts, contributions_frame, today)
    projected_before_run = unallocated_today - payload.amount
    funded_next_run = run_recurring_additions(store.recurring_additions, max(projected_before_run, 0.0))
    projected_next_run_unallocated = projected_before_run - sum(amount for _, amount in funded_next_run)

    return {
        "unallocated_as_of_date": unallocated_as_of_date,
        "exceeds_unallocated": exceeds_unallocated,
        "projected_next_run_unallocated": projected_next_run_unallocated,
        "would_go_negative": projected_next_run_unallocated < 0,
    }
