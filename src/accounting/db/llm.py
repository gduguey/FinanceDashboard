"""One (user, provider) pair's LLM call counter — see `accounting.llm.usage`."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from accounting.db.core import SCHEMA
from db.base import Base, Timestamped


class LLMUsage(Base, Timestamped):
    """One (user, provider) pair's call count and rate-limit state for its current tracking period."""

    __tablename__ = "llm_usage"
    __table_args__ = {"schema": SCHEMA}

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(primary_key=True)
    period_start: Mapped[datetime]
    used_count: Mapped[int] = mapped_column(default=0)
    is_limited: Mapped[bool] = mapped_column(default=False)
    last_error: Mapped[str | None] = mapped_column(default=None)
