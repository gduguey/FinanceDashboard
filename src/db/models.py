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
    """One named credential belonging to a user — a broker token, an LLM API key, whatever comes next.

    Generic by design: a new kind of secret (a new broker's API token, a
    new LLM provider's key) never needs a schema change, only a new `key`
    value that the code reading and writing it agrees on (e.g.
    `"broker:ibkr"`, `"llm:gemini"`) — see `db.secrets` for the
    get/set/delete functions every credential module (`trades.brokers.*`,
    `accounting.llm.settings`) is built on, instead of each keeping its own
    JSON file or reading `.env`.

    `kind` groups secrets by shape (e.g. `"broker_credentials"`,
    `"llm_api_key"`) independently of the free-form `key`, so "every broker
    credential across every user" is a real filter, not a string-match over
    `key`. `ciphertext` is application-encrypted (see `db.encryption`)
    before it ever reaches this column — this table only enforces the
    shape (whose secret, which one, encrypted under which key version),
    never the encryption itself. `encryption_key_version` records which
    `db.encryption.SecretsEncryptionSettings` key encrypted this row, so
    the signing key can rotate without making existing rows undecryptable.
    """

    __tablename__ = "user_secrets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(primary_key=True)
    kind: Mapped[str]
    ciphertext: Mapped[str]
    encryption_key_version: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
