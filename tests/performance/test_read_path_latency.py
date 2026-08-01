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

Every figure here was re-measured after B5
------------------------------------------
The numbers this module used to quote were taken while its own `ANALYZE`
was silently doing nothing (see `conftest._analyze_as_owner`), so they
described a query planned from absent statistics rather than the one the
application issues. They are not adjusted here, they are replaced: the old
values are not a baseline this can be compared against.

The first post-B5 CI run, kept because every bound in this module is
supposed to be derived from CI rather than from a laptop and until now only
one such figure existed:

| measurement | 2k tenant | 10k tenant | ratio |
|-------------|-----------|------------|-------|
| `GET /postings`, 200 | 55 ms | 62 ms | 1.13x |
| `GET /ledger/export`, 5,000 | 123 ms | 172 ms | 1.40x |
| deep offset (0 -> 15,000) | 169 ms | 208 ms | 1.23x |
| `category-totals`, whole history | 119 ms | 426 ms | 3.58x |

and, for the page sizes C6 used to fail on, 79 ms at 400, 136 ms at 1,000
and 667 ms at `PAGE_LIMIT_MAX`. One run is a datapoint, not a distribution —
C8 is the item that collects enough of them to tighten the bounds below.

What C1 changed, measured locally
---------------------------------
`GET /postings` is served from `accounting.resolved_postings` now rather
than by running the overlay pipeline per request, and it got *faster* while
gaining filters: 53 ms at 2k and 77 ms at 10k, against 86 and 91 ms for the
unfiltered page immediately before. A filtered, sorted page — a substring
search and a resolved predicate, ordered on a column that is not the page's
key — is 55 and 84 ms, i.e. within noise of the unfiltered one, because the
filter is an indexed read over stored values rather than anything the
request has to compute. The largest page the contract allows fell from
537 ms to 354 ms.

The two new shapes have no pre-C1 counterpart:

| measurement | 2k tenant | 10k tenant | ratio |
|-------------|-----------|------------|-------|
| filtered + sorted page, 200 | 55 ms | 84 ms | 1.5x |
| `GET /postings/months` | — | 26 ms | — |
| cold rebuild, then a page | 517 ms | 2,029 ms | 3.9x |
| one override, then a page | 94 ms | 91 ms | 1.0x |

The last two are the drain-on-read design stated as numbers. A wide write
concentrates a whole recompute onto the next read and it stays sub-linear; a
narrow one is flat in ledger size, which is the property the whole
per-transaction invalidation exists for. All local, all pending CI figures
for the same reason every other bound here is loose — see C8.

What this gate does not catch, stated plainly
---------------------------------------------
A constant-factor slowdown of roughly 2x or less, and — for now — a page
that stops being a page at all.

The second half is measured rather than assumed. Deliberately breaking
`get_postings` to resolve the whole ledger per request (`limit=None`) moves
the 10k page from 68 ms to 471 ms and the scaling ratio from 1.0x to 3.7x.
That is a much larger separation than the same experiment produced before
C6 was fixed, when healthy was 2.6x and broken was 3.6x and the two could
not be told apart. It is nonetheless still under `MAX_SCALING_FACTOR`, so
the assertion below does not reject it. Tightening the bound to catch it is
a real option now and deliberately not taken here: local healthy runs sit
at 1.0-1.8x, CI has historically measured roughly twice the local ratio
(see `MAX_INCOME_STATEMENT_SCALING_FACTOR`), and a bound derived from a
quiet laptop is how a gate starts flapping. The first post-B5 CI run above
is encouraging on that front — 1.13x for the postings page, against the
1.0-1.8x measured locally — but it is one run, and Item C8 owns tightening
these once there are enough of them to form a distribution.

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

from accounting.api.api_models import NO_SUBCATEGORY, UNCATEGORIZED
from http_api.pagination import PAGE_LIMIT_MAX
from tests.performance.conftest import (
    BIG_TENANT_TRANSACTIONS,
    SMALL_TENANT_TRANSACTIONS,
    REAL_ACCOUNT_KEY,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi.testclient import TestClient

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

MAX_SCALING_FACTOR = 8.0
"""How much slower the big tenant's page may be than the small tenant's.

Three reference points, all for a 5x difference in ledger size. Linear in
the ledger would be 5.0. Quadratic would be 25.0. Measured healthy over
five local runs after B5 and C6: **1.0-1.8x** for `GET /postings` and
**1.2-1.4x** for the export. Both are far below the 2.4-2.7x and 1.5-1.8x
this constant used to quote, because those were measured against a plan
chosen from absent statistics and, for the postings page, one that scanned
every posting the tenant owned.

Eight is left where it is rather than re-derived from those figures. It
still sits far below quadratic, so the regression it exists for cannot hide
under it, and the case for tightening it is real but needs CI numbers
rather than local ones — see this module's docstring and item C8.
"""

MAX_INCOME_STATEMENT_SCALING_FACTOR = 12.0
"""`MAX_SCALING_FACTOR`'s counterpart for the one read path that is *supposed* to be linear.

The two paged paths are healthy at 2.4-2.7x for 5x the ledger, because a
page is bounded and only their fixed costs grow. The income statement has
no page: it reads every posting in its window by construction (known gap
C5), so linear — 5.0 — is what healthy looks like, and 8.0 would leave a
correct implementation 1.6x of headroom on a two-core shared runner.
Measured 2.7x locally and **5.1x on a CI runner** (129 ms at 2k, 655 ms at
10k), which is the number this bound has to accommodate.

Twelve is a little over twice the linear figure and under half of
quadratic (25), so it still rejects the regression it exists for — a rate
resolved per posting instead of joined once — without failing on a runner
having a bad minute.
"""

MAX_DEEP_OFFSET_FACTOR = 4.0
"""How much more the last page of an export may cost than the first.

Measured at 1.1-1.3x over five local runs after B5. `OFFSET` is O(offset)
in Postgres by nature, so this is not asserting it is free; it is asserting
that walking to the end of the collection stays a small constant multiple
rather than becoming the dominant cost of a backup.
"""

MAX_POSTINGS_PAGE_SECONDS = 3.0
"""Wall-clock ceiling for one 200-transaction page of `GET /postings` over a 10k-transaction ledger.

Measured locally at 0.065-0.070 s over five runs after B5 and C6, and at
64 ms on CI, against the 0.28 s this constant used to quote and the 305 ms
CI measured before B5. The ceiling is left at 3.0 s, which
is now roughly 45x rather than 10x — loose, and kept that way for the same
reason as `MAX_SCALING_FACTOR`: the honest re-derivation needs CI figures
(item C8), and a wall clock is the coarse backstop here rather than the
sensitive instrument.

Neither multiple is timidity: the GitHub runners this executes on are
two-core containers with no I/O isolation, and the honest spread between a
quiet one and a loaded one is several-fold. A gate that flaps gets deleted,
which would leave less protection than a loose one. The scaling assertion
above is the sensitive instrument; this one catches a collapse.
"""

MAX_LARGE_POSTINGS_PAGE_SECONDS = 4.0
"""Wall-clock ceiling for the largest page `GET /postings` advertises, over a 10k-transaction ledger.

Looser than `MAX_POSTINGS_PAGE_SECONDS` on purpose, and measuring a
different thing. A `PAGE_LIMIT_MAX` page returns every transaction the big
tenant has, so it is allowed to cost roughly what the whole ledger costs;
what it is not allowed to do is fail. The failure this guards against
produced a 500 at the 15 s `statement_timeout`, not a slow answer, so any
ceiling comfortably under that bound catches it.

Measured locally over the 10k ledger, with statistics present: **86 ms at
400, 132 ms at 1,000 and 427 ms at 5,000**; on CI, 79/136/667 ms. Four seconds is roughly 9x the
largest of those, the same headroom `MAX_POSTINGS_PAGE_SECONDS` carries, and
well under the 15 s bound whose breach is the actual regression.
"""

MAX_FILTERED_PAGE_SECONDS = 3.0
"""Wall-clock ceiling for one filtered, sorted page over a 10k-transaction ledger.

The read C1 exists to make possible, and the one whose *shape* is easiest to
lose: the filter runs over `accounting.resolved_postings`, and a predicate
that stopped being expressible there — a join back to the raw ledger, a
per-row lookup — would turn a page into a scan of history again.

Measured locally over the 10k ledger, with statistics present: a filtered
page is 4 ms of SQL at 20k projection rows and 23 ms at 100k, on top of the
same fixed request cost the unfiltered page pays. The ceiling is left equal
to `MAX_POSTINGS_PAGE_SECONDS` rather than set tighter, for the reason that
constant gives: a wall clock on a two-core shared runner is the coarse
backstop, and the scaling assertion beside it is the instrument.
"""

MAX_DRILLDOWN_PAGE_SECONDS = 3.0
"""Wall-clock ceiling for one page of the insights drilldown over a 10k-transaction ledger.

Its own case rather than a variation on `MAX_FILTERED_PAGE_SECONDS`, because
the predicate is a different shape: two array-valued filters, each carrying
a sentinel that means "is null", against a date window. C6 was a plan flip
caused by exactly that — an array parameter's effect on a cost estimate,
which is invisible until an array is what you pass.

Measured locally with statistics present: **37 ms at 2k and 58 ms at 10k**,
a 1.6x ratio — in line with the 1.5x the filtered page scales at, so the
arrays cost nothing structural. Same runner reasoning as
`MAX_FILTERED_PAGE_SECONDS` for why the ceiling is not set nearer to it.
"""

MAX_MONTHS_SECONDS = 2.0
"""Wall-clock ceiling for the month picker's whole option list (C2).

One row per month the user has ever transacted in — tens of entries for a
decade — but computed by grouping every resolved posting, so it is linear in
the ledger and worth a bound. Measured at 8 ms over 20k projection rows and
34 ms over 100k.
"""

MAX_BULK_ACTION_SECONDS = 6.0
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

Measured locally over the 10k ledger with an empty filter, i.e. the widest
set any of them can resolve: **34 ms** for `matching-ids`, **319 ms** for
`validate-pending` (which loads an override row per matched id, and is the
only one of the three that is not simply a projection query), and **78 ms**
for the pattern suggester. Against 2k: 23 ms, 91 ms and 28 ms. Six seconds
is roughly 19x the largest, the same order of headroom the other wall clocks
carry and for the same reason — a two-core shared runner with no I/O
isolation.
"""

MAX_COLD_REBUILD_SECONDS = 30.0
"""Wall-clock ceiling for the first read after the whole projection is invalidated.

The cost the drain-on-read design concentrates rather than removes: a wide
change — a transfer rule, an account, a category — enqueues every
transaction, and the next read pays for a full recompute before it answers.

This gate measures **517 ms for the 2k tenant and 2,029 ms for the 10k
tenant**, request included. Those are the numbers to compare a future run
against; the module docstring's table carries them too.

A separate standalone script measured the recompute *alone*, with no request
around it and a different seed — 0.77 s over 10k transactions and 3.8 s over
50k, of which the `COPY` of two projection rows per transaction was 193 ms
and 1,306 ms. It is quoted here only because it is where the extrapolation to
about 13 s on the 170k-transaction audit database comes from, and it is
deliberately not the figure this bound is set against.

Thirty seconds is deliberately loose. What this is defending is that a
rebuild stays *linear*: the failure that would matter is a recompute that
re-reads a whole-collection overlay per batch, or per transaction, which at
10k would not finish inside this bound at all. The scaling assertion beside
it is what says so precisely.
"""

MAX_REBUILD_SCALING_FACTOR = 12.0
"""How much slower the big tenant's rebuild may be than the small tenant's, for 5x the ledger.

A rebuild is linear work by construction — every transaction is resolved
once — so 5.0 is what healthy looks like and this is a little over twice it,
the same headroom `MAX_INCOME_STATEMENT_SCALING_FACTOR` carries for the same
reason. Quadratic would be 25.

The regression it exists for is concrete: `ledger.resolution.overlay_context`
reads the rules, accounts, splits, merges and links once per *drain*, and
`repositories.projection` walks its batches inside that. Moving either read
inside the batch loop — or worse, inside the per-transaction path — would
put the whole-collection cost on every batch and show up here long before it
showed up as a timeout.
"""

MAX_EXPORT_PAGE_SECONDS = 2.0
"""Wall-clock ceiling for one 5,000-posting page of `GET /ledger/export`.

Measured locally at 0.119-0.137 s over five runs after B5, and at 169 ms on
CI, against the 0.17 s this constant used to quote — the export's plan did not depend on
C6's predicate, so it moved only by the amount real statistics were worth.
Same runner reasoning as `MAX_POSTINGS_PAGE_SECONDS`. The export is the
cheaper of the two by construction — it applies no overlay — so its ceiling
is lower even though its page covers more rows.
"""

MAX_CATEGORY_TOTALS_SECONDS = 6.0
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

Six seconds is roughly 9x the 656 ms a CI runner measured before B5. The
first post-B5 CI run measures 555 ms and a 3.58x ratio, so the ceiling keeps
its headroom rather than needing it; it is left alone until C8 has enough
runs to re-derive it from a distribution rather than one sample.
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
