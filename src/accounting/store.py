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

from accounting.models import Account, Category, CategoryClassification, ManualOverride, OtherAsset, Rule, Tag
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
    return categories


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


def default_rules() -> list[Rule]:
    """Seed rules recognizing the counterparties already known from `ACCOUNTING_PLAN.md` Part 2.

    Returns
    -------
    list[Rule]
        Starting rules for the Chase checking/credit-card payoff, EQORE
        Inc. payroll, and the Interactive Brokers transfer — the concrete
        cases the plan was built around. Left for a user to edit or extend
        from the UI; not exhaustive by design.
    """
    salary_category = f"income:{slugify('Salary')}"
    return [
        Rule(
            rule_id="chase-card-payoff",
            description_contains="Payment to Chase card ending in 8235",
            account_id="chase:checking:9579",
            counterparty_account_id="chase:credit_card:8235",
            counterparty_account_name="Chase Credit Card (...8235)",
            counterparty_account_kind="credit_card",
            priority=0,
        ),
        Rule(
            rule_id="eqore-payroll",
            description_contains="EQORE Inc.",
            counterparty_account_id="employer:eqore",
            counterparty_account_name="EQORE Inc. (Employer)",
            counterparty_account_kind="income_source",
            category_id=salary_category,
            priority=0,
        ),
        Rule(
            rule_id="interactive-brokers-transfer",
            description_contains="INTERACTIVE BROK",
            counterparty_account_id="external:interactive-brokers",
            counterparty_account_name="Interactive Brokers",
            counterparty_account_kind="external_investment",
            priority=0,
        ),
    ]


class AccountingStore(BaseModel):
    """Every persisted accounting entity that isn't a posting: accounts, categories, tags, rules, other assets.

    `eur_usd_rate` is the one exchange rate this app knows — how many US
    dollars one euro buys — set once by the user and used everywhere an
    amount needs converting into a display currency (see
    `ledger.currency.convert`); it is never fetched automatically.
    """

    model_config = ConfigDict(frozen=True)

    accounts: dict[str, Account] = Field(default_factory=dict)
    categories: dict[str, Category] = Field(default_factory=dict)
    tags: dict[str, Tag] = Field(default_factory=dict)
    rules: list[Rule] = Field(default_factory=list)
    other_assets: list[OtherAsset] = Field(default_factory=list)
    eur_usd_rate: float = Field(default=1.08, gt=0)


def load_store(config: AccountingConfig) -> AccountingStore:
    """Read the persisted accounting store, seeding sensible defaults the first time.

    A fresh install has no `store.json` yet, but still needs the two
    uncategorized placeholder accounts and the default category tree to be
    usable immediately — those are backfilled here rather than requiring a
    separate setup step. Rules are only seeded once, since an empty rule
    list a user has deliberately emptied out should stay empty.

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
        store = AccountingStore(categories=default_categories(), accounts=default_accounts(), rules=default_rules())
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
