"""The category/tag/account taxonomy's domain logic: naming, merging, deleting, and the defaults a new user starts on.

Nothing here writes a row except `seed_new_user_defaults`, and that only
ever *adds* a brand-new user's starting rows. Persistence for each of
these entities lives in `accounting.repositories` — one module per
aggregate root, each owning its own tables (see that package's own
docstring). What's left in this module is the part that isn't
persistence:

- the *pure* tree logic a category or tag edit runs before anything is
  saved — `normalize_categories`, `plan_category_rename`,
  `plan_tag_rename`, `category_ids_to_delete`, `remap_category_ids`,
  `uncategorize_category_ids`, and the color palette they assign from;
- the defaults themselves — `default_categories`, `default_accounts`,
  and `seed_new_user_defaults`, which puts them in front of a user who
  has neither yet.

`seeded_categories`/`seeded_accounts` are the two reads that pair with
that seeding: a caller asking for the tree (or the accounts) on what may
be a user's very first request gets the defaults backfilled rather than
an empty dict. They are all that survives of the whole-store read this
module used to hold; every other collection is read straight from its
own repository by whoever actually needs it.
"""

from __future__ import annotations

import colorsys
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

import accounting.db as adb
from accounting.models import (
    Account,
    Budget,
    Category,
    CategoryClassification,
    CategoryPattern,
    PostingSplit,
    Tag,
)
from accounting.repositories.accounts import load_accounts, replace_accounts
from accounting.repositories.planning import budget_row_key
from accounting.repositories.taxonomy import load_categories, replace_categories

if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterable

    from sqlalchemy.orm import Session

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


class CategoryReferences(BaseModel):
    """Every row that names a category by id and isn't the category tree itself — what a rename or delete has to fix.

    These three travel together because a category merge repoints all of
    them and a category delete clears all of them, in one pass each (see
    `remap_category_ids`/`uncategorize_category_ids`), and because the
    router that runs either then writes all three back through their own
    repositories. `TransferRule` is deliberately absent: it has no
    category fields of its own (see its own docstring).

    Two other places also reference a category and are deliberately *not*
    here: the raw postings (never rewritten — a retired `categories` row
    with a successor is what repoints those, see
    `repositories.taxonomy.load_category_redirects`) and the manual
    per-posting overrides (rewritten one posting at a time, so that only
    the postings a rename actually touches are written).
    """

    model_config = ConfigDict(frozen=True)

    category_patterns: dict[str, CategoryPattern] = Field(default_factory=dict)
    budgets: list[Budget] = Field(default_factory=list)
    """Every spending target, per-month and general (`month is None`) alike — one list, one table."""
    posting_splits: dict[str, PostingSplit] = Field(default_factory=dict)


def remap_category_ids(references: CategoryReferences, id_remap: dict[str, str]) -> CategoryReferences:
    """Repoint every category/subcategory reference outside the category tree after a merge.

    Doesn't touch the tree itself (the caller already applied
    `plan_category_rename`'s own result there) — this only fixes the other
    places a category id is stored: category patterns, budgets (per-month
    and general alike), and posting splits. See `CategoryReferences` for
    what's deliberately not in that set.

    A budget's `budget_id` *is* its `(month, category_id,
    subcategory_id)` triple (see `repositories.planning.budget_row_key`),
    so a repointed budget gets a rebuilt id rather than one still naming
    the merged-away category — otherwise the next single-cell upsert for
    the surviving category would mint a second row that the table's
    `(user, month, category, subcategory)` unique index rejects.

    If the merge target already has a budget for the same
    month/category/subcategory the merged-away category also had one for,
    the merged-away category's entry is dropped rather than kept — the
    survivor's own existing entry always wins, since there's no
    principled way to combine two different budgeted amounts. A caller
    that wants to warn about this before committing to the merge should
    call this same function itself and diff `references.budgets` against
    the result (see `api.routers.categories.get_category_rename_preview`).

    Parameters
    ----------
    references
        Every category-referencing row, as loaded from its own repository.
    id_remap
        `old_id -> new_id`, as returned by `plan_category_rename` — a
        no-op (returns `references` unchanged) when empty.

    Returns
    -------
    CategoryReferences
        The same rows, with every `category_id`/`subcategory_id` field
        referencing a merged-away id repointed to its replacement, and
        any now-colliding budget entry dropped in favor of the survivor's.
    """
    if not id_remap:
        return references

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
        for pattern_id, pattern in references.category_patterns.items()
    }
    budgets_by_key: dict[tuple[str | None, str, str | None], Budget] = {}
    # Sorted so a budget the merge doesn't touch (including the survivor's own)
    # claims its key first — a colliding merged-away budget is then skipped
    # instead of overwriting it.
    for budget in sorted(references.budgets, key=lambda b: was_remapped(b.category_id, b.subcategory_id)):
        category_id = id_remap.get(budget.category_id, budget.category_id)
        subcategory_id = remap(budget.subcategory_id)
        budget_key = budget.month, category_id, subcategory_id
        if budget_key in budgets_by_key:
            continue
        budgets_by_key[budget_key] = budget.model_copy(
            update={
                "budget_id": budget_row_key(budget.month, category_id, subcategory_id),
                "category_id": category_id,
                "subcategory_id": subcategory_id,
            }
        )
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
        for posting_id, split in references.posting_splits.items()
    }
    return CategoryReferences(
        category_patterns=patterns, budgets=list(budgets_by_key.values()), posting_splits=posting_splits
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


def uncategorize_category_ids(references: CategoryReferences, category_ids: set[str]) -> CategoryReferences:
    """Strip every reference to `category_ids` from everything a category delete doesn't already remove.

    Doesn't touch the category tree itself (the caller removes
    `category_ids` from it separately — see `category_ids_to_delete`) or
    the postings/manual overrides, which are deliberately outside
    `CategoryReferences` (the router runs its own scoped override pass,
    mirroring `remap_category_ids`'s split).

    `PostingSplitLeg` has nullable `category_id`/`subcategory_id` fields,
    so a reference there is simply cleared — unlike a merge, there's no
    replacement id to repoint at. `TransferRule` has no category fields of
    its own (see its own docstring), so there's nothing to clear there.
    `Budget`/`CategoryPattern` require a `category_id` (never null): a row
    whose own `category_id` is being deleted has nothing left to be, so
    it's dropped entirely; one only referencing a deleted id via its
    (nullable) `subcategory_id` just has that cleared, same as the
    nullable-field tables. That rule now covers general budgets too (they
    are `Budget` rows with no `month`), where it used to differ.

    Parameters
    ----------
    references
        Every category-referencing row, as loaded from its own repository.
    category_ids
        Every category id being deleted (see `category_ids_to_delete`).

    Returns
    -------
    CategoryReferences
    """
    if not category_ids:
        return references

    def clear(field_id: str | None) -> str | None:
        """Return `None` if `field_id` is one of the ids being deleted, otherwise leave it unchanged.

        Returns
        -------
        str or None
        """
        return None if field_id in category_ids else field_id

    patterns = {
        pattern_id: pattern.model_copy(update={"subcategory_id": clear(pattern.subcategory_id)})
        for pattern_id, pattern in references.category_patterns.items()
        if pattern.category_id not in category_ids
    }
    budgets_by_key: dict[tuple[str | None, str, str | None], Budget] = {}
    # A budget that only referenced a deleted *subcategory* keeps its
    # category-level target, so it can land on a key a whole-category budget
    # for the same month already holds. Sorted so that untouched budget claims
    # the key first and the cleared one is dropped rather than overwriting it —
    # the same survivor-wins arbitration `remap_category_ids` makes, and what
    # keeps the table's own (user, month, category, subcategory) unique index
    # satisfiable afterwards.
    for budget in sorted(references.budgets, key=lambda b: b.subcategory_id in category_ids):
        if budget.category_id in category_ids:
            continue
        subcategory_id = clear(budget.subcategory_id)
        budget_key = budget.month, budget.category_id, subcategory_id
        if budget_key in budgets_by_key:
            continue
        budgets_by_key[budget_key] = budget.model_copy(
            update={
                "budget_id": budget_row_key(budget.month, budget.category_id, subcategory_id),
                "subcategory_id": subcategory_id,
            }
        )
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
        for posting_id, split in references.posting_splits.items()
    }
    return CategoryReferences(
        category_patterns=patterns, budgets=list(budgets_by_key.values()), posting_splits=posting_splits
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
    for tree, classification in taxonomies:
        for top_name, sub_names in tree.items():
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
    # Purely additive (`prune=False`): the two guards above already proved this
    # user owns neither table's rows, so there is nothing a prune could remove
    # — and asking for one would only invite a repository to delete rows a
    # future caller might legitimately have seeded separately.
    replace_accounts(session, user_id, default_accounts().values(), prune=False)
    replace_categories(session, user_id, default_categories().values(), prune=False)
    session.commit()


def seeded_categories(session: Session, user_id: uuid.UUID) -> dict[str, Category]:
    """Read the whole category tree, seeding a brand-new user's defaults first so it is never empty.

    Every caller that reads the tree to *decide* something — is this name
    a duplicate, what does this file's category column resolve to, which
    categories would this delete take down — goes through here rather
    than `repositories.taxonomy.load_categories`, because seeing an empty
    tree would make each of those decisions differently (and, worse,
    persist that decision, permanently disqualifying the user from ever
    being seeded: `seed_new_user_defaults` only fires for someone holding
    neither an account nor a category).

    Parameters
    ----------
    session
        An open database session; committed if defaults were seeded.
    user_id
        Whose tree to read.

    Returns
    -------
    dict[str, Category]
        Every category and subcategory, keyed by `category_id`.
    """
    seed_new_user_defaults(session, user_id)
    return load_categories(session, user_id)


def seeded_accounts(session: Session, user_id: uuid.UUID) -> dict[str, Account]:
    """Read every account, seeding a brand-new user's defaults and backfilling either missing placeholder.

    The backfill is in-memory only and covers the case seeding can't: a
    user who *has* accounts (so `seed_new_user_defaults` is a no-op for
    them) but deleted a placeholder counterparty while it had no postings
    on it. Every import needs both to point at, so they are always
    present in what a caller sees, whatever the table holds.

    Parameters
    ----------
    session
        An open database session; committed if defaults were seeded.
    user_id
        Whose accounts to read.

    Returns
    -------
    dict[str, Account]
        Every account, keyed by `account_id`.
    """
    seed_new_user_defaults(session, user_id)
    stored = load_accounts(session, user_id)
    missing = {account_id: account for account_id, account in default_accounts().items() if account_id not in stored}
    return {**stored, **missing} if missing else stored
