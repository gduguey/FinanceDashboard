"""The 65,535-bind-parameter ceiling stays dead.

Postgres's wire protocol accepts at most 65,535 bind parameters in one
statement. `IN (:p1, :p2, ...)` renders one per element, so any read that
built such a list from a user's own rows had a hard availability cliff:
past 65,535 rows the driver raised `number of parameters must be between 0
and 65535` and the endpoint returned a 500 rather than merely slowing down
(DB-audit D4, speed-audit S2).

Two tests, and they check genuinely different things:

- `test_the_array_helpers_bind_one_parameter_for_the_whole_list` proves the
  helpers (`db.base.any_uuid`/`any_text`) render a single array parameter.
  Fast, but it only proves the helpers — it would still pass if some call
  site had been missed.
- `test_resolving_postings_survives_more_overlay_rows_than_the_parameter_ceiling`
  proves the *absence of the cliff* end to end, through the real
  `GET /postings` route. That is the one that would have caught the missed
  call site, so it is deliberately not skipped, not marked, and not
  conditional on an environment variable.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import accounting.db as adb
import db.models as dbm
from accounting import api as accounting_api
from accounting.config import AccountingConfig
from db.base import any_text, any_uuid
from db.session import get_db
from tests.conftest import DEFAULT_USER_ID
from trades import api as trades_api

_OVER_THE_CEILING = 65_600
"""Comfortably past 65,535, and cheap enough to seed that this test stays in the normal suite.

The shape is deliberately one transaction with this many legs rather than
this many two-leg transactions. What is under test is the *parameter count*
of the lookups `load_overrides` performs — one per overridden posting — so
the only thing that has to cross the ceiling is the number of distinct
overridden postings. Not also seeding 65,600 transaction rows halves the
write and keeps the whole test a few seconds. The legs sum to zero, so the
deferred balance trigger is satisfied honestly rather than worked around.
"""


@pytest.fixture(autouse=True)
def isolated_accounting_config(tmp_path, monkeypatch):
    monkeypatch.setattr(accounting_api.state, "config", AccountingConfig(data_dir=tmp_path))


@pytest.fixture(autouse=True)
def _db_for_api(db_session):
    """See `test_api.py`'s fixture of the same name — routes every request through this test's own session."""
    db_session.add(dbm.User(id=DEFAULT_USER_ID, email="default@example.com"))
    db_session.commit()

    def _override_get_db():
        yield db_session

    trades_api.app.dependency_overrides[get_db] = _override_get_db
    yield
    trades_api.app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client():
    return TestClient(trades_api.app)


def test_the_array_helpers_bind_one_parameter_for_the_whole_list(db_session) -> None:
    """`any_uuid`/`any_text` compile to `= ANY(:param)` — one parameter however long the list."""
    bind = db_session.get_bind()

    compiled_uuid = any_uuid(adb.Posting.id, [uuid.uuid4() for _ in range(1_000)]).compile(bind)
    assert "ANY" in str(compiled_uuid)
    assert len(compiled_uuid.params) == 1

    compiled_text = any_text(adb.Posting.natural_key, [f"posting:{index}" for index in range(1_000)]).compile(bind)
    assert "ANY" in str(compiled_text)
    assert len(compiled_text.params) == 1


def test_resolving_postings_survives_more_overlay_rows_than_the_parameter_ceiling(db_session, client) -> None:
    """`GET /postings` answers 200 with more posting overrides than Postgres will take parameters for.

    Before the array-parameter fix this raised
    `psycopg.OperationalError: number of parameters must be between 0 and
    65535` inside `db.base.natural_keys_by_id`, reached from
    `repositories.interpretation.load_overrides` — so it took down
    `/postings` *and* every dashboard endpoint for that user.
    """
    parameters = {"user_id": DEFAULT_USER_ID}
    # The account and the transaction go through the ORM, which is where this
    # schema's non-null column defaults live; only the 65,600 rows that have
    # to cross the ceiling are worth writing in bulk SQL.
    db_session.execute(text("INSERT INTO accounting.institutions (code) VALUES ('Ceiling') ON CONFLICT DO NOTHING"))
    account = adb.Account(
        user_id=DEFAULT_USER_ID,
        natural_key="acct:ceiling",
        name="Ceiling",
        kind="checking",
        institution="Ceiling",
        currency="USD",
    )
    transaction = adb.Transaction(
        user_id=DEFAULT_USER_ID,
        natural_key="txn:ceiling",
        posted_at=datetime(2024, 1, 1),
        description="ceiling",
        origin="imported",
    )
    db_session.add_all([account, transaction])
    db_session.flush()
    account_id, transaction_id = account.id, transaction.id
    # Half the legs +1, half -1, so the transaction balances and the deferred
    # `postings_balance_at_commit` trigger is satisfied rather than bypassed.
    db_session.execute(
        text("""
            INSERT INTO accounting.postings (user_id, natural_key, transaction_id, account_id, amount, currency, meta)
            SELECT :user_id, 'posting:ceiling:' || leg, :transaction_id, :account_id,
                   CASE WHEN leg <= :half THEN 1 ELSE -1 END, 'USD', '{}'
            FROM generate_series(1, :legs) AS leg
        """),
        {
            **parameters,
            "transaction_id": transaction_id,
            "account_id": account_id,
            "legs": _OVER_THE_CEILING,
            "half": _OVER_THE_CEILING // 2,
        },
    )
    db_session.execute(
        text("""
            INSERT INTO accounting.posting_overrides (user_id, stage, posting_id, tags_overridden)
            SELECT :user_id, 'override', id, false
            FROM accounting.postings
            WHERE user_id = :user_id AND transaction_id = :transaction_id
        """),
        {**parameters, "transaction_id": transaction_id},
    )
    db_session.commit()

    seeded = db_session.execute(
        text("SELECT count(*) FROM accounting.posting_overrides WHERE user_id = :user_id"), parameters
    ).scalar_one()
    assert seeded > 65_535, "the seed must actually cross the ceiling or this test proves nothing"

    response = client.get("/api/accounting/postings")

    assert response.status_code == 200
    assert len(response.json()) == _OVER_THE_CEILING
