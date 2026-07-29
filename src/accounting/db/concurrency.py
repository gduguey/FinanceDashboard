"""A per-user save counter, kept only so `GET /store` can still report a `version` field.

Nothing bumps this any more. It was the backbone of optimistic concurrency
for `accounting.store.save_store`, which wrote every accounting table at
once; that function is gone, and with it the one thing whose scope this
counter's whole-store granularity ever matched. Per-row optimistic
concurrency (`db.base.check_and_bump_row_version`, and the `version`
column on `goals`/`transfer_rules`/`category_patterns`) is what guards a
real lost-update risk now.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, Timestamped

SCHEMA = "accounting"


class StoreVersion(Base, Timestamped):
    """One user's save counter, frozen at whatever the last `save_store` call left it at.

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
