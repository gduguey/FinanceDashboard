"""The taxonomy aggregate: the categories and tags every posting is filed under, plus two standalone user lists.

`categories` and `tags` are the reference data the rest of the app points
at — `postings.category_id`/`subcategory_id`, `posting_tags.tag_id`,
budgets, patterns, and split legs all foreign-key into one of them. Both
are therefore upsert-and-pruned rather than wiped and reinserted (see
`db.base.upsert_and_prune`), and `replace_categories` runs in two passes
for the same reason `accounts.replace_accounts` does:
`categories.parent_category_id` references its own table.

`other_assets` and `simulator_scenarios` are here for now for want of a
better home, not because they belong to the same root: neither is
referenced by anything, and neither is really taxonomy — one is a
hand-entered asset the net-worth view adds in, the other a saved
what-if input set. The DB-design audit's target shape doesn't name an
aggregate for either yet; when it does, they move.

The *pure* category/tag tree logic — `normalize_categories`,
`plan_category_rename`, `plan_tag_rename`, the color palette — is domain
logic, not persistence, and stays in `accounting.store`. Only the row
reads and writes live here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import accounting.db as adb
from accounting.models import Category, OtherAsset, SimulatorScenario, Tag
from db.base import derive_id, upsert_and_prune

if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterable, Mapping

    from sqlalchemy.orm import Session


def _category_id(user_id: uuid.UUID, category_id: str) -> uuid.UUID:
    """Derive this user's stable internal id for the category natural-keyed `category_id`.

    Returns
    -------
    uuid.UUID
    """
    return derive_id(user_id, "categories", category_id)


def _tag_id(user_id: uuid.UUID, tag_id: str) -> uuid.UUID:
    """Derive this user's stable internal id for the tag natural-keyed `tag_id`.

    Returns
    -------
    uuid.UUID
    """
    return derive_id(user_id, "tags", tag_id)


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


def _category_row(user_id: uuid.UUID, category: Category) -> adb.Category:
    """Build the ORM row for one live category.

    Every row this builds is explicitly un-retired
    (`retired_at`/`superseded_by_category_id` both `NULL`), which is what
    makes writing a category the way back from retirement: re-creating
    "Dining" by name, or an import minting it again, resurrects the row
    that `UNIQUE(user_id, natural_key)` would otherwise refuse a second
    copy of. Retirement is only ever *entered* through `retire_categories`.

    Returns
    -------
    accounting.db.Category
    """
    return adb.Category(
        id=_category_id(user_id, category.category_id),
        user_id=user_id,
        natural_key=category.category_id,
        name=category.name,
        classification=category.classification,
        parent_category_id=_category_id(user_id, category.parent_category_id)
        if category.parent_category_id is not None
        else None,
        color=category.color,
        retired_at=None,
        superseded_by_category_id=None,
    )


def load_categories(session: Session, user_id: uuid.UUID) -> dict[str, Category]:
    """Read the live category tree, keyed by natural key.

    Retired categories (see `accounting.db.core.Category`) are excluded:
    they are tombstones kept only so the postings imported under them keep
    a valid foreign key, and nothing outside `load_category_redirects`
    should ever see one.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose categories to read.

    Returns
    -------
    dict[str, Category]
    """
    rows = list(session.query(adb.Category).filter_by(user_id=user_id, retired_at=None))
    category_natural_key_by_id = {row.id: row.natural_key for row in rows}
    return {row.natural_key: _category_from_row(row, category_natural_key_by_id) for row in rows}


def _retired_natural_keys(session: Session, user_id: uuid.UUID) -> set[str]:
    """Every natural key this user has a retired category row for.

    Returns
    -------
    set[str]
    """
    return {
        row.natural_key
        for row in session.query(adb.Category.natural_key).filter(
            adb.Category.user_id == user_id, adb.Category.retired_at.is_not(None)
        )
    }


def load_category_redirects(session: Session, user_id: uuid.UUID) -> dict[str, str | None]:
    """Map every retired category's natural key to what it resolves to today.

    The read half of retirement, and the whole of how a category merge or
    delete reaches the ledger: a posting stores the category it was
    *imported* under and is never rewritten (DB-audit D14), so "Dining is
    now Food & Drink" has to be answered here, at resolution time, rather
    than baked onto the rows that used to point at it. See
    `ledger.categorization.apply_category_redirects` for the frame-side
    application, and `retire_categories` for the write.

    Only one hop is ever needed. `retire_categories` collapses chains as it
    writes them, so a category merged into one that is itself merged away
    later points straight at the survivor rather than at a tombstone.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose taxonomy to read.

    Returns
    -------
    dict[str, str | None]
        Retired natural key to its successor's natural key, or `None` where
        the category was deleted rather than merged (postings imported
        under it resolve to uncategorized). Empty when nothing is retired,
        which is the overwhelmingly common case.
    """
    rows = list(
        session.query(adb.Category).filter(adb.Category.user_id == user_id, adb.Category.retired_at.is_not(None))
    )
    if not rows:
        return {}
    natural_key_by_id = {
        row.id: row.natural_key
        for row in session.query(adb.Category.id, adb.Category.natural_key).filter_by(user_id=user_id)
    }
    return {
        row.natural_key: natural_key_by_id.get(row.superseded_by_category_id)
        if row.superseded_by_category_id is not None
        else None
        for row in rows
    }


def retire_categories(session: Session, user_id: uuid.UUID, successors: Mapping[str, str | None]) -> None:
    """Take categories out of the live tree, recording what (if anything) each folded into.

    The write half of retirement — the *only* way a category leaves the
    live tree once a posting could be pointing at it, and the reason
    neither a merge nor a delete touches a single stored posting any more.
    A row is marked retired in place rather than deleted, so
    `postings.category_id`/`subcategory_id` (real foreign keys holding raw
    import provenance) stay valid by construction instead of by the caller
    remembering to clear them first.

    Chains are collapsed as they are written: any tombstone already
    pointing at one of `successors`' keys is repointed at that key's own
    successor in the same pass, so `load_category_redirects` never has to
    walk more than one hop and a tombstone can never name another
    tombstone. That also degrades a merge into a delete correctly — merging
    A into B and later deleting B leaves A resolving to nothing, which is
    what deleting B means.

    Scoped to exactly the named categories, so a concurrent rename or
    delete of an unrelated category can't be reverted by this write the
    way routing it through a whole-tree rewrite would (the race
    `taxonomy.replace_categories`' own prune still runs, and the reason
    this is not simply "write the tree without them").

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose categories these are.
    successors
        Natural key to retire, mapped to the natural key it merged into, or
        `None` when it is being deleted outright. A no-op when empty.
    """
    if not successors:
        return
    retired_at = datetime.now(tz=UTC)
    for natural_key, successor in successors.items():
        retiring_id = _category_id(user_id, natural_key)
        successor_id = _category_id(user_id, successor) if successor is not None else None
        # Whatever already resolved *to* this category now resolves to
        # whatever this category itself resolves to — one hop, always.
        session.query(adb.Category).filter(
            adb.Category.user_id == user_id, adb.Category.superseded_by_category_id == retiring_id
        ).update({"superseded_by_category_id": successor_id}, synchronize_session=False)
        session.query(adb.Category).filter(adb.Category.user_id == user_id, adb.Category.id == retiring_id).update(
            {"retired_at": retired_at, "superseded_by_category_id": successor_id}, synchronize_session=False
        )
    session.flush()


def replace_categories(
    session: Session, user_id: uuid.UUID, categories: Iterable[Category], *, prune: bool = True
) -> None:
    """Insert-or-update every one of `categories`, then delete this user's categories not among them.

    Two passes, top-level categories first: `categories.parent_category_id`
    references this same table, so a subcategory row inserted before its
    parent exists would violate that foreign key. Both passes prune against
    the *complete* desired set, so a subcategory written in the second pass
    is never swept up by the first pass's prune.

    Retired categories are never pruned, whatever the caller passes. They
    are deliberately absent from every tree a caller can build (see
    `load_categories`), so a prune driven by "what the live tree should be"
    would otherwise delete exactly the tombstones that exist to keep the
    postings imported under them foreign-key-valid — see
    `accounting.db.core.Category` and `retire_categories`.

    A caller that prunes must have already cleared or repointed everything
    referencing the *live* categories being dropped — budgets, category
    patterns, split legs, and manual overrides all foreign-key into
    `categories` (see `store.uncategorize_category_ids` and
    `store.remap_category_ids` for what the delete and merge paths clear
    first). Stored postings need no such pass: they point at raw import
    provenance which is retired rather than removed. A still-referenced
    delete fails loudly here rather than silently orphaning history.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose categories these are.
    categories
        The complete desired *live* tree when `prune` is true; just the
        rows to add or update when it isn't. Anything named here is also
        un-retired (see `_category_row`).
    prune
        `False` makes this purely additive — the create paths
        (`POST /categories`, `POST /categories/{id}/subcategories`, an
        import minting a category it met in a file), which must never
        remove a category they weren't given.
    """
    categories = list(categories)
    keep_natural_keys = {category.category_id for category in categories}
    if prune:
        keep_natural_keys |= _retired_natural_keys(session, user_id)
    for has_parent in (False, True):
        rows = [
            _category_row(user_id, category)
            for category in categories
            if (category.parent_category_id is not None) is has_parent
        ]
        if prune:
            upsert_and_prune(session, adb.Category, user_id, rows, keep_natural_keys)
        else:
            for row in rows:
                session.merge(row)
            session.flush()


def load_tags(session: Session, user_id: uuid.UUID) -> dict[str, Tag]:
    """Read every tag, keyed by natural key.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose tags to read.

    Returns
    -------
    dict[str, Tag]
    """
    return {
        row.natural_key: Tag(tag_id=row.natural_key, name=row.name)
        for row in session.query(adb.Tag).filter_by(user_id=user_id)
    }


def replace_tags(session: Session, user_id: uuid.UUID, tags: Iterable[Tag], *, prune: bool = True) -> None:
    """Insert-or-update every one of `tags`, then delete this user's tags not among them.

    Upsert-and-pruned, not wiped: `posting_tags.tag_id` and
    `posting_override_tags.tag_id` are real foreign keys into this table.
    A pruned-away tag *is* removed from every posting carrying it, via
    those two tables' own `ON DELETE CASCADE`.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose tags these are.
    tags
        The complete desired set when `prune` is true; just the rows to add
        or update when it isn't.
    prune
        `False` makes this purely additive — the single-tag create path
        (`POST /tags`), which must never remove a tag it wasn't given.
    """
    tags = list(tags)
    rows = [
        adb.Tag(id=_tag_id(user_id, tag.tag_id), user_id=user_id, natural_key=tag.tag_id, name=tag.name) for tag in tags
    ]
    if prune:
        upsert_and_prune(session, adb.Tag, user_id, rows, {tag.tag_id for tag in tags})
        return
    for row in rows:
        session.merge(row)
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


def remap_tag_ids(id_remap: dict[str, str], session: Session, user_id: uuid.UUID) -> None:
    """Repoint every tag reference that lives outside the `tags` table itself after a merge.

    Doesn't touch `tags` (the caller applies `store.plan_tag_rename`'s own
    result through `replace_tags`, the same division of labor
    `store.remap_category_ids` has with `replace_categories`) — this only
    fixes the two other places a tag id is stored: the `posting_tags` join
    table and each posting override's `PostingOverrideTag` rows — both real
    FKs to `tags.id`, repointed the same way. That's also why this — unlike
    the pure `remap_category_ids` — needs a `session`/`user_id` of its own.

    A posting (or override) already tagged with both the merged-away and
    target tag would violate one of these tables' own uniqueness on a
    plain update, so that row is deleted instead of retargeted, rather
    than left to raise.

    Parameters
    ----------
    id_remap
        `old_id -> new_id`, as returned by `store.plan_tag_rename` — a
        no-op when empty.
    session
        An open database session; `session.commit()` is called on success.
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


def load_other_assets(session: Session, user_id: uuid.UUID) -> list[OtherAsset]:
    """Read every hand-entered asset the net-worth view adds on top of the ledger.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose assets to read.

    Returns
    -------
    list[OtherAsset]
    """
    return [
        OtherAsset(asset_id=row.natural_key, name=row.name, value=row.value, currency=row.currency, note=row.note)  # type: ignore[arg-type]
        for row in session.query(adb.OtherAsset).filter_by(user_id=user_id)
    ]


def _other_asset_row(user_id: uuid.UUID, asset: OtherAsset) -> adb.OtherAsset:
    """Build the ORM row for one hand-entered asset.

    Returns
    -------
    accounting.db.OtherAsset
    """
    return adb.OtherAsset(
        id=derive_id(user_id, "other_assets", asset.asset_id),
        user_id=user_id,
        natural_key=asset.asset_id,
        name=asset.name,
        value=asset.value,
        currency=asset.currency,
        note=asset.note,
    )


def replace_other_assets(session: Session, user_id: uuid.UUID, assets: Iterable[OtherAsset]) -> None:
    """Replace this user's whole hand-entered-asset list, touching no other table.

    Wipe-and-reinsert: nothing foreign-keys into `other_assets`.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose assets these are.
    assets
        The complete desired set.
    """
    session.query(adb.OtherAsset).filter_by(user_id=user_id).delete()
    session.flush()
    session.add_all(_other_asset_row(user_id, asset) for asset in assets)
    session.flush()


def insert_other_asset(session: Session, user_id: uuid.UUID, asset: OtherAsset) -> None:
    """Persist one new hand-entered asset, touching no asset already saved.

    Parameters
    ----------
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose asset this is.
    asset
        The asset to persist; its `asset_id` is server-minted, so this is
        always genuinely an insert.
    """
    session.add(_other_asset_row(user_id, asset))
    session.commit()


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


def load_simulator_scenarios(session: Session, user_id: uuid.UUID) -> list[SimulatorScenario]:
    """Read every saved compound-growth what-if.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose scenarios to read.

    Returns
    -------
    list[SimulatorScenario]
    """
    return [
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


def _simulator_scenario_row(user_id: uuid.UUID, scenario: SimulatorScenario) -> adb.SimulatorScenario:
    """Build the ORM row for one saved scenario.

    Returns
    -------
    accounting.db.SimulatorScenario
    """
    return adb.SimulatorScenario(
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


def replace_simulator_scenarios(session: Session, user_id: uuid.UUID, scenarios: Iterable[SimulatorScenario]) -> None:
    """Replace this user's whole saved-scenario list, touching no other table.

    Wipe-and-reinsert: nothing foreign-keys into `simulator_scenarios`.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose scenarios these are.
    scenarios
        The complete desired set.
    """
    session.query(adb.SimulatorScenario).filter_by(user_id=user_id).delete()
    session.flush()
    session.add_all(_simulator_scenario_row(user_id, scenario) for scenario in scenarios)
    session.flush()


def insert_simulator_scenario(session: Session, user_id: uuid.UUID, scenario: SimulatorScenario) -> None:
    """Persist one new saved scenario, touching no scenario already saved.

    Parameters
    ----------
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose scenario this is.
    scenario
        The scenario to persist; its `scenario_id` is server-minted, so
        this is always genuinely an insert.
    """
    session.add(_simulator_scenario_row(user_id, scenario))
    session.commit()


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
