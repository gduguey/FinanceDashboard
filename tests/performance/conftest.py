"""A migrated, bulk-seeded database reached through the restricted `app_runtime` role.

Three things about this fixture set are deliberate, and each of them is the
difference between a gate that measures the product and one that measures a
convenience.

**Migrated, not `create_all`.** `tests/conftest.py`'s `_db_engine` builds its
schema from the ORM metadata, which creates no Row-Level Security policies.
Every real read runs under a policy, and a policy is a predicate the planner
has to satisfy on every table it touches — measuring without them would
measure a query the application never issues.

**Through `app_runtime`, not the owner.** Same reason: the owning role holds
every grant and, more importantly, would need `FORCE ROW LEVEL SECURITY` to
be constrained at all. The API connects as `app_runtime`, so the gate does.

**Seeded with bulk SQL, not through the importer.** The rows are the subject
here, not the code that writes them; going through `POST /import/canonical`
would spend the job's time measuring the importer and make the seed's cost
part of the variance. The shape written below is the shape that importer
produces — two legs per transaction, one on a real account and one on the
`uncategorized:expense` placeholder — because that shape is what
`ledger.categorization` treats as rule-eligible, and rule matching is a large
part of what `GET /postings` costs.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from accounting.taxonomy import UNCATEGORIZED_EXPENSE_ACCOUNT_ID
from db.session import create_one_shot_engine, set_rls_user
from db.settings import AppRuntimeDatabaseSettings, TestDatabaseSettings
from tests.support.scratch_db import apply_migrations, scratch_database

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy import Connection, Engine
    from sqlalchemy.engine import URL

BIG_TENANT_TRANSACTIONS = 10_000
"""The volume the gate measures at.

Not the 170k of the `finance_speed` scratch database that PR 4's audit used.
The failure mode worth catching in CI is a complexity regression — an O(n)
read path becoming O(n²) — and that is already unmistakable at 10k, while
seeding 170k would put minutes into every PR to sharpen a signal that is
already unambiguous. The full-volume harness stays a local tool.
"""

SMALL_TENANT_TRANSACTIONS = 2_000
"""The second tenant, five times smaller, and the reason the gate is not just a stopwatch.

A wall-clock threshold on a shared CI runner is a coin flip at the margins.
The scaling ratio between these two volumes is not: it divides out the
runner's speed entirely. See `test_read_path_latency.py` for how the two
assertions divide the work.
"""

_INSTITUTION = "perf-harness-bank"
_REAL_ACCOUNT_KEY = "acct:perf:checking"


@pytest.fixture(scope="session")
def perf_database() -> Iterator[URL]:
    """A migrated scratch database, dropped when the session ends.

    Yields
    ------
    URL
        The scratch database's URL, as the owning role.
    """
    try:
        configured = TestDatabaseSettings().database_url  # type: ignore[call-arg]
    except Exception:  # noqa: BLE001 — any settings failure means "not configured"
        pytest.skip("DATABASE_URL_TEST is not configured")

    with scratch_database(configured, prefix="perf") as url:
        apply_migrations(url)
        yield url


@pytest.fixture(scope="session")
def app_runtime_engine(perf_database: URL) -> Iterator[Engine]:
    """The seeded database as the restricted role the API itself connects as.

    Yields
    ------
    Engine
    """
    try:
        configured = AppRuntimeDatabaseSettings().database_url  # type: ignore[call-arg]
    except Exception:  # noqa: BLE001 — any settings failure means "not configured"
        pytest.skip("DATABASE_URL_APP is not configured")

    app_url = make_url(configured)
    engine = create_one_shot_engine(perf_database.set(username=app_url.username, password=app_url.password))
    try:
        yield engine
    finally:
        engine.dispose()


def _seed_tenant(connection: Connection, tenant: uuid.UUID, *, transactions: int) -> None:
    """Write one tenant's whole ledger in five statements.

    Two legs per transaction, summing to zero so the deferred
    `postings_balance_at_commit` constraint trigger is satisfied honestly
    rather than disabled. Descriptions cycle through a small vocabulary
    because rule matching is a substring test — every description being
    identical would make it trivially selective, and every description being
    unique would be equally unrepresentative.

    The second leg sits on `uncategorized:expense`, which is a real
    `accounts` row rather than a magic string (`taxonomy.default_accounts`
    creates it for every user). It has to exist and it has to be that
    natural key, because `ledger.categorization` only treats a transaction
    as rule-eligible when it has exactly two legs and exactly one of them is
    a placeholder — seed anything else and the most expensive stage of
    `GET /postings` quietly does nothing.

    Parameters
    ----------
    connection
        A live connection, inside a transaction, already armed with this
        tenant's `app.current_user_id`.
    tenant
        Whose ledger this is.
    transactions
        How many transactions to write.
    """
    connection.execute(
        text("INSERT INTO public.users (id, email, is_active) VALUES (:tenant, :email, true)"),
        {"tenant": tenant, "email": f"{tenant}@perf.test"},
    )
    # `institutions` carries no `user_id`, so it has no policy and both
    # tenants race for the same two rows. `internal` is the one the
    # placeholder accounts belong to, per `taxonomy.default_accounts`.
    connection.execute(
        text("INSERT INTO accounting.institutions (code) VALUES (:bank), ('internal') ON CONFLICT DO NOTHING"),
        {"bank": _INSTITUTION},
    )
    connection.execute(
        text("""
            INSERT INTO accounting.accounts
                (user_id, natural_key, name, kind, institution, currency, meta, closed)
            VALUES
                (:tenant, :real_key, 'Perf Checking', 'checking', :bank, 'USD', '{}'::jsonb, false),
                (:tenant, :placeholder_key, 'Uncategorized Expense', 'expense_payee', 'internal',
                 'USD', '{}'::jsonb, false)
        """),
        {
            "tenant": tenant,
            "real_key": _REAL_ACCOUNT_KEY,
            "placeholder_key": UNCATEGORIZED_EXPENSE_ACCOUNT_ID,
            "bank": _INSTITUTION,
        },
    )
    connection.execute(
        text("""
            INSERT INTO accounting.transactions (user_id, natural_key, posted_at, description, origin)
            SELECT :tenant,
                   'txn:perf:' || n,
                   TIMESTAMP '2020-01-01 00:00:00' + (n * INTERVAL '37 minutes'),
                   (ARRAY['TESCO STORES', 'SHELL OIL', 'AMZN MKTP', 'UBER TRIP', 'RENT PAYMENT',
                          'COFFEE HOUSE', 'PHARMACY', 'GYM MEMBERSHIP'])[1 + (n % 8)] || ' #' || n,
                   'imported'
            FROM generate_series(1, :transactions) AS n
        """),
        {"tenant": tenant, "transactions": transactions},
    )
    connection.execute(
        text("""
            INSERT INTO accounting.postings
                (user_id, natural_key, transaction_id, account_id, amount, currency, meta)
            SELECT t.user_id,
                   'posting:perf:' || side.leg || ':' || t.natural_key,
                   t.id,
                   CASE WHEN side.leg = 0 THEN real_account.id ELSE placeholder.id END,
                   CASE WHEN side.leg = 0 THEN -amount.cents ELSE amount.cents END,
                   'USD',
                   '{}'::jsonb
            FROM accounting.transactions AS t
            CROSS JOIN generate_series(0, 1) AS side(leg)
            CROSS JOIN LATERAL (SELECT (10 + (length(t.description) * 7) % 90)::numeric AS cents) AS amount
            JOIN accounting.accounts AS real_account
              ON real_account.user_id = t.user_id AND real_account.natural_key = :real_key
            JOIN accounting.accounts AS placeholder
              ON placeholder.user_id = t.user_id AND placeholder.natural_key = :placeholder_key
            WHERE t.user_id = :tenant
        """),
        {"tenant": tenant, "real_key": _REAL_ACCOUNT_KEY, "placeholder_key": UNCATEGORIZED_EXPENSE_ACCOUNT_ID},
    )


@pytest.fixture(scope="session")
def tenants(app_runtime_engine: Engine) -> dict[str, uuid.UUID]:
    """Two tenants in one database, five times apart in size.

    Seeded as `app_runtime` itself, the same way
    `tests/db/test_rls_isolation.py` does: if a policy's `WITH CHECK`
    rejected a tenant writing its own rows, a seed run as the owner would
    hide that and the gate would time an empty database.

    Parameters
    ----------
    app_runtime_engine
        The restricted-role engine.

    Returns
    -------
    dict[str, uuid.UUID]
        `"big"` and `"small"`.
    """
    big, small = uuid.uuid4(), uuid.uuid4()
    for tenant, transactions in ((big, BIG_TENANT_TRANSACTIONS), (small, SMALL_TENANT_TRANSACTIONS)):
        with app_runtime_engine.begin() as connection:
            connection.execute(text("SELECT set_config('app.current_user_id', :value, false)"), {"value": str(tenant)})
            _seed_tenant(connection, tenant, transactions=transactions)
    # Planner statistics on ten thousand freshly-inserted rows are whatever
    # autovacuum has got round to, which on a CI runner is usually nothing.
    # Timing a plan chosen from empty statistics measures the absence of a
    # vacuum rather than the query.
    with app_runtime_engine.connect() as connection:
        connection.execution_options(isolation_level="AUTOCOMMIT").execute(text("ANALYZE"))
    return {"big": big, "small": small}


@pytest.fixture
def request_as(app_runtime_engine: Engine, tenants: dict[str, uuid.UUID]) -> Iterator[Callable[[str], TestClient]]:
    """Build a `TestClient` whose requests run as one of the two tenants, over `app_runtime`.

    The `get_db` override mirrors `db.session.get_db` itself rather than
    handing back a long-lived session: a new session per request, armed with
    `set_rls_user`, which is also what applies the production statement
    timeout. A gate that measured requests exempt from that bound would be
    measuring something the app cannot do.

    Yields
    ------
    collections.abc.Callable
        `request_as("big")` -> a `TestClient`.
    """
    from db.current_user import get_current_user_id  # noqa: PLC0415 — keeps this module importable without `api`
    from db.session import get_db  # noqa: PLC0415
    from trades import api as trades_api  # noqa: PLC0415

    sessions: list[Session] = []

    def _client(size: str) -> TestClient:
        user_id = tenants[size]

        def _override_get_db() -> Iterator[Session]:
            session = Session(bind=app_runtime_engine)
            sessions.append(session)
            set_rls_user(session, user_id)
            yield session

        trades_api.app.dependency_overrides[get_db] = _override_get_db
        # `tests/conftest.py`'s `_bypass_clerk_auth_by_default` pins this to
        # `DEFAULT_USER_ID` for the whole suite. Left alone, the handler would
        # ask for one user's ledger over a session armed for another and
        # measure an empty result.
        trades_api.app.dependency_overrides[get_current_user_id] = lambda: user_id
        return TestClient(trades_api.app)

    yield _client
    trades_api.app.dependency_overrides.pop(get_db, None)
    trades_api.app.dependency_overrides.pop(get_current_user_id, None)
    for session in sessions:
        session.close()
