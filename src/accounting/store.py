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
import re
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

import accounting.db as adb
from accounting.models import (
    Account,
    Budget,
    Category,
    CategoryClassification,
    CategoryPattern,
    DismissedSuggestion,
    GeneralBudget,
    Goal,
    GoalContribution,
    ManualOverride,
    ManualTransfer,
    OpeningBalance,
    OtherAsset,
    PostingMerge,
    PostingSplit,
    PostingSplitLeg,
    RecurringAddition,
    SimulatorScenario,
    Tag,
    TransferLink,
    TransferRule,
    WithdrawalPriorityEntry,
)
from db.base import (
    VersionConflictError,
    check_and_bump_row_version,
    check_and_bump_version,
    derive_id,
    get_version,
    natural_keys_by_id,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from sqlalchemy.orm import Session

    from db.base import Base

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
    """Convert an HSL color to its `#rrggbb` hex string.

    Returns
    -------
    str
    """
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
    """Whether `category` is one of the auto-created "Other" catch-all subcategories.

    Returns
    -------
    bool
    """
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
    onto the target's own "Other" id, since `normalize_categories` (run a
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
        """Whether `candidate` is an existing, different category the rename would merge into.

        Returns
        -------
        bool
        """
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
    places a category id is stored: category patterns, budgets (both
    per-month and general), and posting splits. `TransferRule` has no
    category fields of its own (see its own docstring). The raw ledger
    cache and manual per-posting overrides live outside `AccountingStore`
    entirely and must be remapped separately.

    If the merge target already has a budget (or general budget) for the
    same month/category/subcategory the merged-away category also had one
    for, the merged-away category's entry is dropped rather than kept —
    the survivor's own existing entry always wins, since there's no
    principled way to combine two different budgeted amounts. A caller
    that wants to warn about this before committing to the merge should
    call this same function itself and diff `store.budgets`/
    `general_budgets` against the result (see
    `api.routers.store.get_category_rename_preview`).

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
        referencing a merged-away id repointed to its replacement, and
        any now-colliding budget entry dropped in favor of the survivor's.
    """
    if not id_remap:
        return store

    def remap(category_id: str | None) -> str | None:
        """Look up `category_id`'s new id, or leave it unchanged if it wasn't merged away.

        Returns
        -------
        str or None
        """
        return id_remap.get(category_id, category_id) if category_id is not None else None

    def was_remapped(category_id: str | None, subcategory_id: str | None) -> bool:
        """Whether either id belonged to a category this merge is moving away from.

        Returns
        -------
        bool
        """
        return category_id in id_remap or subcategory_id in id_remap

    patterns = {
        pattern_id: pattern.model_copy(
            update={"category_id": remap(pattern.category_id), "subcategory_id": remap(pattern.subcategory_id)}
        )
        for pattern_id, pattern in store.category_patterns.items()
    }
    budgets_by_key: dict[tuple[str, str, str | None], Budget] = {}
    # Sorted so a budget the merge doesn't touch (including the survivor's own)
    # claims its key first — a colliding merged-away budget is then skipped
    # instead of overwriting it.
    for budget in sorted(store.budgets, key=lambda b: was_remapped(b.category_id, b.subcategory_id)):
        updated_budget = budget.model_copy(
            update={"category_id": remap(budget.category_id), "subcategory_id": remap(budget.subcategory_id)}
        )
        budget_key = updated_budget.month, updated_budget.category_id, updated_budget.subcategory_id
        if budget_key in budgets_by_key:
            continue
        budgets_by_key[budget_key] = updated_budget
    general_budgets: dict[str, GeneralBudget] = {}
    for general_key, general in sorted(store.general_budgets.items(), key=lambda item: item[0] in id_remap):
        updated_general = general.model_copy(
            update={"category_id": remap(general.category_id), "subcategory_id": remap(general.subcategory_id)}
        )
        new_key = id_remap.get(general_key, general_key)
        if new_key in general_budgets:
            continue
        general_budgets[new_key] = updated_general
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
            "category_patterns": patterns,
            "budgets": list(budgets_by_key.values()),
            "general_budgets": general_budgets,
            "posting_splits": posting_splits,
        }
    )


def category_ids_to_delete(categories: dict[str, Category], category_id: str) -> set[str]:
    """Every category id that deleting `category_id` would remove — itself, plus every subcategory if it's top-level.

    A subcategory's delete never cascades (it has none of its own); a
    top-level category's delete takes every one of its subcategories down
    with it, the same "children go too" behavior deleting it has always
    had, just made explicit here instead of happening as a side effect of
    a client-computed dict diff.

    Parameters
    ----------
    categories
        The full category tree.
    category_id
        The category or subcategory being deleted.

    Returns
    -------
    set[str]
    """
    category = categories[category_id]
    if category.parent_category_id is not None:
        return {category_id}
    children = {c.category_id for c in categories.values() if c.parent_category_id == category_id}
    return {category_id} | children


def uncategorize_category_ids(store: AccountingStore, category_ids: set[str]) -> AccountingStore:
    """Strip every reference to `category_ids` from everything a category delete doesn't already remove.

    Doesn't touch `store.categories` itself (the caller removes
    `category_ids` from it separately — see `category_ids_to_delete`) or
    the ledger/manual overrides, which live outside `AccountingStore`
    entirely (see `importers.ingest.uncategorize_ledger_postings` and the
    router's own override pass, mirroring `remap_category_ids`'s split).

    `PostingSplitLeg` has nullable `category_id`/`subcategory_id` fields,
    so a reference there is simply cleared — unlike a merge, there's no
    replacement id to repoint at. `TransferRule` has no category fields of
    its own (see its own docstring), so there's nothing to clear there.
    `Budget`/`GeneralBudget`/`CategoryPattern` require a `category_id`
    (never null): a row whose own `category_id` is being deleted has
    nothing left to be, so it's dropped entirely; one only referencing a
    deleted id via its (nullable) `subcategory_id` just has that cleared,
    same as the nullable-field tables.

    Parameters
    ----------
    store
        The store, with `store.categories` not yet updated.
    category_ids
        Every category id being deleted (see `category_ids_to_delete`).

    Returns
    -------
    AccountingStore
    """
    if not category_ids:
        return store

    def clear(field_id: str | None) -> str | None:
        """Return `None` if `field_id` is one of the ids being deleted, otherwise leave it unchanged.

        Returns
        -------
        str or None
        """
        return None if field_id in category_ids else field_id

    patterns = {
        pattern_id: pattern.model_copy(update={"subcategory_id": clear(pattern.subcategory_id)})
        for pattern_id, pattern in store.category_patterns.items()
        if pattern.category_id not in category_ids
    }
    budgets = [
        budget.model_copy(update={"subcategory_id": clear(budget.subcategory_id)})
        for budget in store.budgets
        if budget.category_id not in category_ids
    ]
    general_budgets = {
        key: general.model_copy(update={"subcategory_id": clear(general.subcategory_id)})
        for key, general in store.general_budgets.items()
        # `general_budgets` is keyed by whichever of category_id/subcategory_id
        # is most specific — a deleted top-level category's own subcategories
        # are already folded into `category_ids` (see `category_ids_to_delete`),
        # so checking the key alone is enough to catch both cases.
        if key not in category_ids
    }
    posting_splits = {
        posting_id: split.model_copy(
            update={
                "legs": [
                    leg.model_copy(
                        update={"category_id": clear(leg.category_id), "subcategory_id": clear(leg.subcategory_id)}
                    )
                    for leg in split.legs
                ]
            }
        )
        for posting_id, split in store.posting_splits.items()
    }
    return store.model_copy(
        update={
            "category_patterns": patterns,
            "budgets": budgets,
            "general_budgets": general_budgets,
            "posting_splits": posting_splits,
        }
    )


def plan_tag_rename(tags: dict[str, Tag], tag_id: str, new_name: str) -> tuple[dict[str, Tag], dict[str, str]]:
    """Rename a tag, merging it into an existing same-named tag if there is one.

    Mirrors `plan_category_rename`'s no-parent/no-classification case: a
    `Tag` has neither, so the merge match is pure case-insensitive name
    equality against every *other* tag — no classification or parent to
    additionally scope it by.

    Parameters
    ----------
    tags
        Every tag, keyed by `tag_id`.
    tag_id
        The tag being renamed.
    new_name
        Its new display name.

    Returns
    -------
    tuple[dict[str, Tag], dict[str, str]]
        The updated tag map, with the merged-away id removed (or just the
        one renamed tag, if nothing merged); and an `old_id -> new_id` map
        for a caller to repoint every reference elsewhere (see
        `remap_tag_ids`) — empty when this rename didn't merge into
        anything.
    """
    tag = tags[tag_id]
    normalized_name = new_name.strip().lower()
    target = next(
        (
            candidate
            for candidate in tags.values()
            if candidate.tag_id != tag_id and candidate.name.strip().lower() == normalized_name
        ),
        None,
    )
    if target is None:
        renamed = dict(tags)
        renamed[tag_id] = tag.model_copy(update={"name": new_name})
        return renamed, {}

    result = dict(tags)
    del result[tag_id]
    return result, {tag_id: target.tag_id}


def remap_tag_ids(id_remap: dict[str, str], session: Session, user_id: uuid.UUID) -> None:
    """Repoint every tag reference that lives outside `AccountingStore` after a merge.

    Doesn't touch `store.tags` itself (the caller already applied
    `plan_tag_rename`'s own result there, the same division of labor as
    `remap_category_ids`/`store.categories`) — this only fixes the two
    other places a tag id is stored, neither of which `AccountingStore`
    holds: the `posting_tags` join table and each posting override's
    `PostingOverrideTag` rows — both real FKs to `tags.id`, repointed the
    same way. That's also why this — unlike the pure `remap_category_ids`
    — needs a `session`/`user_id` of its own.

    A posting (or override) already tagged with both the merged-away and
    target tag would violate one of these tables' own uniqueness on a
    plain update, so that row is deleted instead of retargeted, rather
    than left to raise.

    Parameters
    ----------
    id_remap
        `old_id -> new_id`, as returned by `plan_tag_rename` — a no-op
        when empty.
    session
        An open database session.
    user_id
        Whose tags these are.
    """
    if not id_remap:
        return

    for old_tag_id, new_tag_id in id_remap.items():
        old_id = _tag_id(user_id, old_tag_id)
        new_id = _tag_id(user_id, new_tag_id)

        already_tagged_postings = {
            row.posting_id for row in session.query(adb.PostingTag.posting_id).filter_by(user_id=user_id, tag_id=new_id)
        }
        session.query(adb.PostingTag).filter_by(user_id=user_id, tag_id=old_id).filter(
            adb.PostingTag.posting_id.in_(already_tagged_postings)
        ).delete(synchronize_session=False)
        session.query(adb.PostingTag).filter_by(user_id=user_id, tag_id=old_id).update(
            {"tag_id": new_id}, synchronize_session=False
        )

        already_tagged_overrides = {
            row.override_id
            for row in session.query(adb.PostingOverrideTag.override_id).filter_by(user_id=user_id, tag_id=new_id)
        }
        session.query(adb.PostingOverrideTag).filter_by(user_id=user_id, tag_id=old_id).filter(
            adb.PostingOverrideTag.override_id.in_(already_tagged_overrides)
        ).delete(synchronize_session=False)
        session.query(adb.PostingOverrideTag).filter_by(user_id=user_id, tag_id=old_id).update(
            {"tag_id": new_id}, synchronize_session=False
        )
    session.commit()


def default_categories() -> dict[str, Category]:
    """Build the starting category tree every new user's store is seeded with.

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
    holds a single current value of. Dismissed suggestions aren't stored
    here either, for a different reason: every other entity is read as
    "give me the whole list," cheap since each stays bounded by how much a
    person is actually organizing (dozens of accounts, categories, rules).
    Dismissed suggestions are the one exception — only ever checked as
    "has this one been dismissed," and unbounded over time — so routing
    that table through `load_store`/`save_store` would make every
    unrelated store mutation pay to load a table that only ever grows. See
    `dismissed_suggestion_ids`/`list_dismissed_suggestions`/
    `dismiss_suggestion`/`undismiss_suggestion`, which query it directly.
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
    transfer_links: list[TransferLink] = Field(default_factory=list)
    goals: dict[str, Goal] = Field(default_factory=dict)
    goal_contributions: dict[str, GoalContribution] = Field(default_factory=dict)
    recurring_additions: list[RecurringAddition] = Field(default_factory=list)
    withdrawal_priorities: list[WithdrawalPriorityEntry] = Field(default_factory=list)


def _account_id(user_id: uuid.UUID, account_id: str) -> uuid.UUID:
    """Derive this user's stable internal id for the account natural-keyed `account_id`.

    Returns
    -------
    uuid.UUID
    """
    return derive_id(user_id, "accounts", account_id)


def _category_id(user_id: uuid.UUID, category_id: str | None) -> uuid.UUID | None:
    """Derive this user's stable internal id for `category_id`, or `None` if `category_id` is `None`.

    Returns
    -------
    uuid.UUID or None
    """
    return derive_id(user_id, "categories", category_id) if category_id is not None else None


def _goal_id(user_id: uuid.UUID, goal_id: str) -> uuid.UUID:
    """Derive this user's stable internal id for the goal natural-keyed `goal_id`.

    Returns
    -------
    uuid.UUID
    """
    return derive_id(user_id, "goals", goal_id)


def _tag_id(user_id: uuid.UUID, tag_id: str) -> uuid.UUID:
    """Derive this user's stable internal id for the tag natural-keyed `tag_id`.

    Returns
    -------
    uuid.UUID
    """
    return derive_id(user_id, "tags", tag_id)


def _transaction_id(user_id: uuid.UUID, transaction_id: str) -> uuid.UUID:
    """Derive this user's stable internal id for the transaction natural-keyed `transaction_id`.

    Returns
    -------
    uuid.UUID
    """
    return derive_id(user_id, "transactions", transaction_id)


def _posting_id(user_id: uuid.UUID, posting_id: str) -> uuid.UUID:
    """Derive this user's stable internal id for the posting natural-keyed `posting_id`.

    Returns
    -------
    uuid.UUID
    """
    return derive_id(user_id, "postings", posting_id)


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


def _category_from_row(row: adb.Category, category_natural_key_by_id: dict[uuid.UUID, str]) -> Category:
    """Convert one persisted `Category` row back into its pydantic model, using natural keys.

    Returns
    -------
    Category
    """
    return Category(
        category_id=row.natural_key,
        name=row.name,
        classification=row.classification,  # type: ignore[arg-type]
        parent_category_id=category_natural_key_by_id.get(row.parent_category_id)
        if row.parent_category_id is not None
        else None,
        color=row.color,
    )


def _rule_from_row(
    row: adb.TransferRule,
    account_natural_key_by_id: dict[uuid.UUID, str],
    excluded_transaction_ids: list[str],
) -> TransferRule:
    """Convert one persisted `TransferRule` row back into its pydantic model, using natural keys.

    Returns
    -------
    TransferRule
    """
    return TransferRule(
        rule_id=row.natural_key,
        description_contains=row.description_contains,
        account_id=account_natural_key_by_id.get(row.account_id) if row.account_id is not None else None,
        counterparty_account_id=account_natural_key_by_id.get(row.counterparty_account_id)
        if row.counterparty_account_id is not None
        else None,
        priority=row.priority,
        description=row.description,
        active=row.active,
        excluded_transaction_ids=excluded_transaction_ids,
        version=row.version,
    )


_TRANSFER_RULES_TABLE = "accounting.transfer_rules"


def update_transfer_rule(
    session: Session, user_id: uuid.UUID, rule: TransferRule, expected_version: int
) -> TransferRule | None:
    """Update one transfer rule's fields in place, touching no other persisted entity.

    `rule.rule_id` identifies which row to update; every other field on
    `rule` (including `rule.version`, which is never read here — only
    `expected_version` is) becomes that row's new state. Unlike
    `save_store`, this never deletes and reinserts the whole
    `transfer_rules` table — it's a single row, guarded by
    `db.base.check_and_bump_row_version` so a stale client can't silently
    clobber a concurrent edit to the same rule. Caller is responsible for
    running `ledger.transfers.reconcile_and_persist_rule_links` afterward,
    same as `POST /transfer-rules` already does.

    Returns
    -------
    TransferRule | None
        The rule as persisted after the update, or `None` if no rule with
        `rule.rule_id` exists for this user. Raises `db.base.VersionConflictError`
        (propagated straight from `check_and_bump_row_version`) if the rule
        exists but `expected_version` no longer matches what's stored.

    Raises
    ------
    RuntimeError
        If the row vanishes between the version check just above and this
        function's own read of it — the version check already proved the
        row exists inside this same transaction, so this is only a
        defensive invariant, never expected to actually happen.
    """
    row_id = derive_id(user_id, "transfer_rules", rule.rule_id)
    new_version = check_and_bump_row_version(session, _TRANSFER_RULES_TABLE, row_id, user_id, expected_version)
    if new_version is None:
        return None
    row = session.get(adb.TransferRule, row_id)
    if row is None:
        message = f"transfer_rules row {row_id} vanished between its version check and this read"
        raise RuntimeError(message)
    row.description_contains = rule.description_contains
    row.account_id = _account_id(user_id, rule.account_id) if rule.account_id is not None else None
    row.counterparty_account_id = (
        _account_id(user_id, rule.counterparty_account_id) if rule.counterparty_account_id is not None else None
    )
    row.priority = rule.priority
    row.description = rule.description
    row.active = rule.active
    session.flush()

    existing_exclusions = list(session.query(adb.TransferRuleExclusion).filter_by(user_id=user_id, rule_id=row.id))
    existing_transaction_ids = {exclusion.transaction_id for exclusion in existing_exclusions}
    desired_transaction_ids = {_transaction_id(user_id, tid) for tid in rule.excluded_transaction_ids}
    to_remove = existing_transaction_ids - desired_transaction_ids
    if to_remove:
        session.query(adb.TransferRuleExclusion).filter_by(user_id=user_id, rule_id=row.id).filter(
            adb.TransferRuleExclusion.transaction_id.in_(to_remove)
        ).delete(synchronize_session=False)
    session.add_all(
        adb.TransferRuleExclusion(user_id=user_id, rule_id=row.id, transaction_id=transaction_id)
        for transaction_id in desired_transaction_ids - existing_transaction_ids
    )
    session.flush()

    account_natural_key_by_id = natural_keys_by_id(
        session, adb.Account, user_id, [row.account_id, row.counterparty_account_id]
    )
    return _rule_from_row(row, account_natural_key_by_id, list(dict.fromkeys(rule.excluded_transaction_ids)))


def delete_transfer_rule(session: Session, user_id: uuid.UUID, rule_id: str) -> bool:
    """Delete one transfer rule, without touching any other persisted entity.

    Idempotent by design: deleting a rule that's already gone isn't an
    error at this layer (the caller — `DELETE /transfer-rules/{rule_id}`
    — turns "gone" into a 404 either way, but never checks a version
    first, since there's nothing left to conflict with once a row doesn't
    exist).

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed.
    """
    row_id = derive_id(user_id, "transfer_rules", rule_id)
    deleted = session.query(adb.TransferRule).filter_by(id=row_id, user_id=user_id).delete()
    session.flush()
    return deleted > 0


def _pattern_from_row(row: adb.CategoryPattern, category_natural_key_by_id: dict[uuid.UUID, str]) -> CategoryPattern:
    """Convert one persisted `CategoryPattern` row back into its pydantic model, using natural keys.

    Returns
    -------
    CategoryPattern
    """
    return CategoryPattern(
        pattern_id=row.natural_key,
        description_contains=row.description_contains,
        category_id=category_natural_key_by_id[row.category_id],
        subcategory_id=category_natural_key_by_id.get(row.subcategory_id) if row.subcategory_id is not None else None,
        priority=row.priority,
        active=row.active,
        version=row.version,
    )


def _split_from_rows(
    posting_natural_key: str,
    legs: Iterable[adb.PostingSplitLeg],
    category_natural_key_by_id: dict[uuid.UUID, str],
) -> PostingSplit:
    """Convert one posting's persisted split-leg rows back into a `PostingSplit`, using natural keys.

    Returns
    -------
    PostingSplit
    """
    ordered = sorted(legs, key=lambda leg: leg.ordinal)
    return PostingSplit(
        posting_id=posting_natural_key,
        legs=[
            PostingSplitLeg(
                amount=leg.amount,
                category_id=category_natural_key_by_id.get(leg.category_id) if leg.category_id is not None else None,
                subcategory_id=category_natural_key_by_id.get(leg.subcategory_id)
                if leg.subcategory_id is not None
                else None,
                description=leg.description,
            )
            for leg in ordered
        ],
    )


def _merge_from_rows(
    row: adb.PostingMerge, duplicate_ids: list[uuid.UUID], transaction_natural_key_by_id: dict[uuid.UUID, str]
) -> PostingMerge:
    """Convert one persisted `PostingMerge` row back into its pydantic model, using natural keys.

    Returns
    -------
    PostingMerge
    """
    return PostingMerge(
        merge_id=row.natural_key,
        kept_transaction_id=transaction_natural_key_by_id[row.kept_transaction_id],
        duplicate_transaction_ids=[transaction_natural_key_by_id[duplicate_id] for duplicate_id in duplicate_ids],
        description=row.description,
    )


def _transfer_link_from_row(
    row: adb.TransferLink, transaction_ids: list[uuid.UUID], transaction_natural_key_by_id: dict[uuid.UUID, str]
) -> TransferLink:
    """Convert one persisted `TransferLink` row (plus its two membership rows) back into its pydantic model.

    Returns
    -------
    TransferLink
    """
    first, second = sorted(transaction_natural_key_by_id[transaction_id] for transaction_id in transaction_ids)
    return TransferLink(
        link_id=row.natural_key,
        transaction_id_a=first,
        transaction_id_b=second,
        source=row.source,  # type: ignore[arg-type]
        rule_id=row.rule_id,
    )


def load_store(session: Session, user_id: uuid.UUID) -> AccountingStore:  # noqa: PLR0914 (one local per AccountingStore field being loaded — splitting this up would just add indirection)
    """Read the persisted accounting store, seeding sensible defaults the first time.

    A brand-new user has no rows yet, but still needs the two uncategorized
    placeholder accounts and the default category tree to be usable
    immediately — those are backfilled here rather than requiring a
    separate setup step. No rule is seeded: every rule necessarily points
    at one person's own account/employer/payee, so there's nothing generic
    enough to start a fresh install with — a user writes their own from
    the Rules tab, after creating the counterparty account it points at.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose store to load.

    Returns
    -------
    AccountingStore
        The persisted store, with default accounts/categories backfilled if missing.
    """
    account_rows = list(session.query(adb.Account).filter_by(user_id=user_id))
    category_rows = list(session.query(adb.Category).filter_by(user_id=user_id))
    if not account_rows and not category_rows:
        store = AccountingStore(categories=default_categories(), accounts=default_accounts())
        save_store(store, session, user_id=user_id)
        return store

    account_natural_key_by_id = {row.id: row.natural_key for row in account_rows}
    category_natural_key_by_id = {row.id: row.natural_key for row in category_rows}
    accounts = {row.natural_key: _account_from_row(row, account_natural_key_by_id) for row in account_rows}
    categories = {row.natural_key: _category_from_row(row, category_natural_key_by_id) for row in category_rows}

    tags = {
        row.natural_key: Tag(tag_id=row.natural_key, name=row.name)
        for row in session.query(adb.Tag).filter_by(user_id=user_id)
    }
    rule_rows = list(session.query(adb.TransferRule).filter_by(user_id=user_id))
    exclusion_rows = list(session.query(adb.TransferRuleExclusion).filter_by(user_id=user_id))
    exclusion_transaction_natural_key_by_id = natural_keys_by_id(
        session, adb.Transaction, user_id, [exclusion.transaction_id for exclusion in exclusion_rows]
    )
    exclusions_by_rule: dict[uuid.UUID, list[adb.TransferRuleExclusion]] = _group_by(
        exclusion_rows, key=lambda row: row.rule_id
    )
    rules = [
        _rule_from_row(
            row,
            account_natural_key_by_id,
            [
                exclusion_transaction_natural_key_by_id[exclusion.transaction_id]
                for exclusion in exclusions_by_rule.get(row.id, [])
            ],
        )
        for row in rule_rows
    ]
    category_patterns = {
        row.natural_key: _pattern_from_row(row, category_natural_key_by_id)
        for row in session.query(adb.CategoryPattern).filter_by(user_id=user_id)
    }
    other_assets = [
        OtherAsset(asset_id=row.natural_key, name=row.name, value=row.value, currency=row.currency, note=row.note)  # type: ignore[arg-type]
        for row in session.query(adb.OtherAsset).filter_by(user_id=user_id)
    ]
    opening_balances = {
        account_natural_key_by_id[row.account_id]: OpeningBalance(
            account_id=account_natural_key_by_id[row.account_id], amount=row.amount, as_of_date=row.as_of_date
        )
        for row in session.query(adb.OpeningBalance).filter_by(user_id=user_id)
    }
    manual_transfers = [
        ManualTransfer(
            transfer_id=row.natural_key,
            date=row.date,
            from_account_id=account_natural_key_by_id[row.from_account_id],
            to_account_id=account_natural_key_by_id[row.to_account_id],
            from_amount=row.from_amount,
            to_amount=row.to_amount,
            description=row.description,
        )
        for row in session.query(adb.ManualTransfer).filter_by(user_id=user_id)
    ]
    budgets = [
        Budget(
            budget_id=row.natural_key,
            month=row.month,
            category_id=category_natural_key_by_id[row.category_id],
            subcategory_id=category_natural_key_by_id.get(row.subcategory_id)
            if row.subcategory_id is not None
            else None,
            amount=row.amount,
            currency=row.currency,  # type: ignore[arg-type]
        )
        for row in session.query(adb.Budget).filter_by(user_id=user_id)
    ]
    general_budget_rows = list(session.query(adb.GeneralBudget).filter_by(user_id=user_id))
    general_budgets = {
        category_natural_key_by_id[row.category_id]
        if row.subcategory_id is None
        else category_natural_key_by_id[row.subcategory_id]: GeneralBudget(
            category_id=category_natural_key_by_id[row.category_id],
            subcategory_id=category_natural_key_by_id.get(row.subcategory_id)
            if row.subcategory_id is not None
            else None,
            amount=row.amount,
            currency=row.currency,  # type: ignore[arg-type]
        )
        for row in general_budget_rows
    }
    simulator_scenarios = [
        SimulatorScenario(
            scenario_id=row.natural_key,
            name=row.name,
            initial_capital=row.initial_capital,
            monthly_contribution=row.monthly_contribution,
            horizon_years=row.horizon_years,
            annual_rate_pct=row.annual_rate_pct,
            compounding_frequency=row.compounding_frequency,  # type: ignore[arg-type]
            currency=row.currency,  # type: ignore[arg-type]
        )
        for row in session.query(adb.SimulatorScenario).filter_by(user_id=user_id)
    ]

    split_leg_rows = list(session.query(adb.PostingSplitLeg).filter_by(user_id=user_id))
    posting_split_rows = list(session.query(adb.PostingSplit).filter_by(user_id=user_id))
    merge_rows = list(session.query(adb.PostingMerge).filter_by(user_id=user_id))
    duplicate_rows = list(session.query(adb.PostingMergeDuplicate).filter_by(user_id=user_id))
    goal_contribution_rows = list(session.query(adb.GoalContribution).filter_by(user_id=user_id))
    transfer_link_rows = list(session.query(adb.TransferLink).filter_by(user_id=user_id))
    transfer_linked_transaction_rows = list(session.query(adb.TransferLinkedTransaction).filter_by(user_id=user_id))

    posting_natural_key_by_id = natural_keys_by_id(
        session,
        adb.Posting,
        user_id,
        [split.posting_id for split in posting_split_rows]
        + [contribution.source_posting_id for contribution in goal_contribution_rows],
    )
    transaction_natural_key_by_id = natural_keys_by_id(
        session,
        adb.Transaction,
        user_id,
        [merge.kept_transaction_id for merge in merge_rows]
        + [duplicate.duplicate_transaction_id for duplicate in duplicate_rows]
        + [row.transaction_id for row in transfer_linked_transaction_rows],
    )

    posting_split_natural_key_by_id = {
        split.id: posting_natural_key_by_id[split.posting_id] for split in posting_split_rows
    }
    posting_splits = {
        posting_split_natural_key_by_id[posting_split_id]: _split_from_rows(
            posting_split_natural_key_by_id[posting_split_id], legs, category_natural_key_by_id
        )
        for posting_split_id, legs in _group_by(split_leg_rows, key=lambda leg: leg.posting_split_id).items()
    }
    duplicates_by_merge: dict[uuid.UUID, list[adb.PostingMergeDuplicate]] = _group_by(
        duplicate_rows, key=lambda row: row.merge_id
    )
    posting_merges = {
        row.natural_key: _merge_from_rows(
            row,
            [dup.duplicate_transaction_id for dup in duplicates_by_merge.get(row.id, [])],
            transaction_natural_key_by_id,
        )
        for row in merge_rows
    }
    transactions_by_link: dict[uuid.UUID, list[adb.TransferLinkedTransaction]] = _group_by(
        transfer_linked_transaction_rows, key=lambda row: row.link_id
    )
    transfer_links = [
        _transfer_link_from_row(
            row,
            [member.transaction_id for member in transactions_by_link.get(row.id, [])],
            transaction_natural_key_by_id,
        )
        for row in transfer_link_rows
    ]
    goal_rows = list(session.query(adb.Goal).filter_by(user_id=user_id))
    goal_natural_key_by_id = {row.id: row.natural_key for row in goal_rows}
    goals = {
        row.natural_key: Goal(
            goal_id=row.natural_key,
            name=row.name,
            target_amount=row.target_amount,
            target_currency=row.target_currency,  # type: ignore[arg-type]
            target_date=row.target_date,
            color=row.color,
            created_at=row.created_at,
            version=row.version,
        )
        for row in goal_rows
    }
    goal_contributions = {
        row.natural_key: GoalContribution(
            contribution_id=row.natural_key,
            goal_id=goal_natural_key_by_id[row.goal_id],
            date=row.date,
            amount=row.amount,
            currency=row.currency,  # type: ignore[arg-type]
            note=row.note,
            source_posting_id=posting_natural_key_by_id.get(row.source_posting_id)
            if row.source_posting_id is not None
            else None,
            origin=row.origin,  # type: ignore[arg-type]
            edited=row.edited,
        )
        for row in goal_contribution_rows
    }
    recurring_additions = [
        RecurringAddition(
            addition_id=row.natural_key,
            goal_id=goal_natural_key_by_id[row.goal_id],
            start_date=row.start_date,
            frequency=row.frequency,  # type: ignore[arg-type]
            end_date=row.end_date,
            mode=row.mode,  # type: ignore[arg-type]
            value=row.value,
            currency=row.currency,  # type: ignore[arg-type]
            priority=row.priority,
        )
        for row in session.query(adb.RecurringAddition).filter_by(user_id=user_id)
    ]
    withdrawal_priorities = [
        WithdrawalPriorityEntry(goal_id=goal_natural_key_by_id[row.goal_id], priority=row.priority)
        for row in session.query(adb.WithdrawalPriorityEntry).filter_by(user_id=user_id)
    ]
    store = AccountingStore(
        accounts=accounts,
        categories=categories,
        tags=tags,
        rules=rules,
        category_patterns=category_patterns,
        other_assets=other_assets,
        opening_balances=opening_balances,
        manual_transfers=manual_transfers,
        budgets=budgets,
        general_budgets=general_budgets,
        simulator_scenarios=simulator_scenarios,
        posting_splits=posting_splits,
        posting_merges=posting_merges,
        transfer_links=transfer_links,
        goals=goals,
        goal_contributions=goal_contributions,
        recurring_additions=recurring_additions,
        withdrawal_priorities=withdrawal_priorities,
    )

    missing_accounts = {k: v for k, v in default_accounts().items() if k not in store.accounts}
    if missing_accounts:
        store = store.model_copy(update={"accounts": {**store.accounts, **missing_accounts}})
    return store


def _group_by[T, K](rows: Iterable[T], key: Callable[[T], K]) -> dict[K, list[T]]:
    """Group `rows` into lists keyed by `key(row)`, preserving each group's original order.

    Returns
    -------
    dict[K, list[T]]
    """
    grouped: dict[K, list[T]] = {}
    for row in rows:
        grouped.setdefault(key(row), []).append(row)
    return grouped


def _upsert_and_prune(
    session: Session, model: type[Base], user_id: uuid.UUID, rows: Iterable[Base], keep_natural_keys: set[str]
) -> None:
    """Insert-or-update every one of `rows`, then delete this user's rows of `model` not in `keep_natural_keys`.

    Used only for `Account`/`Category`/`Tag` — every other entity in the
    store is safe to delete-all-then-reinsert (see `save_store`), but these
    three are referenced by the ledger's own `postings`/`posting_tags`
    tables (a different domain, not managed here), so blindly deleting one
    still referenced by a real posting must fail loudly with a foreign key
    error instead of silently dropping ledger history's own referential
    integrity. `session.merge()` (not `add()`) is what makes this an
    upsert rather than a duplicate-key error on a row that already exists
    — matching on `id`, which `db.base.derive_id` makes stable across calls
    for the same natural key, so this still finds the same existing row
    `account_id`/`category_id`/`tag_id` used to match on directly.
    """
    for row in rows:
        session.merge(row)
    session.flush()
    existing_natural_keys = {
        existing.natural_key  # type: ignore[attr-defined]
        for existing in session.query(model).filter_by(user_id=user_id)
    }
    removed_natural_keys = existing_natural_keys - keep_natural_keys
    if removed_natural_keys:
        session.query(model).filter_by(user_id=user_id).filter(
            model.natural_key.in_(removed_natural_keys)  # type: ignore[attr-defined]
        ).delete(synchronize_session=False)


def _upsert_transfer_rules_and_prune(session: Session, user_id: uuid.UUID, rules: list[TransferRule]) -> None:
    """Insert-or-update every one of `rules`, then delete this user's rows not among them — never touching `version`.

    `TransferRule` can't use `_upsert_and_prune` above: that helper's
    `session.merge()` overwrites every mapped column on an existing row
    with the transient instance's own value, including `version` (which
    the transient instance never sets, so it would silently reset to the
    column default) — exactly the bug `PATCH /transfer-rules/{rule_id}`'s
    per-row optimistic concurrency depends on not happening. A raw
    `INSERT ... ON CONFLICT (id) DO UPDATE` whose `SET` clause simply
    omits `version` avoids that: an existing row keeps whatever version
    `check_and_bump_row_version` last left it at, no matter how many times
    an unrelated `POST /transfer-rules` round-trips through here.
    """
    keep_ids: set[uuid.UUID] = set()
    for rule in rules:
        row_id = derive_id(user_id, "transfer_rules", rule.rule_id)
        keep_ids.add(row_id)
        session.execute(
            text(
                """
                INSERT INTO accounting.transfer_rules
                    (id, user_id, natural_key, description_contains, account_id,
                     counterparty_account_id, priority, description, active, version)
                VALUES
                    (:id, :user_id, :natural_key, :description_contains, :account_id,
                     :counterparty_account_id, :priority, :description, :active, 1)
                ON CONFLICT (id) DO UPDATE SET
                    description_contains = EXCLUDED.description_contains,
                    account_id = EXCLUDED.account_id,
                    counterparty_account_id = EXCLUDED.counterparty_account_id,
                    priority = EXCLUDED.priority,
                    description = EXCLUDED.description,
                    active = EXCLUDED.active
                """
            ),
            {
                "id": str(row_id),
                "user_id": str(user_id),
                "natural_key": rule.rule_id,
                "description_contains": rule.description_contains,
                "account_id": str(_account_id(user_id, rule.account_id)) if rule.account_id is not None else None,
                "counterparty_account_id": str(_account_id(user_id, rule.counterparty_account_id))
                if rule.counterparty_account_id is not None
                else None,
                "priority": rule.priority,
                "description": rule.description,
                "active": rule.active,
            },
        )
    session.flush()
    existing_ids = {row.id for row in session.query(adb.TransferRule.id).filter_by(user_id=user_id)}
    removed_ids = existing_ids - keep_ids
    if removed_ids:
        session.query(adb.TransferRule).filter_by(user_id=user_id).filter(adb.TransferRule.id.in_(removed_ids)).delete(
            synchronize_session=False
        )


def _upsert_goals_and_prune(session: Session, user_id: uuid.UUID, goals: list[Goal]) -> None:
    """Insert-or-update every one of `goals`, then delete this user's rows not among them — never touching `version`.

    Same reasoning as `_upsert_transfer_rules_and_prune`: `Goal` carries a
    `version` column `PATCH /goals/{goal_id}` depends on for its own
    per-row optimistic concurrency, so `save_store`'s usual delete-all/
    reinsert-all treatment (which would silently reset it) can't be used
    here either.
    """
    keep_ids: set[uuid.UUID] = set()
    for goal in goals:
        row_id = derive_id(user_id, "goals", goal.goal_id)
        keep_ids.add(row_id)
        session.execute(
            text(
                """
                INSERT INTO accounting.goals
                    (id, user_id, natural_key, name, target_amount, target_currency, target_date,
                     color, created_at, version)
                VALUES
                    (:id, :user_id, :natural_key, :name, :target_amount, :target_currency, :target_date,
                     :color, :created_at, 1)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    target_amount = EXCLUDED.target_amount,
                    target_currency = EXCLUDED.target_currency,
                    target_date = EXCLUDED.target_date,
                    color = EXCLUDED.color
                """
            ),
            {
                "id": str(row_id),
                "user_id": str(user_id),
                "natural_key": goal.goal_id,
                "name": goal.name,
                "target_amount": goal.target_amount,
                "target_currency": goal.target_currency,
                "target_date": goal.target_date,
                "color": goal.color,
                "created_at": goal.created_at,
            },
        )
    session.flush()
    existing_ids = {row.id for row in session.query(adb.Goal.id).filter_by(user_id=user_id)}
    removed_ids = existing_ids - keep_ids
    if removed_ids:
        session.query(adb.Goal).filter_by(user_id=user_id).filter(adb.Goal.id.in_(removed_ids)).delete(
            synchronize_session=False
        )


_GOALS_TABLE = "accounting.goals"


def update_goal(session: Session, user_id: uuid.UUID, goal: Goal, expected_version: int) -> Goal | None:
    """Update one goal's fields in place, touching no other persisted entity.

    `goal.goal_id` identifies which row to update; every other field on
    `goal` (`goal.version` is never read here — only `expected_version`
    is) becomes that row's new state. Unlike `save_store`, this never
    deletes and reinserts the whole `goals` table — it's a single row,
    guarded by `db.base.check_and_bump_row_version` so a stale client
    can't silently clobber a concurrent edit to the same goal.

    Returns
    -------
    Goal | None
        The goal as persisted after the update, or `None` if no goal with
        `goal.goal_id` exists for this user. Raises `db.base.VersionConflictError`
        (propagated straight from `check_and_bump_row_version`) if the goal
        exists but `expected_version` no longer matches what's stored.

    Raises
    ------
    RuntimeError
        If the row vanishes between the version check just above and this
        function's own read of it — the version check already proved the
        row exists inside this same transaction, so this is only a
        defensive invariant, never expected to actually happen.
    """
    row_id = derive_id(user_id, "goals", goal.goal_id)
    new_version = check_and_bump_row_version(session, _GOALS_TABLE, row_id, user_id, expected_version)
    if new_version is None:
        return None
    row = session.get(adb.Goal, row_id)
    if row is None:
        message = f"goals row {row_id} vanished between its version check and this read"
        raise RuntimeError(message)
    row.name = goal.name
    row.target_amount = goal.target_amount
    row.target_currency = goal.target_currency
    row.target_date = goal.target_date
    row.color = goal.color
    session.flush()
    return Goal(
        goal_id=goal.goal_id,
        name=row.name,
        target_amount=row.target_amount,
        target_currency=row.target_currency,
        target_date=row.target_date,
        color=row.color,
        created_at=row.created_at,
        version=new_version,
    )


def delete_goal(session: Session, user_id: uuid.UUID, goal_id: str) -> bool:
    """Delete one goal, without touching any other persisted entity.

    Idempotent by design, same reasoning as `delete_transfer_rule`: no
    version check, since a goal that's already gone has nothing left to
    conflict with. Fails loudly (an `IntegrityError`, uncaught) if the
    goal still has real `GoalContribution`/`RecurringAddition`/
    `WithdrawalPriorityEntry` rows referencing it — same as it already
    would deleting through the whole-store path.

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed.
    """
    row_id = derive_id(user_id, "goals", goal_id)
    deleted = session.query(adb.Goal).filter_by(id=row_id, user_id=user_id).delete()
    session.flush()
    return deleted > 0


def _upsert_category_patterns_and_prune(session: Session, user_id: uuid.UUID, patterns: list[CategoryPattern]) -> None:
    """Insert-or-update every one of `patterns`, then delete this user's rows not among them — never touching `version`.

    Same reasoning as `_upsert_transfer_rules_and_prune`/`_upsert_goals_and_prune`: `CategoryPattern`
    carries a `version` column `PATCH /category-patterns/{pattern_id}` depends on, so it can't use
    `save_store`'s usual delete-all/reinsert-all treatment.
    """
    keep_ids: set[uuid.UUID] = set()
    for pattern in patterns:
        row_id = derive_id(user_id, "category_patterns", pattern.pattern_id)
        keep_ids.add(row_id)
        session.execute(
            text(
                """
                INSERT INTO accounting.category_patterns
                    (id, user_id, natural_key, description_contains, category_id, subcategory_id,
                     priority, active, version)
                VALUES
                    (:id, :user_id, :natural_key, :description_contains, :category_id, :subcategory_id,
                     :priority, :active, 1)
                ON CONFLICT (id) DO UPDATE SET
                    description_contains = EXCLUDED.description_contains,
                    category_id = EXCLUDED.category_id,
                    subcategory_id = EXCLUDED.subcategory_id,
                    priority = EXCLUDED.priority,
                    active = EXCLUDED.active
                """
            ),
            {
                "id": str(row_id),
                "user_id": str(user_id),
                "natural_key": pattern.pattern_id,
                "description_contains": pattern.description_contains,
                "category_id": str(derive_id(user_id, "categories", pattern.category_id)),
                "subcategory_id": str(sub)
                if (sub := _category_id(user_id, pattern.subcategory_id)) is not None
                else None,
                "priority": pattern.priority,
                "active": pattern.active,
            },
        )
    session.flush()
    existing_ids = {row.id for row in session.query(adb.CategoryPattern.id).filter_by(user_id=user_id)}
    removed_ids = existing_ids - keep_ids
    if removed_ids:
        session.query(adb.CategoryPattern).filter_by(user_id=user_id).filter(
            adb.CategoryPattern.id.in_(removed_ids)
        ).delete(synchronize_session=False)


_CATEGORY_PATTERNS_TABLE = "accounting.category_patterns"


def update_category_pattern(
    session: Session, user_id: uuid.UUID, pattern: CategoryPattern, expected_version: int
) -> CategoryPattern | None:
    """Update one category pattern's fields in place, touching no other persisted entity.

    `pattern.pattern_id` identifies which row to update; every other field on `pattern`
    (`pattern.version` is never read here — only `expected_version` is) becomes that row's new state.
    Guarded by `db.base.check_and_bump_row_version`, same shape as `update_transfer_rule`.

    Returns
    -------
    CategoryPattern | None
        The pattern as persisted, or `None` if no pattern with `pattern.pattern_id` exists for this
        user. Raises `db.base.VersionConflictError` (propagated from `check_and_bump_row_version`) if
        the pattern exists but `expected_version` no longer matches what's stored.

    Raises
    ------
    RuntimeError
        If the row vanishes between the version check and this function's own read of it — a
        defensive invariant, never expected to actually happen.
    """
    row_id = derive_id(user_id, "category_patterns", pattern.pattern_id)
    new_version = check_and_bump_row_version(session, _CATEGORY_PATTERNS_TABLE, row_id, user_id, expected_version)
    if new_version is None:
        return None
    row = session.get(adb.CategoryPattern, row_id)
    if row is None:
        message = f"category_patterns row {row_id} vanished between its version check and this read"
        raise RuntimeError(message)
    row.description_contains = pattern.description_contains
    row.category_id = derive_id(user_id, "categories", pattern.category_id)
    row.subcategory_id = _category_id(user_id, pattern.subcategory_id)
    row.priority = pattern.priority
    row.active = pattern.active
    session.flush()
    return pattern.model_copy(update={"version": new_version})


def delete_category_pattern(session: Session, user_id: uuid.UUID, pattern_id: str) -> bool:
    """Delete one category pattern, without touching any other persisted entity.

    Idempotent, no version check — same reasoning as `delete_transfer_rule`.

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed.
    """
    row_id = derive_id(user_id, "category_patterns", pattern_id)
    deleted = session.query(adb.CategoryPattern).filter_by(id=row_id, user_id=user_id).delete()
    session.flush()
    return deleted > 0


# A thin, accounting-flavored name for the shared primitive in `db.base` —
# every call site here and in `accounting.api` was written against this
# name before the same mechanism was generalized for `trades.dashboard.
# settings` to reuse (see `db.base.VersionConflictError`'s own docstring);
# keeping the alias means neither those call sites nor the tests that
# import it by this name needed to change when the logic moved.
StoreVersionConflictError = VersionConflictError

_STORE_VERSION_TABLE = "accounting.store_versions"


def get_store_version(session: Session, user_id: uuid.UUID) -> int:
    """Read this user's current save-version counter.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose counter to read.

    Returns
    -------
    int
        `0` if this user has never saved anything yet (no row exists).
    """
    return get_version(session, _STORE_VERSION_TABLE, user_id)


def _check_and_bump_store_version(session: Session, user_id: uuid.UUID) -> None:
    """Atomically verify no other save has landed since the caller's expected version, then bump by one.

    Reads the expected version from `session.info["expected_store_version"]`
    — stashed once per request by `accounting.api.dependencies`'s
    `_stash_expected_store_version`, from the client's own
    `X-Expected-Store-Version` header — rather than taking it as a
    parameter here, so every one of `save_store`'s call sites gets this
    check for free without threading a version through each of them
    individually. See `db.base.check_and_bump_version` for the actual
    atomic check-and-bump mechanics and the `None`-skips-the-check
    behavior, shared verbatim with `trades.dashboard.settings.save_settings`.

    Re-stashes the freshly-bumped version back into `session.info` after a
    successful check — some requests call `save_store` more than once (e.g.
    `POST /transfer-rules` calling it once for the rule itself, then again
    inside `ledger.transfers.reconcile_and_persist_rule_links` if a new
    link was found); without this, that second call would re-check
    against the same now-stale client-submitted version and spuriously
    raise `StoreVersionConflictError` even though nothing external
    conflicted — the first call already consumed that version.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose store this is.
    """
    expected_version = session.info.get("expected_store_version")
    session.info["expected_store_version"] = check_and_bump_version(
        session, _STORE_VERSION_TABLE, user_id, expected_version
    )


def _add_transfer_links(session: Session, user_id: uuid.UUID, transfer_links: list[TransferLink]) -> None:
    """Insert every `TransferLink` row and its two membership rows, self-contained (own internal flush).

    `TransferLinkedTransaction.link_id` foreign-keys into `TransferLink.id`,
    so the parent rows need their own flush before the membership rows can
    be inserted — kept as one self-contained helper (rather than two
    `session.add_all` calls inline in `save_store`) purely to keep that
    function's own statement count down.
    """
    session.add_all(
        adb.TransferLink(
            id=derive_id(user_id, "transfer_links", link.link_id),
            user_id=user_id,
            natural_key=link.link_id,
            source=link.source,
            rule_id=link.rule_id,
        )
        for link in transfer_links
    )
    session.flush()
    session.add_all(
        adb.TransferLinkedTransaction(
            user_id=user_id,
            link_id=derive_id(user_id, "transfer_links", link.link_id),
            transaction_id=_transaction_id(user_id, transaction_id),
        )
        for link in transfer_links
        for transaction_id in (link.transaction_id_a, link.transaction_id_b)
    )


def save_store(store: AccountingStore, session: Session, user_id: uuid.UUID) -> None:
    """Persist the accounting store, overwriting whatever was saved before.

    `Account`/`Category`/`Tag` are upserted and pruned (see
    `_upsert_and_prune`) since the ledger's own `postings`/`posting_tags`
    tables foreign-key into them — a real posting keeps its account/
    category/tag rows alive even across a `save_store` call that no longer
    mentions them by name in-memory, exactly as it should. Every other
    entity here is deleted in full and reinserted in full, inside one
    transaction — the same all-or-nothing "whole store overwrite" semantics
    `save_store` has always had (its caller always passes the complete
    desired end-state, never a partial patch), just backed by Postgres
    instead of a JSON file. Tables are deleted leaves-first and inserted
    roots-first so foreign keys are never briefly violated mid-transaction.

    Parameters
    ----------
    store
        The store to persist.
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose store this is.

    See `_check_and_bump_store_version` for the `StoreVersionConflictError`
    this can raise before touching anything else.
    """
    _check_and_bump_store_version(session, user_id)
    session.query(adb.PostingSplitLeg).filter_by(user_id=user_id).delete()
    session.query(adb.PostingSplit).filter_by(user_id=user_id).delete()
    session.query(adb.PostingMergeDuplicate).filter_by(user_id=user_id).delete()
    session.query(adb.PostingMerge).filter_by(user_id=user_id).delete()
    session.query(adb.TransferLinkedTransaction).filter_by(user_id=user_id).delete()
    session.query(adb.TransferLink).filter_by(user_id=user_id).delete()
    session.query(adb.GoalContribution).filter_by(user_id=user_id).delete()
    session.query(adb.RecurringAddition).filter_by(user_id=user_id).delete()
    session.query(adb.WithdrawalPriorityEntry).filter_by(user_id=user_id).delete()
    session.query(adb.Budget).filter_by(user_id=user_id).delete()
    session.query(adb.GeneralBudget).filter_by(user_id=user_id).delete()
    session.query(adb.ManualTransfer).filter_by(user_id=user_id).delete()
    session.query(adb.OpeningBalance).filter_by(user_id=user_id).delete()
    session.query(adb.TransferRuleExclusion).filter_by(user_id=user_id).delete()
    session.query(adb.OtherAsset).filter_by(user_id=user_id).delete()
    session.query(adb.SimulatorScenario).filter_by(user_id=user_id).delete()
    session.flush()

    _upsert_and_prune(
        session,
        adb.Account,
        user_id,
        (
            adb.Account(
                id=_account_id(user_id, account.account_id),
                user_id=user_id,
                natural_key=account.account_id,
                name=account.name,
                kind=account.kind,
                institution=account.institution,
                currency=account.currency,
                last_four=account.last_four,
                parent_account_id=None,
                external_ref=account.external_ref,
                meta=account.meta,
                closed=account.closed,
            )
            for account in store.accounts.values()
            if account.parent_account_id is None
        ),
        set(store.accounts.keys()),
    )
    _upsert_and_prune(
        session,
        adb.Account,
        user_id,
        (
            adb.Account(
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
            for account in store.accounts.values()
            if account.parent_account_id is not None
        ),
        set(store.accounts.keys()),
    )

    _upsert_and_prune(
        session,
        adb.Category,
        user_id,
        (
            adb.Category(
                id=_category_id(user_id, category.category_id),
                user_id=user_id,
                natural_key=category.category_id,
                name=category.name,
                classification=category.classification,
                parent_category_id=None,
                color=category.color,
            )
            for category in store.categories.values()
            if category.parent_category_id is None
        ),
        set(store.categories.keys()),
    )
    _upsert_and_prune(
        session,
        adb.Category,
        user_id,
        (
            adb.Category(
                id=_category_id(user_id, category.category_id),
                user_id=user_id,
                natural_key=category.category_id,
                name=category.name,
                classification=category.classification,
                parent_category_id=_category_id(user_id, category.parent_category_id),
                color=category.color,
            )
            for category in store.categories.values()
            if category.parent_category_id is not None
        ),
        set(store.categories.keys()),
    )

    _upsert_and_prune(
        session,
        adb.Tag,
        user_id,
        (
            adb.Tag(id=_tag_id(user_id, tag.tag_id), user_id=user_id, natural_key=tag.tag_id, name=tag.name)
            for tag in store.tags.values()
        ),
        set(store.tags.keys()),
    )

    session.add_all(
        adb.OtherAsset(
            id=derive_id(user_id, "other_assets", asset.asset_id),
            user_id=user_id,
            natural_key=asset.asset_id,
            name=asset.name,
            value=asset.value,
            currency=asset.currency,
            note=asset.note,
        )
        for asset in store.other_assets
    )
    session.add_all(
        adb.SimulatorScenario(
            id=derive_id(user_id, "simulator_scenarios", scenario.scenario_id),
            user_id=user_id,
            natural_key=scenario.scenario_id,
            name=scenario.name,
            initial_capital=scenario.initial_capital,
            monthly_contribution=scenario.monthly_contribution,
            horizon_years=scenario.horizon_years,
            annual_rate_pct=scenario.annual_rate_pct,
            compounding_frequency=scenario.compounding_frequency,
            currency=scenario.currency,
        )
        for scenario in store.simulator_scenarios
    )
    _upsert_category_patterns_and_prune(session, user_id, list(store.category_patterns.values()))
    session.flush()

    _upsert_transfer_rules_and_prune(session, user_id, store.rules)
    session.add_all(
        adb.OpeningBalance(
            id=derive_id(user_id, "opening_balances", ob.account_id),
            user_id=user_id,
            account_id=_account_id(user_id, ob.account_id),
            amount=ob.amount,
            as_of_date=ob.as_of_date,
        )
        for ob in store.opening_balances.values()
    )
    session.add_all(
        adb.ManualTransfer(
            id=derive_id(user_id, "manual_transfers", mt.transfer_id),
            user_id=user_id,
            natural_key=mt.transfer_id,
            date=mt.date,
            from_account_id=_account_id(user_id, mt.from_account_id),
            to_account_id=_account_id(user_id, mt.to_account_id),
            from_amount=mt.from_amount,
            to_amount=mt.to_amount,
            description=mt.description,
        )
        for mt in store.manual_transfers
    )
    session.add_all(
        adb.GeneralBudget(
            id=derive_id(user_id, "general_budgets", f"{gb.category_id}:{gb.subcategory_id or ''}"),
            user_id=user_id,
            category_id=_category_id(user_id, gb.category_id),
            subcategory_id=_category_id(user_id, gb.subcategory_id),
            amount=gb.amount,
            currency=gb.currency,
        )
        for gb in store.general_budgets.values()
    )
    session.flush()

    session.add_all(
        adb.TransferRuleExclusion(
            user_id=user_id,
            rule_id=derive_id(user_id, "transfer_rules", rule.rule_id),
            transaction_id=_transaction_id(user_id, transaction_id),
        )
        for rule in store.rules
        for transaction_id in rule.excluded_transaction_ids
    )

    session.add_all(
        adb.Budget(
            id=derive_id(user_id, "budgets", budget.budget_id),
            user_id=user_id,
            natural_key=budget.budget_id,
            month=budget.month,
            category_id=_category_id(user_id, budget.category_id),
            subcategory_id=_category_id(user_id, budget.subcategory_id),
            amount=budget.amount,
            currency=budget.currency,
        )
        for budget in store.budgets
    )
    _upsert_goals_and_prune(session, user_id, list(store.goals.values()))
    session.flush()

    session.add_all(
        adb.WithdrawalPriorityEntry(
            id=derive_id(user_id, "withdrawal_priority_entries", entry.goal_id),
            user_id=user_id,
            goal_id=_goal_id(user_id, entry.goal_id),
            priority=entry.priority,
        )
        for entry in store.withdrawal_priorities
    )
    session.add_all(
        adb.RecurringAddition(
            id=derive_id(user_id, "recurring_additions", addition.addition_id),
            user_id=user_id,
            natural_key=addition.addition_id,
            goal_id=_goal_id(user_id, addition.goal_id),
            start_date=addition.start_date,
            frequency=addition.frequency,
            end_date=addition.end_date,
            mode=addition.mode,
            value=addition.value,
            currency=addition.currency,
            priority=addition.priority,
        )
        for addition in store.recurring_additions
    )
    session.add_all(
        adb.GoalContribution(
            id=derive_id(user_id, "goal_contributions", contribution.contribution_id),
            user_id=user_id,
            natural_key=contribution.contribution_id,
            goal_id=_goal_id(user_id, contribution.goal_id),
            date=contribution.date,
            amount=contribution.amount,
            currency=contribution.currency,
            note=contribution.note,
            source_posting_id=_posting_id(user_id, contribution.source_posting_id)
            if contribution.source_posting_id is not None
            else None,
            origin=contribution.origin,
            edited=contribution.edited,
        )
        for contribution in store.goal_contributions.values()
    )
    session.add_all(
        adb.PostingMerge(
            id=derive_id(user_id, "posting_merges", merge.merge_id),
            user_id=user_id,
            natural_key=merge.merge_id,
            kept_transaction_id=_transaction_id(user_id, merge.kept_transaction_id),
            description=merge.description,
        )
        for merge in store.posting_merges.values()
    )
    _add_transfer_links(session, user_id, store.transfer_links)
    session.add_all(
        adb.PostingSplit(
            id=derive_id(user_id, "posting_splits", split.posting_id),
            user_id=user_id,
            posting_id=_posting_id(user_id, split.posting_id),
        )
        for split in store.posting_splits.values()
    )
    session.flush()

    session.add_all(
        adb.PostingMergeDuplicate(
            user_id=user_id,
            merge_id=derive_id(user_id, "posting_merges", merge.merge_id),
            duplicate_transaction_id=_transaction_id(user_id, duplicate_id),
        )
        for merge in store.posting_merges.values()
        for duplicate_id in merge.duplicate_transaction_ids
    )
    session.add_all(
        adb.PostingSplitLeg(
            user_id=user_id,
            posting_split_id=derive_id(user_id, "posting_splits", split.posting_id),
            ordinal=ordinal,
            amount=leg.amount,
            category_id=_category_id(user_id, leg.category_id),
            subcategory_id=_category_id(user_id, leg.subcategory_id),
            description=leg.description,
        )
        for split in store.posting_splits.values()
        for ordinal, leg in enumerate(split.legs)
    )
    session.commit()


def load_overrides(session: Session, user_id: uuid.UUID) -> dict[str, ManualOverride]:
    """Read every persisted manual per-posting override.

    Merges `PostingOverride` (persistent corrections) and
    `PostingPendingSuggestion` (transient, not-yet-confirmed automation
    state) back into one `ManualOverride` per posting — the split only
    exists at the storage layer (see `accounting.db.corrections`), never
    in the pydantic shape every caller of this function already expects.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose overrides to load.

    Returns
    -------
    dict[str, ManualOverride]
        Keyed by `posting_id`; empty if nothing has been overridden yet.
    """
    override_rows = list(session.query(adb.PostingOverride).filter_by(user_id=user_id))
    pending_rows = list(session.query(adb.PostingPendingSuggestion).filter_by(user_id=user_id))
    return _overrides_from_rows(override_rows, pending_rows, session, user_id)


def load_overrides_for_postings(
    session: Session, user_id: uuid.UUID, posting_ids: Iterable[str]
) -> dict[str, ManualOverride]:
    """Like `load_overrides`, but only for `posting_ids` — never reads any other posting's override.

    The scoped counterpart callers should use whenever they only need (and
    are only about to write back) a known, bounded set of postings — using
    `load_overrides` there would still be correct, just a wasted whole-table
    read for a caller that only cares about a handful of rows.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose overrides to load.
    posting_ids
        Which postings to load; a posting with no stored override is
        simply absent from the result, same as `load_overrides`.

    Returns
    -------
    dict[str, ManualOverride]
        Keyed by `posting_id`, only ever containing keys from `posting_ids`.
    """
    posting_row_ids = [_posting_id(user_id, posting_id) for posting_id in posting_ids]
    if not posting_row_ids:
        return {}
    override_rows = list(
        session.query(adb.PostingOverride).filter(
            adb.PostingOverride.user_id == user_id, adb.PostingOverride.posting_id.in_(posting_row_ids)
        )
    )
    pending_rows = list(
        session.query(adb.PostingPendingSuggestion).filter(
            adb.PostingPendingSuggestion.user_id == user_id,
            adb.PostingPendingSuggestion.posting_id.in_(posting_row_ids),
        )
    )
    return _overrides_from_rows(override_rows, pending_rows, session, user_id)


def _overrides_from_rows(
    override_rows: list[adb.PostingOverride],
    pending_rows: list[adb.PostingPendingSuggestion],
    session: Session,
    user_id: uuid.UUID,
) -> dict[str, ManualOverride]:
    """Shared row->pydantic mapping for `load_overrides`/`load_overrides_for_postings`.

    Returns
    -------
    dict[str, ManualOverride]
    """
    posting_natural_key_by_id = natural_keys_by_id(
        session,
        adb.Posting,
        user_id,
        [row.posting_id for row in override_rows] + [row.posting_id for row in pending_rows],
    )
    account_natural_key_by_id = natural_keys_by_id(
        session, adb.Account, user_id, [row.account_id for row in override_rows]
    )
    category_natural_key_by_id = natural_keys_by_id(
        session,
        adb.Category,
        user_id,
        [row.category_id for row in override_rows]
        + [row.subcategory_id for row in override_rows]
        + [row.previous_category_id for row in pending_rows]
        + [row.previous_subcategory_id for row in pending_rows],
    )

    override_tag_rows = list(
        session.query(adb.PostingOverrideTag).filter(
            adb.PostingOverrideTag.user_id == user_id,
            adb.PostingOverrideTag.override_id.in_([row.id for row in override_rows]),
        )
    )
    tag_natural_key_by_id = natural_keys_by_id(session, adb.Tag, user_id, [row.tag_id for row in override_tag_rows])
    tag_ids_by_override_id: dict[uuid.UUID, list[str]] = defaultdict(list)
    for override_tag_row in override_tag_rows:
        tag_ids_by_override_id[override_tag_row.override_id].append(tag_natural_key_by_id[override_tag_row.tag_id])

    overrides: dict[str, ManualOverride] = {}
    for override_row in override_rows:
        overrides[posting_natural_key_by_id[override_row.posting_id]] = ManualOverride(
            account_id=account_natural_key_by_id.get(override_row.account_id)
            if override_row.account_id is not None
            else None,
            category_id=category_natural_key_by_id.get(override_row.category_id)
            if override_row.category_id is not None
            else None,
            subcategory_id=category_natural_key_by_id.get(override_row.subcategory_id)
            if override_row.subcategory_id is not None
            else None,
            tag_ids=tag_ids_by_override_id.get(override_row.id, []) if override_row.tags_overridden else None,
        )
    for pending_row in pending_rows:
        posting_key = posting_natural_key_by_id[pending_row.posting_id]
        overrides[posting_key] = overrides.get(posting_key, ManualOverride()).model_copy(
            update={
                "pending_source": pending_row.source,
                "pending_selected": pending_row.selected,
                "pending_previous_category_id": category_natural_key_by_id.get(pending_row.previous_category_id)
                if pending_row.previous_category_id is not None
                else None,
                "pending_previous_subcategory_id": category_natural_key_by_id.get(pending_row.previous_subcategory_id)
                if pending_row.previous_subcategory_id is not None
                else None,
            }
        )
    return overrides


def save_overrides(overrides: dict[str, ManualOverride], session: Session, user_id: uuid.UUID) -> None:
    """Persist every manual per-posting override, overwriting whatever was saved before.

    Writes a `PostingOverride` row only when at least one actual
    correction field is set, and a `PostingPendingSuggestion` row only
    when `pending_source` is set (that table's `source` column is
    `NOT NULL` — a pending suggestion that isn't actually pending isn't a
    row at all, not a row with `source=None`).

    Parameters
    ----------
    overrides
        Every override, keyed by `posting_id`.
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose overrides these are.
    """
    session.query(adb.PostingOverride).filter_by(user_id=user_id).delete()
    session.query(adb.PostingPendingSuggestion).filter_by(user_id=user_id).delete()

    override_rows: list[adb.PostingOverride] = []
    override_tag_rows: list[adb.PostingOverrideTag] = []
    for posting_id, override in overrides.items():
        if (
            override.account_id is None
            and override.category_id is None
            and override.subcategory_id is None
            and override.tag_ids is None
        ):
            continue
        override_row_id = uuid.uuid4()
        override_rows.append(
            adb.PostingOverride(
                id=override_row_id,
                user_id=user_id,
                posting_id=_posting_id(user_id, posting_id),
                account_id=_account_id(user_id, override.account_id) if override.account_id is not None else None,
                category_id=_category_id(user_id, override.category_id),
                subcategory_id=_category_id(user_id, override.subcategory_id),
                tags_overridden=override.tag_ids is not None,
            )
        )
        if override.tag_ids is not None:
            override_tag_rows.extend(
                adb.PostingOverrideTag(user_id=user_id, override_id=override_row_id, tag_id=_tag_id(user_id, tag_id))
                for tag_id in dict.fromkeys(override.tag_ids)
            )
    session.add_all(override_rows)
    session.flush()
    session.add_all(override_tag_rows)
    session.add_all(
        adb.PostingPendingSuggestion(
            user_id=user_id,
            posting_id=_posting_id(user_id, posting_id),
            source=override.pending_source,
            selected=override.pending_selected,
            previous_category_id=_category_id(user_id, override.pending_previous_category_id),
            previous_subcategory_id=_category_id(user_id, override.pending_previous_subcategory_id),
        )
        for posting_id, override in overrides.items()
        if override.pending_source is not None
    )
    session.commit()
    session.commit()


def save_overrides_for_postings(
    posting_ids: Iterable[str], overrides: dict[str, ManualOverride], session: Session, user_id: uuid.UUID
) -> None:
    """Persist overrides for exactly `posting_ids`, touching no other posting's stored override.

    The scoped counterpart to `save_overrides`: that function always
    deletes and reinserts every posting's override, so two callers racing
    on *different* postings — one reads, the other reads, one writes back
    its whole-table snapshot, the other then writes back its own
    (now-stale) whole-table snapshot — silently erase each other's change.
    This never reads or rewrites anything outside `posting_ids`, so two
    such calls for different postings can't conflict no matter how they
    interleave.

    Parameters
    ----------
    posting_ids
        Every posting this call is allowed to touch — always
        deleted-and-optionally-reinserted, whether or not it's also a key
        of `overrides`. A posting present here but absent from `overrides`
        (or present with an all-`None` override) ends up with no stored
        override at all, e.g. a resolved pending suggestion or a fully
        cleared correction.
    overrides
        The new override for each posting that should end up with one;
        must be a subset of `posting_ids`.
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose overrides these are.
    """
    posting_row_ids = [_posting_id(user_id, posting_id) for posting_id in posting_ids]
    if not posting_row_ids:
        return
    session.query(adb.PostingOverride).filter(
        adb.PostingOverride.user_id == user_id, adb.PostingOverride.posting_id.in_(posting_row_ids)
    ).delete(synchronize_session=False)
    session.query(adb.PostingPendingSuggestion).filter(
        adb.PostingPendingSuggestion.user_id == user_id,
        adb.PostingPendingSuggestion.posting_id.in_(posting_row_ids),
    ).delete(synchronize_session=False)

    override_rows: list[adb.PostingOverride] = []
    override_tag_rows: list[adb.PostingOverrideTag] = []
    for posting_id, override in overrides.items():
        if (
            override.account_id is None
            and override.category_id is None
            and override.subcategory_id is None
            and override.tag_ids is None
        ):
            continue
        override_row_id = uuid.uuid4()
        override_rows.append(
            adb.PostingOverride(
                id=override_row_id,
                user_id=user_id,
                posting_id=_posting_id(user_id, posting_id),
                account_id=_account_id(user_id, override.account_id) if override.account_id is not None else None,
                category_id=_category_id(user_id, override.category_id),
                subcategory_id=_category_id(user_id, override.subcategory_id),
                tags_overridden=override.tag_ids is not None,
            )
        )
        if override.tag_ids is not None:
            override_tag_rows.extend(
                adb.PostingOverrideTag(user_id=user_id, override_id=override_row_id, tag_id=_tag_id(user_id, tag_id))
                for tag_id in dict.fromkeys(override.tag_ids)
            )
    session.add_all(override_rows)
    session.flush()
    session.add_all(override_tag_rows)
    session.add_all(
        adb.PostingPendingSuggestion(
            user_id=user_id,
            posting_id=_posting_id(user_id, posting_id),
            source=override.pending_source,
            selected=override.pending_selected,
            previous_category_id=_category_id(user_id, override.pending_previous_category_id),
            previous_subcategory_id=_category_id(user_id, override.pending_previous_subcategory_id),
        )
        for posting_id, override in overrides.items()
        if override.pending_source is not None
    )
    session.commit()


def save_posting_split(split: PostingSplit, session: Session, user_id: uuid.UUID) -> None:
    """Persist one posting's split (and its legs), replacing only that posting's prior split.

    Scoped counterpart to routing a split through `save_store` (which
    blanket-deletes and reinserts every posting's split for the user):
    two callers splitting *different* postings at once can't clobber each
    other, since this only ever touches the one `posting_id`'s rows. A
    split is one coherent replace-in-full unit keyed by `posting_id` (not
    a set of independently-editable fields), so last-write-wins on the
    same posting is the intended semantics — see
    `docs/app-stack/optimistic-concurrency-versioning.md` on why a
    whole-unit replace scoped to its own key needs no version column.

    Parameters
    ----------
    split
        The split to persist; `split.posting_id` names the posting.
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose split this is.
    """
    split_row_id = derive_id(user_id, "posting_splits", split.posting_id)
    session.query(adb.PostingSplitLeg).filter_by(user_id=user_id, posting_split_id=split_row_id).delete(
        synchronize_session=False
    )
    session.query(adb.PostingSplit).filter_by(id=split_row_id, user_id=user_id).delete(synchronize_session=False)
    session.flush()
    session.add(adb.PostingSplit(id=split_row_id, user_id=user_id, posting_id=_posting_id(user_id, split.posting_id)))
    session.flush()
    session.add_all(
        adb.PostingSplitLeg(
            user_id=user_id,
            posting_split_id=split_row_id,
            ordinal=ordinal,
            amount=leg.amount,
            category_id=_category_id(user_id, leg.category_id),
            subcategory_id=_category_id(user_id, leg.subcategory_id),
            description=leg.description,
        )
        for ordinal, leg in enumerate(split.legs)
    )
    session.commit()


def delete_posting_split(session: Session, user_id: uuid.UUID, posting_id: str) -> bool:
    """Delete one posting's split (and its legs), touching no other posting's split.

    Idempotent, no version check — same reasoning as `delete_transfer_rule`.
    The legs go first because they foreign-key into the split row.

    Returns
    -------
    bool
        `True` if a split row was actually deleted, `False` if none existed.
    """
    split_row_id = derive_id(user_id, "posting_splits", posting_id)
    session.query(adb.PostingSplitLeg).filter_by(user_id=user_id, posting_split_id=split_row_id).delete(
        synchronize_session=False
    )
    deleted = (
        session.query(adb.PostingSplit).filter_by(id=split_row_id, user_id=user_id).delete(synchronize_session=False)
    )
    session.flush()
    return deleted > 0


def upsert_budget(budget: Budget, session: Session, user_id: uuid.UUID) -> None:
    """Insert-or-update one per-month budget, touching no other budget cell.

    Scoped counterpart to routing a budget through `save_store` (which
    blanket-deletes and reinserts every budget for the user): two callers
    editing *different* month/category cells at once can't clobber each
    other. Keyed by `budget.budget_id` (derived from month+category+
    subcategory), so re-setting the same cell is last-write-wins, the
    intended semantics for a single amount.

    Parameters
    ----------
    budget
        The budget to persist.
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose budget this is.
    """
    session.execute(
        text(
            """
            INSERT INTO accounting.budgets
                (id, user_id, natural_key, month, category_id, subcategory_id, amount, currency)
            VALUES
                (:id, :user_id, :natural_key, :month, :category_id, :subcategory_id, :amount, :currency)
            ON CONFLICT (id) DO UPDATE SET
                month = EXCLUDED.month,
                category_id = EXCLUDED.category_id,
                subcategory_id = EXCLUDED.subcategory_id,
                amount = EXCLUDED.amount,
                currency = EXCLUDED.currency
            """
        ),
        {
            "id": str(derive_id(user_id, "budgets", budget.budget_id)),
            "user_id": str(user_id),
            "natural_key": budget.budget_id,
            "month": budget.month,
            "category_id": str(derive_id(user_id, "categories", budget.category_id)),
            "subcategory_id": str(sub) if (sub := _category_id(user_id, budget.subcategory_id)) is not None else None,
            "amount": budget.amount,
            "currency": budget.currency,
        },
    )
    session.commit()


def remove_budget(session: Session, user_id: uuid.UUID, budget_id: str) -> bool:
    """Delete one per-month budget, touching no other budget cell. Idempotent, no version check.

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed.
    """
    row_id = derive_id(user_id, "budgets", budget_id)
    deleted = session.query(adb.Budget).filter_by(id=row_id, user_id=user_id).delete()
    session.flush()
    return deleted > 0


def upsert_general_budget(general_budget: GeneralBudget, session: Session, user_id: uuid.UUID) -> None:
    """Insert-or-update one standing (all-month) budget, touching no other entry. Scoped like `upsert_budget`.

    Keyed (like `save_store`'s own general-budget rows) by the
    category/subcategory pair, so re-setting the same category's standing
    target is last-write-wins.

    Parameters
    ----------
    general_budget
        The standing budget to persist.
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose general budget this is.
    """
    row_key = f"{general_budget.category_id}:{general_budget.subcategory_id or ''}"
    session.execute(
        text(
            """
            INSERT INTO accounting.general_budgets
                (id, user_id, category_id, subcategory_id, amount, currency)
            VALUES
                (:id, :user_id, :category_id, :subcategory_id, :amount, :currency)
            ON CONFLICT (id) DO UPDATE SET
                category_id = EXCLUDED.category_id,
                subcategory_id = EXCLUDED.subcategory_id,
                amount = EXCLUDED.amount,
                currency = EXCLUDED.currency
            """
        ),
        {
            "id": str(derive_id(user_id, "general_budgets", row_key)),
            "user_id": str(user_id),
            "category_id": str(derive_id(user_id, "categories", general_budget.category_id)),
            "subcategory_id": str(sub)
            if (sub := _category_id(user_id, general_budget.subcategory_id)) is not None
            else None,
            "amount": general_budget.amount,
            "currency": general_budget.currency,
        },
    )
    session.commit()


def remove_general_budget(session: Session, user_id: uuid.UUID, category_id: str, subcategory_id: str | None) -> bool:
    """Delete one standing budget by its category/subcategory, touching no other. Idempotent, no version check.

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed.
    """
    row_id = derive_id(user_id, "general_budgets", f"{category_id}:{subcategory_id or ''}")
    deleted = session.query(adb.GeneralBudget).filter_by(id=row_id, user_id=user_id).delete()
    session.flush()
    return deleted > 0


def dismissed_suggestion_ids(session: Session, user_id: uuid.UUID, suggestion_ids: Iterable[str]) -> set[str]:
    """Which of `suggestion_ids` have already been dismissed, without loading anything else.

    `DismissedSuggestion` lives outside `AccountingStore` entirely — unlike
    every other entity there, it's never read as "give me the whole
    list to build something," only ever checked as "has this one
    already been dismissed" against a handful of candidate suggestion
    ids computed fresh on every request (see `postings.get_transfer_suggestions`/
    `get_duplicate_suggestions`). Routing it through `load_store`'s full
    read would mean every unrelated store mutation — editing a budget,
    adding a goal — pays the cost of loading a table that only ever grows,
    never shrinks, for no benefit.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose dismissed suggestions to check.
    suggestion_ids
        The candidate ids to check; never touches rows outside this set.

    Returns
    -------
    set[str]
        The subset of `suggestion_ids` already dismissed.
    """
    candidates = list(suggestion_ids)
    if not candidates:
        return set()
    rows = session.query(adb.DismissedSuggestion.natural_key).filter(
        adb.DismissedSuggestion.user_id == user_id, adb.DismissedSuggestion.natural_key.in_(candidates)
    )
    return {row.natural_key for row in rows}


def list_dismissed_suggestions(session: Session, user_id: uuid.UUID) -> list[DismissedSuggestion]:
    """Every archived (dismissed) suggestion, most recently dismissed first.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose dismissed suggestions to list.

    Returns
    -------
    list[DismissedSuggestion]
    """
    rows = (
        session
        .query(adb.DismissedSuggestion)
        .filter_by(user_id=user_id)
        .order_by(adb.DismissedSuggestion.dismissed_at.desc())
    )
    return [
        DismissedSuggestion(
            suggestion_id=row.natural_key,
            kind=row.kind,  # type: ignore[arg-type]
            description=row.description,
            dismissed_at=row.dismissed_at,
        )
        for row in rows
    ]


def dismiss_suggestion(session: Session, user_id: uuid.UUID, entry: DismissedSuggestion) -> None:
    """Archive one suggestion so it stops being proposed, replacing any existing entry with the same id.

    Parameters
    ----------
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose suggestion this is.
    entry
        The suggestion to archive.
    """
    session.merge(
        adb.DismissedSuggestion(
            id=derive_id(user_id, "dismissed_suggestions", entry.suggestion_id),
            user_id=user_id,
            natural_key=entry.suggestion_id,
            kind=entry.kind,
            description=entry.description,
            dismissed_at=entry.dismissed_at,
        )
    )
    session.commit()


def undismiss_suggestion(session: Session, user_id: uuid.UUID, suggestion_id: str) -> bool:
    """Restore one dismissed suggestion so it can be proposed again.

    Parameters
    ----------
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose suggestion this is.
    suggestion_id
        The id to restore.

    Returns
    -------
    bool
        Whether an archived entry with this id existed to remove.
    """
    deleted = session.query(adb.DismissedSuggestion).filter_by(user_id=user_id, natural_key=suggestion_id).delete()
    session.commit()
    return deleted > 0
