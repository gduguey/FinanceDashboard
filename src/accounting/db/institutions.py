"""The reference list of institutions an account can be held at.

`accounts.institution` was free text with no constraint of any kind — the
one string in this schema that both *selects code* and is *typed by a user*.
It picks the CSV standardizer an import runs
(`importers.ingest._STANDARDIZERS` is keyed by `(institution, account_kind)`),
it is a path segment in the raw-statement archive
(`utils.statement_archive`, `statements/{user_id}/{institution}/{account_id}/...`),
and it is whatever the person typed into the account form. So a typo did not
merely look wrong: `"chase "` with a trailing space archived statements to a
second directory and matched no standardizer, and nothing anywhere said so.

## Why a table rather than a CHECK

The vocabulary is **open**. A `CHECK (institution IN (...))` — the shape the
nine `currency` columns used — cannot express it, because the allowed values
are not knowable when the migration runs: half of them are the importers
this repo happens to implement, and the other half are the names of banks and
credit unions this app has never heard of. That is exactly Karwin's
"31 Flavors" argument for a lookup table over an enumeration, and the reason
the audit's move #3 asks for a dimension table here specifically.

What the table buys, given the vocabulary stays open, is that the *same*
spelling is shared: two accounts at Chase now reference one row, so
"institution" is a thing with an identity rather than a string each row
happens to hold its own copy of. The write path creates the row the first
time it sees a name (`db.base.ensure_reference_rows`, called from
`repositories.accounts`) and references it forever after.

## What it does not include

`trades.broker_connections.broker` stays where it is, and is deliberately
*not* folded into this table. The two look alike and are not: `broker` names
an *implemented integration* — it selects a code path
(`trades.brokers.ibkr`) and is the key one user's credentials are stored
under (`trades.broker_credentials._secret_key` builds `broker:{broker}`) — so
its vocabulary is closed by what this repo has actually built, and a value
outside it has no adapter to run. `institution` is open by construction.
Unifying them would either admit "My Local Credit Union" as a broker with no
code behind it, or restrict an account's institution to the two or three
brokers with integrations. Neither is true, so they are two vocabularies.

`trades.dashboard_settings.hysa_bank_id` is not in here either, for a
different reason: its values are apyarchives.com's own bank identifiers, and
the authoritative list of them lives in the scraped rate cache on disk
(`market_data.hysa_rates`), which is global reference data this project
deliberately keeps out of Postgres (see `trades.db.models`). A foreign key
into a table Postgres holds would be claiming authority over a vocabulary
Postgres does not have.
"""

from __future__ import annotations

from sqlalchemy.orm import Mapped, mapped_column

from accounting.db.core import SCHEMA
from db.base import Base, Timestamped


class Institution(Base, Timestamped):
    """One institution an account can be held at — a bank, a broker, or `internal` for a placeholder counterparty.

    `code` is the primary key, and the only column. Two consequences, both
    deliberate:

    - **The referencing column keeps holding the name.** `accounts.institution`
      is the same string it always was, so nothing above the persistence
      boundary changed: `models.Account.institution` is still a `str`,
      `_STANDARDIZERS` is still keyed by it, and the archive path still
      contains it. A surrogate id would have forced a join to recover the
      one piece of information the row contains. Same argument as
      `db.models.Currency`, which spells it out in full.
    - **Nothing else is stored, because nothing else is known.** There is no
      display name, no country, no logo anywhere in this codebase to put
      here — `importers.detect` guesses a code, `taxonomy.default_accounts`
      names `internal`, and the account form takes one string. Inventing
      columns nothing populates would make the table a promise rather than a
      fact.

    No `user_id`: an institution is not one person's data (see
    `db.tenant.is_reference_table`), so this carries no Row-Level Security
    policy and needs no exemption. The trade-off that comes with a shared
    namespace is that the *set* of names is visible across tenants to
    anything that queries the table directly. Nothing does — the app only
    ever writes a name it already has and reads it back off the account —
    and a bank's name is not a financial fact, so one shared row per
    institution is worth more than a private copy per user.
    """

    __tablename__ = "institutions"
    __table_args__ = {"schema": SCHEMA}

    code: Mapped[str] = mapped_column(primary_key=True)
    """The institution's name as every account holding it stores it, e.g. `chase`."""
