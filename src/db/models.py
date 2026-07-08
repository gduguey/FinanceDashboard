"""`users` and `user_secrets` — the two tables shared by `accounting` and `trades`.

See `db/__init__.py` for why these two, and only these two, live outside
either module's own schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class User(Base):
    """One person using this app. Every other table's `user_id` foreign-keys here.

    Shaped to match what `fastapi-users` expects, so adding real
    authentication later needs no schema change — only wiring a login flow
    on top of a table that already has the columns it needs.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(unique=True)
    hashed_password: Mapped[str]
    is_active: Mapped[bool] = mapped_column(default=True)
    is_superuser: Mapped[bool] = mapped_column(default=False)
    is_verified: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class UserSecret(Base):
    """One named credential belonging to a user — an IBKR token, an LLM API key, whatever comes next.

    Generic by design: a new kind of secret (a new broker's API token, a
    new LLM provider's key) never needs a schema change, only a new `key`
    value that the code reading and writing it agrees on (e.g.
    `"ibkr_flex_token"`, `"gemini_api_key"`) — the same role
    `trades.credentials.IbkrCredentialOverride` and
    `accounting.llm.settings`'s credentials file play today, unified into
    one table instead of one bespoke JSON file per secret kind.

    `value` is application-encrypted before it ever reaches this column —
    this table only enforces the shape (whose secret, which one), never
    the encryption itself.
    """

    __tablename__ = "user_secrets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
