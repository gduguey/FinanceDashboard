"""The projection equals a fresh pipeline run — after every write that can change what a posting resolves to.

This is the assertion the whole resolved-projection design rests on. A cache
of `ledger.resolution` that silently disagrees with it produces a filtered
list that does not match the screen, and nothing else in the system would
notice. So one property is asserted, over and over, against a ledger that
exercises every overlay stage:

    every stored row == `resolved_display_rows(resolve_postings(...))`

field for field, with no tolerance and no subset comparison.

Two things make that meaningful rather than decorative.

**The mutations go through the real routes.** Each case below calls the
endpoint a user's click would, so the write path under test is the one
production takes — including the ones that issue raw SQL, the ones that go
through `session.query(...).delete()`, and the ones that commit twice. A
test that wrote overlay rows directly would prove the recompute works and
say nothing about whether the trigger fires.

**The fixture covers every stage.** `seeded_ledger` builds a ledger with a
rule-repointed counterparty, a split, a manual override, a pending
suggestion, a merged-away duplicate, a manual transfer link, a
rule-found link, a retired category, tags on both a posting and an override,
and a manual-origin transaction. `test_the_fixture_exercises_every_overlay`
holds that claim, so the coverage cannot quietly erode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from sqlalchemy.orm import Session

import accounting.db as adb
from accounting.importers.ingest import load_ledger
from accounting.repositories.projection import drain, fresh_display_rows, rebuild
from db.money import quantize_money
from tests.accounting.conftest import ACCOUNTING, _create_account, _import, _posting_on, _postings
from tests.conftest import DEFAULT_USER_ID

if TYPE_CHECKING:
    import uuid


_COMPARED_FIELDS = (
    "posting_id",
    "transaction_id",
    "account_id",
    "posted_at",
    "amount",
    "currency",
    "category_id",
    "subcategory_id",
    "budget_id",
    "tag_ids",
    "description",
    "pending_source",
    "pending_selected",
    "resolved_by_transfer_rule_id",
    "manual_transfer_override_posting_id",
    "is_linked_transfer",
    "linked_transaction_id",
    "transfer_link_source",
    "is_real_income_expense",
    "is_excluded_from_rule",
)
"""Every field compared, which is every column the projection stores bar its two internal keys.

`transaction_row_id` is a storage key rather than a resolved value, and
`meta` is passed through untouched by every stage — but both are covered
anyway by `test_the_projection_stores_the_same_columns_the_row_shape_carries`
below, which asserts this list against the model rather than letting it
quietly fall behind.
"""


def _normalised(row: dict[str, Any]) -> tuple:
    """One row reduced to the values equality is meaningful over.

    Two of them are normalised first — see `_comparable`.

    Returns
    -------
    tuple
    """
    return tuple(_comparable(field, row[field]) for field in _COMPARED_FIELDS)


def _comparable(field: str, value: Any) -> Any:
    """One field reduced to the form equality is meaningful over.

    Two fields need it. `amount` crosses the T1 boundary in both directions —
    stored as `NUMERIC(18, 4)`, resolved as the float `ledger.frame`
    sanctions — so both sides are quantized to the storage scale rather than
    compared as float against Decimal. That is not a tolerance: it is the same
    rounding the write itself applies, so a genuinely different amount still
    fails.

    `tag_ids` is sorted because the two sides order it differently and neither
    order is wrong: the raw ledger path sorts by tag natural key inside its
    `array_agg`, while an override's tags come back in whatever order
    `PostingOverrideTag` rows are read. A tag *set* is what both mean.

    Returns
    -------
    Any
    """
    if field == "amount":
        return quantize_money(value)
    if field == "tag_ids":
        return sorted(value or ())
    return value


def _stored_rows(session: Session, user_id: uuid.UUID) -> list[tuple]:
    """The projection as stored, ordered so a comparison is order-independent.

    Returns
    -------
    list[tuple]
    """
    rows = session.query(adb.ResolvedPosting).filter_by(user_id=user_id).all()
    return sorted(_normalised({field: getattr(row, field) for field in _COMPARED_FIELDS}) for row in rows)


def _pipeline_rows(session: Session, user_id: uuid.UUID) -> list[tuple]:
    """What the projection is supposed to equal, computed with no cache involved.

    Returns
    -------
    list[tuple]
    """
    return sorted(_normalised(row) for row in fresh_display_rows(session, user_id))


def assert_projection_equals_the_pipeline(session: Session, user_id: uuid.UUID = DEFAULT_USER_ID) -> None:
    """Drain, then assert the stored projection is exactly a fresh pipeline run.

    Draining first is the contract, not a convenience: `drain` is what every
    read that trusts the projection calls, so asserting without it would be
    asserting about a state no reader ever observes.
    """
    drain(session, user_id)
    stored = _stored_rows(session, user_id)
    pipeline = _pipeline_rows(session, user_id)
    assert stored == pipeline, (
        f"the projection holds {len(stored)} rows and the pipeline resolves {len(pipeline)}; "
        f"first difference: {next((pair for pair in zip(stored, pipeline, strict=False) if pair[0] != pair[1]), None)}"
    )


def test_the_projection_stores_the_same_columns_the_row_shape_carries(seeded_ledger, db_session) -> None:
    """`_COMPARED_FIELDS` is every stored column bar the two this test names, so it cannot fall behind."""
    stored_columns = {column.name for column in adb.ResolvedPosting.__table__.columns}
    assert stored_columns - set(_COMPARED_FIELDS) == {
        "user_id",
        "transaction_row_id",
        "meta",
        "created_at",
        "updated_at",
    }


def test_the_fixture_exercises_every_overlay(seeded_ledger, client, db_session) -> None:
    """Guard the guard: every case below is meaningless if the seed did not land.

    Each assertion names one stage of `precedence.OVERLAY_PRECEDENCE`, plus
    the taxonomy lookup that runs before them, so a fixture that silently
    stopped producing (say) a split would fail here rather than making
    twenty equivalence assertions pass over a simpler ledger than they claim.
    """
    postings = _postings(client)

    assert any(posting["account_id"] == seeded_ledger["groceries"]["account_id"] for posting in postings), (
        "counterparty: no posting was repointed by the rule"
    )
    assert any(":split:" in posting["posting_id"] for posting in postings), "split: no split leg in the ledger"
    assert any(posting["tag_ids"] for posting in postings), "override: no overridden tag set"
    assert any(posting["pending_source"] == "ai" for posting in postings), "override: no pending suggestion"
    assert all(posting["transaction_id"] != seeded_ledger["merged_away_transaction_id"] for posting in postings), (
        "merge: the duplicate transaction is still in the resolved ledger"
    )
    assert any(posting["is_linked_transfer"] for posting in postings), "link: no linked transfer"
    # Read off the pipeline rather than the wire: these two are resolved
    # values the projection filters on, and `GET /postings` has no reason to
    # return either.
    resolved = fresh_display_rows(db_session, DEFAULT_USER_ID)
    assert any(row["is_real_income_expense"] for row in resolved), "no real income/expense leg"
    assert any(row["category_id"] == "expense:shopping" for row in resolved), "no overridden category to redirect"
    # The taxonomy lookup, read off the *raw* ledger rather than the resolved
    # one. `apply_category_redirects` only ever rewrites a posting whose stored
    # `category_id` is a retired key, so an override carrying the same value
    # says nothing about it — and until this fixture imported a categorized
    # statement, every category case below passed over an empty redirect map.
    raw = load_ledger(db_session, DEFAULT_USER_ID)
    assert "expense:shopping" in raw["category_id"].to_list(), (
        "taxonomy: no posting is filed under a raw category, so `apply_category_redirects` has nothing to redirect"
    )


def test_a_cold_projection_matches_the_pipeline(seeded_ledger, db_session) -> None:
    """The base case: nothing has been drained yet, and the first drain has to produce the whole answer."""
    assert_projection_equals_the_pipeline(db_session)


def test_a_rebuild_matches_the_pipeline(seeded_ledger, db_session) -> None:
    """`rebuild` ignores the queue entirely, so this separates "recompute is right" from "invalidation is right"."""
    rebuild(db_session, DEFAULT_USER_ID)
    assert _stored_rows(db_session, DEFAULT_USER_ID) == _pipeline_rows(db_session, DEFAULT_USER_ID)


def test_draining_twice_changes_nothing(seeded_ledger, db_session) -> None:
    """A read after a read does no work — the property that makes drain-on-read affordable."""
    drain(db_session, DEFAULT_USER_ID)
    assert drain(db_session, DEFAULT_USER_ID) == 0


# --------------------------------------------------------------------------
# One case per write path that can change what a posting resolves to.
#
# Each is a `(name, mutate)` pair: `mutate` performs the write through the
# route a user's click takes, and the shared body then asserts the projection
# still equals the pipeline. Adding a write path means adding a line here —
# and if one is forgotten, the guard is
# `tests/accounting/test_resolution_sources.py::test_a_real_resolution_reads_no_table_outside_the_declaration`
# — which watches which tables a resolution *reads* rather than which this
# battery writes to, so it catches an undeclared input even when no case here
# exercises the write that changes it.
# --------------------------------------------------------------------------


def _set_a_category(client, seeded, _session):
    posting = _posting_on(client, seeded["checking"]["account_id"], "BOOKSHOP")
    assert (
        client.put(
            f"{ACCOUNTING}/postings/{posting['posting_id']}/override", json={"category_id": "expense:home-housing"}
        ).status_code
        == 200
    )


def _clear_a_category(client, seeded, _session):
    assert (
        client.put(
            f"{ACCOUNTING}/postings/{seeded['coffee_posting_id']}/override", json={"category_id": None}
        ).status_code
        == 200
    )


def _flag_a_transfer_manually(client, seeded, _session):
    placeholder = next(
        posting
        for posting in _postings(client)
        if posting["account_id"] == "uncategorized:expense" and "PHARMACY" in posting["description"]
    )
    assert (
        client.put(
            f"{ACCOUNTING}/postings/{placeholder['posting_id']}/override",
            json={"account_id": seeded["savings"]["account_id"]},
        ).status_code
        == 200
    )


def _tag_a_posting(client, seeded, _session):
    assert (
        client.put(
            f"{ACCOUNTING}/postings/{seeded['bookshop_posting_id']}/override", json={"tag_ids": [seeded["tag_id"]]}
        ).status_code
        == 200
    )


def _split_a_posting(client, seeded, _session):
    posting = _posting_on(client, seeded["checking"]["account_id"], "BOOKSHOP")
    assert (
        client.put(
            f"{ACCOUNTING}/postings/{posting['posting_id']}/split",
            json=[
                {"amount": "-20.00", "category_id": "expense:home-housing"},
                {"amount": "-10.00", "category_id": None},
            ],
        ).status_code
        == 200
    )


def _undo_a_split(client, seeded, _session):
    assert client.delete(f"{ACCOUNTING}/postings/{seeded['salary_posting_id']}/split").status_code == 204


def _record_a_merge(client, seeded, _session):
    coffee = _posting_on(client, seeded["checking"]["account_id"], "COFFEE HOUSE")
    bookshop = _posting_on(client, seeded["checking"]["account_id"], "BOOKSHOP")
    assert (
        client.post(
            f"{ACCOUNTING}/posting-merges",
            json={
                "kept_transaction_id": coffee["transaction_id"],
                "duplicate_transaction_ids": [bookshop["transaction_id"]],
                "description": "One event after all",
            },
        ).status_code
        == 201
    )


def _undo_a_merge(client, seeded, _session):
    assert client.delete(f"{ACCOUNTING}/posting-merges/merge:{seeded['kept_transaction_id']}").status_code == 204


def _confirm_a_link(client, seeded, _session):
    coffee = _posting_on(client, seeded["checking"]["account_id"], "COFFEE HOUSE")
    bookshop = _posting_on(client, seeded["checking"]["account_id"], "BOOKSHOP")
    assert (
        client.post(
            f"{ACCOUNTING}/transfer-links",
            json={
                "transaction_id_a": coffee["transaction_id"],
                "transaction_id_b": bookshop["transaction_id"],
            },
        ).status_code
        == 201
    )


def _undo_a_link(client, seeded, _session):
    assert client.delete(f"{ACCOUNTING}/transfer-links/{seeded['link_id']}").status_code == 204


def _add_a_rule(client, seeded, _session):
    assert (
        client.post(
            f"{ACCOUNTING}/transfer-rules",
            json={"description_contains": "BOOKSHOP", "counterparty_account_id": seeded["groceries"]["account_id"]},
        ).status_code
        == 201
    )


def _patch_rule(client, rule_id: str, **changes) -> None:
    """`PATCH /transfer-rules/{id}` replaces the whole rule, guarded by the version the client last saw."""
    rule = client.get(f"{ACCOUNTING}/transfer-rules/{rule_id}").json()
    body = {
        "description_contains": rule["description_contains"],
        "account_id": rule["account_id"],
        "counterparty_account_id": rule["counterparty_account_id"],
        "priority": rule["priority"],
        "description": rule["description"],
        "active": rule["active"],
        "excluded_transaction_ids": rule["excluded_transaction_ids"],
        "expected_version": rule["version"],
        **changes,
    }
    response = client.patch(f"{ACCOUNTING}/transfer-rules/{rule_id}", json=body)
    assert response.status_code == 200, response.text


def _edit_a_rule(client, seeded, _session):
    _patch_rule(client, seeded["rule_id"], description_contains="COFFEE")


def _exclude_a_transaction_from_a_rule(client, seeded, _session):
    groceries = _posting_on(client, seeded["groceries"]["account_id"], "CORNER SHOP")
    _patch_rule(client, seeded["rule_id"], excluded_transaction_ids=[groceries["transaction_id"]])


def _delete_a_rule(client, seeded, _session):
    """Two commits in one request — the rule row, then the links it created."""
    assert client.delete(f"{ACCOUNTING}/transfer-rules/{seeded['rule_id']}").status_code == 204


def _rename_a_category_into_another(client, _seeded, _session):
    """A merge: `expense:shopping` folds into `expense:home`, retiring the merged-away row."""
    home = client.get(f"{ACCOUNTING}/categories/expense:home-housing").json()
    assert (
        client.post(f"{ACCOUNTING}/categories/expense:shopping/rename", json={"name": home["name"]}).status_code == 200
    )


def _delete_a_category(client, _seeded, _session):
    assert client.delete(f"{ACCOUNTING}/categories/expense:shopping").status_code == 200


def _add_a_subcategory(client, _seeded, _session):
    assert (
        client.post(
            f"{ACCOUNTING}/categories/expense:home-housing/subcategories", json={"name": "Repairs", "color": "#334455"}
        ).status_code
        == 201
    )


def _rename_a_tag(client, seeded, _session):
    """A merge: the second tag folds into the first, repointing the join rows and pruning the merged-away tag."""
    second = client.post(f"{ACCOUNTING}/tags", json={"name": "Checked"}).json()
    assert client.post(f"{ACCOUNTING}/tags/{second['tag_id']}/rename", json={"name": "Reviewed"}).status_code == 200


def _delete_a_tag(client, seeded, _session):
    assert client.delete(f"{ACCOUNTING}/tags/{seeded['tag_id']}").status_code == 204


def _rename_an_account(client, seeded, _session):
    account = client.get(f"{ACCOUNTING}/accounts/{seeded['checking']['account_id']}").json()
    assert (
        client.put(
            f"{ACCOUNTING}/accounts/{seeded['checking']['account_id']}", json={**account, "name": "Renamed"}
        ).status_code
        == 200
    )


def _add_an_account(client, _seeded, _session):
    _create_account(client, name="Latecomer", kind="savings", institution="Chase")


def _import_more(client, seeded, _session):
    _import(client, seeded["checking"], "2026-04-01,LATE ARRIVAL,-8.00\n")


def _validate_a_pending_suggestion(client, seeded, _session):
    assert (
        client.post(
            f"{ACCOUNTING}/postings/validate-pending", json={"posting_ids": [seeded["pharmacy_posting_id"]]}
        ).status_code
        == 200
    )


MUTATIONS = [
    ("set a category", _set_a_category),
    ("clear a category", _clear_a_category),
    ("flag a transfer manually", _flag_a_transfer_manually),
    ("tag a posting", _tag_a_posting),
    ("split a posting", _split_a_posting),
    ("undo a split", _undo_a_split),
    ("record a merge", _record_a_merge),
    ("undo a merge", _undo_a_merge),
    ("confirm a link", _confirm_a_link),
    ("undo a link", _undo_a_link),
    ("add a rule", _add_a_rule),
    ("edit a rule", _edit_a_rule),
    ("exclude a transaction from a rule", _exclude_a_transaction_from_a_rule),
    ("delete a rule", _delete_a_rule),
    ("rename a category into another", _rename_a_category_into_another),
    ("delete a category", _delete_a_category),
    ("add a subcategory", _add_a_subcategory),
    ("rename a tag", _rename_a_tag),
    ("delete a tag", _delete_a_tag),
    ("rename an account", _rename_an_account),
    ("add an account", _add_an_account),
    ("import more", _import_more),
    ("validate a pending suggestion", _validate_a_pending_suggestion),
]


@pytest.mark.parametrize(("name", "mutate"), MUTATIONS, ids=[name for name, _ in MUTATIONS])
def test_the_projection_still_equals_the_pipeline_after(name, mutate, seeded_ledger, client, db_session) -> None:
    """The whole design in one assertion, once per write path.

    Drained first so the projection starts fresh, then mutated, then drained
    again — because "was already correct, then a write happened, then a read"
    is the sequence a user produces.
    """
    assert_projection_equals_the_pipeline(db_session)
    mutate(client, seeded_ledger, db_session)
    assert_projection_equals_the_pipeline(db_session)


def _resolved_category(session: Session, posting_id: str) -> str | None:
    """One posting's stored resolved category.

    Returns
    -------
    str or None
    """
    row = session.query(adb.ResolvedPosting).filter_by(user_id=DEFAULT_USER_ID, posting_id=posting_id).one()
    return row.category_id


def test_a_category_merge_repoints_the_postings_filed_under_it(seeded_ledger, client, db_session) -> None:
    """The taxonomy lookup's own outcome, asserted rather than left to the equality above.

    `test_the_projection_still_equals_the_pipeline_after["rename a category
    into another"]` proves the cache agrees with the pipeline — including when
    both are wrong together, and including when the merge moved nothing at
    all. This names the row and the value: the posting the statement filed
    under `expense:shopping` reads as `expense:home-housing` afterwards,
    with no posting rewritten.

    It is also what the narrowed `categories` staleness trigger rests on
    (item C9). Narrowing invalidation to "the postings filed under the
    changed category" is only safe if a change to that category actually
    reaches those postings and nothing else.
    """
    assert_projection_equals_the_pipeline(db_session)
    stationery = seeded_ledger["stationery_posting_id"]
    assert _resolved_category(db_session, stationery) == "expense:shopping"

    _rename_a_category_into_another(client, seeded_ledger, db_session)
    drain(db_session, DEFAULT_USER_ID)

    assert _resolved_category(db_session, stationery) == "expense:home-housing"
    assert_projection_equals_the_pipeline(db_session)


def test_a_category_delete_uncategorizes_the_postings_filed_under_it(seeded_ledger, client, db_session) -> None:
    """The other half of retirement: no successor, so the postings read as never categorized."""
    assert_projection_equals_the_pipeline(db_session)
    stationery = seeded_ledger["stationery_posting_id"]
    assert _resolved_category(db_session, stationery) == "expense:shopping"

    _delete_a_category(client, seeded_ledger, db_session)
    drain(db_session, DEFAULT_USER_ID)

    assert _resolved_category(db_session, stationery) is None
    assert_projection_equals_the_pipeline(db_session)


def _dirty_transaction_ids(session: Session) -> set[str]:
    """Every transaction the staleness triggers have enqueued, by natural key.

    Returns
    -------
    set[str]
    """
    dirty = session.query(adb.ResolvedPostingDirty.transaction_id).filter_by(user_id=DEFAULT_USER_ID)
    rows = session.query(adb.Transaction.natural_key).filter(
        adb.Transaction.user_id == DEFAULT_USER_ID, adb.Transaction.id.in_(dirty.scalar_subquery())
    )
    return {natural_key for (natural_key,) in rows}


def test_a_category_write_dirties_only_what_it_can_change(seeded_ledger, client, db_session) -> None:
    """The narrowed `categories` trigger, asserted on the queue rather than on a stopwatch (item C9).

    Every case above proves the projection ends up *right*. None of them can
    prove it was not rebuilt wholesale to get there — which is what every
    category write used to cost, `_WHOLE_LEDGER` being the mapping and 2.0 s
    over a 10k-transaction ledger being the price.

    So this reads `resolved_postings_dirty` directly, and tracks one
    transaction: the one whose posting the statement filed under
    `expense:shopping`. It is the entire set a `categories` write can reach,
    so a write that enqueues it when it did not change it is over-invalidating
    and a write that fails to enqueue it when it did is silently wrong.

    Two things this deliberately does not assert. It does not assert an empty
    queue after a rename: `api.routers.categories._write_category_references`
    rewrites `posting_splits` whole on every rename, merging or not, so the
    split's transaction is enqueued by *that* table's trigger doing its job.
    And it does not assert the merge enqueues nothing else, for the same
    reason. What it pins is which writes reach the categorized transaction.
    """
    drain(db_session, DEFAULT_USER_ID)
    categorized = seeded_ledger["stationery_transaction_id"]
    every_transaction = {
        row.natural_key for row in db_session.query(adb.Transaction).filter_by(user_id=DEFAULT_USER_ID)
    }
    assert _dirty_transaction_ids(db_session) == set()

    created = client.post(
        f"{ACCOUNTING}/categories", json={"name": "Stationery", "classification": "expense", "color": "#445566"}
    )
    assert created.status_code == 201, created.text
    assert _dirty_transaction_ids(db_session) == set(), (
        "creating a category invalidated something, and no posting can be filed under a row that did not exist"
    )

    drain(db_session, DEFAULT_USER_ID)
    renamed = client.post(f"{ACCOUNTING}/categories/expense:stationery/rename", json={"name": "Desk Supplies"})
    assert renamed.status_code == 200, renamed.text
    dirtied_by_the_rename = _dirty_transaction_ids(db_session)
    assert categorized not in dirtied_by_the_rename, (
        "a rename that merges nothing invalidated a categorized transaction; only `name` changed, "
        "and `name` does not reach the resolved frame"
    )
    assert dirtied_by_the_rename != every_transaction, "a rename still invalidates the whole ledger"

    drain(db_session, DEFAULT_USER_ID)
    _rename_a_category_into_another(client, seeded_ledger, db_session)
    assert categorized in _dirty_transaction_ids(db_session), (
        "a category merge did not enqueue the transaction filed under the retired key — the narrowing is wrong, "
        "and the projection would keep serving the old category"
    )
    assert_projection_equals_the_pipeline(db_session)


CHASE_CHECKING_CSV = (
    "Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #\n"
    "DEBIT,03/02/2026,CORNER SHOP GROCERIES,-21.50,DEBIT_CARD,100.00,,\n"
    "CREDIT,03/03/2026,SALARY MARCH,1500.00,ACH_CREDIT,1600.00,,\n"
)


def test_the_projection_still_equals_the_pipeline_after_a_rebuild_from_raw_statements(client, db_session) -> None:
    """`POST /rebuild` deletes and reinserts the whole imported ledger — the widest write there is.

    Its own test rather than a case in the battery above, because a rebuild
    replays each archive through the standardizer registered for its
    `(institution, account_kind)` and the canonical importer has no entry in
    `importers.ingest._STANDARDIZERS` — so the battery's canonical fixture
    cannot be rebuilt at all. This seeds a Chase statement, which can.

    Every transaction gets a new row id, so this is also the case that
    proves the projection is keyed by something a rebuild reassigns
    correctly rather than leaking rows of transactions that no longer exist.
    """
    account = _create_account(client, name="Chase Checking", kind="checking", institution="Chase", last_four="9579")
    imported = client.post(
        f"{ACCOUNTING}/import",
        files={"file": ("Chase9579.csv", CHASE_CHECKING_CSV, "text/csv")},
        data={
            "institution": "Chase",
            "account_kind": "checking",
            "account_id": account["account_id"],
            "account_name": "Chase Checking",
        },
    )
    assert imported.status_code == 200, imported.text
    coffee = _posting_on(client, account["account_id"], "CORNER SHOP")
    client.put(f"{ACCOUNTING}/postings/{coffee['posting_id']}/override", json={"category_id": "expense:food-drink"})
    assert_projection_equals_the_pipeline(db_session)

    rebuilt = client.post(f"{ACCOUNTING}/rebuild")
    assert rebuilt.status_code == 200, rebuilt.text

    assert_projection_equals_the_pipeline(db_session)


def test_a_cascade_that_removes_a_transaction_removes_its_projection_rows(client, seeded_ledger, db_session) -> None:
    """Deleting a transaction cascades into its postings and their overlays — the case the triggers were built around.

    An overlay table's trigger resolves its transaction by joining up to
    `postings`, and that join finds nothing when the posting is being
    deleted in the same statement. `transactions` and `postings` carry
    triggers that need no join, which is what covers it; this is the test
    that fails if either is ever removed as redundant.

    Written against the table rather than a route because no route deletes a
    transaction outright — `POST /rebuild`'s prune does, and so does a
    tenant teardown.
    """
    assert_projection_equals_the_pipeline(db_session)
    doomed = (
        db_session
        .query(adb.Transaction)
        .filter_by(user_id=DEFAULT_USER_ID, natural_key=seeded_ledger["kept_transaction_id"])
        .one()
    )
    stored_before = db_session.query(adb.ResolvedPosting).filter_by(
        user_id=DEFAULT_USER_ID, transaction_row_id=doomed.id
    )
    assert stored_before.count() > 0, "the fixture's kept transaction should be in the projection to start with"

    db_session.query(adb.Transaction).filter_by(user_id=DEFAULT_USER_ID, id=doomed.id).delete()
    db_session.commit()

    assert_projection_equals_the_pipeline(db_session)
    assert stored_before.count() == 0, "the deleted transaction's rows survived the drain"


def _count_commits(monkeypatch) -> list[int]:
    """Count `Session.commit()` calls, so a test can assert a request publishes exactly one state.

    Counting the call rather than observing the window is the stronger
    assertion and the only one that stays deterministic: a request that
    commits once has no interleaving to find, and a test that hunts for
    one can only ever fail to find it for the wrong reason. It is also
    what these tests replaced — see their own docstrings.

    Returns
    -------
    list[int]
        A single-element list holding the running count, so a caller can
        reset it to zero before the request it is measuring.
    """
    counter = [0]
    real = Session.commit

    def _counting(self) -> None:
        counter[0] += 1
        real(self)

    monkeypatch.setattr(Session, "commit", _counting)
    return counter


def test_a_tag_rename_publishes_one_state_and_not_two(client, seeded_ledger, db_session, monkeypatch) -> None:
    """`POST /tags/{id}/rename` used to commit twice, and a read could land between the two (item D4).

    `repositories.taxonomy.remap_tag_ids` committed the repointed
    `posting_tags`/`posting_override_tags` rows itself, and the handler
    committed again once `replace_tags` had pruned the merged-away row —
    so a concurrent read could see the postings already moved to the
    surviving tag while the merged-away tag still existed. PR E proved
    the projection self-healed from that state; this asserts the state no
    longer exists to be seen. The helper flushes now and the handler owns
    the only commit.
    """
    assert_projection_equals_the_pipeline(db_session)
    second = client.post(f"{ACCOUNTING}/tags", json={"name": "Checked"}).json()
    client.put(
        f"{ACCOUNTING}/postings/{seeded_ledger['bookshop_posting_id']}/override",
        json={"tag_ids": [second["tag_id"]]},
    )
    assert_projection_equals_the_pipeline(db_session)

    commits = _count_commits(monkeypatch)
    commits[0] = 0
    renamed = client.post(f"{ACCOUNTING}/tags/{second['tag_id']}/rename", json={"name": "Reviewed"})

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["merged"] is True, "the rename did not merge, so it never reached the two-commit path"
    assert commits[0] == 1, f"the rename published {commits[0]} states; a merge must be one transaction"
    assert_projection_equals_the_pipeline(db_session)


def test_a_rule_delete_publishes_one_state_and_not_two(client, seeded_ledger, db_session, monkeypatch) -> None:
    """`DELETE /transfer-rules/{id}` used to commit the removal, then reconciliation committed again (item D4).

    Same property as the tag rename, on the other router that had it: a
    read landing between the two saw a rule that was gone while links it
    no longer implies were still stored.
    `ledger.transfers.reconcile_and_persist_rule_links` flushes now and
    the handler owns the only commit.
    """
    assert_projection_equals_the_pipeline(db_session)
    commits = _count_commits(monkeypatch)
    commits[0] = 0

    deleted = client.delete(f"{ACCOUNTING}/transfer-rules/{seeded_ledger['rule_id']}")

    assert deleted.status_code == 204, deleted.text
    assert commits[0] == 1, f"the delete published {commits[0]} states; the rule and its links must be one transaction"
    assert_projection_equals_the_pipeline(db_session)


def test_a_rule_write_that_actually_links_something_publishes_one_state(
    client, seeded_ledger, db_session, monkeypatch
) -> None:
    """The create and the edit had the same two commits, and only the linking path reaches the second one.

    Item D4's entry names `DELETE` alone. `POST /transfer-rules` commits
    inside `repositories.interpretation.upsert_transfer_rule` and `PATCH`
    committed in the handler, and both then called
    `reconcile_and_persist_rule_links`, which committed again — but only
    when it found a link. A rule matching nothing therefore looked
    single-commit and hid the window, which is why this case builds a
    rule that genuinely pairs two transactions.
    """
    assert_projection_equals_the_pipeline(db_session)
    # A pair the seeded fixture does not already link by hand: one outbound leg
    # on checking and its opposite on savings, two days apart. Without a pair a
    # rule can safely resolve, reconciliation finds nothing and never reaches
    # the commit this case exists to count.
    _import(client, seeded_ledger["checking"], "2026-04-01,CARD AUTOPAY,-140.00\n")
    _import(client, seeded_ledger["savings"], "2026-04-02,AUTOPAY RECEIVED,140.00\n")
    body = {
        "description_contains": "CARD AUTOPAY",
        "account_id": seeded_ledger["checking"]["account_id"],
        "counterparty_account_id": seeded_ledger["savings"]["account_id"],
        "priority": 1,
        "description": "moves money between my own accounts",
    }

    commits = _count_commits(monkeypatch)
    commits[0] = 0
    created = client.post(f"{ACCOUNTING}/transfer-rules", json=body)
    created_commits = commits[0]

    assert created.status_code == 201, created.text
    rule_links = [link for link in client.get(f"{ACCOUNTING}/store").json()["transfer_links"] if link["rule_id"]]
    assert rule_links, "the rule linked nothing, so this never reached the path that used to commit twice"
    assert created_commits == 1, f"the create published {created_commits} states"

    commits[0] = 0
    patched = client.patch(
        f"{ACCOUNTING}/transfer-rules/{created.json()['rule_id']}",
        json={
            **body,
            "priority": 2,
            "active": True,
            "excluded_transaction_ids": [],
            "expected_version": created.json()["version"],
        },
    )

    assert patched.status_code == 200, patched.text
    assert commits[0] == 1, f"the edit published {commits[0]} states"
    assert_projection_equals_the_pipeline(db_session)
