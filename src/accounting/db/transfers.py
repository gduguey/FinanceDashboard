"""Confirmed pairings of two transactions as the two sides of one real-world transfer."""

from __future__ import annotations

import uuid
from typing import get_args

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.db.core import SCHEMA
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
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    natural_key: Mapped[str]
    source: Mapped[str]
    # A plain historical label, not a foreign key — see `models.TransferLink`'s
    # own docstring for why this deliberately survives the referenced rule
    # being deleted later, rather than being enforced (and nulled) by the DB.
    rule_id: Mapped[str | None] = mapped_column(default=None)


class TransferLinkedTransaction(Base, Timestamped):
    """One transaction belonging to one `TransferLink` — exactly two rows per link.

    Mirrors `PostingMergeDuplicate`'s own shape (a join table with a real
    foreign key into `transactions`, not a pair of columns) — but the
    `UniqueConstraint` here is the whole point, not incidental: a
    transaction appearing in a second link must fail at the database, not
    be silently allowed and left for application code to notice.
    """

    __tablename__ = "transfer_linked_transactions"
    __table_args__ = (
        UniqueConstraint("user_id", "transaction_id", name="uq_transfer_linked_transactions_user_transaction"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.transfer_links.id", ondelete="CASCADE")
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.transactions.id"))
