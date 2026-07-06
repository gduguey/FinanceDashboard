"""Persisted accounting entities: accounts, categories, tags, rules, and manually-added assets.

None of this is fetched or derived data — every row here is either created
by an importer meeting a new counterparty for the first time, or typed in
directly by a user (a category, a rule, a manually-added asset). It lives in
one small JSON file per the same reasoning `dashboard.settings` already
uses for `DashboardSettings`: a preference-shaped record, not a
disposable cache, so it gets its own file rather than living in
`ledger_csv_path`.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from accounting.models import (
    Account,
    Budget,
    Category,
    CategoryClassification,
    CategoryPattern,
    GeneralBudget,
    Goal,
    GoalContribution,
    ManualOverride,
    OpeningBalance,
    OtherAsset,
    PostingSplit,
    RecurringAddition,
    Rule,
    SimulatorScenario,
    Tag,
    WithdrawalPriorityEntry,
)
from trades.utils.io_utils import write_json_atomic

if TYPE_CHECKING:
    from accounting.config import AccountingConfig

UNCATEGORIZED_EXPENSE_ACCOUNT_ID = "uncategorized:expense"
UNCATEGORIZED_INCOME_ACCOUNT_ID = "uncategorized:income"

_EXPENSE_TAXONOMY: dict[str, list[str]] = {
    "Food & Drink": ["Groceries", "Restaurants & Takeout", "Coffee & Snacks", "Delivery"],
    "Going Out & Entertainment": ["Bars & Alcohol", "Activities", "Events"],
    "Transport": ["Public Transit", "Rideshare", "Gas", "Parking & Tolls", "Car Rental", "Bike"],
    "Home & Housing": ["Rent", "Utilities", "Furnishing & Move-in", "Household Supplies"],
    "Health": ["Insurance", "Medical & Pharmacy", "Fitness"],
    "Shopping": ["Clothing", "Electronics", "Personal Care", "Gifts Given", "Hobbies & Recreation"],
    "Subscriptions": ["Phone & Internet", "Software", "Streaming"],
    "Travel": ["Flights", "Lodging", "Activities"],
    "Admin & Fees": ["Bank Fees", "Visa & Immigration", "Taxes", "Legal & Equity", "Shipping & Postal"],
}

_INCOME_TAXONOMY: dict[str, list[str]] = {
    "Salary": [],
    "Bonus": [],
    "Reimbursement": ["Employer", "Friend Repayment"],
    "Gift Received": [],
    "Tax Refund": [],
    "Interest Earned": [],
    "Deposit Returned": [],
    "Other Income": [],
}

_PALETTE = [
    "#e99537",
    "#4da568",
    "#6471eb",
    "#db5a54",
    "#df4e92",
    "#c44fe9",
    "#eb5429",
    "#61c9ea",
    "#805dee",
    "#6ad28a",
]


def slugify(text: str) -> str:
    """Turn a display name into a stable, URL-safe id fragment.

    Parameters
    ----------
    text
        Any display name, e.g. a category or account name.

    Returns
    -------
    str
        Lowercase, hyphen-separated, alphanumeric-only.
    """
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


OTHER_SUBCATEGORY_SUFFIX = ":other"


def _is_other_subcategory(category: Category) -> bool:
    return category.category_id.endswith(OTHER_SUBCATEGORY_SUFFIX)


def normalize_categories(categories: dict[str, Category]) -> dict[str, Category]:
    """Enforce the "Other" catch-all subcategory invariant after any edit to the category tree.

    Whenever a top-level category ends up with at least one *real*
    (non-"Other") subcategory, it must also have an "Other" one —
    auto-created here if missing, so adding a category's first subcategory
    never requires remembering to add a catch-all too, and "Other" can't
    be removed by hand while real subcategories still exist (this just
    re-adds it on the next save). Conversely, "Other" left as a category's
    *only* subcategory is pointless — it's removed here, collapsing the
    category back to having none, since "Other" only makes sense alongside
    real subcategories, never alone.

    Parameters
    ----------
    categories
        The full proposed category tree, as a client would submit it.

    Returns
    -------
    dict[str, Category]
        The same tree, with every top-level category's "Other" subcategory added or removed as needed.
    """
    result = dict(categories)
    children_by_parent: dict[str, list[Category]] = {}
    for category in categories.values():
        if category.parent_category_id is not None:
            children_by_parent.setdefault(category.parent_category_id, []).append(category)

    for parent_id, children in children_by_parent.items():
        parent = categories.get(parent_id)
        if parent is None:
            continue
        other_id = f"{parent_id}{OTHER_SUBCATEGORY_SUFFIX}"
        has_real_children = any(not _is_other_subcategory(child) for child in children)
        if has_real_children and other_id not in result:
            result[other_id] = Category(
                category_id=other_id,
                name="Other",
                classification=parent.classification,
                parent_category_id=parent_id,
                color=parent.color,
            )
        elif not has_real_children and other_id in result:
            del result[other_id]
    return result


def default_categories() -> dict[str, Category]:
    """Build the starting category tree (see `ACCOUNTING_PLAN.md` Part 6).

    Returns
    -------
    dict[str, Category]
        Every default top-level category and subcategory, keyed by `category_id`.
    """
    categories: dict[str, Category] = {}
    taxonomies: tuple[tuple[dict[str, list[str]], CategoryClassification], ...] = (
        (_EXPENSE_TAXONOMY, "expense"),
        (_INCOME_TAXONOMY, "income"),
    )
    for taxonomy, classification in taxonomies:
        for index, (top_name, sub_names) in enumerate(taxonomy.items()):
            color = _PALETTE[index % len(_PALETTE)]
            top_id = f"{classification}:{slugify(top_name)}"
            categories[top_id] = Category(category_id=top_id, name=top_name, classification=classification, color=color)
            for sub_name in sub_names:
                sub_id = f"{top_id}:{slugify(sub_name)}"
                categories[sub_id] = Category(
                    category_id=sub_id,
                    name=sub_name,
                    classification=classification,
                    parent_category_id=top_id,
                    color=color,
                )
    return normalize_categories(categories)


def default_accounts() -> dict[str, Account]:
    """Build the two placeholder counterparties every import needs before any rule runs.

    Returns
    -------
    dict[str, Account]
        The uncategorized-expense and uncategorized-income placeholder accounts, keyed by `account_id`.
    """
    return {
        UNCATEGORIZED_EXPENSE_ACCOUNT_ID: Account(
            account_id=UNCATEGORIZED_EXPENSE_ACCOUNT_ID,
            name="Uncategorized Expense",
            kind="expense_payee",
            institution="internal",
            currency="USD",
        ),
        UNCATEGORIZED_INCOME_ACCOUNT_ID: Account(
            account_id=UNCATEGORIZED_INCOME_ACCOUNT_ID,
            name="Uncategorized Income",
            kind="income_source",
            institution="internal",
            currency="USD",
        ),
    }


class AccountingStore(BaseModel):
    """Every persisted accounting entity that isn't a posting: accounts, categories, tags, rules, other assets.

    Exchange rates are not stored here — they're fetched and cached by
    `market_data.exchange_rates`, keyed by date, not something this store
    holds a single current value of.
    """

    model_config = ConfigDict(frozen=True)

    accounts: dict[str, Account] = Field(default_factory=dict)
    categories: dict[str, Category] = Field(default_factory=dict)
    tags: dict[str, Tag] = Field(default_factory=dict)
    rules: list[Rule] = Field(default_factory=list)
    category_patterns: dict[str, CategoryPattern] = Field(default_factory=dict)
    other_assets: list[OtherAsset] = Field(default_factory=list)
    opening_balances: dict[str, OpeningBalance] = Field(default_factory=dict)
    budgets: list[Budget] = Field(default_factory=list)
    general_budgets: dict[str, GeneralBudget] = Field(default_factory=dict)
    simulator_scenarios: list[SimulatorScenario] = Field(default_factory=list)
    posting_splits: dict[str, PostingSplit] = Field(default_factory=dict)
    goals: dict[str, Goal] = Field(default_factory=dict)
    goal_contributions: dict[str, GoalContribution] = Field(default_factory=dict)
    recurring_additions: list[RecurringAddition] = Field(default_factory=list)
    withdrawal_priorities: list[WithdrawalPriorityEntry] = Field(default_factory=list)


def load_store(config: AccountingConfig) -> AccountingStore:
    """Read the persisted accounting store, seeding sensible defaults the first time.

    A fresh install has no `store.json` yet, but still needs the two
    uncategorized placeholder accounts and the default category tree to be
    usable immediately — those are backfilled here rather than requiring a
    separate setup step. No rule is seeded: every rule necessarily points
    at one person's own account/employer/payee, so there's nothing generic
    enough to start a fresh install with — a user writes their own from
    the Rules tab, after creating the counterparty account it points at.

    Parameters
    ----------
    config
        Application configuration; `config.store_path` is read.

    Returns
    -------
    AccountingStore
        The persisted store, with default accounts/categories backfilled if missing.
    """
    if config.store_path.exists():
        store = AccountingStore.model_validate_json(config.store_path.read_text())
    else:
        store = AccountingStore(categories=default_categories(), accounts=default_accounts())
        save_store(store, config)
        return store

    missing_accounts = {k: v for k, v in default_accounts().items() if k not in store.accounts}
    if missing_accounts:
        store = store.model_copy(update={"accounts": {**store.accounts, **missing_accounts}})
    return store


def save_store(store: AccountingStore, config: AccountingConfig) -> None:
    """Persist the accounting store, overwriting whatever was saved before.

    Parameters
    ----------
    store
        The store to persist.
    config
        Application configuration; `config.store_path` is written to.
    """
    write_json_atomic(store.model_dump(mode="json"), config.store_path)


def load_overrides(config: AccountingConfig) -> dict[str, ManualOverride]:
    """Read every persisted manual per-posting override.

    Parameters
    ----------
    config
        Application configuration; `config.overrides_path` is read.

    Returns
    -------
    dict[str, ManualOverride]
        Keyed by `posting_id`; empty if nothing has been overridden yet.
    """
    if not config.overrides_path.exists():
        return {}
    raw = json.loads(config.overrides_path.read_text())
    return {posting_id: ManualOverride.model_validate(value) for posting_id, value in raw.items()}


def save_overrides(overrides: dict[str, ManualOverride], config: AccountingConfig) -> None:
    """Persist every manual per-posting override, overwriting whatever was saved before.

    Parameters
    ----------
    overrides
        Every override, keyed by `posting_id`.
    config
        Application configuration; `config.overrides_path` is written to.
    """
    serialized = {posting_id: value.model_dump(mode="json") for posting_id, value in overrides.items()}
    write_json_atomic(serialized, config.overrides_path)
