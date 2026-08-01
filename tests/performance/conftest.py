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
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import polars as pl
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
REAL_ACCOUNT_KEY = "acct:perf:checking"
_EUR_ACCOUNT_KEY = "acct:perf:eur"

_RATE_HISTORY_START = date(2019, 1, 1)
"""First day of the seeded exchange-rate cache — a year before the seeded ledger's own first transaction."""


@pytest.fixture(scope="session", autouse=True)
def _seeded_exchange_rates(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point the running app at a full daily EUR rate history, so the per-date FX join is actually measured.

    The seed carries a EUR account (with no postings of its own), which is
    all it takes for `_currencies_in_use` to report two currencies and for
    every income-statement request to build a per-date rate table and join
    every posting against it. Without both halves the flow aggregations take
    the single-currency shortcut, and a latency gate over them would be
    watching a branch the code never enters.

    Daily rather than weekday-only: this is the pessimistic shape for the
    rolling window `smoothed_rate_series` computes per request.

    Yields
    ------
    None
    """
    from accounting.api import dependencies as accounting_dependencies  # noqa: PLC0415
    from accounting.config import AccountingConfig  # noqa: PLC0415
    from accounting.market_data.exchange_rates import RATE_HISTORY_SCHEMA  # noqa: PLC0415
    from accounting.utils.io_utils import write_csv_atomic  # noqa: PLC0415

    config = AccountingConfig(data_dir=tmp_path_factory.mktemp("perf-rates"))
    days = (datetime.now(tz=UTC).date() - _RATE_HISTORY_START).days + 1
    history = pl.DataFrame(
        {
            "date": [_RATE_HISTORY_START + timedelta(days=offset) for offset in range(days)],
            "currency": ["EUR"] * days,
            "rate_to_base": [1.05 + (offset % 100) / 1000 for offset in range(days)],
        },
        schema=RATE_HISTORY_SCHEMA,
    )
    write_csv_atomic(history, config.exchange_rates_csv_path)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(accounting_dependencies.state, "config", config)
        yield


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
                (:tenant, :eur_key, 'Perf EUR Checking', 'checking', :bank, 'EUR', '{}'::jsonb, false),
                (:tenant, :placeholder_key, 'Uncategorized Expense', 'expense_payee', 'internal',
                 'USD', '{}'::jsonb, false)
        """),
        {
            "tenant": tenant,
            "real_key": REAL_ACCOUNT_KEY,
            # Empty, and there on purpose: a second currency in use is what
            # puts every income-statement read through the per-date rate
            # join rather than the single-currency shortcut around it.
            "eur_key": _EUR_ACCOUNT_KEY,
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
        {"tenant": tenant, "real_key": REAL_ACCOUNT_KEY, "placeholder_key": UNCATEGORIZED_EXPENSE_ACCOUNT_ID},
    )


_ANALYZED_TABLES = ("postings", "transactions", "resolved_postings")
"""The three relations every threshold in this module depends on the planner having statistics for."""


def _fill_the_projection(app_runtime_engine: Engine, tenants: dict[str, uuid.UUID]) -> None:
    """Build each tenant's resolved projection before the gate measures a read of it.

    The bulk seed fires the staleness triggers, so every seeded transaction
    lands in the dirty queue and the *first* request would otherwise pay for
    a whole rebuild. That is a real cost and it has its own case
    (`test_a_cold_projection_is_rebuilt_in_proportion_to_the_ledger`), but it
    is not what the paged-read thresholds are about — a gate that measured it
    inside every other case would be timing a cache fill and calling it a
    page.

    Run through `repositories.projection.rebuild` rather than by issuing a
    request, so the fill is not itself one of the measurements, and as
    `app_runtime` so the rows are written under the policy that will read
    them back.
    """
    from accounting.repositories.projection import rebuild  # noqa: PLC0415 — keeps this module importable without `api`

    for tenant in tenants.values():
        with Session(bind=app_runtime_engine) as session:
            set_rls_user(session, tenant)
            rebuild(session, tenant)


def _analyze_as_owner(perf_database: URL) -> None:
    """`ANALYZE` the seeded database over the owning role, and prove it actually happened.

    Split out from `tenants` because the assertion is the point. `ANALYZE`
    reports success whether or not it analyzed anything: a role without
    ownership gets a `WARNING` per table and an empty `pg_statistic`, which is
    indistinguishable from a working call unless something checks. This gate
    ran that way from the day it landed until C6 was diagnosed, so the check
    stays even though the role is now correct — a permission change that
    quietly re-broke it would otherwise cost another investigation.

    Parameters
    ----------
    perf_database
        The scratch database's URL, as the role that owns it.

    Raises
    ------
    RuntimeError
        If either relation still has no rows in `pg_statistic` afterwards.
    """
    engine = create_one_shot_engine(perf_database)
    try:
        with engine.connect() as connection:
            connection.execution_options(isolation_level="AUTOCOMMIT").execute(text("ANALYZE"))
            missing = [
                table
                for table in _ANALYZED_TABLES
                if not connection.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_statistic WHERE starelid = "
                        "(quote_ident('accounting') || '.' || quote_ident(:table))::regclass)"
                    ),
                    {"table": table},
                ).scalar_one()
            ]
        if missing:
            message = f"ANALYZE left {', '.join(missing)} without statistics; every threshold here would time a guess"
            raise RuntimeError(message)
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def tenants(app_runtime_engine: Engine, perf_database: URL) -> dict[str, uuid.UUID]:
    """Two tenants in one database, five times apart in size.

    Seeded as `app_runtime` itself, the same way
    `tests/db/test_rls_isolation.py` does: if a policy's `WITH CHECK`
    rejected a tenant writing its own rows, a seed run as the owner would
    hide that and the gate would time an empty database.

    Parameters
    ----------
    app_runtime_engine
        The restricted-role engine, which the rows are written over.
    perf_database
        The same database as the owning role, which is the only role that can
        `ANALYZE` it — see `_analyze_as_owner`.

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
    #
    # Run as the *owning* role, not as `app_runtime`. `ANALYZE` silently skips
    # any table the caller does not own — it emits
    # `WARNING: permission denied to analyze "postings", skipping it` and
    # returns success — so issuing it over the restricted engine left
    # `pg_statistic` empty and `last_analyze` null, and this gate spent every
    # run since it landed racing autovacuum for whether it measured a planned
    # query or an unplanned one. That is what produced C6's "cliff between
    # limit=300 and limit=400": not a page size, but whichever side of that
    # race the runner happened to land on (B5).
    tenants = {"big": big, "small": small}
    # Before `ANALYZE`, so the projection's own statistics are built from the
    # rows the gate will actually read rather than from an empty table.
    _fill_the_projection(app_runtime_engine, tenants)
    _analyze_as_owner(perf_database)
    return tenants


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
