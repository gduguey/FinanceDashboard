"""The latency gate over the read paths whose cost is proportional to the ledger.

PR 2 and PR 4 made `GET /postings` and `GET /ledger/export` cost
O(what is shown) rather than O(all history). Nothing stopped that from
regressing: the bundle budget guards bytes, and no gate guarded time. This
module is that gate.

`GET /income-statement/category-totals` joined them in PR B. It is not
paginated and never was (known gap C5), so there is no O(page) claim to
defend there; what it is here for is the per-date FX join A3b put in front
of every posting it reads. A read path that gains a rate lookup per row and
has no gate over it is how a linear aggregation quietly becomes a quadratic
one.

What it is actually defending
-----------------------------
Not "the API is fast" — a stopwatch on a shared GitHub runner cannot make
that claim, and a gate that flaps gets deleted within a month. What it
defends is the *shape* of the cost curve. The regressions that matter here
are structural: a `LIMIT` dropped so a page loads the whole ledger, a
per-row query reintroduced inside a loop, an overlay stage that starts
scanning history for each posting it resolves. All of those turn a linear
read path into a quadratic one, and all of them are unmistakable at 10k
rows.

So each path is measured twice, in two different currencies:

**The scaling assertion** times the same request against two tenants in the
same database, five times apart in size, and asserts the ratio. This is the
real detector. It divides the runner's speed out of the answer entirely — a
slow runner makes both numbers larger and leaves the ratio alone — so it can
be set close to the truth without flapping. Linear work scales at 5x;
quadratic work scales at 25x. The bounds below sit between those — one for
the paged paths, where healthy is *sub*-linear, and a looser one for the
income statement, where linear is what correct looks like.

**The wall-clock assertion** is a coarse backstop for the regressions
scaling cannot see: a constant per-request cost that is the same at 2k as at
10k (an unindexed lookup, a needless full-table count, an accidental
`ANALYZE`). It is set several times above the measured local figure,
because that is the only honest way to set a wall clock on hardware you do
not control.

Both are measured as a median of repeated calls, not a single one, because
the first request through a fresh process pays for imports, Polars' thread
pool and the connection handshake, and none of that is what regresses.

Where the bounds below come from
--------------------------------
Item C8. Every bound was originally derived while this module's own
`ANALYZE` was silently doing nothing (B5), so it described a query planned
from absent statistics; PR D replaced the *measurements* and left the
*bounds* alone, deliberately loose, pending enough CI runs to form a
distribution rather than a datapoint. This is that re-derivation, from six
consecutive CI runs of this job on the current code — the slowest of them
roughly 1.4x the fastest, which is the runner variance any bound here has to
absorb. The sixth ran under the tightened bounds and passed every one of
them; it also nudged three of the bands below, which is why they are quoted
as ranges and re-read rather than trusted.

| measurement | 2k tenant | 10k tenant | ratio |
|-------------|-----------|------------|-------|
| `GET /postings`, 200 | 31-45 ms | 61-92 ms | 1.93-2.08x |
| `GET /ledger/export`, 5,000 | 88-131 ms | 119-182 ms | 1.34-1.38x |
| deep offset (0 -> 15,000) | 121-184 ms | 147-214 ms | 1.16-1.26x |
| filtered + sorted page, 200 | 30-43 ms | 53-80 ms | 1.75-1.84x |
| drilldown page, 50 | 25-36 ms | 52-79 ms | 2.04-2.23x |
| `category-totals`, whole history | 85-124 ms | 412-617 ms | 4.83-5.32x |
| `POST /postings/matching-ids` | 13-18 ms | 22-38 ms | 1.73-2.00x |
| `POST /postings/validate-pending` | 61-86 ms | 255-350 ms | 3.97-4.20x |
| `POST .../pattern-suggest-category/bulk` | 17-24 ms | 33-50 ms | 1.95-2.33x |
| cold rebuild, then a page | 524-799 ms | 2,201-3,120 ms | 3.85-4.20x |
| one override, then a page | 63-91 ms | 93-130 ms | 1.21-1.51x |
| `GET /postings/months` | — | 15-25 ms | — |
| category merge, then a page (C9) | — | 1,070-1,419 ms | — |

and, for the page sizes C6 used to fail on, 70-99 ms at 400, 100-145 ms at
1,000 and 415-645 ms at `PAGE_LIMIT_MAX`.

Two things that distribution settles. The per-path spread is small — the
postings page ratio moved 1.93 to 2.08 across five runs, the export 1.34 to
1.38 — so these are stable enough to bound near the truth rather than an
order of magnitude above it. And CI is *not* uniformly "twice the local
ratio", which the previous revision of this docstring asserted: the postings
page is dearer on CI (2.0x against 1.6x local) while the rebuild is cheaper
(4.0x against 4.4x). Each bound below therefore names its own worst observed
CI figure and the multiple it sits at, rather than deriving from a rule of
thumb about runners.

What this gate catches, and what it does not
--------------------------------------------
It catches a complexity change — a linear read path becoming quadratic — and
a collapse. It does not catch a constant-factor slowdown of roughly 1.5x or
less, which is inside the runner variance above and always will be.

**A page that stops being a page is caught, and not by a stopwatch.** This
section used to say the opposite, and it was true when written. It is not
now: `test_the_seed_is_the_shape_the_gate_assumes` asserts a `limit=3` page
returns six rows and
`test_the_drilldown_page_matches_the_rows_it_claims_to_measure` asserts its
own row counts, so a page that returns the whole ledger fails on the count
rather than on the clock. Those are the better gates — deterministic, with
no runner speed in the answer — and the scaling bounds below are a backstop
rather than the primary detector.

Measured, by deleting `.limit()/.offset()` from
`repositories.projection.filtered_page`: the 10k page moves from 66 ms to
614 ms and its ratio from 1.5x to 4.8x. Before item C8 that failed **two**
cases, both of them row counts, and every timing bound admitted it. It now
fails **five** — the two row counts plus the postings-page, drilldown and
narrow-write scaling assertions.

One honest weak spot remains. The *filtered* page's ratio separates least
under that experiment (2.5x broken against 1.8x healthy), because the
`TESCO` predicate matches an eighth of the ledger, so both tenants inflate
together. It has no row-count guard of its own; what covers it is that it
shares `filtered_page` with the unfiltered page and the drilldown, both of
which do.

What changed with C6, and why the ratios above are so much lower than the
ones this module used to quote: the page's SQL read is now genuinely
proportional to the page rather than to the ledger. Matching the page's
transaction array against `Transaction.id` made the planner reach `postings`
with a sequential scan of every row the tenant owns; matching it against
the indexed `Posting.transaction_id` reaches them through
`ix_postings_transaction_id_user_id`. Fitting the two measured volumes now
gives about **65 ms fixed and 0.0005 ms per transaction**, against the
61 ms and 0.021 ms recorded before — so the claim that "at 10k roughly
three quarters of the page's cost is already ledger-proportional" no longer
holds, and the whole-collection loads that remain (rules, accounts, merges,
links) are the small constant they were always described as.
"""

from __future__ import annotations

import statistics
import time
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from accounting.api.api_models import NO_SUBCATEGORY, UNCATEGORIZED
from http_api.pagination import PAGE_LIMIT_MAX
from tests.performance.conftest import (
    BIG_TENANT_TRANSACTIONS,
    SMALL_TENANT_TRANSACTIONS,
    REAL_ACCOUNT_KEY,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable

    from fastapi.testclient import TestClient
    from sqlalchemy import Engine

pytestmark = pytest.mark.perf

_POSTINGS = "/api/v1/accounting/postings"
_MONTHS = "/api/v1/accounting/postings/months"
_EXPORT = "/api/v1/accounting/ledger/export"
_CATEGORY_TOTALS = "/api/v1/accounting/income-statement/category-totals"

_LEDGER_WINDOW = {"start": "2019-01-01", "end": "2030-01-01"}
"""Wider than the seeded ledger, so the measurement covers every posting rather than a slice of them."""

_PAGE_SIZE = 200
"""The default page the Transactions screen asks for (`http_api.pagination.PAGE_LIMIT_DEFAULT`)."""

_DRILLDOWN_PAGE_SIZE = 50
"""The page the insights drilldown asks for — a panel inside a chart card, not a screen."""

_ABOVE_THE_CLIFF_PAGE_SIZES = (400, 1_000, PAGE_LIMIT_MAX)
"""Page sizes `GET /postings` used to 500 on, and the cap it advertises.

400 is the size C6 was reported at. 1,000 and `PAGE_LIMIT_MAX` are there
because the reported size was not the cause: the failure was a plan chosen
from absent statistics, and the boundary it produced moved between runs
(between 300 and 400 on one, between 200 and 300 on another). Pinning the
regression test to 400 alone would re-test the coincidence rather than the
defect, so this walks the range up to the largest page the contract permits.
"""

_REPEATS = 5
"""Calls per measurement. The median of five is stable enough to compare and cheap enough to run."""

_WARMUP = 1
"""Calls made and thrown away before timing, so imports and pool setup are not in the sample."""

VOLUME_RATIO = BIG_TENANT_TRANSACTIONS / SMALL_TENANT_TRANSACTIONS
"""5.0 — how much more data the big tenant has."""

MAX_SCALING_FACTOR = 3.5
"""How much slower the big tenant's page may be than the small tenant's, for 5x the ledger.

Shared by the five bounded reads: the postings page, the export, the
filtered page, the drilldown, and the page after a single override. All five
are *sub*-linear when healthy — a page is bounded, so only their fixed costs
grow — where linear would be 5.0 and quadratic 25.0.

Worst observed across six CI runs, by path: **2.23x** (drilldown), 2.08x
(postings page), 1.84x (filtered page), 1.51x (one override), 1.38x
(export). This is 1.6x the worst of those, and the per-path spread across
those runs is under 10%, so the margin is wide relative to the noise it has
to absorb rather than merely wide.

Eight until item C8, which was a bound derived from nothing: it was set
before B5 against a plan chosen from absent statistics, and PR D replaced
the measurements without revisiting it. Eight admitted a page that had
stopped being a page — the deliberate break in this module's docstring
measures 4.8x — where 3.5 rejects it. That break is caught by two row-count
assertions as well, deterministically; this is the backstop.

One constant rather than five, even though the five healthy figures span
1.38x to 2.23x. Five constants whose justifications differed only in the
number would be five things to keep current, and the loosest of them would
still be this one.
"""

MAX_INCOME_STATEMENT_SCALING_FACTOR = 9.0
"""`MAX_SCALING_FACTOR`'s counterpart for the read paths that are *supposed* to be linear.

The bounded reads are sub-linear because a page caps what they touch. The
income statement has no page — it reads every posting in its window by
construction (known gap C5) — and an unfiltered bulk action matches every
row by definition, so for both of them linear, 5.0, is what healthy looks
like and sub-linear is not on offer.

Worst observed across six CI runs: **5.32x** for `category-totals`, 4.20x
for `validate-pending`, 2.33x and 2.00x for the other two bulk actions. Note
that the first sits *above* the nominal 5.0, which is why this keeps more
proportional headroom than `MAX_SCALING_FACTOR` does: 1.7x the worst
observed, against 1.6x there.

Twelve before item C8, from a single CI sample of 5.1x. Nine still rejects
the regression this exists for — a rate resolved per posting rather than
joined once, which is super-linear and would land far above it — while
staying well clear of a runner having a bad minute. Quadratic is 25.
"""

MAX_DEEP_OFFSET_FACTOR = 2.5
"""How much more the last page of an export may cost than the first.

`OFFSET` is O(offset) in Postgres by nature, so this is not asserting it is
free; it is asserting that walking to the end of the collection stays a
small constant multiple rather than becoming the dominant cost of a backup.

Worst observed across six CI runs: **1.26x**, in a band of 1.16-1.26 — the
tightest distribution in this module, because both figures come from the
same tenant in the same test and the runner's speed divides out almost
exactly. Two and a half is 2.0x that, the most headroom any bound here
carries relative to its worst case, because the failure it guards against
degrades gradually rather than stepping.
"""

MAX_POSTINGS_PAGE_SECONDS = 1.0
"""Wall-clock ceiling for one 200-transaction page of `GET /postings` over a 10k-transaction ledger.

Worst observed across six CI runs: **92 ms**, in a band of 61-92. One
second is 10.9x that.

Two cases time this same request — the scaling assertion and the wall-clock
one — so the band spans both rather than only this test's own six figures,
whose worst was 87 ms. Quoting the lower of the two would understate the
number this bound has to clear.

Three seconds before item C8, which was roughly 45x and defensible only
because nobody had the CI numbers. Every wall clock in this module now sits
at about 8-12x its worst observed CI figure, which is the multiple these
runners justify — two-core containers with no I/O isolation, and a measured
1.4x spread between the fastest and slowest of the six runs.

That multiple is not timidity and it is not precision either. A gate that
flaps gets deleted, which leaves less protection than a loose one; the
scaling assertion above is the sensitive instrument, and this catches a
collapse.
"""

MAX_LARGE_POSTINGS_PAGE_SECONDS = 4.0
"""Wall-clock ceiling for the largest page `GET /postings` advertises, over a 10k-transaction ledger.

Looser than `MAX_POSTINGS_PAGE_SECONDS` on purpose, and measuring a
different thing. A `PAGE_LIMIT_MAX` page returns every transaction the big
tenant has, so it is allowed to cost roughly what the whole ledger costs;
what it is not allowed to do is fail. The failure this guards against
produced a 500 at the 15 s `statement_timeout`, not a slow answer, so any
ceiling comfortably under that bound catches it.

Worst observed across six CI runs: **99 ms at 400, 145 ms at 1,000 and
645 ms at `PAGE_LIMIT_MAX`**. Four seconds is 6.2x the largest — less
headroom than the other wall clocks carry, and deliberately so: a
`PAGE_LIMIT_MAX` page is the one measurement here that legitimately costs
most of a ledger read, so what matters is only that it stays far under the
15 s `statement_timeout` whose breach is the actual regression. Left at 4.0
by item C8 rather than moved.
"""

MAX_FILTERED_PAGE_SECONDS = 1.0
"""Wall-clock ceiling for one filtered, sorted page over a 10k-transaction ledger.

The read C1 exists to make possible, and the one whose *shape* is easiest to
lose: the filter runs over `accounting.resolved_postings`, and a predicate
that stopped being expressible there — a join back to the raw ledger, a
per-row lookup — would turn a page into a scan of history again.

Worst observed across six CI runs: **80 ms**, in a band of 53-80 — within
noise of the unfiltered page, because the filter is an indexed read over
stored values rather than anything the request computes. One second is 12x
that, and equal to `MAX_POSTINGS_PAGE_SECONDS` because the two measure the
same shape of work.
"""

MAX_DRILLDOWN_PAGE_SECONDS = 1.0
"""Wall-clock ceiling for one page of the insights drilldown over a 10k-transaction ledger.

Its own case rather than a variation on `MAX_FILTERED_PAGE_SECONDS`, because
the predicate is a different shape: two array-valued filters, each carrying
a sentinel that means "is null", against a date window. C6 was a plan flip
caused by exactly that — an array parameter's effect on a cost estimate,
which is invisible until an array is what you pass.

Worst observed across six CI runs: **79 ms**, in a band of 52-79, scaling
at 2.04-2.23x — the steepest of the bounded reads, and still nowhere near
linear, so the arrays cost nothing structural. One second is 13x that.
"""

MAX_MONTHS_SECONDS = 0.5
"""Wall-clock ceiling for the month picker's whole option list (C2).

One row per month the user has ever transacted in — tens of entries for a
decade — but computed by grouping every resolved posting, so it is linear in
the ledger and worth a bound.

Worst observed across six CI runs: **25 ms**, in a band of 15-25. Half a
second is 20x that, the widest multiple in this module, because it is also
the smallest absolute figure and therefore the one most easily doubled by a
runner's bad moment rather than by a regression.
"""

MAX_BULK_ACTION_SECONDS = 2.5
"""Wall-clock ceiling for one filter-shaped bulk action over a 10k-transaction ledger.

The three actions the transactions screen fires — `POST /postings/matching-ids`,
`POST /postings/validate-pending`, `POST /postings/pattern-suggest-category/bulk`
— all resolve their target set from the filter server-side, which is what
lets the screen hold a page (C1). None of them is scoped to a page, and none
of them should be: a bulk action covers what the filter matches. So they are
allowed to be linear in the matched set, which is why this is a wall clock
rather than a scaling ratio.

What it defends is that they stay *queries* over
`accounting.resolved_postings`. The bulk pattern suggester in particular used
to call `ledger.resolution.resolved_postings` and replay the entire ledger in
Python to obtain four columns the projection already stores — a cost the
cutover would have made trivial to trigger, since the same button now covers
every page rather than the rows one screen had rendered.

Worst observed across six CI runs, with an empty filter — the widest set
any of them can resolve: **350 ms** for `validate-pending` (which loads an
override row per matched id, and is the only one of the three that is not
simply a projection query), 50 ms for the pattern suggester and 38 ms for
`matching-ids`. Two and a half seconds is 7.1x the largest.

One bound for three endpoints, sized by the dearest: a separate constant for
each would be two more numbers to keep current in exchange for tightening
two assertions that are already an order of magnitude inside this one.
"""

MAX_COLD_REBUILD_SECONDS = 20.0
"""Wall-clock ceiling for the first read after the whole projection is invalidated.

The cost the drain-on-read design concentrates rather than removes: a wide
change — a transfer rule, an account, a category — enqueues every
transaction, and the next read pays for a full recompute before it answers.

Worst observed across six CI runs: **3,120 ms** for the 10k tenant,
request included, in a band of 2,201-3,120 — the widest spread of any
measurement here at 1.42x, which is why this keeps proportionally more
headroom than the page bounds do. The 2k tenant runs 524-799 ms.

A separate standalone script measured the recompute *alone*, with no request
around it and a different seed — 0.77 s over 10k transactions and 3.8 s over
50k, of which the `COPY` of two projection rows per transaction was 193 ms
and 1,306 ms. It is quoted here only because it is where the extrapolation to
about 13 s on the 170k-transaction audit database comes from, and it is
deliberately not the figure this bound is set against.

Twenty seconds is 6.4x the worst of those, and deliberately loose. What this
is defending is that a rebuild stays *linear*: the failure that would matter
is a recompute that re-reads a whole-collection overlay per batch, or per
transaction, which at 10k would not finish inside this bound at all. The
scaling assertion beside it is what says so precisely. Thirty before item C8.
"""

MAX_REBUILD_SCALING_FACTOR = 8.0
"""How much slower the big tenant's rebuild may be than the small tenant's, for 5x the ledger.

A rebuild is linear work by construction — every transaction is resolved
once — so 5.0 is what healthy looks like. Worst observed across six CI
runs: **4.20x**, in a band of 3.85-4.20, i.e. reliably a little *under*
linear, because the fixed cost of one drain is amortised over five times as
many transactions. Eight is 1.9x the worst of those; quadratic would be 25.
Twelve before item C8.

The regression it exists for is concrete: `ledger.resolution.overlay_context`
reads the rules, accounts, splits, merges and links once per *drain*, and
`repositories.projection` walks its batches inside that. Moving either read
inside the batch loop — or worse, inside the per-transaction path — would
put the whole-collection cost on every batch and show up here long before it
showed up as a timeout.
"""

MAX_EXPORT_PAGE_SECONDS = 1.5
"""Wall-clock ceiling for one 5,000-posting page of `GET /ledger/export`.

Worst observed across six CI runs: **182 ms**, in a band of 119-182. One
and a half seconds is 8.2x that. The export is the cheaper path by
construction — it applies no overlay — so its ceiling is lower than the
postings page's even though its page covers more rows. Two seconds before
item C8.
"""

MAX_CATEGORY_TOTALS_SECONDS = 5.0
"""Wall-clock ceiling for one whole-history `GET /income-statement/category-totals`.

The third path here, and the odd one out: it is not paginated at all (known
gap C5 — a dashboard reads every posting inside its window, and the owner
declined bounding it), so its cost is the whole ledger by design and the
scaling assertion below is the only one that can say anything useful about
it.

It is measured because A3b put a per-row rate join into it: each posting is
now converted at its own date's trailing-30-day mean instead of one scalar
rate for the report, against a rate table built per request. The harness
seeds daily rates back to 2019 — well past the two years
`exchange_rates.DEFAULT_HISTORY_YEARS` keeps in production — so the
rolling window this measures is the pessimistic one, not the real one.
Five local runs over the 10k ledger, two-currency (the expensive path — a
single-currency tenant skips the join entirely), after B5: **305, 307, 313,
315 and 323 ms**, against the 343-388 ms measured before real statistics
existed. The A3b comparison that established the join costs nothing
measurable was made under those same absent statistics and has not been
repeated; what it concluded still holds for the reason it gave — the cost
of this endpoint is reading and resolving the ledger, which C5 owns — and
its scaling ratio is 3.0-3.2x here, well inside linear.

Worst observed across six CI runs: **617 ms**, in a band of 412-617, at
4.83-5.32x. Five seconds is 8.1x that, down from six on a single pre-B5
sample of 656 ms (item C8).
"""


def _median_seconds(client: TestClient, path: str, **params: int | str | list[str]) -> float:
    """Time one request repeatedly and return the median, discarding a warm-up.

    Parameters
    ----------
    client
        The tenant's client.
    path
        Endpoint to call.
    **params
        Query parameters. A list value is sent as its key repeated, which
        is the only encoding `PostingFilters`' multi-selects read as a list
        — see `web/src/lib/accountingApi.ts`' `queryString` for the same
        hazard on the client's side.

    Returns
    -------
    float
        Median elapsed seconds.
    """
    for _ in range(_WARMUP):
        assert client.get(path, params=params).status_code == 200

    samples = []
    for _ in range(_REPEATS):
        started = time.perf_counter()
        response = client.get(path, params=params)
        samples.append(time.perf_counter() - started)
        assert response.status_code == 200
    median = statistics.median(samples)
    # Printed, not merely asserted on, for the same reason `web/tooling/budget.ts`
    # reports the landing size it measured: the next person to argue with a
    # threshold needs the number CI actually saw. The perf job runs `-s`.
    print(f"  {path} {params} -> {median * 1000:.0f} ms median of {_REPEATS}")  # noqa: T201
    return median


def _median_post_seconds(client: TestClient, path: str, body: dict[str, object]) -> float:
    """Time one `POST` repeatedly and return the median, discarding a warm-up.

    Only used for the three filter-shaped bulk actions. Each is a write in
    principle, and each is a no-op over this seed — it stages no override and
    stores no category pattern — so repeating one measures the read half,
    which is the half that scales with the ledger and the half a regression
    would show up in.

    Returns
    -------
    float
        Median elapsed seconds.
    """
    for _ in range(_WARMUP):
        assert client.post(path, json=body).status_code == 200

    samples = []
    for _ in range(_REPEATS):
        started = time.perf_counter()
        response = client.post(path, json=body)
        samples.append(time.perf_counter() - started)
        assert response.status_code == 200, response.text
    median = statistics.median(samples)
    print(f"  POST {path} {body} -> {median * 1000:.0f} ms median of {_REPEATS}")  # noqa: T201
    return median


_BULK_ACTIONS = (
    "/api/v1/accounting/postings/matching-ids",
    "/api/v1/accounting/postings/validate-pending",
    "/api/v1/accounting/postings/pattern-suggest-category/bulk",
)
"""Every endpoint that resolves its own target set from `PostingFilters`."""


def test_the_seed_is_the_shape_the_gate_assumes(request_as: Callable[[str], TestClient]) -> None:
    """Guard the measurements: two legs per transaction, one of them a rule-eligible placeholder.

    Every threshold below is meaningless if the seed did not land. A
    transaction with one leg, or with no `uncategorized:expense` leg, skips
    the most expensive stage of resolution — so this would keep passing while
    measuring almost nothing.
    """
    page = request_as("big").get(_POSTINGS, params={"limit": 3}).json()

    assert page["total"] == BIG_TENANT_TRANSACTIONS
    assert len(page["items"]) == 6, "two legs per transaction"
    placeholders = [posting for posting in page["items"] if posting["account_id"] == "uncategorized:expense"]
    assert len(placeholders) == 3, "exactly one placeholder leg per transaction, or rule matching does nothing"


def test_a_tenant_sees_only_its_own_ledger(request_as: Callable[[str], TestClient]) -> None:
    """The two volumes are two tenants in one database, so the gate rests on RLS actually separating them."""
    assert request_as("big").get(_POSTINGS, params={"limit": 1}).json()["total"] == BIG_TENANT_TRANSACTIONS
    assert request_as("small").get(_POSTINGS, params={"limit": 1}).json()["total"] == SMALL_TENANT_TRANSACTIONS


def test_the_postings_page_scales_with_the_page_not_the_ledger(request_as: Callable[[str], TestClient]) -> None:
    """Five times the history must not cost anything like twenty-five times the page."""
    small = _median_seconds(request_as("small"), _POSTINGS, limit=_PAGE_SIZE)
    big = _median_seconds(request_as("big"), _POSTINGS, limit=_PAGE_SIZE)

    factor = big / small
    assert factor < MAX_SCALING_FACTOR, (
        f"GET /postings cost {factor:.1f}x more for {VOLUME_RATIO:.0f}x the ledger "
        f"({small * 1000:.0f} ms at {SMALL_TENANT_TRANSACTIONS} transactions, "
        f"{big * 1000:.0f} ms at {BIG_TENANT_TRANSACTIONS}). "
        f"Quadratic would be {VOLUME_RATIO**2:.0f}x. Something now reads the whole ledger per page."
    )


def test_the_ledger_export_page_scales_with_the_page_not_the_ledger(
    request_as: Callable[[str], TestClient],
) -> None:
    """The export applies no overlay, so its page should be near-flat in ledger size."""
    small = _median_seconds(request_as("small"), _EXPORT, limit=PAGE_LIMIT_MAX)
    big = _median_seconds(request_as("big"), _EXPORT, limit=PAGE_LIMIT_MAX)

    factor = big / small
    assert factor < MAX_SCALING_FACTOR, (
        f"GET /ledger/export cost {factor:.1f}x more for {VOLUME_RATIO:.0f}x the ledger "
        f"({small * 1000:.0f} ms, then {big * 1000:.0f} ms). Quadratic would be {VOLUME_RATIO**2:.0f}x."
    )


def test_a_deep_offset_costs_about_what_the_first_page_costs(request_as: Callable[[str], TestClient]) -> None:
    """Paging to the end must not get progressively slower — the export loop walks every page.

    `GET /ledger/export` is the one caller that legitimately reads the whole
    collection (see `docs/http-api-contract.md`), so a last page that costs
    many times the first turns a backup into a timeout. `OFFSET` is O(offset)
    in Postgres by nature; what this bounds is that it stays a small
    constant-factor cost rather than becoming the dominant one.
    """
    client = request_as("big")
    first = _median_seconds(client, _EXPORT, limit=PAGE_LIMIT_MAX, offset=0)
    last = _median_seconds(client, _EXPORT, limit=PAGE_LIMIT_MAX, offset=2 * BIG_TENANT_TRANSACTIONS - PAGE_LIMIT_MAX)

    assert last < first * MAX_DEEP_OFFSET_FACTOR, (
        f"the last export page cost {last / first:.1f}x the first ({first * 1000:.0f} ms -> {last * 1000:.0f} ms)"
    )


def test_the_postings_page_stays_within_its_wall_clock_budget(request_as: Callable[[str], TestClient]) -> None:
    """The coarse backstop — see this module's docstring for why it is set where it is."""
    elapsed = _median_seconds(request_as("big"), _POSTINGS, limit=_PAGE_SIZE)

    assert elapsed < MAX_POSTINGS_PAGE_SECONDS, (
        f"one {_PAGE_SIZE}-transaction page took {elapsed:.2f} s over a "
        f"{BIG_TENANT_TRANSACTIONS}-transaction ledger, budget {MAX_POSTINGS_PAGE_SECONDS} s"
    )


def test_a_page_above_the_old_cliff_is_served_at_all(request_as: Callable[[str], TestClient]) -> None:
    """Every page size the contract permits must answer, not time out (C6).

    This is a correctness assertion wearing a stopwatch. `GET /postings`
    returned 500 for any `limit` above roughly 300 on a 10k-transaction
    ledger, because the page's transaction array was matched against
    `Transaction.id` — the side of the join with no index to drive from — and
    the planner, with no statistics, costed the inner side as running once
    when it ran twenty thousand times. `PAGE_LIMIT_MAX` is 5,000, so those
    were legal requests that the advertised contract could not serve.

    Separate from the wall-clock test below rather than folded into it
    because what is being defended is different: not that a large page is
    fast, but that it exists. The budget here is deliberately loose — a
    5,000-transaction page is a large answer and is allowed to cost
    something. What it is not allowed to do is hit `statement_timeout`.
    """
    client = request_as("big")

    for limit in _ABOVE_THE_CLIFF_PAGE_SIZES:
        response = client.get(_POSTINGS, params={"limit": limit})
        assert response.status_code == 200, (
            f"GET /postings?limit={limit} returned {response.status_code} over a "
            f"{BIG_TENANT_TRANSACTIONS}-transaction ledger; every limit up to "
            f"{PAGE_LIMIT_MAX} is a legal request (C6)"
        )
        elapsed = _median_seconds(client, _POSTINGS, limit=limit)
        assert elapsed < MAX_LARGE_POSTINGS_PAGE_SECONDS, (
            f"one {limit}-transaction page took {elapsed:.2f} s, budget {MAX_LARGE_POSTINGS_PAGE_SECONDS} s"
        )


def test_the_ledger_export_page_stays_within_its_wall_clock_budget(request_as: Callable[[str], TestClient]) -> None:
    """The same backstop for the export path."""
    elapsed = _median_seconds(request_as("big"), _EXPORT, limit=PAGE_LIMIT_MAX)

    assert elapsed < MAX_EXPORT_PAGE_SECONDS, (
        f"one {PAGE_LIMIT_MAX}-posting export page took {elapsed:.2f} s, budget {MAX_EXPORT_PAGE_SECONDS} s"
    )


def test_the_income_statement_scales_with_the_ledger_and_not_worse(request_as: Callable[[str], TestClient]) -> None:
    """A whole-history income statement reads every posting; it must not read them a second time per rate.

    This path has no `LIMIT` to lose (see `MAX_CATEGORY_TOTALS_SECONDS`), so
    what is being defended is the shape of the FX conversion A3b added: a
    join against a per-date rate table keeps the whole read linear, and a
    per-row rate lookup that walked the history for each posting would not.
    Its bound is `MAX_INCOME_STATEMENT_SCALING_FACTOR` rather than the one
    the paged paths use — read that for why linear is the healthy shape
    here and sub-linear is not available.
    """
    small = _median_seconds(request_as("small"), _CATEGORY_TOTALS, **_LEDGER_WINDOW)
    big = _median_seconds(request_as("big"), _CATEGORY_TOTALS, **_LEDGER_WINDOW)

    factor = big / small
    assert factor < MAX_INCOME_STATEMENT_SCALING_FACTOR, (
        f"the income statement cost {factor:.1f}x more for {VOLUME_RATIO:.0f}x the ledger "
        f"({small * 1000:.0f} ms, then {big * 1000:.0f} ms). Linear is {VOLUME_RATIO:.0f}x and expected here; "
        f"quadratic would be {VOLUME_RATIO**2:.0f}x. "
        f"Something now resolves a rate per posting instead of joining one table."
    )


def test_the_income_statement_stays_within_its_wall_clock_budget(request_as: Callable[[str], TestClient]) -> None:
    """The coarse backstop for the third path — see `MAX_CATEGORY_TOTALS_SECONDS`."""
    elapsed = _median_seconds(request_as("big"), _CATEGORY_TOTALS, **_LEDGER_WINDOW)

    assert elapsed < MAX_CATEGORY_TOTALS_SECONDS, (
        f"a whole-history income statement took {elapsed:.2f} s over a "
        f"{BIG_TENANT_TRANSACTIONS}-transaction ledger, budget {MAX_CATEGORY_TOTALS_SECONDS} s"
    )


_FILTERED = {
    "search": "TESCO",
    "categorized": "uncategorized",
    "sort": "amount",
    "descending": True,
    "limit": _PAGE_SIZE,
}
"""One filtered, sorted page as the transactions screen asks for it.

A substring search and a resolved-value predicate together, sorted on a
column that is not the page's own key — so the measurement covers the
filter, the aggregate the sort is built from, and the two counts beside it,
rather than the cheapest path through them.
"""


def test_a_filtered_page_scales_with_the_page_not_the_ledger(request_as: Callable[[str], TestClient]) -> None:
    """Filtering server-side must stay a page read, not become a scan of history wearing a `LIMIT`."""
    small = _median_seconds(request_as("small"), _POSTINGS, **_FILTERED)
    big = _median_seconds(request_as("big"), _POSTINGS, **_FILTERED)

    factor = big / small
    assert factor < MAX_SCALING_FACTOR, (
        f"a filtered page cost {factor:.1f}x more for {VOLUME_RATIO:.0f}x the ledger "
        f"({small * 1000:.0f} ms, then {big * 1000:.0f} ms). Quadratic would be {VOLUME_RATIO**2:.0f}x. "
        f"Something in the filter now reads the whole ledger per page."
    )


def test_a_filtered_page_stays_within_its_wall_clock_budget(request_as: Callable[[str], TestClient]) -> None:
    """The coarse backstop for the read C1 exists to make possible."""
    elapsed = _median_seconds(request_as("big"), _POSTINGS, **_FILTERED)

    assert elapsed < MAX_FILTERED_PAGE_SECONDS, (
        f"one filtered {_PAGE_SIZE}-transaction page took {elapsed:.2f} s over a "
        f"{BIG_TENANT_TRANSACTIONS}-transaction ledger, budget {MAX_FILTERED_PAGE_SECONDS} s"
    )


_DRILLDOWN = {
    "start": "2020-01-01",
    "end": "2030-01-01",
    "account": REAL_ACCOUNT_KEY,
    "categories": [UNCATEGORIZED],
    "subcategories": [NO_SUBCATEGORY],
    "income_expense": "expense",
    "limit": _DRILLDOWN_PAGE_SIZE,
}
"""One page of the insights drilldown, as `CategoryDrilldownPie` asks for it.

Both array filters carry their "is null" sentinel, which is what this seed's
postings are — it stores no category, so a slice naming a real category id
would measure an empty result set and assert nothing. The array *shape* is
what the measurement is for (see `MAX_DRILLDOWN_PAGE_SECONDS`), and it is
identical either way.
"""


def test_the_drilldown_page_matches_the_rows_it_claims_to_measure(
    request_as: Callable[[str], TestClient],
) -> None:
    """A filter that matched nothing would time an empty page and pass for ever.

    The failure mode this guards is the one an array parameter produces on
    its own: a list encoded as one comma-joined value is a filter that
    matches nothing and answers `200`, so the gate beside it would measure
    the cheapest possible query and call it the drilldown.
    """
    page = request_as("big").get(_POSTINGS, params=_DRILLDOWN).json()

    assert page["total"] == BIG_TENANT_TRANSACTIONS
    assert page["counts"]["matched_postings"] == BIG_TENANT_TRANSACTIONS, "one matching leg per transaction"
    # The window is a transaction and every leg of it comes back, placeholders
    # included (see `repositories.projection.page_rows`) — so the page carries
    # twice the transactions it names, and the panel narrows it itself.
    assert len(page["items"]) == 2 * _DRILLDOWN_PAGE_SIZE
    matched = [item for item in page["items"] if item["account_id"] == REAL_ACCOUNT_KEY]
    assert len(matched) == _DRILLDOWN_PAGE_SIZE


def test_the_drilldown_page_scales_with_the_page_not_the_ledger(request_as: Callable[[str], TestClient]) -> None:
    """The insights drilldown must be a page read too, arrays and sentinels included."""
    small = _median_seconds(request_as("small"), _POSTINGS, **_DRILLDOWN)
    big = _median_seconds(request_as("big"), _POSTINGS, **_DRILLDOWN)

    factor = big / small
    assert factor < MAX_SCALING_FACTOR, (
        f"a drilldown page cost {factor:.1f}x more for {VOLUME_RATIO:.0f}x the ledger "
        f"({small * 1000:.0f} ms, then {big * 1000:.0f} ms). Quadratic would be {VOLUME_RATIO**2:.0f}x. "
        f"An array-valued filter now reads the whole ledger per page."
    )


def test_the_drilldown_page_stays_within_its_wall_clock_budget(request_as: Callable[[str], TestClient]) -> None:
    """The coarse backstop for the read the insights drilldown moved onto."""
    elapsed = _median_seconds(request_as("big"), _POSTINGS, **_DRILLDOWN)

    assert elapsed < MAX_DRILLDOWN_PAGE_SECONDS, (
        f"one drilldown {_DRILLDOWN_PAGE_SIZE}-transaction page took {elapsed:.2f} s over a "
        f"{BIG_TENANT_TRANSACTIONS}-transaction ledger, budget {MAX_DRILLDOWN_PAGE_SECONDS} s"
    )


def test_the_month_picker_costs_a_group_by_and_not_a_ledger_read(request_as: Callable[[str], TestClient]) -> None:
    """C2's endpoint. Linear in the ledger by nature, so the bound is a wall clock rather than a ratio."""
    elapsed = _median_seconds(request_as("big"), _MONTHS)

    assert elapsed < MAX_MONTHS_SECONDS, (
        f"the month list took {elapsed:.2f} s over a {BIG_TENANT_TRANSACTIONS}-transaction ledger, "
        f"budget {MAX_MONTHS_SECONDS} s"
    )


@pytest.mark.parametrize("path", _BULK_ACTIONS)
def test_a_bulk_action_stays_within_its_wall_clock_budget(path: str, request_as: Callable[[str], TestClient]) -> None:
    """Over the widest set any of them can resolve — an empty filter, i.e. the whole ledger."""
    elapsed = _median_post_seconds(request_as("big"), path, {})

    assert elapsed < MAX_BULK_ACTION_SECONDS, (
        f"{path} took {elapsed:.2f} s over a {BIG_TENANT_TRANSACTIONS}-transaction ledger, "
        f"budget {MAX_BULK_ACTION_SECONDS} s"
    )


@pytest.mark.parametrize("path", _BULK_ACTIONS)
def test_a_bulk_action_stays_linear_in_the_set_it_resolves(path: str, request_as: Callable[[str], TestClient]) -> None:
    """The instrument beside that wall clock, and the one that would catch a resolve creeping back in.

    An unfiltered bulk action matches every row, so linear — 5.0 for 5x the
    ledger — is what healthy looks like, exactly as it is for the income
    statement, and this borrows that constant rather than inventing a second
    one with the same justification.

    The regression it exists for is concrete and was real until this PR:
    `post_pattern_suggest_category_bulk` called
    `ledger.resolution.resolved_postings`, which replays the whole ledger
    through every overlay stage in Python. That is super-linear in the ledger
    the way `holdings.lots_table` is (C4b), so it separates from a query over
    the projection here long before it does on a wall clock.
    """
    small = _median_post_seconds(request_as("small"), path, {})
    big = _median_post_seconds(request_as("big"), path, {})

    factor = big / small
    assert factor < MAX_INCOME_STATEMENT_SCALING_FACTOR, (
        f"{path} cost {factor:.1f}x more for {VOLUME_RATIO:.0f}x the ledger "
        f"({small * 1000:.0f} ms, then {big * 1000:.0f} ms). Linear would be {VOLUME_RATIO:.0f}x and "
        f"quadratic {VOLUME_RATIO**2:.0f}x. Something in the action now resolves the ledger rather than querying it."
    )


def _invalidate_everything(client: TestClient) -> None:
    """Make a wide change, so the next read has to rebuild the whole projection.

    A transfer rule is the honest way to do it: its `description_contains`
    can match anything, so `accounting.db.projection` enqueues every one of
    the user's transactions — the widest blast radius any single write has,
    and the case the rebuild bound exists for.
    """
    response = client.post(
        "/api/v1/accounting/transfer-rules",
        params={},
        json={"description_contains": "PERF", "counterparty_account_id": None},
    )
    assert response.status_code in {200, 201}, response.text


def test_a_cold_projection_is_rebuilt_in_proportion_to_the_ledger(request_as: Callable[[str], TestClient]) -> None:
    """The cost drain-on-read concentrates: a wide write, then the next page pays for the whole recompute.

    Two assertions, and the ratio is the real one. A rebuild resolves every
    transaction exactly once, so it is linear by construction; what would
    break that is a whole-collection overlay read moving inside the batch
    loop, which turns one read into one per batch. That is invisible at a
    single volume and unmistakable across two.
    """

    def _rebuild_seconds(size: str) -> float:
        client = request_as(size)
        _invalidate_everything(client)
        started = time.perf_counter()
        assert client.get(_POSTINGS, params={"limit": _PAGE_SIZE}).status_code == 200
        elapsed = time.perf_counter() - started
        print(f"  cold rebuild + page ({size}) -> {elapsed * 1000:.0f} ms")  # noqa: T201
        return elapsed

    small = _rebuild_seconds("small")
    big = _rebuild_seconds("big")

    assert big < MAX_COLD_REBUILD_SECONDS, (
        f"rebuilding a {BIG_TENANT_TRANSACTIONS}-transaction projection took {big:.2f} s, "
        f"budget {MAX_COLD_REBUILD_SECONDS} s"
    )
    factor = big / small
    assert factor < MAX_REBUILD_SCALING_FACTOR, (
        f"a rebuild cost {factor:.1f}x more for {VOLUME_RATIO:.0f}x the ledger "
        f"({small * 1000:.0f} ms, then {big * 1000:.0f} ms). Linear is {VOLUME_RATIO:.0f}x and expected here; "
        f"quadratic would be {VOLUME_RATIO**2:.0f}x. Something is re-read per batch instead of per drain."
    )


def test_a_narrow_write_costs_a_recompute_of_what_it_touched(request_as: Callable[[str], TestClient]) -> None:
    """The common case, and the one the whole design is for: one override, then a page.

    Must be flat in ledger size — the drain recomputes the transactions the
    triggers named and nothing else. A regression to "any write invalidates
    everything" would show here as the big tenant's figure tracking its
    ledger rather than its edit.
    """

    def _edit_then_page(size: str) -> float:
        client = request_as(size)
        posting = client.get(_POSTINGS, params={"limit": 1}).json()["items"][0]
        # An empty tag set, not a category: it is a real override that writes
        # `posting_overrides` and fires its trigger, and unlike a category it
        # needs no reference row the bulk seed does not create.
        assert (
            client.put(
                f"/api/v1/accounting/postings/{posting['posting_id']}/override", json={"tag_ids": []}
            ).status_code
            == 200
        )
        started = time.perf_counter()
        assert client.get(_POSTINGS, params={"limit": _PAGE_SIZE}).status_code == 200
        elapsed = time.perf_counter() - started
        print(f"  narrow drain + page ({size}) -> {elapsed * 1000:.0f} ms")  # noqa: T201
        return elapsed

    small = _edit_then_page("small")
    big = _edit_then_page("big")

    factor = big / small
    assert factor < MAX_SCALING_FACTOR, (
        f"the page after a single override cost {factor:.1f}x more for {VOLUME_RATIO:.0f}x the ledger "
        f"({small * 1000:.0f} ms, then {big * 1000:.0f} ms). A narrow write should not invalidate the ledger."
    )


MAX_CATEGORY_MERGE_SECONDS = 8.0
"""Wall-clock ceiling for the page after a category merge, over a 10k-transaction ledger.

Item C9's number. A `categories` write used to enqueue every one of the
user's transactions, so merging two categories cost a full projection
rebuild — for a change that can only ever affect the postings *filed under*
the retired key. `accounting.db.projection._CATEGORIES_AFFECTED` is that set.

Measured here, on one seed, changing nothing but the trigger's mapping:
**3,681 ms before and 1,743 ms after**, with 1,879 of the 10,000
transactions filed under the merged-away category (1,879 enqueued rather
than 10,000). Both figures are larger than the 2.0 s PR E quoted for the
same operation, and that is the seed rather than a regression: PR E measured
a tenant with no categories at all, where `apply_category_redirects` returns
early and `load_ledger`'s two taxonomy joins find nothing. A ledger that
actually uses categories is dearer to resolve per transaction, which is
exactly the ledger this saving matters on.

Worst observed across the two CI runs that have carried this case:
**1,419 ms**, against 1,070 ms on the faster. Eight seconds is 5.6x that and
4.1x the local 1,743-1,830 ms, keeping more headroom than the other wall clocks
until this measurement has a distribution of its own.

Deliberately a wall clock and not a ratio: the work is proportional to the
*matched* set rather than to the ledger, so the small tenant says nothing
useful about the big one. What proves the narrowing holds is
`tests/accounting/test_projection_equivalence.py::test_a_category_write_dirties_only_what_it_can_change`,
which asserts the dirty queue's contents in the default suite — deterministic,
and with no runner speed in the answer. This is the backstop that catches a
recompute which stops being proportional at all.
"""

_CATEGORIZED_SHARE = 10
"""One posting in this many is filed under the merged-away category — a tenth of the ledger.

Not all of it, and not one row. All of it would make the narrowing
indistinguishable from the whole-ledger mapping it replaced; one row would
measure an empty recompute and pass however wide the trigger got.
"""

_MERGED_AWAY_CATEGORY = "expense:perf-source"
_MERGE_TARGET_CATEGORY = "expense:perf-target"


def _file_postings_under_a_category(engine: Engine, tenant: uuid.UUID, natural_key: str) -> int:
    """Stamp every `_CATEGORIZED_SHARE`-th posting with a raw `category_id`, and report how many transactions that is.

    Raw `postings.category_id` rather than an override, because that is the
    only column `ledger.categorization.apply_category_redirects` reads — an
    override naming the same category is repointed by its own route and its
    own trigger, and would measure nothing about this one.

    Written in SQL rather than through the importer for the same reason the
    rest of this seed is (see `conftest`): the rows are the subject, not the
    code that writes them.

    Returns
    -------
    int
        How many of the tenant's transactions now have a posting filed under
        `natural_key`.
    """
    with engine.begin() as connection:
        connection.execute(text("SELECT set_config('app.current_user_id', :value, false)"), {"value": str(tenant)})
        connection.execute(
            text("""
                INSERT INTO accounting.categories (user_id, natural_key, name, classification, color)
                VALUES (:tenant, :source, 'Perf Source', 'expense', '#112233'),
                       (:tenant, :target, 'Perf Target', 'expense', '#445566')
                ON CONFLICT DO NOTHING
            """),
            {"tenant": tenant, "source": _MERGED_AWAY_CATEGORY, "target": _MERGE_TARGET_CATEGORY},
        )
        connection.execute(
            text("""
                UPDATE accounting.postings AS p
                SET category_id = c.id
                FROM accounting.categories AS c
                WHERE c.user_id = :tenant AND c.natural_key = :source
                  AND p.user_id = :tenant
                  AND ('x' || substr(md5(p.natural_key), 1, 8))::bit(32)::bigint % :share = 0
            """),
            {"tenant": tenant, "source": _MERGED_AWAY_CATEGORY, "share": _CATEGORIZED_SHARE},
        )
        return connection.execute(
            text("""
                SELECT count(DISTINCT p.transaction_id)
                FROM accounting.postings AS p
                JOIN accounting.categories AS c ON c.id = p.category_id
                WHERE p.user_id = :tenant AND c.natural_key = :source
            """),
            {"tenant": tenant, "source": _MERGED_AWAY_CATEGORY},
        ).scalar_one()


def _unfile_every_posting_and_drop_the_categories(engine: Engine, tenant: uuid.UUID) -> None:
    """Put the tenant back exactly as `conftest` seeded it — no categories, no raw `category_id`, nothing retired.

    The case below is the only one in this module that changes the *shape* of
    the seed rather than adding an overlay row, so it is the only one that has
    to undo itself. Position in the file is not a safeguard: `-k`, `-p xdist`
    or a plugin that shuffles collection would run it first, and every bound
    after it would then be measured against a ledger carrying categories and a
    redirect map — a different seed from the one the bounds were derived from,
    which is the exact failure `test_the_seed_is_the_shape_the_gate_assumes`
    exists to make impossible.

    Every category goes, not only the two this case created: the merge route
    calls `taxonomy.seeded_categories`, which seeds the whole default tree on
    first use, so leaving "only what we added" behind would still not be the
    seeded state. Nothing else references them by then — the postings are
    unfiled first, and this tenant has no budgets, patterns or splits.
    """
    with engine.begin() as connection:
        connection.execute(text("SELECT set_config('app.current_user_id', :value, false)"), {"value": str(tenant)})
        connection.execute(
            text("UPDATE accounting.postings SET category_id = NULL WHERE user_id = :tenant"),
            {"tenant": tenant},
        )
        connection.execute(text("DELETE FROM accounting.categories WHERE user_id = :tenant"), {"tenant": tenant})


def test_a_category_merge_recomputes_only_the_postings_filed_under_it(
    request_as: Callable[[str], TestClient], app_runtime_engine: Engine, tenants: dict[str, uuid.UUID]
) -> None:
    """Item C9, at the volume it was costed at: a merge, then the next page.

    The one case here that changes the *shape* of the shared seed, so it is
    bracketed on both sides. It asserts its own precondition, because a setup
    step that reports success whether or not it did anything is how B5
    survived for months (see `conftest._analyze_as_owner`); and it restores
    the seed in a `finally`, because the alternative is every bound in this
    module depending on this function staying last in the file.

    Last in the file anyway, which is belt and braces rather than the
    mechanism.
    """
    client = request_as("big")
    categorized = _file_postings_under_a_category(app_runtime_engine, tenants["big"], _MERGED_AWAY_CATEGORY)
    print(f"  {categorized} of {BIG_TENANT_TRANSACTIONS} transactions filed under {_MERGED_AWAY_CATEGORY}")  # noqa: T201
    assert categorized > BIG_TENANT_TRANSACTIONS // (2 * _CATEGORIZED_SHARE), (
        f"only {categorized} transactions carry the merged-away category, so this would time a recompute of "
        f"almost nothing and pass however wide the staleness trigger became"
    )
    assert categorized < BIG_TENANT_TRANSACTIONS // 2, (
        f"{categorized} of {BIG_TENANT_TRANSACTIONS} transactions carry it, which is enough of the ledger that a "
        f"whole-ledger invalidation would be indistinguishable from the narrow one"
    )
    try:
        # The stamp itself dirtied those transactions, through `postings`' own
        # trigger. Drained here so the measurement below is the merge's cost
        # and not this fixture's.
        assert client.get(_POSTINGS, params={"limit": 1}).status_code == 200

        merged = client.post(
            f"/api/v1/accounting/categories/{_MERGED_AWAY_CATEGORY}/rename", json={"name": "Perf Target"}
        )
        assert merged.status_code == 200, merged.text
        assert merged.json()["merged"] is True, (
            "the rename did not merge, so no category was retired and nothing recomputed"
        )

        started = time.perf_counter()
        assert client.get(_POSTINGS, params={"limit": _PAGE_SIZE}).status_code == 200
        elapsed = time.perf_counter() - started
        print(f"  category merge + page -> {elapsed * 1000:.0f} ms")  # noqa: T201
    finally:
        _unfile_every_posting_and_drop_the_categories(app_runtime_engine, tenants["big"])
        # Those two statements dirtied the same transactions again. Drained
        # here rather than left for whichever case runs next, which would
        # otherwise time a recompute it did not cause.
        assert client.get(_POSTINGS, params={"limit": 1}).status_code == 200

    assert elapsed < MAX_CATEGORY_MERGE_SECONDS, (
        f"the page after a category merge took {elapsed:.2f} s over a {BIG_TENANT_TRANSACTIONS}-transaction "
        f"ledger with {categorized} affected transactions, budget {MAX_CATEGORY_MERGE_SECONDS} s"
    )
