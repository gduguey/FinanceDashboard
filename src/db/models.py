"""`users`, `external_identities`, and `user_secrets` — the tables shared by `accounting` and `trades`.

See `db/__init__.py` for why these, and only these, live outside either
module's own schema.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import UUID7_DEFAULT, Base, Timestamped
from db.indexes import ensure_foreign_key_indexes


class User(Base, Timestamped):
    """One person using this app. Every other table's `user_id` foreign-keys here.

    Knows nothing about Clerk, or any other identity provider, on purpose
    — see `db.external_identities` for where that mapping actually lives,
    and its own docstring for why it's a separate table rather than a
    column here. `is_active` is a soft-delete marker: `trades.api.webhooks`
    clears it when Clerk reports the account deleted, so the row survives
    as a record rather than cascading every table away.

    `email` is deliberately **not** unique: it's display information, not
    a lookup key (`external_identities` is the real identity link) —
    deleting someone in Clerk and re-inviting the same address creates a
    second, unrelated row with the same email rather than colliding with
    the first one's now-orphaned data. See `trades.api.webhooks`.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=UUID7_DEFAULT)
    email: Mapped[str]
    is_active: Mapped[bool] = mapped_column(default=True)


class ExternalIdentity(Base, Timestamped):
    """Links one identity-provider account to a row in `users` — see `db.external_identities`.

    `(provider, external_id)` is the primary key: one external account
    links to exactly one internal user. Deliberately excluded from Row-
    Level Security (migration `817ace9deb09`'s `_USER_SCOPED_TABLES` never
    lists this table) — it holds no financial data, only an identity
    mapping, and it's the one place a lookup has to work *before* the
    caller already knows which user is asking, which RLS would otherwise
    block. See `db.external_identities`'s own module docstring for the
    full reasoning.
    """

    __tablename__ = "external_identities"

    provider: Mapped[str] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))


class UserSecret(Base, Timestamped):
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


# Every foreign key in this schema gets its index here rather than on each
# model, so adding a foreign key cannot ship without one. See `db.indexes`.
ensure_foreign_key_indexes(Base.metadata, schema="public")
