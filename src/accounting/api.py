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

from fastapi import APIRouter, Form, HTTPException, UploadFile
from pydantic import BaseModel

from accounting.config import AccountingConfig
from accounting.dashboard.net_worth import net_worth_summary
from accounting.importers.detect import detect_bank_account
from accounting.importers.ingest import UnsupportedImportError, ingest_csv, load_ledger, rebuild_from_raw_statements
from accounting.ledger.categorization import apply_manual_overrides, apply_rules
from accounting.ledger.transfers import find_unmatched_transfer_candidates
from accounting.models import Account, Category, ManualOverride, OtherAsset, Rule, Tag
from accounting.store import load_overrides, load_store, save_overrides, save_store


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
            currency=currency,
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
def get_net_worth(as_of: date | None = None) -> dict[str, Any]:
    """Return the full net-worth view: every account's balance, grouped, plus manually-added assets.

    Returns
    -------
    dict[str, Any]
        See `dashboard.net_worth.NetWorthSummary`.
    """
    postings, store = _resolved_postings_and_store(state.config)
    has_external_investment = any(account.kind == "external_investment" for account in store.accounts.values())
    summary = net_worth_summary(
        postings,
        store.accounts,
        store.other_assets,
        as_of or datetime.now(tz=UTC).date(),
        external_investment_value_usd=_external_investment_value_usd() if has_external_investment else None,
    )
    return {
        "as_of": summary.as_of.isoformat(),
        "assets_usd": summary.assets_usd,
        "liabilities_usd": summary.liabilities_usd,
        "other_assets_usd": summary.other_assets_usd,
        "net_worth_usd": summary.net_worth_usd,
        "accounts": [vars(row) for row in summary.accounts],
        "other_assets": [asset.model_dump(mode="json") for asset in summary.other_assets],
    }
