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

import colorsys
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
    ManualTransfer,
    OpeningBalance,
    OtherAsset,
    PostingMerge,
    PostingSplit,
    RecurringAddition,
    SimulatorScenario,
    Tag,
    TransferRule,
    WithdrawalPriorityEntry,
)
from trades.utils.io_utils import write_json_atomic

if TYPE_CHECKING:
    from collections.abc import Iterable

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

_NAMED_CATEGORY_COLORS = [
    "#ef4444",  # red
    "#f97316",  # orange
    "#f59e0b",  # amber
    "#eab308",  # yellow
    "#84cc16",  # lime
    "#22c55e",  # green
    "#10b981",  # emerald
    "#14b8a6",  # teal
    "#06b6d4",  # cyan
    "#0ea5e9",  # sky
    "#3b82f6",  # blue
    "#6366f1",  # indigo
    "#8b5cf6",  # violet
    "#a855f7",  # purple
    "#d946ef",  # fuchsia
    "#ec4899",  # pink
    "#f43f5e",  # rose
]


def _hsl_to_hex(hue_deg: float, saturation: float, lightness: float) -> str:
    red, green, blue = colorsys.hls_to_rgb(hue_deg / 360, lightness, saturation)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def _build_category_color_palette(count: int) -> list[str]:
    """Build a large, deterministic set of visually distinct category colors.

    Starts with the most recognizable named colors, then fills the rest by
    stepping the hue by the golden angle (~137.5°) — a standard trick for
    spreading points around a circle with minimal clustering — across a
    few lightness bands, so a category/subcategory never runs out of a
    genuinely distinct color to be assigned. Lightness always stays well
    under 1.0, so this never produces white or near-white (the app's own
    background color).

    Parameters
    ----------
    count
        How many distinct colors to build.

    Returns
    -------
    list[str]
        `count` lowercase-distinct hex colors, most-recognizable first.
    """
    palette = list(_NAMED_CATEGORY_COLORS)
    seen = {color.lower() for color in palette}
    lightness_bands = (0.5, 0.35, 0.62)
    hue = 0.0
    band = 0
    while len(palette) < count:
        hue = (hue + 137.508) % 360
        color = _hsl_to_hex(hue, 0.6, lightness_bands[band % len(lightness_bands)])
        band += 1
        if color.lower() in seen:
            continue
        seen.add(color.lower())
        palette.append(color)
    return palette


CATEGORY_COLOR_PALETTE = _build_category_color_palette(1000)


def next_available_color(used_colors: Iterable[str]) -> str:
    """Pick the first palette color not already assigned to a category.

    Parameters
    ----------
    used_colors
        Every color already in use — callers pass the colors of whatever
        already exists plus anything freshly assigned earlier in the same
        batch, so two categories/subcategories created together never
        collide either.

    Returns
    -------
    str
        A hex color from `CATEGORY_COLOR_PALETTE` not present in `used_colors` (case-insensitively).
    """
    used = {color.lower() for color in used_colors}
    return next((color for color in CATEGORY_COLOR_PALETTE if color.lower() not in used), CATEGORY_COLOR_PALETTE[-1])


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
                color=next_available_color(category.color for category in result.values()),
            )
        elif not has_real_children and other_id in result:
            del result[other_id]
    return result


def plan_category_rename(
    categories: dict[str, Category], category_id: str, new_name: str
) -> tuple[dict[str, Category], dict[str, str]]:
    """Rename a category or subcategory, merging it into an existing same-named one if there is one.

    A top-level category only ever merges into another *top-level*
    category of the same classification with the same (case-insensitive)
    name; a subcategory only ever merges into a *sibling* under the same
    parent — the same name under a different parent never merges. On a
    top-level merge, each of the renamed category's own subcategories
    either merges into a same-named one already under the target, or is
    simply reparented under it; its "Other" catch-all (if any) always maps
    onto the target's own "Other" id, since `normalize_categories` (run at
    the end here) guarantees that id exists post-merge whenever there was
    a real subcategory to reparent or merge.

    Parameters
    ----------
    categories
        The full category tree.
    category_id
        The category or subcategory being renamed.
    new_name
        Its new display name.

    Returns
    -------
    tuple[dict[str, Category], dict[str, str]]
        The updated tree, with every merged-away id removed (or just the
        one renamed category, if nothing merged); and an `old_id -> new_id`
        map for every id a caller holding a reference to a category (a
        posting, a rule, a budget, ...) needs to update — empty when this
        rename didn't merge into anything.
    """
    category = categories[category_id]
    normalized_name = new_name.strip().lower()
    is_top_level = category.parent_category_id is None

    def is_merge_target(candidate: Category) -> bool:
        if candidate.category_id == category_id or _is_other_subcategory(candidate):
            return False
        if candidate.name.strip().lower() != normalized_name:
            return False
        if is_top_level:
            return candidate.parent_category_id is None and candidate.classification == category.classification
        return candidate.parent_category_id == category.parent_category_id

    target = next((candidate for candidate in categories.values() if is_merge_target(candidate)), None)
    if target is None:
        renamed = dict(categories)
        renamed[category_id] = category.model_copy(update={"name": new_name})
        return normalize_categories(renamed), {}

    result = dict(categories)
    del result[category_id]
    id_remap = {category_id: target.category_id}

    if is_top_level:
        children = [c for c in categories.values() if c.parent_category_id == category_id]
        target_children_by_name = {
            c.name.strip().lower(): c
            for c in categories.values()
            if c.parent_category_id == target.category_id and not _is_other_subcategory(c)
        }
        for child in children:
            if _is_other_subcategory(child):
                id_remap[child.category_id] = f"{target.category_id}{OTHER_SUBCATEGORY_SUFFIX}"
                del result[child.category_id]
                continue
            matched = target_children_by_name.get(child.name.strip().lower())
            if matched is not None:
                id_remap[child.category_id] = matched.category_id
                del result[child.category_id]
            else:
                result[child.category_id] = child.model_copy(update={"parent_category_id": target.category_id})

    return normalize_categories(result), id_remap


def remap_category_ids(store: AccountingStore, id_remap: dict[str, str]) -> AccountingStore:
    """Repoint every category/subcategory reference elsewhere in the store after a merge.

    Doesn't touch `store.categories` itself (the caller already applied
    `plan_category_rename`'s own result there) — this only fixes the other
    places a category id is stored: transfer rules, category patterns,
    budgets (both per-month and general), and posting splits. The raw
    ledger cache and manual per-posting overrides live outside
    `AccountingStore` entirely and must be remapped separately.

    Parameters
    ----------
    store
        The store, with `store.categories` already updated by the merge.
    id_remap
        `old_id -> new_id`, as returned by `plan_category_rename` — a
        no-op (returns `store` unchanged) when empty.

    Returns
    -------
    AccountingStore
        The same store, with every `category_id`/`subcategory_id` field
        referencing a merged-away id repointed to its replacement.
    """
    if not id_remap:
        return store

    def remap(category_id: str | None) -> str | None:
        return id_remap.get(category_id, category_id) if category_id is not None else None

    rules = [
        rule.model_copy(update={"category_id": remap(rule.category_id), "subcategory_id": remap(rule.subcategory_id)})
        for rule in store.rules
    ]
    patterns = {
        pattern_id: pattern.model_copy(
            update={"category_id": remap(pattern.category_id), "subcategory_id": remap(pattern.subcategory_id)}
        )
        for pattern_id, pattern in store.category_patterns.items()
    }
    budgets_by_key: dict[tuple[str, str, str | None], Budget] = {}
    for budget in store.budgets:
        updated_budget = budget.model_copy(
            update={"category_id": remap(budget.category_id), "subcategory_id": remap(budget.subcategory_id)}
        )
        budget_key = updated_budget.month, updated_budget.category_id, updated_budget.subcategory_id
        budgets_by_key[budget_key] = updated_budget
    general_budgets: dict[str, GeneralBudget] = {}
    for general_key, general in store.general_budgets.items():
        updated_general = general.model_copy(
            update={"category_id": remap(general.category_id), "subcategory_id": remap(general.subcategory_id)}
        )
        general_budgets[id_remap.get(general_key, general_key)] = updated_general
    posting_splits = {
        posting_id: split.model_copy(
            update={
                "legs": [
                    leg.model_copy(
                        update={"category_id": remap(leg.category_id), "subcategory_id": remap(leg.subcategory_id)}
                    )
                    for leg in split.legs
                ]
            }
        )
        for posting_id, split in store.posting_splits.items()
    }
    return store.model_copy(
        update={
            "rules": rules,
            "category_patterns": patterns,
            "budgets": list(budgets_by_key.values()),
            "general_budgets": general_budgets,
            "posting_splits": posting_splits,
        }
    )


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
        for top_name, sub_names in taxonomy.items():
            top_color = next_available_color(category.color for category in categories.values())
            top_id = f"{classification}:{slugify(top_name)}"
            categories[top_id] = Category(
                category_id=top_id, name=top_name, classification=classification, color=top_color
            )
            for sub_name in sub_names:
                sub_color = next_available_color(category.color for category in categories.values())
                sub_id = f"{top_id}:{slugify(sub_name)}"
                categories[sub_id] = Category(
                    category_id=sub_id,
                    name=sub_name,
                    classification=classification,
                    parent_category_id=top_id,
                    color=sub_color,
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
    rules: list[TransferRule] = Field(default_factory=list)
    category_patterns: dict[str, CategoryPattern] = Field(default_factory=dict)
    other_assets: list[OtherAsset] = Field(default_factory=list)
    opening_balances: dict[str, OpeningBalance] = Field(default_factory=dict)
    manual_transfers: list[ManualTransfer] = Field(default_factory=list)
    budgets: list[Budget] = Field(default_factory=list)
    general_budgets: dict[str, GeneralBudget] = Field(default_factory=dict)
    simulator_scenarios: list[SimulatorScenario] = Field(default_factory=list)
    posting_splits: dict[str, PostingSplit] = Field(default_factory=dict)
    posting_merges: dict[str, PostingMerge] = Field(default_factory=dict)
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
