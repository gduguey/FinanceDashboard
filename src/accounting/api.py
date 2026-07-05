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

from datetime import UTC, date, datetime
from typing import Annotated, Any

import polars as pl
from fastapi import APIRouter, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from accounting.config import AccountingConfig
from accounting.dashboard import income_statement
from accounting.dashboard.net_worth import net_worth_summary
from accounting.importers.detect import detect_bank_account
from accounting.importers.ingest import (
    UnsupportedImportError,
    ingest_csv,
    ingest_sofi_statement_pdf,
    load_ledger,
    rebuild_from_raw_statements,
)
from accounting.ledger.categorization import apply_manual_overrides, apply_rules
from accounting.ledger.currency import DisplayCurrency
from accounting.ledger.transfers import find_unmatched_transfer_candidates
from accounting.market_data import exchange_rates
from accounting.models import (
    BASE_CURRENCY,
    SUPPORTED_CURRENCIES,
    Account,
    AccountKind,
    Category,
    CurrencyCode,
    ManualOverride,
    OtherAsset,
    Rule,
    Tag,
)
from accounting.store import AccountingStore, load_overrides, load_store, save_overrides, save_store


class _State:
    """Everything a running server needs, held off the shared `app` object so tests can swap it per-test."""

    def __init__(self) -> None:
        self.config = AccountingConfig()


state = _State()
router = APIRouter(prefix="/api/accounting")


def _resolved_postings_and_store(config: AccountingConfig) -> tuple[Any, Any]:
    """Load the raw ledger, resolve it against the current rules and manual overrides, persisting new accounts.

    Rule-driven account creation (a newly-seen vault, a rule's declared
    counterparty) is the one place this module writes as a side effect of
    a read — an unavoidable consequence of accounts being allowed to
    auto-vivify at all. Manual overrides are never written back here; they
    already live in their own file and are only ever applied on top.

    Returns
    -------
    tuple[polars.DataFrame, accounting.store.AccountingStore]
        The fully resolved postings, and the store (with any newly
        discovered accounts already persisted).
    """
    raw = load_ledger(config)
    store = load_store(config)
    resolved, accounts = apply_rules(raw, store.rules, store.accounts)
    if accounts != store.accounts:
        store = store.model_copy(update={"accounts": accounts})
        save_store(store, config)
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
        `accounts`, `categories`, `tags` (each a dict keyed by id),
        `rules`, `other_assets` (each a list).
    """
    _postings, store = _resolved_postings_and_store(state.config)
    return {
        "accounts": {account_id: account.model_dump(mode="json") for account_id, account in store.accounts.items()},
        "categories": {cat_id: category.model_dump(mode="json") for cat_id, category in store.categories.items()},
        "tags": {tag_id: tag.model_dump(mode="json") for tag_id, tag in store.tags.items()},
        "rules": [rule.model_dump(mode="json") for rule in store.rules],
        "other_assets": [asset.model_dump(mode="json") for asset in store.other_assets],
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
    """Replace the whole category tree.

    Returns
    -------
    dict[str, Any]
        The categories just persisted, keyed by `category_id`.
    """
    store = load_store(state.config)
    store = store.model_copy(update={"categories": categories})
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


class DetectRequest(BaseModel):
    """Request body for `POST /api/accounting/detect`."""

    header: list[str]
    filename: str


@router.post("/detect")
def post_detect(request: DetectRequest) -> dict[str, Any] | None:
    """Guess the institution, account kind, and account id a CSV's header and filename describe.

    Returns
    -------
    dict[str, Any] or None
        The best guess, or `None` if nothing matched.
    """
    detected = detect_bank_account(request.header, request.filename)
    return None if detected is None else vars(detected)


@router.post("/import")
async def post_import(
    file: UploadFile,
    institution: Annotated[str, Form()],
    account_kind: Annotated[str, Form()],
    account_id: Annotated[str, Form()],
    account_name: Annotated[str, Form()],
    currency: Annotated[str, Form()] = "USD",
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
    """Upsert one posting's manual override, always winning over whatever a rule would produce.

    Returns
    -------
    dict[str, Any]
        The override just persisted.
    """
    overrides = load_overrides(state.config)
    overrides[posting_id] = override
    save_overrides(overrides, state.config)
    return override.model_dump(mode="json")


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


def _external_investment_value_usd() -> float | None:
    """Look up the tracked investment portfolio's current value from the live `trades` server.

    Imported lazily, and reads the *running* `trades.api` app's own
    `app.state.config` (the same one its own endpoints use, so this
    reflects whatever cache directory that server is actually configured
    for) rather than a disconnected default — this is the one place
    accounting code reaches into trades at all.

    Returns
    -------
    float or None
        The portfolio's value, or `None` if trades has never been synced.
    """
    from trades import api as trades_api  # noqa: PLC0415
    from trades import dashboard as trades_dashboard  # noqa: PLC0415
    from trades.brokers.ibkr import main as trades_main  # noqa: PLC0415

    trades_config = trades_api.app.state.config
    ledger = trades_main.load_ledger(trades_config)
    if ledger.is_empty():
        return None
    cards = trades_dashboard.overview_cards(ledger, trades_config, datetime.now(tz=UTC).date())
    return cards.value_usd


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
    summary = net_worth_summary(
        postings,
        store.accounts,
        store.other_assets,
        resolved_as_of,
        _display_currency(display_currency, store, resolved_as_of),
        external_investment_value_usd=_external_investment_value_usd() if has_external_investment else None,
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
    external_value = _external_investment_value_usd() if has_external_investment else None
    dates = pl.date_range(start, end, interval=f"{interval_days}d", eager=True).to_list()
    return [
        {
            "date": day.isoformat(),
            "net_worth": net_worth_summary(
                postings,
                store.accounts,
                store.other_assets,
                day,
                _display_currency(display_currency, store, day),
                external_value,
            ).net_worth,
        }
        for day in dates
    ]


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
