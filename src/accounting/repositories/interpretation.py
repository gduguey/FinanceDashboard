"""The interpretation aggregate: everything layered on top of the immutable posting ledger.

Five concepts live here, and the DB-design audit is explicit that they stay
distinct rather than collapsing into one generic annotation table, because
each changes the resolved ledger's shape in a different way:

- **rules and patterns** (`categorization_rules`) are one matcher over a
  transaction's description with two effects — a `transfer` row repoints a
  posting's counterparty, a `categorize` row suggests a category. Genuine
  synonyms, so one table; the effect is a typed column, not a convention;
- **overrides** (`posting_overrides`) are the user's own last word on one posting;
- **splits** and **merges** change how many rows a transaction resolves to;
- **links** (`transfer_links`) pair two transactions as one transfer;
- **suggestions** (`suggestions`) are proposals awaiting an answer — a
  category staged on a posting (`pending`), or a detected transfer/duplicate
  the user archived (`dismissed`). One concept at two lifecycle stages, so
  one table with a typed `status`.

None of them is ever baked into a posting, so re-importing a statement or
rebuilding from raw archives can never silently erase one. The order the
first four are applied in is declared in `accounting.precedence` and stored
on each table's own `stage` column, never inferred from call order.
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
    from datetime import datetime

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


def _pending_suggestion_key(posting_id: str) -> str:
    """Build the `suggestions.natural_key` of the pending suggestion staged on `posting_id`.

    Derived from the posting rather than random, so `UNIQUE(user_id,
    natural_key)` on the merged table enforces the same "one posting is
    pending at most once" the old `posting_pending_suggestions`
    `UNIQUE(user_id, posting_id)` did, and a re-stage of the same posting
    replaces its row instead of adding a second one. The `pending:` prefix
    keeps it clear of the `transfer:`/`duplicate:` keys the dismissed
    lifecycle mints (see `api._transfer_suggestion_id`).

    Returns
    -------
    str
    """
    return f"pending:{posting_id}"


def _pending_suggestion_rows(session: Session, user_id: uuid.UUID) -> list[adb.Suggestion]:
    """Every posting-level suggestion this user has not resolved yet.

    Returns
    -------
    list[accounting.db.Suggestion]
    """
    return list(session.query(adb.Suggestion).filter_by(user_id=user_id, status="pending"))


def _pending_suggestion_row(user_id: uuid.UUID, posting_id: str, override: ManualOverride) -> adb.Suggestion:
    """Build the `suggestions` row staging `override`'s not-yet-confirmed category on one posting.

    Only ever called for an override whose `pending_source` is set — that
    column is `NOT NULL` on the row (a pending suggestion that isn't
    actually pending isn't a row at all, not a row with `source=None`).

    Returns
    -------
    accounting.db.Suggestion
    """
    natural_key = _pending_suggestion_key(posting_id)
    return adb.Suggestion(
        id=derive_id(user_id, _SUGGESTIONS_NAMESPACE, natural_key),
        user_id=user_id,
        natural_key=natural_key,
        status="pending",
        kind="category",
        source=override.pending_source,
        posting_id=_posting_id(user_id, posting_id),
        selected=override.pending_selected,
        previous_category_id=_category_id(user_id, override.pending_previous_category_id),
        previous_subcategory_id=_category_id(user_id, override.pending_previous_subcategory_id),
    )


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
    row: adb.CategorizationRule,
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


_CATEGORIZATION_RULES_TABLE = "accounting.categorization_rules"
"""The one table both `TransferRule` and `CategoryPattern` persist to, `schema.table` qualified.

Passed to `db.base.check_and_bump_row_version`, which builds raw SQL from
it — a fixed internal constant, never anything a caller supplies.
"""

_RULES_NAMESPACE = "categorization_rules"
"""The `db.base.derive_id` namespace both effects share.

Safe to share because the two natural keys are prefixed apart at the point
they are minted (`rule:` / `pattern:`, see
`api.routers.store._transfer_rule_id`/`_category_pattern_id`), so no
transfer rule and category pattern can ever derive the same row id.
"""


def _upsert_rule(session: Session, user_id: uuid.UUID, row_id: uuid.UUID, natural_key: str, **columns: object) -> None:
    """Insert-or-update one `categorization_rules` row by id, leaving `version` untouched.

    Shared by `replace_transfer_rules` and `replace_category_patterns`,
    which differ only in which effect's columns they fill. A plain
    `session.merge()` would overwrite every mapped column on an existing
    row, including `version` (which a transient instance never sets, so it
    would silently reset to the column default) — exactly the bug the
    `PATCH` endpoints' per-row optimistic concurrency depends on not
    happening. A raw `INSERT ... ON CONFLICT (id) DO UPDATE` whose `SET`
    clause simply omits `version` avoids that: an existing row keeps
    whatever version `db.base.check_and_bump_row_version` last left it at,
    no matter how many times an unrelated create round-trips through here.

    Parameters
    ----------
    session
        An open database session; the caller flushes and commits.
    user_id
        Whose rule this is.
    row_id
        The row's already-derived id.
    natural_key
        The rule's own `rule_id`/`pattern_id`.
    columns
        Every remaining column's value, `effect` and `stage` included —
        stringified UUIDs where the column is a foreign key.
    """
    names = ["id", "user_id", "natural_key", *columns]
    session.execute(
        text(
            f"""
            INSERT INTO {_CATEGORIZATION_RULES_TABLE} ({", ".join(names)}, version)
            VALUES ({", ".join(f":{name}" for name in names)}, 1)
            ON CONFLICT (id) DO UPDATE SET
                {", ".join(f"{name} = EXCLUDED.{name}" for name in columns)}
            """  # noqa: S608 — every interpolated name is a literal from this module, never caller input
        ),
        {"id": str(row_id), "user_id": str(user_id), "natural_key": natural_key, **columns},
    )


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
    rows = list(session.query(adb.CategorizationRule).filter_by(user_id=user_id, effect="transfer"))
    exclusion_rows = list(session.query(adb.CategorizationRuleExclusion).filter_by(user_id=user_id))
    transaction_natural_key_by_id = natural_keys_by_id(
        session, adb.Transaction, user_id, [exclusion.transaction_id for exclusion in exclusion_rows]
    )
    account_natural_key_by_id = natural_keys_by_id(
        session, adb.Account, user_id, [row.account_id for row in rows] + [row.counterparty_account_id for row in rows]
    )
    exclusions_by_rule: dict[uuid.UUID, list[adb.CategorizationRuleExclusion]] = _group_by(
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
    foreign-key into `categorization_rules`, so they have to be deleted
    before that call's prune and re-inserted after its upsert.

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
        adb.CategorizationRuleExclusion(
            user_id=user_id,
            rule_id=derive_id(user_id, _RULES_NAMESPACE, rule.rule_id),
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
    session.query(adb.CategorizationRuleExclusion).filter_by(user_id=user_id).delete()
    session.flush()


def replace_transfer_rules(
    session: Session, user_id: uuid.UUID, rules: Iterable[TransferRule], *, prune: bool = True
) -> None:
    """Insert-or-update every one of `rules`, then delete this user's transfer rows not among them.

    Never touches `version` — see `_upsert_rule` for why the write is a raw
    `ON CONFLICT` rather than a `session.merge()`.

    The prune is scoped to `effect="transfer"`: `categorization_rules` also
    holds this user's category patterns, and a rewrite of one effect's rows
    must not be able to delete the other's.

    Never touches `categorization_rule_exclusions` — see
    `replace_rule_exclusions` for why those are a separate call.

    `prune=False` makes this purely additive — the single-rule create path
    (`upsert_transfer_rule`), which must never remove a rule it wasn't given.
    """
    keep_ids: set[uuid.UUID] = set()
    for rule in rules:
        row_id = derive_id(user_id, _RULES_NAMESPACE, rule.rule_id)
        keep_ids.add(row_id)
        _upsert_rule(
            session,
            user_id,
            row_id,
            rule.rule_id,
            effect="transfer",
            stage="counterparty",
            description_contains=rule.description_contains,
            account_id=str(_account_id(user_id, rule.account_id)) if rule.account_id is not None else None,
            counterparty_account_id=str(_account_id(user_id, rule.counterparty_account_id))
            if rule.counterparty_account_id is not None
            else None,
            category_id=None,
            subcategory_id=None,
            priority=rule.priority,
            description=rule.description,
            active=rule.active,
        )
    session.flush()
    if not prune:
        return
    existing_ids = {
        row.id for row in session.query(adb.CategorizationRule.id).filter_by(user_id=user_id, effect="transfer")
    }
    removed_ids = existing_ids - keep_ids
    if removed_ids:
        session.query(adb.CategorizationRule).filter_by(user_id=user_id, effect="transfer").filter(
            adb.CategorizationRule.id.in_(removed_ids)
        ).delete(synchronize_session=False)


def _sync_rule_exclusions(
    session: Session, user_id: uuid.UUID, rule_row_id: uuid.UUID, transaction_ids: list[str]
) -> None:
    """Make one rule's opted-out transaction set exactly `transaction_ids`, touching no other rule's exclusions.

    Diffs against what's stored rather than deleting and reinserting, so a
    rule whose exclusion set didn't change issues no writes at all.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose exclusions these are.
    rule_row_id
        The `categorization_rules.id` these exclusions belong to.
    transaction_ids
        The complete desired set, as transaction natural keys.
    """
    existing_exclusions = list(
        session.query(adb.CategorizationRuleExclusion).filter_by(user_id=user_id, rule_id=rule_row_id)
    )
    existing_transaction_ids = {exclusion.transaction_id for exclusion in existing_exclusions}
    desired_transaction_ids = {_transaction_id(user_id, tid) for tid in transaction_ids}
    to_remove = existing_transaction_ids - desired_transaction_ids
    if to_remove:
        session.query(adb.CategorizationRuleExclusion).filter_by(user_id=user_id, rule_id=rule_row_id).filter(
            adb.CategorizationRuleExclusion.transaction_id.in_(to_remove)
        ).delete(synchronize_session=False)
    session.add_all(
        adb.CategorizationRuleExclusion(user_id=user_id, rule_id=rule_row_id, transaction_id=transaction_id)
        for transaction_id in desired_transaction_ids - existing_transaction_ids
    )
    session.flush()


def upsert_transfer_rule(rule: TransferRule, session: Session, user_id: uuid.UUID) -> None:
    """Insert-or-update one rule (and its exclusion set), touching no other rule already saved.

    Scoped counterpart to routing `POST /transfer-rules` through the
    whole-store save, which rewrote every one of this user's rules from the
    caller's (possibly stale) snapshot — so creating one rule could silently
    revert, or resurrect, a rule some concurrent request had just edited or
    deleted. This only ever writes `rule.rule_id`'s own row, so two creates
    for different rules can't conflict no matter how they interleave.

    `version` is left alone (see `replace_transfer_rules`), so re-posting a
    rule never invalidates a version a client already holds for it.

    Parameters
    ----------
    rule
        The rule to persist; `rule.rule_id` names the row.
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose rule this is.
    """
    replace_transfer_rules(session, user_id, [rule], prune=False)
    _sync_rule_exclusions(
        session, user_id, derive_id(user_id, _RULES_NAMESPACE, rule.rule_id), list(rule.excluded_transaction_ids)
    )
    session.commit()


def update_transfer_rule(
    session: Session, user_id: uuid.UUID, rule: TransferRule, expected_version: int | None
) -> TransferRule | None:
    """Update one transfer rule's fields in place, touching no other persisted entity.

    `rule.rule_id` identifies which row to update; every other field on
    `rule` (including `rule.version`, which is never read here — only
    `expected_version` is) becomes that row's new state. Unlike
    `replace_transfer_rules`, this never reads or prunes any other rule —
    it's a single row, guarded by
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
    row_id = derive_id(user_id, _RULES_NAMESPACE, rule.rule_id)
    new_version = check_and_bump_row_version(session, _CATEGORIZATION_RULES_TABLE, row_id, user_id, expected_version)
    if new_version is None:
        return None
    row = session.get(adb.CategorizationRule, row_id)
    if row is None:
        message = f"categorization_rules row {row_id} vanished between its version check and this read"
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

    _sync_rule_exclusions(session, user_id, row.id, list(rule.excluded_transaction_ids))

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
    row_id = derive_id(user_id, _RULES_NAMESPACE, rule_id)
    deleted = session.query(adb.CategorizationRule).filter_by(id=row_id, user_id=user_id, effect="transfer").delete()
    session.flush()
    return deleted > 0


def _pattern_from_row(row: adb.CategorizationRule, category_natural_key_by_id: dict[uuid.UUID, str]) -> CategoryPattern:
    """Convert one persisted `categorize`-effect row back into its `CategoryPattern`, using natural keys.

    Returns
    -------
    CategoryPattern

    Raises
    ------
    RuntimeError
        If the row carries no `category_id` — ruled out by
        `ck_categorization_rules_effect_columns`, so a defensive invariant only.
    """
    if row.category_id is None:
        # Structurally impossible: `ck_categorization_rules_effect_columns`
        # requires a `categorize` row to carry a category, and this is only
        # ever called with rows filtered to that effect.
        message = f"categorization_rules row {row.id} has effect 'categorize' but no category_id"
        raise RuntimeError(message)
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
    rows = list(session.query(adb.CategorizationRule).filter_by(user_id=user_id, effect="categorize"))
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
    """Insert-or-update every one of `patterns`, then delete this user's categorize rows not among them.

    Same reasoning as `replace_transfer_rules`, and the same `_upsert_rule`
    write: `CategoryPattern` carries a `version` column
    `PATCH /category-patterns/{pattern_id}` depends on, so a
    delete-all/reinsert-all treatment would silently reset it. The prune is
    likewise scoped to this effect, so rewriting the pattern list can never
    delete a transfer rule sharing the table.

    `prune=False` makes this purely additive — the single-pattern create
    path, which must never remove a pattern it wasn't given.
    """
    keep_ids: set[uuid.UUID] = set()
    for pattern in patterns:
        row_id = derive_id(user_id, _RULES_NAMESPACE, pattern.pattern_id)
        keep_ids.add(row_id)
        _upsert_rule(
            session,
            user_id,
            row_id,
            pattern.pattern_id,
            effect="categorize",
            stage="override",
            description_contains=pattern.description_contains,
            account_id=None,
            counterparty_account_id=None,
            category_id=str(derive_id(user_id, "categories", pattern.category_id)),
            subcategory_id=str(subcategory)
            if (subcategory := _category_id(user_id, pattern.subcategory_id)) is not None
            else None,
            priority=pattern.priority,
            description="",
            active=pattern.active,
        )
    session.flush()
    if not prune:
        return
    existing_ids = {
        row.id for row in session.query(adb.CategorizationRule.id).filter_by(user_id=user_id, effect="categorize")
    }
    removed_ids = existing_ids - keep_ids
    if removed_ids:
        session.query(adb.CategorizationRule).filter_by(user_id=user_id, effect="categorize").filter(
            adb.CategorizationRule.id.in_(removed_ids)
        ).delete(synchronize_session=False)


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
    row_id = derive_id(user_id, _RULES_NAMESPACE, pattern.pattern_id)
    new_version = check_and_bump_row_version(session, _CATEGORIZATION_RULES_TABLE, row_id, user_id, expected_version)
    if new_version is None:
        return None
    row = session.get(adb.CategorizationRule, row_id)
    if row is None:
        message = f"categorization_rules row {row_id} vanished between its version check and this read"
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
    row_id = derive_id(user_id, _RULES_NAMESPACE, pattern_id)
    deleted = session.query(adb.CategorizationRule).filter_by(id=row_id, user_id=user_id, effect="categorize").delete()
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


def upsert_posting_merge(merge: PostingMerge, session: Session, user_id: uuid.UUID) -> None:
    """Persist one duplicate-resolution decision, replacing only that merge's prior rows.

    Scoped counterpart to routing `POST /posting-merges` through the
    whole-store save, which blanket-deleted and reinserted every merge from
    the caller's snapshot — so recording one merge could silently resurrect
    a merge some concurrent request had just undone. This only ever touches
    `merge.merge_id`'s own rows. A merge is one coherent replace-in-full
    unit keyed by `merge_id` (its duplicate set isn't independently
    editable), so last-write-wins on the same merge is the intended
    semantics, the same reasoning `save_posting_split` spells out.

    Parameters
    ----------
    merge
        The merge to persist; `merge.merge_id` names the row.
    session
        An open database session; `session.commit()` is called on success.
    user_id
        Whose merge this is.
    """
    row_id = derive_id(user_id, "posting_merges", merge.merge_id)
    # The membership rows go first: they foreign-key into `posting_merges`,
    # so the parent row can't be replaced while they still point at it.
    session.query(adb.PostingMergeDuplicate).filter_by(user_id=user_id, merge_id=row_id).delete(
        synchronize_session=False
    )
    session.query(adb.PostingMerge).filter_by(id=row_id, user_id=user_id).delete(synchronize_session=False)
    session.flush()
    session.add(
        adb.PostingMerge(
            id=row_id,
            user_id=user_id,
            natural_key=merge.merge_id,
            kept_transaction_id=_transaction_id(user_id, merge.kept_transaction_id),
            description=merge.description,
        )
    )
    session.flush()
    session.add_all(
        adb.PostingMergeDuplicate(
            user_id=user_id, merge_id=row_id, duplicate_transaction_id=_transaction_id(user_id, duplicate_id)
        )
        for duplicate_id in merge.duplicate_transaction_ids
    )
    session.commit()


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
    row: adb.TransferLink,
    transaction_ids: list[uuid.UUID],
    transaction_natural_key_by_id: dict[uuid.UUID, str],
    rule_natural_key_by_id: dict[uuid.UUID, str],
) -> TransferLink:
    """Convert one persisted `TransferLink` row (plus its two membership rows) back into its pydantic model.

    `rule_id` is stored as a real foreign key now (DB-audit D7) and reversed
    back to the rule's natural key here, the same way every other stored
    reference in this module is. A link whose rule has since been deleted
    comes back with `rule_id=None` — `ON DELETE SET NULL` cleared it — where
    it used to come back naming a rule nobody could look up.

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
        # ValueError that takes down every read of this user's links.
        message = f"TransferLink {row.natural_key!r} has {len(natural_keys)} membership rows, expected exactly 2"
        raise ValueError(message)
    first, second = natural_keys
    return TransferLink(
        link_id=row.natural_key,
        transaction_id_a=first,
        transaction_id_b=second,
        source=row.source,  # type: ignore[arg-type]
        rule_id=rule_natural_key_by_id.get(row.rule_id) if row.rule_id is not None else None,
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
    rule_natural_key_by_id = natural_keys_by_id(
        session, adb.CategorizationRule, user_id, [row.rule_id for row in link_rows]
    )
    return [
        _transfer_link_from_row(
            row,
            [member.transaction_id for member in members_by_link.get(row.id, [])],
            transaction_natural_key_by_id,
            rule_natural_key_by_id,
        )
        for row in link_rows
    ]


def insert_transfer_links(session: Session, user_id: uuid.UUID, transfer_links: Iterable[TransferLink]) -> None:
    """Add transfer links additively, touching none already confirmed.

    `TransferLinkedTransaction.link_id` foreign-keys into `TransferLink.id`,
    so the parent rows need their own flush before the membership rows can
    be inserted.

    `TransferLink.rule_id` is a foreign key into `categorization_rules`
    too (DB-audit D7), so a link naming a rule can only be written once
    that rule exists. Every caller already satisfies that: a rule-sourced
    link is only ever minted by `ledger.transfers.apply_transfer_rules`,
    from rules it has just read back out of that table.

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
            rule_id=derive_id(user_id, _RULES_NAMESPACE, link.rule_id) if link.rule_id is not None else None,
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

    This still has to run *before* the rule row itself is deleted: the
    column is a real foreign key with `ON DELETE SET NULL` now (DB-audit
    D7), so dropping the rule first would clear every link's `rule_id` and
    leave nothing here to match on.

    Parameters
    ----------
    session
        An open database session; the caller commits.
    user_id
        Whose links these are.
    rule_id
        The rule's own natural key, resolved to its row id the same way
        every other reference in this module is.

    Returns
    -------
    int
        How many links were deleted (0 if the rule created none).
    """
    deleted = (
        session
        .query(adb.TransferLink)
        .filter_by(user_id=user_id, source="rule", rule_id=derive_id(user_id, _RULES_NAMESPACE, rule_id))
        .delete()
    )
    session.flush()
    return deleted


def load_overrides(session: Session, user_id: uuid.UUID) -> dict[str, ManualOverride]:
    """Read every persisted manual per-posting override.

    Merges `PostingOverride` (persistent corrections) and the pending rows
    of `Suggestion` (transient, not-yet-confirmed automation state) back
    into one `ManualOverride` per posting — the split only exists at the
    storage layer (see `accounting.db.corrections`), never in the pydantic
    shape every caller of this function already expects.

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
    return _overrides_from_rows(override_rows, _pending_suggestion_rows(session, user_id), session, user_id)


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
        session.query(adb.Suggestion).filter(
            adb.Suggestion.user_id == user_id,
            adb.Suggestion.status == "pending",
            adb.Suggestion.posting_id.in_(posting_row_ids),
        )
    )
    return _overrides_from_rows(override_rows, pending_rows, session, user_id)


def _overrides_from_rows(
    override_rows: list[adb.PostingOverride],
    pending_rows: list[adb.Suggestion],
    session: Session,
    user_id: uuid.UUID,
) -> dict[str, ManualOverride]:
    """Shared row->pydantic mapping for `load_overrides`/`load_overrides_for_postings`.

    Returns
    -------
    dict[str, ManualOverride]

    Raises
    ------
    RuntimeError
        If a pending suggestion row names no posting — ruled out by
        `ck_suggestions_pending_shape`, so a defensive invariant only.
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
        if pending_row.posting_id is None:
            # `ck_suggestions_pending_shape` requires one; only a dismissed
            # row may have none, and none are read here.
            message = f"suggestions row {pending_row.id} is pending but names no posting"
            raise RuntimeError(message)
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
    correction field is set, and a pending `Suggestion` row only when
    `pending_source` is set (see `_pending_suggestion_row`). The pending
    delete is scoped to `status="pending"`, so rewriting every override
    never touches this user's dismissed-suggestion archive, which shares
    the table.

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
    session.query(adb.Suggestion).filter_by(user_id=user_id, status="pending").delete()

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
        _pending_suggestion_row(user_id, posting_id, override)
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
    session.query(adb.Suggestion).filter(
        adb.Suggestion.user_id == user_id,
        adb.Suggestion.status == "pending",
        adb.Suggestion.posting_id.in_(posting_row_ids),
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
        _pending_suggestion_row(user_id, posting_id, override)
        for posting_id, override in overrides.items()
        if override.pending_source is not None
    )
    session.commit()


_SUGGESTIONS_NAMESPACE = "suggestions"
"""The `db.base.derive_id` namespace both suggestion lifecycles share.

Safe to share because the natural keys are prefixed apart at the point they
are minted — `pending:` (see `_pending_suggestion_key`) against
`transfer:`/`duplicate:` (see `api._transfer_suggestion_id`/
`_duplicate_suggestion_id`).
"""


def _dismissed_at(row: adb.Suggestion) -> datetime:
    """Read a dismissed row's `dismissed_at`, which `ck_suggestions_dismissed_shape` makes non-null.

    Returns
    -------
    datetime.datetime

    Raises
    ------
    RuntimeError
        If the column is null anyway — the constraint already rules that
        out, so this is a defensive invariant, never expected to happen.
    """
    if row.dismissed_at is None:
        message = f"suggestions row {row.id} is dismissed but has no dismissed_at"
        raise RuntimeError(message)
    return row.dismissed_at


def dismissed_suggestion_ids(session: Session, user_id: uuid.UUID, suggestion_ids: Iterable[str]) -> set[str]:
    """Which of `suggestion_ids` have already been dismissed, without loading anything else.

    The dismissed half of `suggestions` has no `load_*` of its own for a
    reason — unlike every other entity in this package, it's never read as
    "give me the whole list to build something," only ever checked as "has
    this one already been dismissed" against a handful of candidate
    suggestion ids computed fresh on every request (see
    `postings.get_transfer_suggestions`/`get_duplicate_suggestions`).
    Offering a whole-table read would invite a caller to pay the cost of
    loading rows that only ever grow, never shrink, for no benefit.

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
    rows = session.query(adb.Suggestion.natural_key).filter(
        adb.Suggestion.user_id == user_id,
        adb.Suggestion.status == "dismissed",
        adb.Suggestion.natural_key.in_(candidates),
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
        .query(adb.Suggestion)
        .filter_by(user_id=user_id, status="dismissed")
        .order_by(adb.Suggestion.dismissed_at.desc())
    )
    return [
        DismissedSuggestion(
            suggestion_id=row.natural_key,
            kind=row.kind,  # type: ignore[arg-type]
            description=row.description,
            dismissed_at=_dismissed_at(row),
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
        adb.Suggestion(
            id=derive_id(user_id, _SUGGESTIONS_NAMESPACE, entry.suggestion_id),
            user_id=user_id,
            natural_key=entry.suggestion_id,
            status="dismissed",
            kind=entry.kind,
            source="detector",
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
    deleted = (
        session.query(adb.Suggestion).filter_by(user_id=user_id, status="dismissed", natural_key=suggestion_id).delete()
    )
    session.commit()
    return deleted > 0
