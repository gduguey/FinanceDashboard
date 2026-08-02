"""Fixtures shared across `tests/accounting/`.

Two things live here. The first is the R2 guard described below. The second
is `seeded_ledger` and the helpers around it — a ledger built through the
real routes that exercises every stage of `accounting.precedence`, shared by
`test_projection_equivalence.py` and `test_resolution_sources.py` because
both are only as good as the ledger they run over and neither should be
holding its own copy of one.

Guards `accounting.utils.cache_backup` the same way `tests/conftest.py`'s
`_no_r2_by_default` already guards `accounting.utils.statement_archive`:
`CacheBackupR2Credentials` reads `.env` directly (via `model_config`'s
`env_file`), so deleting `R2_*` from `os.environ` alone (which
`_no_r2_by_default` does) isn't enough to stop it resolving this repo's
real R2 credentials — a pydantic-settings model has two independent
sources for a real credential (the environment and its own `.env` file
fallback), and both have to be closed for the same model: clearing the
env var alone still leaves the `.env`-file fallback able to silently
resolve the same real secret, and passing `_env_file=None` alone does
nothing about a value already sitting in `os.environ`. Without this, any
accounting test that exercises `exchange_rates.update_rate_history_cache`/
`load_rate_history` (which call `backup_cache_file`/`restore_cache_file`
with no explicit `credentials`) would silently read from and write to the
real production R2 bucket whenever this developer's own `.env` has R2
configured.

This also redirects the local-disk fallback (`cache_backup._LOCAL_BACKUP_ROOT`)
into each test's own `tmp_path`, so a test that never mentions backups at
all can't leave stray files under this repo's own `data/backups/cache/accounting/`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

import db.models as dbm
from accounting import api as accounting_api
from accounting.config import AccountingConfig
from accounting.models import ManualOverride
from accounting.repositories.interpretation import save_overrides_for_postings
from accounting.utils import cache_backup
from db.session import get_db
from tests.conftest import DEFAULT_USER_ID
from trades import api as trades_api

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

ACCOUNTING = "/api/v1/accounting"


@pytest.fixture(autouse=True)
def _no_cache_backup_r2_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(
        cache_backup, "get_cache_backup_r2_credentials", lambda: cache_backup.CacheBackupR2Credentials(_env_file=None)
    )
    monkeypatch.setattr(cache_backup, "_LOCAL_BACKUP_ROOT", tmp_path / "cache-backups")


@pytest.fixture
def isolated_accounting_config(tmp_path, monkeypatch):
    monkeypatch.setattr(accounting_api.state, "config", AccountingConfig(data_dir=tmp_path))


@pytest.fixture
def _db_for_api(db_session):
    """Route every request through this test's own rolled-back session — see `tests/accounting/api/test_api.py`."""
    db_session.add(dbm.User(id=DEFAULT_USER_ID, email="default@example.com"))
    db_session.commit()

    def _override_get_db():
        yield db_session

    trades_api.app.dependency_overrides[get_db] = _override_get_db
    yield
    trades_api.app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(isolated_accounting_config, _db_for_api):
    """A `TestClient` whose requests reach this test's own session and data directory.

    Both dependencies used to be `autouse` in the one module that had them.
    They are explicit here because this is now the package conftest, and an
    autouse fixture that seeds a user and rebinds `get_db` would reach every
    accounting test rather than the ones that asked for a client.
    """
    return TestClient(trades_api.app)


def _create_account(client: TestClient, **overrides: Any) -> dict:
    payload = {"name": "Test Account", "kind": "checking", "institution": "Chase", "currency": "USD", **overrides}
    response = client.post(f"{ACCOUNTING}/accounts", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _import(client: TestClient, account: dict, rows: str, *, header: str = "Date,Description,Amount") -> None:
    """Import canonical rows. `header` names the optional columns `rows` carries — see `csv.CANONICAL_COLUMNS`."""
    response = client.post(
        f"{ACCOUNTING}/import/canonical",
        files={"file": ("ledger.csv", f"{header}\n{rows}", "text/csv")},
        data={
            "institution": account["institution"],
            "account_kind": account["kind"],
            "account_id": account["account_id"],
            "account_name": account["name"],
        },
    )
    assert response.status_code == 200, response.text


def _postings(client: TestClient) -> list[dict]:
    response = client.get(f"{ACCOUNTING}/postings", params={"limit": 500})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _posting_on(client: TestClient, account_id: str, description_contains: str) -> dict:
    """One resolved posting, named by the account it sits on and part of its description."""
    matches = [
        posting
        for posting in _postings(client)
        if posting["account_id"] == account_id and description_contains in posting["description"]
    ]
    assert len(matches) == 1, f"expected exactly one {description_contains!r} posting on {account_id}, got {matches}"
    return matches[0]


@pytest.fixture
def seeded_ledger(client: TestClient, db_session: Session) -> dict:  # noqa: PLR0914 — one local per overlay it seeds
    """A ledger exercising every overlay stage, built through the real routes.

    Returned as a dict of the ids later cases need, rather than as a set of
    module-level constants, so a case reads as "the thing the fixture made"
    rather than as a string that has to match one.
    """
    checking = _create_account(client, name="Everyday Checking", kind="checking", institution="Chase")
    savings = _create_account(client, name="Rainy Day", kind="savings", institution="Chase")
    groceries = client.post(
        f"{ACCOUNTING}/accounts",
        json={"name": "Corner Shop", "kind": "expense_payee", "institution": "internal", "currency": "USD"},
    ).json()

    first = date(2026, 3, 1)
    _import(
        client,
        checking,
        "".join(
            f"{first + timedelta(days=index)},{description},{amount}\n"
            for index, (description, amount) in enumerate([
                ("CORNER SHOP GROCERIES", "-21.50"),
                ("SALARY MARCH", "3200.00"),
                ("COFFEE HOUSE", "-4.25"),
                ("DUPLICATE CHARGE", "-99.00"),
                ("DUPLICATE CHARGE", "-99.00"),
                ("MOVE TO SAVINGS", "-500.00"),
                ("PHARMACY", "-13.00"),
                ("BOOKSHOP", "-30.00"),
            ])
        ),
    )
    _import(client, savings, f"{first + timedelta(days=5)},MOVE FROM CHECKING,500.00\n")

    # The taxonomy lookup's own input, and the only posting here that has one:
    # a *raw* `postings.category_id`, written by the statement rather than by a
    # user. `apply_category_redirects` rewrites nothing else (see
    # `accounting.db.projection.AFFECTED_TRANSACTIONS`), so without this row
    # every category case below would resolve an empty redirect map and assert
    # nothing about the one stage that reads `categories`.
    _import(
        client,
        checking,
        f"{first + timedelta(days=8)},STATIONERY SHOP,-12.00,Shopping\n",
        header="Date,Description,Amount,Category",
    )

    # `counterparty` — a rule repointing a placeholder at a real, non-importable account.
    rule = client.post(
        f"{ACCOUNTING}/transfer-rules",
        json={"description_contains": "CORNER SHOP", "counterparty_account_id": groceries["account_id"]},
    )
    assert rule.status_code == 201, rule.text

    # `split` — one posting into two independently-categorized legs.
    salary = _posting_on(client, checking["account_id"], "SALARY MARCH")
    split = client.put(
        f"{ACCOUNTING}/postings/{salary['posting_id']}/split",
        json=[
            {"amount": "3000.00", "category_id": "income:salary"},
            {"amount": "200.00", "category_id": "income:reimbursement"},
        ],
    )
    assert split.status_code == 200, split.text

    # `override` — a user's own category, plus a tag set, plus a manual "flag as transfer".
    tag = client.post(f"{ACCOUNTING}/tags", json={"name": "Reviewed"})
    assert tag.status_code == 201, tag.text
    coffee = _posting_on(client, checking["account_id"], "COFFEE HOUSE")
    override = client.put(
        f"{ACCOUNTING}/postings/{coffee['posting_id']}/override",
        json={"category_id": "expense:food-drink", "tag_ids": [tag.json()["tag_id"]]},
    )
    assert override.status_code == 200, override.text

    # A pending suggestion, folded into the same override at the `override`
    # stage. No route stages one without an LLM call, so this is the
    # repository the LLM router itself uses.
    pharmacy = _posting_on(client, checking["account_id"], "PHARMACY")
    save_overrides_for_postings(
        [pharmacy["posting_id"]],
        {pharmacy["posting_id"]: ManualOverride(category_id="expense:health", pending_source="ai")},
        db_session,
        DEFAULT_USER_ID,
    )

    # `merge` — one of the two identical charges dropped, the kept one renamed.
    duplicates = sorted({
        posting["transaction_id"] for posting in _postings(client) if "DUPLICATE CHARGE" in posting["description"]
    })
    assert len(duplicates) == 2
    merge = client.post(
        f"{ACCOUNTING}/posting-merges",
        json={
            "kept_transaction_id": duplicates[0],
            "duplicate_transaction_ids": [duplicates[1]],
            "description": "Charged twice, kept once",
        },
    )
    assert merge.status_code == 201, merge.text

    # `link` — the two sides of one real transfer, confirmed by hand.
    outbound = _posting_on(client, checking["account_id"], "MOVE TO SAVINGS")
    inbound = _posting_on(client, savings["account_id"], "MOVE FROM CHECKING")
    link = client.post(
        f"{ACCOUNTING}/transfer-links",
        json={"transaction_id_a": outbound["transaction_id"], "transaction_id_b": inbound["transaction_id"]},
    )
    assert link.status_code == 201, link.text

    # A live subcategory, and a category on a second posting. Neither retires
    # anything — `apply_category_redirects` has an empty map after this
    # fixture, and the retirement that fills it is
    # `test_projection_equivalence._rename_a_category_into_another`. The
    # subcategory is load-bearing for
    # `test_needs_categorizing_keeps_a_parent_category_with_no_subcategory_picked`,
    # which needs a parent that has one.
    subcategory = client.post(
        f"{ACCOUNTING}/categories/expense:food-drink/subcategories",
        json={"name": "Takeaway", "color": "#8899aa"},
    )
    assert subcategory.status_code == 201, subcategory.text
    bookshop = _posting_on(client, checking["account_id"], "BOOKSHOP")
    client.put(f"{ACCOUNTING}/postings/{bookshop['posting_id']}/override", json={"category_id": "expense:shopping"})

    stationery = _posting_on(client, checking["account_id"], "STATIONERY SHOP")

    return {
        "checking": checking,
        "savings": savings,
        "groceries": groceries,
        "tag_id": tag.json()["tag_id"],
        "rule_id": rule.json()["rule_id"],
        "salary_posting_id": salary["posting_id"],
        "coffee_posting_id": coffee["posting_id"],
        "pharmacy_posting_id": pharmacy["posting_id"],
        "bookshop_posting_id": bookshop["posting_id"],
        # Filed under `expense:shopping` by the statement, not by a user — the
        # raw category the redirect lookup reads. See the import above.
        "stationery_posting_id": stationery["posting_id"],
        "stationery_transaction_id": stationery["transaction_id"],
        "kept_transaction_id": duplicates[0],
        "merged_away_transaction_id": duplicates[1],
        "link_id": link.json()["link_id"],
        "outbound_transaction_id": outbound["transaction_id"],
        "inbound_transaction_id": inbound["transaction_id"],
        "subcategory_id": subcategory.json()["category_id"],
    }
