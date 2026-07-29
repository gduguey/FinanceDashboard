"""`GET /postings` pages by transaction — see `api.api_models.PostingPage`.

Everything here is about the page window itself: what a client gets when it
asks for no particular size, what happens to a `limit` past the cap or below
the floor, and the two properties the Transactions page's paging loop
depends on — that paging through the collection sees every posting exactly
once, and that a page holds every leg of every transaction it covers.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import db.models as dbm
from accounting import api as accounting_api
from accounting.api.api_models import PAGE_LIMIT_DEFAULT, PAGE_LIMIT_MAX
from accounting.config import AccountingConfig
from db.session import get_db
from tests.conftest import DEFAULT_USER_ID
from trades import api as trades_api


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


def _page(client, **params) -> dict:
    """One whole `PostingPage` envelope from `GET /postings`, window fields included."""
    response = client.get("/api/v1/accounting/postings", params=params)
    assert response.status_code == 200
    return response.json()


def _transaction_ids(items: list[dict]) -> set[str]:
    """Which transactions `items` covers, each named once."""
    return {posting["transaction_id"] for posting in items}


def _transactions_newest_first(items: list[dict]) -> list[str]:
    """The transactions `items` covers, ordered the way pages are cut — newest `posted_at` first.

    Deliberately not the order `items` itself arrives in: a page's rows come
    back oldest-first (`repositories.ledger.ledger_rows_to_frame` sorts the
    frame), while the page *window* is taken newest-first.
    """
    ordered = sorted({(posting["posted_at"], posting["transaction_id"]) for posting in items}, reverse=True)
    return [transaction_id for _posted_at, transaction_id in ordered]


def _seed_transactions(client, count: int) -> str:
    """Import `count` distinct one-row transactions, oldest first, and return the account they're in.

    Every row gets its own date and amount so no two collapse into one
    transaction, and each yields two postings — the real leg plus the
    uncategorized placeholder — which is what makes `total` (transactions)
    and `len(items)` (postings) genuinely different numbers.
    """
    account = client.post(
        "/api/v1/accounting/accounts",
        json={"name": "Generic Checking", "kind": "checking", "institution": "Generic Bank", "currency": "USD"},
    ).json()
    rows = "".join(
        f"{date(2026, 1, 1) + timedelta(days=index)},Purchase {index},-{index + 1}.00\n" for index in range(count)
    )
    response = client.post(
        "/api/v1/accounting/import/canonical",
        files={"file": ("generic.csv", f"Date,Description,Amount\n{rows}", "text/csv")},
        data={
            "institution": "Generic Bank",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Generic Checking",
        },
    )
    assert response.status_code == 200
    assert response.json()["new_posting_count"] == 2 * count
    return account["account_id"]


def test_omitting_limit_returns_the_default_page_size(client) -> None:
    _seed_transactions(client, PAGE_LIMIT_DEFAULT + 10)

    page = _page(client)

    assert page["limit"] == PAGE_LIMIT_DEFAULT
    assert page["offset"] == 0
    assert page["total"] == PAGE_LIMIT_DEFAULT + 10
    assert len(_transaction_ids(page["items"])) == PAGE_LIMIT_DEFAULT
    # The newest `PAGE_LIMIT_DEFAULT` of the 210 seeded days, so the ten oldest are off the page.
    assert min(posting["posted_at"] for posting in page["items"]).startswith("2026-01-11")


def test_a_limit_above_the_cap_is_clamped_rather_than_rejected(client) -> None:
    """A client asking for more than the server will give is asking for as much as possible, not erroring."""
    _seed_transactions(client, 3)

    page = _page(client, limit=PAGE_LIMIT_MAX + 1)

    assert page["limit"] == PAGE_LIMIT_MAX
    assert page["total"] == 3
    assert len(_transaction_ids(page["items"])) == 3


def test_a_limit_below_one_is_rejected(client) -> None:
    _seed_transactions(client, 3)

    assert client.get("/api/v1/accounting/postings", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/accounting/postings", params={"limit": -1}).status_code == 422


def test_a_negative_offset_is_rejected(client) -> None:
    _seed_transactions(client, 3)

    assert client.get("/api/v1/accounting/postings", params={"offset": -1}).status_code == 422
    assert _page(client, offset=0)["offset"] == 0


def test_total_counts_transactions_and_does_not_move_with_the_page_window(client) -> None:
    _seed_transactions(client, 6)

    whole_ledger = _page(client, limit=PAGE_LIMIT_MAX)
    assert whole_ledger["total"] == 6
    assert len(whole_ledger["items"]) == 12  # two legs per transaction — `total` is not a posting count

    assert _page(client, limit=2)["total"] == 6
    assert _page(client, limit=2, offset=4)["total"] == 6
    past_the_end = _page(client, offset=6)
    assert past_the_end["total"] == 6
    assert past_the_end["items"] == []


def test_a_page_still_holds_the_transactions_asked_for_after_a_merge(client) -> None:
    """A merged-away duplicate is excluded before the `LIMIT`, not filtered out of the page afterwards.

    Filtering after the cut would hand back one transaction fewer than asked
    for and skew every later offset — see
    `repositories.ledger.visible_transaction_page`.
    """
    _seed_transactions(client, 12)
    newest_first = _transactions_newest_first(_page(client, limit=PAGE_LIMIT_MAX)["items"])
    kept, duplicate = newest_first[0], newest_first[1]

    response = client.post(
        "/api/v1/accounting/posting-merges",
        json={"kept_transaction_id": kept, "duplicate_transaction_ids": [duplicate]},
    )
    assert response.status_code == 200

    page = _page(client, limit=3)
    assert page["total"] == 11
    # The page slid onto the next live transaction rather than coming back short.
    assert _transactions_newest_first(page["items"]) == [kept, newest_first[2], newest_first[3]]
    assert duplicate not in _transaction_ids(_page(client, limit=PAGE_LIMIT_MAX)["items"])


def test_paging_with_a_small_limit_sees_the_same_postings_as_one_big_page(client) -> None:
    """The property the frontend's paging loop rests on: every posting exactly once, none twice."""
    _seed_transactions(client, 23)
    one_big_page = _page(client, limit=PAGE_LIMIT_MAX)

    paged: list[dict] = []
    offset = 0
    while offset < one_big_page["total"]:
        page = _page(client, limit=5, offset=offset)
        assert page["total"] == one_big_page["total"]
        paged.extend(page["items"])
        offset += page["limit"]

    posting_ids = [posting["posting_id"] for posting in paged]
    assert len(posting_ids) == len(set(posting_ids))
    assert set(posting_ids) == {posting["posting_id"] for posting in one_big_page["items"]}


def test_every_leg_of_a_transaction_lands_on_the_same_page(client) -> None:
    """Pages are cut by transaction, so no page ever holds part of one — including a split's extra legs."""
    account_id = _seed_transactions(client, 7)
    real_leg = next(
        posting for posting in _page(client, limit=PAGE_LIMIT_MAX)["items"] if posting["account_id"] == account_id
    )
    split_response = client.put(
        f"/api/v1/accounting/postings/{real_leg['posting_id']}/split",
        json=[{"amount": real_leg["amount"] + 1.0}, {"amount": -1.0}],
    )
    assert split_response.status_code == 200

    whole_ledger = _page(client, limit=PAGE_LIMIT_MAX)
    legs_per_transaction = Counter(posting["transaction_id"] for posting in whole_ledger["items"])
    assert max(legs_per_transaction.values()) == 3  # the split transaction, or this proves nothing

    for offset in range(whole_ledger["total"]):
        items = _page(client, limit=1, offset=offset)["items"]
        transaction_ids = {posting["transaction_id"] for posting in items}
        assert len(transaction_ids) == 1
        assert len(items) == legs_per_transaction[transaction_ids.pop()]
