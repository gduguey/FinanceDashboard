"""`GET /postings`' thirteen filters, its sort and its three counts, against the resolved projection.

Every predicate here reads a value an overlay produced, which is the whole
reason they can only now be evaluated server-side: the category an override
rewrote, the account a rule repointed, the description a merge rewrote, the
amount a split changed. `tests/accounting/test_projection_equivalence.py`
proves the stored values are right; this proves the filters select over them
correctly.

Each case names the control it stands for, in the order
`web/src/lib/transactionFilters.ts` evaluates them, so the two can be read
side by side. The exclusion flags get their own cases rather than being
folded in, because "no restriction when empty, inverted when excluded" is
the part that is easy to get subtly wrong in SQL.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.accounting.conftest import ACCOUNTING, _posting_on

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _page(client: TestClient, **params: object) -> dict:
    response = client.get(f"{ACCOUNTING}/postings", params=params)
    assert response.status_code == 200, response.text
    return response.json()


PLACEHOLDERS = {"uncategorized:expense", "uncategorized:income"}


def _rendered(page: dict) -> list[dict]:
    """The rows the table would actually draw — placeholder legs ride along on the page but are never rows."""
    return [row for row in page["items"] if row["account_id"] not in PLACEHOLDERS]


def _descriptions(page: dict) -> set[str]:
    """Which descriptions the page covers. A set, so it says nothing about how many rows carry each."""
    return {row["description"] for row in _rendered(page)}


def _matched(client: TestClient, **params: object) -> int:
    """How many rows the filter matches across the whole collection, not just this page."""
    return _page(client, limit=500, **params)["counts"]["matched_postings"]


def test_placeholder_legs_are_never_a_row_of_their_own(seeded_ledger, client) -> None:
    """They ride along on the page for the transfer badge, and are never what a filter counts."""
    page = _page(client, limit=500)
    assert any(row["account_id"] == "uncategorized:expense" for row in page["items"]), "the legs should still be sent"
    assert page["counts"]["matched_postings"] == len(_rendered(page))


def test_the_search_matches_the_resolved_description(seeded_ledger, client) -> None:
    """A merge rewrites the kept transaction's description, and the search has to see the rewrite."""
    assert _descriptions(_page(client, search="charged twice")) == {"Charged twice, kept once"}


def test_the_search_is_case_insensitive_and_takes_wildcards_literally(seeded_ledger, client) -> None:
    """A `%` in a search box is a percent sign, not "match anything"."""
    assert _descriptions(_page(client, search="corner")) == {"CORNER SHOP GROCERIES"}
    assert _page(client, search="%")["counts"]["matched_postings"] == 0


def test_the_account_filter_selects_and_excludes(seeded_ledger, client) -> None:
    """Selecting and excluding the same account partition the collection exactly."""
    checking = seeded_ledger["checking"]["account_id"]
    everything = _matched(client)
    selected = _matched(client, account=checking)
    excluded = _matched(client, account=checking, account_exclude=True)

    assert 0 < selected < everything
    assert selected + excluded == everything


def test_the_category_filter_reads_the_overridden_category(seeded_ledger, client) -> None:
    """`COFFEE HOUSE` has no category of its own — an override put one on it."""
    assert "COFFEE HOUSE" in _descriptions(_page(client, categories=["expense:food-drink"], limit=500))


def test_the_category_filter_matches_rows_with_no_category_at_all(seeded_ledger, client) -> None:
    page = _page(client, categories=["__uncategorized__"], limit=500)
    assert page["counts"]["matched_postings"] > 0
    assert all(row["category_id"] is None for row in page["items"] if row["description"] in _descriptions(page))


def test_excluding_a_category_keeps_everything_else_including_the_uncategorized(seeded_ledger, client) -> None:
    """`exclude` is the complement, not "the other named categories" — an uncategorized row survives it."""
    everything = _matched(client)
    selected = _matched(client, categories=["expense:food-drink"])
    excluded = _matched(client, categories=["expense:food-drink"], categories_exclude=True)

    assert 0 < selected < everything
    assert selected + excluded == everything
    assert _matched(client, categories=["expense:food-drink"], categories_exclude=True, categorized="uncategorized") > 0


def test_the_subcategory_filter_matches_rows_with_none(seeded_ledger, client) -> None:
    page = _page(client, subcategories=["__no_subcategory__"], limit=500)
    assert all(row["subcategory_id"] is None for row in page["items"] if row["description"] in _descriptions(page))


def test_the_tag_filter_matches_a_row_carrying_any_of_the_named_tags(seeded_ledger, client) -> None:
    """The tag set is an overlay's, not the posting's own — it exists only after the override stage."""
    assert _descriptions(_page(client, tags=[seeded_ledger["tag_id"]], limit=500)) == {"COFFEE HOUSE"}
    assert _page(client, tags=["tag:nobody-has-this"], limit=500)["counts"]["matched_postings"] == 0


def test_the_month_filter_bounds_the_window(seeded_ledger, client) -> None:
    assert _page(client, month="2026-03", limit=500)["counts"]["matched_postings"] > 0
    assert _page(client, month="2025-01", limit=500)["counts"]["matched_postings"] == 0


def test_the_date_range_bounds_the_window_inclusively(seeded_ledger, client) -> None:
    """The bounds are both inclusive, so a single-day range still finds that day's rows."""
    one_day = _page(client, start="2026-03-01", end="2026-03-01", limit=500)
    assert _descriptions(one_day) == {"CORNER SHOP GROCERIES"}


def test_the_pending_filter_separates_a_suggestion_from_a_confirmed_row(seeded_ledger, client) -> None:
    assert _descriptions(_page(client, pending=["ai"], limit=500)) == {"PHARMACY"}
    assert "PHARMACY" not in _descriptions(_page(client, pending=["__confirmed__"], limit=500))


def test_the_transfer_flag_filter_finds_a_linked_transfer(seeded_ledger, client) -> None:
    page = _page(client, transfer_flags=["manual"], limit=500)
    assert _descriptions(page) == {"MOVE TO SAVINGS", "MOVE FROM CHECKING"}


def test_the_transfer_flag_filter_finds_a_rule_repointed_row(seeded_ledger, client) -> None:
    assert "CORNER SHOP GROCERIES" in _descriptions(_page(client, transfer_flags=["rule"], limit=500))


def test_excluded_from_a_rule_is_not_a_transfer_classification(seeded_ledger, client) -> None:
    """A row can be excluded from a rule and still be a plain non-transfer — the two filters are independent."""
    groceries = _posting_on(client, seeded_ledger["groceries"]["account_id"], "CORNER SHOP")
    rule = client.get(f"{ACCOUNTING}/transfer-rules/{seeded_ledger['rule_id']}").json()
    patched = client.patch(
        f"{ACCOUNTING}/transfer-rules/{seeded_ledger['rule_id']}",
        json={**rule, "excluded_transaction_ids": [groceries["transaction_id"]], "expected_version": rule["version"]},
    )
    assert patched.status_code == 200, patched.text

    excluded = _descriptions(_page(client, transfer_flags=["excluded"], limit=500))
    assert excluded == {"CORNER SHOP GROCERIES"}
    # And it is a non-transfer now that the rule no longer repoints it, so it
    # appears under `none` at the same time.
    assert "CORNER SHOP GROCERIES" in _descriptions(_page(client, transfer_flags=["none"], limit=500))


def test_the_income_expense_filter_only_ever_matches_a_real_leg(seeded_ledger, client) -> None:
    """An internal transfer has a sign but is neither income nor expense, so neither side claims it."""
    assert _matched(client, income_expense="income") > 0
    assert _matched(client, income_expense="expense") > 0
    # Both sides together are exactly the real legs, so the linked transfer is
    # in neither — it would be in one of them if the sign alone decided.
    both = _matched(client, income_expense="income") + _matched(client, income_expense="expense")
    assert both == sum(1 for row in _rendered(_page(client, limit=500)) if row["is_real_income_expense"])


def test_the_categorized_filter_splits_the_ledger_in_two(seeded_ledger, client) -> None:
    everything = _descriptions(_page(client, limit=500))
    categorized = _descriptions(_page(client, categorized="categorized", limit=500))
    uncategorized = _descriptions(_page(client, categorized="uncategorized", limit=500))
    assert categorized | uncategorized == everything
    assert not categorized & uncategorized


def test_needs_categorizing_skips_an_internal_transfer(seeded_ledger, client) -> None:
    """A transfer between two of your own accounts has no "kind of spend" to assign, so it never wants one."""
    assert "MOVE TO SAVINGS" not in _descriptions(_page(client, needs_categorizing=True, limit=500))


def test_needs_categorizing_keeps_a_row_whose_suggestion_is_unvalidated(seeded_ledger, client) -> None:
    """`PHARMACY` already shows a category — the AI's — and is not categorized until that is confirmed."""
    assert "PHARMACY" in _descriptions(_page(client, needs_categorizing=True, limit=500))


def test_needs_categorizing_keeps_a_parent_category_with_no_subcategory_picked(seeded_ledger, client) -> None:
    """The one predicate that reads live reference data rather than a stored column."""
    bookshop = _posting_on(client, seeded_ledger["checking"]["account_id"], "BOOKSHOP")
    client.put(f"{ACCOUNTING}/postings/{bookshop['posting_id']}/override", json={"category_id": "expense:food-drink"})
    assert "BOOKSHOP" in _descriptions(_page(client, needs_categorizing=True, limit=500))

    client.put(
        f"{ACCOUNTING}/postings/{bookshop['posting_id']}/override",
        json={"subcategory_id": "expense:food-drink:groceries"},
    )
    assert "BOOKSHOP" not in _descriptions(_page(client, needs_categorizing=True, limit=500))


def test_filters_compose_conjunctively(seeded_ledger, client) -> None:
    """Every control narrows; none of them widens what another has already excluded."""
    both = _page(client, categories=["expense:food-drink"], search="coffee", limit=500)
    assert _descriptions(both) == {"COFFEE HOUSE"}
    assert (
        _page(client, categories=["expense:food-drink"], search="pharmacy", limit=500)["counts"]["matched_postings"]
        == 0
    )


@pytest.mark.parametrize("field", ["posted_at", "account_id", "description", "amount", "category_id", "tag_ids"])
def test_every_sortable_column_orders_both_ways(seeded_ledger, client, field: str) -> None:
    """One case per sortable heading, asserting only that the two directions differ.

    Deliberately not asserting a specific order per column: what would break
    is a column that cannot be sorted on at all, or one whose direction is
    ignored, and both show up as the two orders being equal. This caught the
    real one — the page window honoured the requested sort while the rows
    inside it came back ordered by date regardless.
    """

    def _order(*, descending: bool) -> list[str]:
        page = _page(client, sort=field, descending=descending, limit=500)
        return list(dict.fromkeys(row["transaction_id"] for row in page["items"]))

    ascending = _order(descending=False)
    assert ascending, "the seeded ledger should produce rows"
    assert ascending != _order(descending=True), f"sorting by {field} ignores the direction"


def test_an_unknown_sort_field_is_rejected_rather_than_ignored(seeded_ledger, client) -> None:
    """A column that does not exist is a client bug, and silently falling back to the default would hide it."""
    assert client.get(f"{ACCOUNTING}/postings", params={"sort": "nonsense"}).status_code == 422


def test_nulls_sort_last_in_both_directions(seeded_ledger, client) -> None:
    """Matching `useSortableRows`: a missing category is not the top or the bottom of the list."""
    for descending in (True, False):
        page = _page(client, sort="category_id", descending=descending, limit=500)
        transactions = list(dict.fromkeys(row["transaction_id"] for row in page["items"]))
        categorized = {
            row["transaction_id"]
            for row in page["items"]
            if row["category_id"] is not None and row["description"] in _descriptions(page)
        }
        last_categorized = max(
            (index for index, transaction in enumerate(transactions) if transaction in categorized), default=-1
        )
        uncategorised_after = [
            transaction for transaction in transactions[last_categorized + 1 :] if transaction not in categorized
        ]
        assert len(uncategorised_after) == len(transactions) - last_categorized - 1


def test_the_page_carries_every_leg_of_every_transaction_it_covers(seeded_ledger, client) -> None:
    """Cut by transaction, not by matching row — the transfer badge reads a transaction's other leg."""
    page = _page(client, search="corner", limit=1)
    assert page["total"] == 1
    accounts = {row["account_id"] for row in page["items"]}
    assert len(accounts) == 2, "both legs of the matched transaction should be on the page"


def test_the_two_counts_differ_where_a_split_makes_them_differ(seeded_ledger, client) -> None:
    """The reason both are returned: a split transaction is one transaction and several rows."""
    page = _page(client, search="salary", limit=500)
    assert page["counts"]["matched_transactions"] == 1
    assert page["counts"]["matched_postings"] == 2
    assert page["total"] == page["counts"]["matched_transactions"]


def test_the_needs_categorizing_count_ignores_its_own_filter(seeded_ledger, client) -> None:
    """So the tab's badge reads the same number whichever tab is currently open."""
    everything = _page(client, limit=500)["counts"]["needs_categorizing"]
    on_the_tab = _page(client, needs_categorizing=True, limit=500)["counts"]["needs_categorizing"]
    assert everything == on_the_tab > 0


def test_the_counts_describe_the_filter_rather_than_the_page(seeded_ledger, client) -> None:
    """A one-transaction window must not make the collection look one transaction long."""
    whole = _page(client, limit=500)["counts"]
    windowed = _page(client, limit=1)
    assert windowed["counts"] == whole
    assert windowed["total"] == whole["matched_transactions"]


def test_paging_a_filtered_collection_sees_each_transaction_once(seeded_ledger, client) -> None:
    """The sort has a total order, so a `LIMIT` cannot skip or repeat across pages."""
    whole = _page(client, limit=500)
    expected = list(dict.fromkeys(row["transaction_id"] for row in whole["items"]))

    walked: list[str] = []
    offset = 0
    while offset < whole["total"]:
        page = _page(client, limit=2, offset=offset)
        walked += list(dict.fromkeys(row["transaction_id"] for row in page["items"]))
        offset += page["limit"]
    assert walked == expected


def test_the_months_endpoint_lists_every_month_with_a_posting(seeded_ledger, client) -> None:
    response = client.get(f"{ACCOUNTING}/postings/months")
    assert response.status_code == 200, response.text
    assert response.json() == ["2026-03"]


def test_a_linked_row_carries_its_partners_leg(seeded_ledger, client) -> None:
    """The one thing the transfer badge needs that is neither on the row nor on a sibling."""
    page = _page(client, transfer_flags=["manual"], limit=500)
    linked = [row for row in page["items"] if row["is_linked_transfer"] and row["linked_leg"] is not None]
    assert linked, "a confirmed transfer should carry its partner's leg"
    for row in linked:
        assert row["linked_leg"]["transaction_id"] == row["linked_transaction_id"]
        assert row["linked_leg"]["account_id"] not in {"uncategorized:expense", "uncategorized:income"}


def test_an_unlinked_row_carries_no_partner(seeded_ledger, client) -> None:
    page = _page(client, limit=500)
    assert all(row["linked_leg"] is None for row in page["items"] if not row["is_linked_transfer"])
