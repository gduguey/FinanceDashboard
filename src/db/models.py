"""`users`, `external_identities`, `user_secrets`, and `currencies` — the tables shared by `accounting` and `trades`.

See `db/__init__.py` for why these, and only these, live outside either
module's own schema.
"""

from __future__ import annotations

import uuid

from sqlalchemy import DDL, ForeignKey, SmallInteger, event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import UUID7_DEFAULT, Base, Timestamped
from db.currency import CURRENCY_REFERENCE
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
    Level Security: it is the one entry in `db.tenant.RLS_EXEMPT`, which
    requires a reason beside every exemption — it holds no financial data, only an identity
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


class Currency(Base, Timestamped):
    """The reference list of currencies every `currency` column in both schemas foreign-keys into.

    Nine columns across two schemas hold a currency code. Eight of them
    carried their own `CHECK (currency IN ('USD', 'EUR'))`, restated per table
    by `db.base.check_in_sql`; the ninth, `trades.ledger_events.currency`, is
    the identical column and carried nothing at all, because the list lived in
    `accounting.models` and `trades` may not import it (DB-audit move #3). One
    `FOREIGN KEY` per column replaces all eight, and the one that was missing
    is no longer a special case.

    ## `code` is the primary key, not a surrogate id

    Every *tenant* table in this schema keys on a database-minted `uuid7()`
    and carries the human-meaningful string as `natural_key` beside it —
    because a tenant row's natural key is user-chosen and renameable, so an
    id is the only stable thing to reference (see `db.base.ids_by_natural_key`).
    None of that reasoning applies here. A three-letter ISO 4217 code is not
    user-chosen, cannot be renamed, and is already the value every posting,
    budget and goal *displays*; a surrogate id would buy stability against a
    change that cannot happen, and charge a join for every rendered amount to
    get back the string the row already had.

    So the referencing columns keep holding the code itself. That is what
    makes this whole change invisible above the persistence boundary: no
    pydantic model, no API response, no `schema.ts` union, and no
    `ids_by_natural_key` call site changes at all — `postings.currency` is
    still the `'USD'` it always was, and is now a reference rather than a
    restated `CHECK`.

    ## Reference rows, not tenant rows

    No `user_id`, deliberately: `USD` is the same currency for everyone.
    `db.tenant.tenant_tables` derives the Row-Level Security set from the
    presence of that column, so this table is excluded automatically and
    needs no `RLS_EXEMPT` entry — an exemption records a tenant table that
    deliberately has no policy, and this is not a tenant table at all. See
    `db.tenant.is_reference_table`, which is also what stops `db.indexes`
    minting a useless two-value index behind each of the nine references.
    """

    __tablename__ = "currencies"

    code: Mapped[str] = mapped_column(primary_key=True)
    """The ISO 4217 code, e.g. `USD` — the value every referencing column stores."""
    symbol: Mapped[str]
    """The glyph an amount in this currency renders with, e.g. `$`."""
    decimal_places: Mapped[int] = mapped_column(SmallInteger)
    """How many fractional digits this currency is quoted to."""


CURRENCY_CODE_COLUMN = f"{Currency.__tablename__}.code"
"""The foreign-key target every `currency` column in both schemas names.

Written once here rather than as nine copies of the string `"currencies.code"`
scattered across `accounting.db` and `trades.db`, and importing it is also
what *registers* `Currency` on `Base.metadata` for the module doing the
referencing — a `ForeignKey("currencies.code")` can only be resolved once the
table it names exists in the same metadata, and each schema's models are
imported independently (see `migration/env.py`). Naming the target and
guaranteeing it is loaded are therefore the same act, which is why this is a
constant to import rather than a string to retype.
"""


def _currency_seed_sql() -> str:
    """Build the `INSERT` that fills `public.currencies` from `db.currency.CURRENCY_REFERENCE`.

    Returns
    -------
    str
    """
    values = ", ".join(
        f"('{code}', '{reference.symbol}', {reference.decimal_places})"
        for code, reference in CURRENCY_REFERENCE.items()
    )
    columns = f"public.{Currency.__tablename__} (code, symbol, decimal_places)"
    # The only interpolations are this module's own constants, never input.
    return f"INSERT INTO {columns} VALUES {values} ON CONFLICT (code) DO NOTHING"  # noqa: S608


CURRENCY_SEED_STATEMENTS: tuple[str, ...] = (_currency_seed_sql(),)
"""Every statement that fills the currency reference list, in order.

Executed from two places against one definition, exactly as
`accounting.db.triggers.ZERO_SUM_STATEMENTS` and `db.base.UUID7_STATEMENTS`
are: the baseline migration (the real database) and the `after_create` hook
below (the test suite's `Base.metadata.create_all`, which knows nothing about
seed data). Without the hook, every test that writes a row with a `currency`
would fail a foreign key against an empty reference table.

`after_create` on `currencies` specifically — not `before_create` on the
metadata like `uuid7()`, and not `after_create` on the last table. A row
insert is what needs these rows to exist, not a `CREATE TABLE`, and
`metadata.create_all` orders `currencies` before every table that references
it, so the seed lands before anything could possibly reference it.

`ON CONFLICT DO NOTHING` so re-running is a no-op: the test suite's
`create_all` runs against a database the previous run may have left the rows
in, and a future revision re-running this statement must not fail.
"""

for _currency_statement in CURRENCY_SEED_STATEMENTS:
    event.listen(Currency.__table__, "after_create", DDL(_currency_statement))


# Every foreign key in this schema gets its index here rather than on each
# model, so adding a foreign key cannot ship without one. See `db.indexes`.
ensure_foreign_key_indexes(Base.metadata, schema="public")
