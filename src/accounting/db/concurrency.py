"""A per-user save counter — the backbone of optimistic concurrency for `accounting.store.save_store`."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, Timestamped

SCHEMA = "accounting"


class StoreVersion(Base, Timestamped):
    """One user's save counter, bumped by exactly one on every successful `save_store` call.

    Exactly zero or one row per user (a singleton counter) — `user_id` is
    the primary key directly, the same shape as
    `trades.db.models.DashboardSettings`: nothing else ever foreign-keys
    against it, and there's no natural-key/re-import concept here.
    """

    __tablename__ = "store_versions"
    __table_args__ = {"schema": SCHEMA}

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(default=0)
