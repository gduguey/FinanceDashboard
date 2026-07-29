"""The one description-matcher table: rules that repoint a counterparty, and patterns that suggest a category."""

from __future__ import annotations

import uuid
from typing import get_args

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.db.core import SCHEMA
from accounting.models import RuleEffect
from accounting.precedence import OverlayStage
from db.base import Base, Timestamped, check_in_sql

_STAGE_BY_EFFECT: dict[RuleEffect, OverlayStage] = {"transfer": "counterparty", "categorize": "override"}
"""Which resolution stage each effect is applied at — see `accounting.precedence`.

A `transfer` row runs at `counterparty`, the first stage: it repoints a
posting's placeholder counterparty before anything else reads the account.
A `categorize` row runs at `override`, because a pattern match never
touches the ledger directly — it stages a pending `ManualOverride` on the
posting (see `ledger.pending`), which is applied at the override stage.
"""

_EFFECT_COLUMNS_SQL = " OR ".join(
    f"(effect = '{effect}' AND stage = '{stage}' AND {columns})"
    for effect, stage, columns in (
        # A transfer rule's own two columns stay nullable on purpose (see
        # `models.TransferRule`): `account_id` unset means "any account",
        # `counterparty_account_id` unset means the rule matches but resolves
        # nothing yet. What is *not* optional is that it holds none of the
        # other effect's columns.
        ("transfer", _STAGE_BY_EFFECT["transfer"], "category_id IS NULL AND subcategory_id IS NULL"),
        (
            "categorize",
            _STAGE_BY_EFFECT["categorize"],
            "category_id IS NOT NULL AND account_id IS NULL AND counterparty_account_id IS NULL",
        ),
    )
)
"""SQL pinning each effect to its own columns, its own stage, and `NULL` in the other effect's.

Written as one constraint rather than several so there is no arrangement of
columns that satisfies every individual rule while still being a row
neither effect could ever produce — a `transfer` row carrying a
`category_id`, say, or a `categorize` row claiming the `counterparty` stage.
"""


class CategorizationRule(Base, Timestamped):
    """A user-maintained description matcher, plus what to do with the postings it matches.

    Merged from the old `transfer_rules` and `category_patterns`, which
    were the same matcher over a transaction's description
    (`description_contains`, `priority`, `active`) differing only in what
    they did on a match. `effect` is that difference, made a typed column:

    - `transfer` repoints the posting's counterparty, using
      `account_id`/`counterparty_account_id` (see `models.TransferRule`);
    - `categorize` proposes a category, using
      `category_id`/`subcategory_id` (see `models.CategoryPattern`).

    `_EFFECT_COLUMNS_SQL` is what keeps that a real distinction rather than
    a convention: a row must hold its own effect's columns and `NULL` in
    the other's, and must claim the `stage` its effect is actually applied
    at. Two pydantic models still map onto this one table — the storage
    shape is shared, the two API resources are not.

    `account_id`/`counterparty_account_id`/`category_id`/`subcategory_id`
    are all real foreign keys — a rule can only ever name a row that
    already exists. Creating a rule for a counterparty that doesn't exist
    yet requires creating that account first (see `Account`); there is no
    forward-reference case left to accommodate.
    """

    __tablename__ = "categorization_rules"
    __table_args__ = (
        CheckConstraint(check_in_sql("effect", get_args(RuleEffect)), name="effect"),
        CheckConstraint(check_in_sql("stage", sorted(set(_STAGE_BY_EFFECT.values()))), name="stage"),
        CheckConstraint(_EFFECT_COLUMNS_SQL, name="effect_columns"),
        UniqueConstraint("user_id", "natural_key", name="uq_categorization_rules_user_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    """Prefixed per effect at the point it is derived (`rule:…` / `pattern:…`, see
    `api.routers.store._transfer_rule_id`/`_category_pattern_id`), which is what lets one
    `UNIQUE(user_id, natural_key)` cover both effects without a transfer rule and a category
    pattern ever colliding on the same key."""
    effect: Mapped[str]
    stage: Mapped[str]
    """Which resolution stage this row is applied at — see `accounting.precedence`.

    Derived from `effect` and pinned to it by `_EFFECT_COLUMNS_SQL` rather than
    chosen per row: it is stored so the resolver can read its running order out
    of the schema instead of out of the line order of a function body."""
    description_contains: Mapped[str]
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.accounts.id"), default=None
    )
    counterparty_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.accounts.id"), default=None
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    subcategory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categories.id"), default=None
    )
    priority: Mapped[int] = mapped_column(default=0)
    description: Mapped[str] = mapped_column(default="")
    active: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(default=1)
    """Bumped by `db.base.check_and_bump_row_version` on every `PATCH /transfer-rules/{rule_id}`
    and `PATCH /category-patterns/{pattern_id}` — see that function's own docstring. Never touched
    by the repository's own upsert path for this table (see
    `accounting.repositories.interpretation.replace_transfer_rules`/`replace_category_patterns`),
    so an unrelated create/reorder elsewhere never invalidates a version a client already has in
    hand."""


class CategorizationRuleExclusion(Base, Timestamped):
    """One transaction opted out of matching one otherwise-applicable `CategorizationRule`.

    Only ever written for a `transfer`-effect rule — a `categorize` row
    produces a suggestion the user can simply reject, so it has nothing to
    opt out of. That is an application-level fact rather than a
    `CheckConstraint`, since the effect it depends on lives on the other
    side of `rule_id`.

    Mirrors `PostingMergeDuplicate`'s own shape (a join table with a real
    foreign key into `transactions`, not a JSON array of ids) for the same
    reason: `transaction_id` values are deterministic (`db.base.derive_id`),
    so a lasting reference into `transactions` survives a ledger rebuild.
    """

    __tablename__ = "categorization_rule_exclusions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "rule_id", "transaction_id", name="uq_categorization_rule_exclusions_user_rule_txn"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categorization_rules.id", ondelete="CASCADE")
    )
    # CASCADE here is safe (unlike TransferLinkedTransaction/PostingMerge's
    # kept_transaction_id): this row is a single, standalone exclusion, not
    # one half of a pair — nothing else needs to go with it when its
    # transaction is pruned by a ledger rebuild (see
    # `importers.ingest._write_ledger`).
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.transactions.id", ondelete="CASCADE")
    )
