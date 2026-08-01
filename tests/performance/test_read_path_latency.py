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

from http_api.pagination import PAGE_LIMIT_MAX
from tests.performance.conftest import BIG_TENANT_TRANSACTIONS, SMALL_TENANT_TRANSACTIONS

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi.testclient import TestClient

pytestmark = pytest.mark.perf

_POSTINGS = "/api/v1/accounting/postings"
_EXPORT = "/api/v1/accounting/ledger/export"
_CATEGORY_TOTALS = "/api/v1/accounting/income-statement/category-totals"

_LEDGER_WINDOW = {"start": "2019-01-01", "end": "2030-01-01"}
"""Wider than the seeded ledger, so the measurement covers every posting rather than a slice of them."""

_PAGE_SIZE = 200
"""The default page the Transactions screen asks for (`http_api.pagination.PAGE_LIMIT_DEFAULT`)."""

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


def _median_seconds(client: TestClient, path: str, **params: int | str) -> float:
    """Time one request repeatedly and return the median, discarding a warm-up.

    Parameters
    ----------
    client
        The tenant's client.
    path
        Endpoint to call.
    **params
        Query parameters.

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
