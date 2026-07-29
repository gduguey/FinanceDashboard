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
    GeneralBudget,
    Goal,
    GoalContribution,
    ManualTransfer,
    OpeningBalance,
    OtherAsset,
    PostingMerge,
    PostingSplit,
    RecurringAddition,
    SimulatorScenario,
    Tag,
    TransferLink,
    TransferRule,
    WithdrawalPriorityEntry,
)
from accounting.repositories import interpretation, planning
from db.base import (
    VersionConflictError,
    check_and_bump_version,
    derive_id,
    get_version,
)

if TYPE_CHECKING:
    import uuid
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


def seed_new_user_defaults(session: Session, user_id: uuid.UUID) -> None:
    """Give a brand-new user the two uncategorized placeholder accounts and the default category tree.

    A user with no accounts and no categories can't do anything — every
    import needs a counterparty to point at and every posting needs a
    category tree to be filed into — so these are backfilled on first
    contact rather than requiring a separate setup step. No rule is
    seeded: every rule necessarily points at one person's own
    account/employer/payee, so there's nothing generic enough to start a
    fresh install with.

    Callers that only write (a budget, a general budget) call this
    directly, since the rows they write foreign-key into the category tree
    this creates.

    Parameters
    ----------
    session
        An open database session; committed if anything was seeded.
    user_id
        Whose defaults to seed.

    A no-op for everyone who already has either.
    """
    if session.query(adb.Account.id).filter_by(user_id=user_id).first() is not None:
        return
    if session.query(adb.Category.id).filter_by(user_id=user_id).first() is not None:
        return
    save_store(AccountingStore(categories=default_categories(), accounts=default_accounts()), session, user_id=user_id)


def load_store(session: Session, user_id: uuid.UUID) -> AccountingStore:  # noqa: PLR0914 (one local per AccountingStore field being loaded — splitting this up would just add indirection)
    """Read the persisted accounting store, seeding sensible defaults the first time.

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
    seed_new_user_defaults(session, user_id)
    account_rows = list(session.query(adb.Account).filter_by(user_id=user_id))
    category_rows = list(session.query(adb.Category).filter_by(user_id=user_id))
    account_natural_key_by_id = {row.id: row.natural_key for row in account_rows}
    category_natural_key_by_id = {row.id: row.natural_key for row in category_rows}
    accounts = {row.natural_key: _account_from_row(row, account_natural_key_by_id) for row in account_rows}
    categories = {row.natural_key: _category_from_row(row, category_natural_key_by_id) for row in category_rows}

    tags = {
        row.natural_key: Tag(tag_id=row.natural_key, name=row.name)
        for row in session.query(adb.Tag).filter_by(user_id=user_id)
    }
    rules = interpretation.load_transfer_rules(session, user_id)
    category_patterns = interpretation.load_category_patterns(session, user_id)
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
    budgets = planning.load_budgets(session, user_id)
    general_budgets = planning.load_general_budgets(session, user_id)
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

    posting_splits = interpretation.load_posting_splits(session, user_id)
    posting_merges = interpretation.load_posting_merges(session, user_id)
    transfer_links = interpretation.load_transfer_links(session, user_id)
    goals = planning.load_goals(session, user_id)
    goal_contributions = planning.load_goal_contributions(session, user_id)
    recurring_additions = planning.load_recurring_additions(session, user_id)
    withdrawal_priorities = planning.load_withdrawal_priorities(session, user_id)
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


def save_store(store: AccountingStore, session: Session, user_id: uuid.UUID) -> None:
    """Persist the accounting store, overwriting whatever was saved before.

    Shrinking, one aggregate at a time: the planning tables (budgets,
    goals, contributions, automations) are already gone from here and live
    in `accounting.repositories.planning`, which writes only the rows a
    request actually names. What remains still follows the old whole-store
    contract — the caller always passes the complete desired end-state.

    `Account`/`Category`/`Tag` are upserted and pruned (see
    `_upsert_and_prune`) since the ledger's own `postings`/`posting_tags`
    tables foreign-key into them — a real posting keeps its account/
    category/tag rows alive even across a `save_store` call that no longer
    mentions them by name in-memory, exactly as it should. Every other
    entity here is deleted in full and reinserted in full, inside one
    transaction. Tables are deleted leaves-first and inserted roots-first
    so foreign keys are never briefly violated mid-transaction.

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
    interpretation.clear_rule_exclusions(session, user_id)
    session.query(adb.ManualTransfer).filter_by(user_id=user_id).delete()
    session.query(adb.OpeningBalance).filter_by(user_id=user_id).delete()
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
    interpretation.replace_category_patterns(session, user_id, store.category_patterns.values())
    interpretation.replace_transfer_rules(session, user_id, store.rules)
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
    session.flush()

    interpretation.replace_rule_exclusions(session, user_id, store.rules)

    interpretation.replace_posting_merges(session, user_id, store.posting_merges.values())
    interpretation.replace_transfer_links(session, user_id, store.transfer_links)
    interpretation.replace_posting_splits(session, user_id, store.posting_splits.values())
    session.commit()


def update_account_fields(session: Session, user_id: uuid.UUID, account: Account) -> bool:
    """Update one account's editable columns in place, touching no other account.

    Scoped counterpart to routing an account edit through `save_store`,
    whose `_upsert_and_prune` re-merges *every* account from the caller's
    (possibly stale) snapshot — so editing account A could silently revert
    a concurrent edit to account B. This fetches only `account.account_id`'s
    row and mutates its columns, so an UPDATE is issued for that one row
    alone. `parent_account_id` is intentionally not touched (it isn't part
    of the edit surface).

    Returns
    -------
    bool
        `True` if the account existed and was updated, `False` otherwise.
    """
    row = session.get(adb.Account, _account_id(user_id, account.account_id))
    if row is None or row.user_id != user_id:
        return False
    row.name = account.name
    row.kind = account.kind
    row.institution = account.institution
    row.currency = account.currency
    row.last_four = account.last_four
    row.external_ref = account.external_ref
    row.meta = account.meta
    row.closed = account.closed
    session.flush()
    return True


def set_account_closed(session: Session, user_id: uuid.UUID, account_id: str, *, closed: bool) -> bool:
    """Flip one account's `closed` flag in place, touching no other account.

    Returns
    -------
    bool
        `True` if the account existed, `False` otherwise.
    """
    row = session.get(adb.Account, _account_id(user_id, account_id))
    if row is None or row.user_id != user_id:
        return False
    row.closed = closed
    session.flush()
    return True


def remove_account(session: Session, user_id: uuid.UUID, account_id: str) -> bool:
    """Delete one account, touching no other. Idempotent, no version check.

    The caller checks the no-postings precondition first; a delete that
    still violates a foreign key (a posting somehow references it) fails
    loudly, same as through the whole-store path.

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed.
    """
    deleted = session.query(adb.Account).filter_by(id=_account_id(user_id, account_id), user_id=user_id).delete()
    session.flush()
    return deleted > 0


def upsert_opening_balance(opening_balance: OpeningBalance, session: Session, user_id: uuid.UUID) -> None:
    """Insert-or-update one account's opening balance, touching no other. Scoped like `upsert_budget`.

    Parameters
    ----------
    opening_balance
        The opening balance to persist; its `account_id` names the account.
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose opening balance this is.
    """
    session.execute(
        text(
            """
            INSERT INTO accounting.opening_balances (id, user_id, account_id, amount, as_of_date)
            VALUES (:id, :user_id, :account_id, :amount, :as_of_date)
            ON CONFLICT (id) DO UPDATE SET
                amount = EXCLUDED.amount,
                as_of_date = EXCLUDED.as_of_date
            """
        ),
        {
            "id": str(derive_id(user_id, "opening_balances", opening_balance.account_id)),
            "user_id": str(user_id),
            "account_id": str(_account_id(user_id, opening_balance.account_id)),
            "amount": opening_balance.amount,
            "as_of_date": opening_balance.as_of_date,
        },
    )
    session.commit()


def remove_opening_balance(session: Session, user_id: uuid.UUID, account_id: str) -> bool:
    """Delete one account's opening balance, touching no other. Idempotent, no version check.

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed.
    """
    row_id = derive_id(user_id, "opening_balances", account_id)
    deleted = session.query(adb.OpeningBalance).filter_by(id=row_id, user_id=user_id).delete()
    session.flush()
    return deleted > 0


def insert_manual_transfers(transfers: Iterable[ManualTransfer], session: Session, user_id: uuid.UUID) -> None:
    """Insert manual-transfer rows additively, touching no existing transfer.

    Scoped counterpart to routing these through `save_store` (which
    blanket-deletes and reinserts every manual transfer from the caller's
    snapshot — so recording a transfer from a stale snapshot could drop a
    concurrently-added one). Each row is keyed by its own derived id, so
    re-recording the same transfer is a harmless upsert rather than a
    duplicate.

    Parameters
    ----------
    transfers
        The transfers to record.
    session
        An open database session; the caller commits.
    user_id
        Whose transfers these are.
    """
    for transfer in transfers:
        session.execute(
            text(
                """
                INSERT INTO accounting.manual_transfers
                    (id, user_id, natural_key, date, from_account_id, to_account_id,
                     from_amount, to_amount, description)
                VALUES
                    (:id, :user_id, :natural_key, :date, :from_account_id, :to_account_id,
                     :from_amount, :to_amount, :description)
                ON CONFLICT (id) DO UPDATE SET
                    date = EXCLUDED.date,
                    from_account_id = EXCLUDED.from_account_id,
                    to_account_id = EXCLUDED.to_account_id,
                    from_amount = EXCLUDED.from_amount,
                    to_amount = EXCLUDED.to_amount,
                    description = EXCLUDED.description
                """
            ),
            {
                "id": str(derive_id(user_id, "manual_transfers", transfer.transfer_id)),
                "user_id": str(user_id),
                "natural_key": transfer.transfer_id,
                "date": transfer.date,
                "from_account_id": str(_account_id(user_id, transfer.from_account_id)),
                "to_account_id": str(_account_id(user_id, transfer.to_account_id)),
                "from_amount": transfer.from_amount,
                "to_amount": transfer.to_amount,
                "description": transfer.description,
            },
        )
    session.flush()


def delete_tag(session: Session, user_id: uuid.UUID, tag_id: str) -> bool:
    """Delete one tag, touching no other. Idempotent, no version check.

    A tag still applied to postings is removed from them too — `posting_tags`
    and `posting_override_tags` both foreign-key `tags.id` with `ON DELETE
    CASCADE`, the same cascade the old whole-list `PUT /tags` prune relied on.

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed.
    """
    deleted = session.query(adb.Tag).filter_by(id=_tag_id(user_id, tag_id), user_id=user_id).delete()
    session.flush()
    return deleted > 0


def delete_other_asset(session: Session, user_id: uuid.UUID, asset_id: str) -> bool:
    """Delete one manually-entered asset, touching no other. Idempotent, no version check.

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed.
    """
    row_id = derive_id(user_id, "other_assets", asset_id)
    deleted = session.query(adb.OtherAsset).filter_by(id=row_id, user_id=user_id).delete()
    session.flush()
    return deleted > 0


def delete_simulator_scenario(session: Session, user_id: uuid.UUID, scenario_id: str) -> bool:
    """Delete one saved simulator scenario, touching no other. Idempotent, no version check.

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed.
    """
    row_id = derive_id(user_id, "simulator_scenarios", scenario_id)
    deleted = session.query(adb.SimulatorScenario).filter_by(id=row_id, user_id=user_id).delete()
    session.flush()
    return deleted > 0
