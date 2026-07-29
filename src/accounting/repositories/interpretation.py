"""The interpretation aggregate: everything layered on top of the immutable posting ledger.

Five concepts live here, and the DB-design audit is explicit that they stay
distinct rather than collapsing into one generic annotation table, because
each changes the resolved ledger's shape in a different way:

- **rules** (`transfer_rules`) repoint a posting's counterparty;
- **patterns** (`category_patterns`) suggest a category;
- **overrides** (`posting_overrides`) are the user's own last word on one posting;
- **splits** and **merges** change how many rows a transaction resolves to;
- **links** (`transfer_links`) pair two transactions as one transfer.

None of them is ever baked into a posting, so re-importing a statement or
rebuilding from raw archives can never silently erase one.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import TYPE_CHECKING

from sqlalchemy import text

import accounting.db as adb
from accounting.models import (
    CategoryPattern,
    DismissedSuggestion,
    ManualOverride,
    PostingMerge,
    PostingSplit,
    PostingSplitLeg,
    TransferLink,
    TransferRule,
)
from db.base import check_and_bump_row_version, derive_id, natural_keys_by_id

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from sqlalchemy.orm import Session


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


def load_transfer_rules(session: Session, user_id: uuid.UUID) -> list[TransferRule]:
    """Read every counterparty-resolution rule, each with the transactions opted out of it.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose rules to read.

    Returns
    -------
    list[TransferRule]
    """
    rows = list(session.query(adb.TransferRule).filter_by(user_id=user_id))
    exclusion_rows = list(session.query(adb.TransferRuleExclusion).filter_by(user_id=user_id))
    transaction_natural_key_by_id = natural_keys_by_id(
        session, adb.Transaction, user_id, [exclusion.transaction_id for exclusion in exclusion_rows]
    )
    account_natural_key_by_id = natural_keys_by_id(
        session, adb.Account, user_id, [row.account_id for row in rows] + [row.counterparty_account_id for row in rows]
    )
    exclusions_by_rule: dict[uuid.UUID, list[adb.TransferRuleExclusion]] = _group_by(
        exclusion_rows, key=lambda row: row.rule_id
    )
    return [
        _rule_from_row(
            row,
            account_natural_key_by_id,
            [
                transaction_natural_key_by_id[exclusion.transaction_id]
                for exclusion in exclusions_by_rule.get(row.id, [])
            ],
        )
        for row in rows
    ]


def replace_rule_exclusions(session: Session, user_id: uuid.UUID, rules: Iterable[TransferRule]) -> None:
    """Rewrite every rule's opted-out transaction set from `rules`, touching no other table.

    Kept separate from `replace_transfer_rules` because the exclusion rows
    foreign-key into `transfer_rules`, so they have to be deleted before
    that call's prune and re-inserted after its upsert.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose exclusions these are.
    rules
        The rules whose exclusion sets to write.
    """
    session.add_all(
        adb.TransferRuleExclusion(
            user_id=user_id,
            rule_id=derive_id(user_id, "transfer_rules", rule.rule_id),
            transaction_id=_transaction_id(user_id, transaction_id),
        )
        for rule in rules
        for transaction_id in rule.excluded_transaction_ids
    )
    session.flush()


def clear_rule_exclusions(session: Session, user_id: uuid.UUID) -> None:
    """Drop every rule exclusion this user has, so `replace_rule_exclusions` can rewrite them.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose exclusions to clear.
    """
    session.query(adb.TransferRuleExclusion).filter_by(user_id=user_id).delete()
    session.flush()


def replace_transfer_rules(session: Session, user_id: uuid.UUID, rules: Iterable[TransferRule]) -> None:
    """Insert-or-update every one of `rules`, then delete this user's rows not among them — never touching `version`.

    A plain `session.merge()` would overwrite every mapped column on an
    existing row, including `version` (which a transient instance never
    sets, so it would silently reset to the column default) — exactly the
    bug `PATCH /transfer-rules/{rule_id}`'s per-row optimistic concurrency
    depends on not happening. A raw `INSERT ... ON CONFLICT (id) DO
    UPDATE` whose `SET` clause simply omits `version` avoids that: an
    existing row keeps whatever version `check_and_bump_row_version` last
    left it at, no matter how many times an unrelated create round-trips
    through here.
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


def update_transfer_rule(
    session: Session, user_id: uuid.UUID, rule: TransferRule, expected_version: int | None
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


def load_category_patterns(session: Session, user_id: uuid.UUID) -> dict[str, CategoryPattern]:
    """Read every description-match pattern, keyed by `pattern_id`.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose patterns to read.

    Returns
    -------
    dict[str, CategoryPattern]
    """
    rows = list(session.query(adb.CategoryPattern).filter_by(user_id=user_id))
    category_natural_key_by_id = natural_keys_by_id(
        session, adb.Category, user_id, [row.category_id for row in rows] + [row.subcategory_id for row in rows]
    )
    return {row.natural_key: _pattern_from_row(row, category_natural_key_by_id) for row in rows}


def upsert_category_pattern(session: Session, user_id: uuid.UUID, pattern: CategoryPattern) -> None:
    """Insert-or-update one pattern, touching no other pattern already saved.

    Parameters
    ----------
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose pattern this is.
    pattern
        The pattern to persist.
    """
    replace_category_patterns(session, user_id, [pattern], prune=False)
    session.commit()


def replace_category_patterns(
    session: Session, user_id: uuid.UUID, patterns: Iterable[CategoryPattern], *, prune: bool = True
) -> None:
    """Insert-or-update every one of `patterns`, then delete this user's rows not among them — never touching `version`.

    Same reasoning as `replace_transfer_rules`: `CategoryPattern` carries a
    `version` column `PATCH /category-patterns/{pattern_id}` depends on, so
    a delete-all/reinsert-all treatment would silently reset it.

    `prune=False` makes this purely additive — the single-pattern create
    path, which must never remove a pattern it wasn't given.
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
    if not prune:
        return
    existing_ids = {row.id for row in session.query(adb.CategoryPattern.id).filter_by(user_id=user_id)}
    removed_ids = existing_ids - keep_ids
    if removed_ids:
        session.query(adb.CategoryPattern).filter_by(user_id=user_id).filter(
            adb.CategoryPattern.id.in_(removed_ids)
        ).delete(synchronize_session=False)


_CATEGORY_PATTERNS_TABLE = "accounting.category_patterns"


def update_category_pattern(
    session: Session, user_id: uuid.UUID, pattern: CategoryPattern, expected_version: int | None
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


def load_posting_splits(session: Session, user_id: uuid.UUID) -> dict[str, PostingSplit]:
    """Read every posting split, keyed by the natural key of the posting it splits.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose splits to read.

    Returns
    -------
    dict[str, PostingSplit]
    """
    split_rows = list(session.query(adb.PostingSplit).filter_by(user_id=user_id))
    if not split_rows:
        return {}
    leg_rows = list(session.query(adb.PostingSplitLeg).filter_by(user_id=user_id))
    posting_natural_key_by_id = natural_keys_by_id(
        session, adb.Posting, user_id, [split.posting_id for split in split_rows]
    )
    category_natural_key_by_id = natural_keys_by_id(
        session, adb.Category, user_id, [leg.category_id for leg in leg_rows] + [leg.subcategory_id for leg in leg_rows]
    )
    posting_key_by_split_id = {split.id: posting_natural_key_by_id[split.posting_id] for split in split_rows}
    return {
        posting_key_by_split_id[split_id]: _split_from_rows(
            posting_key_by_split_id[split_id], legs, category_natural_key_by_id
        )
        for split_id, legs in _group_by(leg_rows, key=lambda leg: leg.posting_split_id).items()
    }


def replace_posting_splits(session: Session, user_id: uuid.UUID, splits: Iterable[PostingSplit]) -> None:
    """Replace every posting split this user has, touching no other table.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose splits these are.
    splits
        The complete desired set.
    """
    session.query(adb.PostingSplitLeg).filter_by(user_id=user_id).delete()
    session.query(adb.PostingSplit).filter_by(user_id=user_id).delete()
    session.flush()
    splits = list(splits)
    session.add_all(
        adb.PostingSplit(
            id=derive_id(user_id, "posting_splits", split.posting_id),
            user_id=user_id,
            posting_id=_posting_id(user_id, split.posting_id),
        )
        for split in splits
    )
    session.flush()
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
        for split in splits
        for ordinal, leg in enumerate(split.legs)
    )
    session.flush()


def save_posting_split(split: PostingSplit, session: Session, user_id: uuid.UUID) -> None:
    """Persist one posting's split (and its legs), replacing only that posting's prior split.

    Two callers splitting *different* postings at once can't clobber each
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


def load_posting_merges(session: Session, user_id: uuid.UUID) -> dict[str, PostingMerge]:
    """Read every duplicate-resolution decision, keyed by `merge_id`.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose merges to read.

    Returns
    -------
    dict[str, PostingMerge]
    """
    merge_rows = list(session.query(adb.PostingMerge).filter_by(user_id=user_id))
    duplicate_rows = list(session.query(adb.PostingMergeDuplicate).filter_by(user_id=user_id))
    transaction_natural_key_by_id = natural_keys_by_id(
        session,
        adb.Transaction,
        user_id,
        [merge.kept_transaction_id for merge in merge_rows]
        + [duplicate.duplicate_transaction_id for duplicate in duplicate_rows],
    )
    duplicates_by_merge: dict[uuid.UUID, list[adb.PostingMergeDuplicate]] = _group_by(
        duplicate_rows, key=lambda row: row.merge_id
    )
    return {
        row.natural_key: _merge_from_rows(
            row,
            [duplicate.duplicate_transaction_id for duplicate in duplicates_by_merge.get(row.id, [])],
            transaction_natural_key_by_id,
        )
        for row in merge_rows
    }


def replace_posting_merges(session: Session, user_id: uuid.UUID, merges: Iterable[PostingMerge]) -> None:
    """Replace every duplicate-resolution decision this user has, touching no other table.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose merges these are.
    merges
        The complete desired set.
    """
    merges = list(merges)
    session.query(adb.PostingMergeDuplicate).filter_by(user_id=user_id).delete()
    session.query(adb.PostingMerge).filter_by(user_id=user_id).delete()
    session.flush()
    session.add_all(
        adb.PostingMerge(
            id=derive_id(user_id, "posting_merges", merge.merge_id),
            user_id=user_id,
            natural_key=merge.merge_id,
            kept_transaction_id=_transaction_id(user_id, merge.kept_transaction_id),
            description=merge.description,
        )
        for merge in merges
    )
    session.flush()
    session.add_all(
        adb.PostingMergeDuplicate(
            user_id=user_id,
            merge_id=derive_id(user_id, "posting_merges", merge.merge_id),
            duplicate_transaction_id=_transaction_id(user_id, duplicate_id),
        )
        for merge in merges
        for duplicate_id in merge.duplicate_transaction_ids
    )
    session.flush()


def remove_posting_merge(session: Session, user_id: uuid.UUID, merge_id: str) -> bool:
    """Delete one duplicate-resolution merge, touching no other. Idempotent, no version check.

    Its `PostingMergeDuplicate` membership rows go automatically via their
    `ON DELETE CASCADE` FK into `posting_merges`.

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed.
    """
    row_id = derive_id(user_id, "posting_merges", merge_id)
    deleted = session.query(adb.PostingMerge).filter_by(id=row_id, user_id=user_id).delete()
    session.flush()
    return deleted > 0


def _transfer_link_from_row(
    row: adb.TransferLink, transaction_ids: list[uuid.UUID], transaction_natural_key_by_id: dict[uuid.UUID, str]
) -> TransferLink:
    """Convert one persisted `TransferLink` row (plus its two membership rows) back into its pydantic model.

    Returns
    -------
    TransferLink

    Raises
    ------
    ValueError
        If the link doesn't have exactly two membership rows.
    """
    natural_keys = sorted(transaction_natural_key_by_id[transaction_id] for transaction_id in transaction_ids)
    if len(natural_keys) != 2:  # noqa: PLR2004 — a transfer link is by definition exactly two transactions
        # A link is always exactly two transactions; a malformed membership set
        # should fail with a clear message naming the row, not a bare unpacking
        # ValueError that takes down the whole load_store for this user.
        message = f"TransferLink {row.natural_key!r} has {len(natural_keys)} membership rows, expected exactly 2"
        raise ValueError(message)
    first, second = natural_keys
    return TransferLink(
        link_id=row.natural_key,
        transaction_id_a=first,
        transaction_id_b=second,
        source=row.source,  # type: ignore[arg-type]
        rule_id=row.rule_id,
    )


def load_transfer_links(session: Session, user_id: uuid.UUID) -> list[TransferLink]:
    """Read every confirmed transfer link, each with its two member transactions.

    Parameters
    ----------
    session
        An open database session.
    user_id
        Whose links to read.

    Returns
    -------
    list[TransferLink]
    """
    link_rows = list(session.query(adb.TransferLink).filter_by(user_id=user_id))
    member_rows = list(session.query(adb.TransferLinkedTransaction).filter_by(user_id=user_id))
    transaction_natural_key_by_id = natural_keys_by_id(
        session, adb.Transaction, user_id, [row.transaction_id for row in member_rows]
    )
    members_by_link: dict[uuid.UUID, list[adb.TransferLinkedTransaction]] = _group_by(
        member_rows, key=lambda row: row.link_id
    )
    return [
        _transfer_link_from_row(
            row,
            [member.transaction_id for member in members_by_link.get(row.id, [])],
            transaction_natural_key_by_id,
        )
        for row in link_rows
    ]


def insert_transfer_links(session: Session, user_id: uuid.UUID, transfer_links: Iterable[TransferLink]) -> None:
    """Add transfer links additively, touching none already confirmed.

    `TransferLinkedTransaction.link_id` foreign-keys into `TransferLink.id`,
    so the parent rows need their own flush before the membership rows can
    be inserted.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose links these are.
    transfer_links
        The links to add.
    """
    transfer_links = list(transfer_links)
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
    session.flush()


def replace_transfer_links(session: Session, user_id: uuid.UUID, transfer_links: Iterable[TransferLink]) -> None:
    """Replace every confirmed transfer link this user has, touching no other table.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose links these are.
    transfer_links
        The complete desired set.
    """
    session.query(adb.TransferLinkedTransaction).filter_by(user_id=user_id).delete()
    session.query(adb.TransferLink).filter_by(user_id=user_id).delete()
    session.flush()
    insert_transfer_links(session, user_id, transfer_links)


def remove_transfer_link(session: Session, user_id: uuid.UUID, link_id: str) -> bool:
    """Delete one confirmed transfer link, touching no other. Idempotent, no version check.

    Its `TransferLinkedTransaction` membership rows go automatically via
    their `ON DELETE CASCADE` FK into `transfer_links`.

    Returns
    -------
    bool
        `True` if a row was actually deleted, `False` if none existed.
    """
    row_id = derive_id(user_id, "transfer_links", link_id)
    deleted = session.query(adb.TransferLink).filter_by(id=row_id, user_id=user_id).delete()
    session.flush()
    return deleted > 0


def remove_rule_transfer_links(session: Session, user_id: uuid.UUID, rule_id: str) -> int:
    """Delete every transfer link a given rule created, touching manual links or other rules' links not at all.

    Called when a `TransferRule` is deleted so the links it produced don't
    outlive it (see `DELETE /transfer-rules/{rule_id}`). Matches on the link's
    own `source == "rule"` and `rule_id` columns, so a manually-confirmed link
    (`source == "manual"`) is never swept up even if its two transactions also
    happen to match the deleted rule. Each link's `TransferLinkedTransaction`
    membership rows go automatically via their `ON DELETE CASCADE` FK.

    Returns
    -------
    int
        How many links were deleted (0 if the rule created none).
    """
    deleted = session.query(adb.TransferLink).filter_by(user_id=user_id, source="rule", rule_id=rule_id).delete()
    session.flush()
    return deleted


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
