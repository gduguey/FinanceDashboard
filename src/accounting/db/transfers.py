"""Confirmed pairings of two transactions as the two sides of one real-world transfer."""

from __future__ import annotations

import uuid
from typing import get_args

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.db.core import SCHEMA, stage_constraint
from accounting.models import TransferLinkSource
from db.base import Base, Timestamped, check_in_sql


class TransferLink(Base, Timestamped):
    """A confirmed pairing of two transactions as the two sides of one real-world transfer.

    `natural_key` is always derived from the two transaction ids sorted
    once (see `ledger.transfers.make_transfer_link`), so re-confirming the
    same real-world pair from either side is idempotent. The actual
    membership — which two transactions this links — lives in
    `TransferLinkedTransaction` below, a real join table, not a pair of
    columns here, since that's what lets a `UNIQUE` constraint enforce "a
    transaction is never in more than one link" at the database itself.
    """

    __tablename__ = "transfer_links"
    __table_args__ = (
        UniqueConstraint("user_id", "natural_key", name="uq_transfer_links_user_natural_key"),
        CheckConstraint(check_in_sql("source", get_args(TransferLinkSource)), name="source"),
        stage_constraint("link"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    stage: Mapped[str] = mapped_column(default="link")
    """Which resolution stage this overlay is applied at — see `accounting.precedence`.

    Last of the six, and not merely by preference — the stage ordering
    module records why moving it earlier would silently drop the columns it
    adds."""
    source: Mapped[str]
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.categorization_rules.id", ondelete="SET NULL"), default=None
    )
    """Which rule proposed this pairing, or `NULL` — a real foreign key now, not a bare `String`.

    This was Karwin's "Keyless Entry" verbatim (DB-audit D7): a plain
    `String` column holding what was supposed to be a
    `categorization_rules` natural key, with nothing stopping it naming a
    rule that never existed or had long since been deleted. The
    justification was that the label should outlive the rule — but a label
    naming a row nobody can look up is not provenance, it is a string that
    reads like one.

    `ON DELETE SET NULL` keeps the useful half of that intent: deleting the
    rule leaves the link itself standing (`source` still records that a
    rule, rather than the user, proposed it) and clears only the reference
    that no longer resolves."""


class TransferLinkedTransaction(Base, Timestamped):
    """One transaction belonging to one `TransferLink` — exactly two rows per link.

    Mirrors `PostingMergeDuplicate`'s own shape (a join table with a real
    foreign key into `transactions`, not a pair of columns), composite
    primary key included — the association *is* `(user_id, link_id,
    transaction_id)`, and the surrogate `id` it used to carry on top was
    Karwin's "ID Required" (DB-audit D9).

    The `UniqueConstraint` is not that surrogate's replacement and does not
    go with it: it is a strictly stronger, different rule — a transaction
    appearing in a *second* link must fail at the database, not be silently
    allowed and left for application code to notice — and the primary key,
    which allows exactly that, cannot express it.
    """

    __tablename__ = "transfer_linked_transactions"
    __table_args__ = (
        UniqueConstraint("user_id", "transaction_id", name="uq_transfer_linked_transactions_user_transaction"),
        {"schema": SCHEMA},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.transfer_links.id", ondelete="CASCADE"), primary_key=True
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.transactions.id"), primary_key=True
    )
