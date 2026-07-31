"""The latency gate over the two paginated read paths.

PR 2 and PR 4 made `GET /postings` and `GET /ledger/export` cost
O(what is shown) rather than O(all history). Nothing stopped that from
regressing: the bundle budget guards bytes, and no gate guarded time. This
module is that gate.

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
quadratic work scales at 25x. The bound below sits between those, far from
both.

**The wall-clock assertion** is a coarse backstop for the regressions
scaling cannot see: a constant per-request cost that is the same at 2k as at
10k (an unindexed lookup, a needless full-table count, an accidental
`ANALYZE`). It is set several times above the measured local figure,
because that is the only honest way to set a wall clock on hardware you do
not control.

Both are measured as a median of repeated calls, not a single one, because
the first request through a fresh process pays for imports, Polars' thread
pool and the connection handshake, and none of that is what regresses.

What this gate does not catch, stated plainly
---------------------------------------------
A constant-factor slowdown of roughly 2x or less. This was measured rather
than assumed: deliberately breaking `get_postings` to resolve the whole
ledger per request (`limit=None`) moved the 10k page from 275 ms to 526 ms
and the scaling ratio from 2.6x to 3.6x — a real regression that neither
assertion below rejects. It cannot: 3.6x is too close to the healthy 2.6x
to separate on a shared runner, and no CI-safe wall clock distinguishes
275 ms from 526 ms.

That is a consequence of the read path's real shape rather than a gap in
the gate. Even healthy, `GET /postings` is mostly proportional to total
ledger size, not page size — fitting the two measured volumes gives about
61 ms fixed and 0.021 ms per transaction, so at 10k roughly three quarters
of the page's cost is already ledger-proportional. `load_overrides`, the
rules and the merges are each loaded in full regardless of the page; known
gap 6 is the entry that owns that, and closing it is what would make a
tighter ratio possible here. Until then this gate catches complexity
changes and collapses, which is what it claims and no more.
"""

from __future__ import annotations

import statistics
import time
from typing import TYPE_CHECKING

import pytest

from accounting.api.api_models import PAGE_LIMIT_MAX
from tests.performance.conftest import BIG_TENANT_TRANSACTIONS, SMALL_TENANT_TRANSACTIONS

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi.testclient import TestClient

pytestmark = pytest.mark.perf

_POSTINGS = "/api/v1/accounting/postings"
_EXPORT = "/api/v1/accounting/ledger/export"

_PAGE_SIZE = 200
"""The default page the Transactions screen asks for (`api_models.PAGE_LIMIT_DEFAULT`)."""

_REPEATS = 5
"""Calls per measurement. The median of five is stable enough to compare and cheap enough to run."""

_WARMUP = 1
"""Calls made and thrown away before timing, so imports and pool setup are not in the sample."""

VOLUME_RATIO = BIG_TENANT_TRANSACTIONS / SMALL_TENANT_TRANSACTIONS
"""5.0 — how much more data the big tenant has."""

MAX_SCALING_FACTOR = 8.0
"""How much slower the big tenant's page may be than the small tenant's.

Three reference points, all for a 5x difference in ledger size. Linear in
the ledger would be 5.0. Quadratic would be 25.0. Measured healthy over six
local runs: **2.4-2.7x** for `GET /postings` and **1.5-1.8x** for the
export, plus one anomalous run at 0.8x where the larger tenant came back
faster than the smaller one — noise here moves the ratio down, towards
passing, not up.

Eight is chosen to sit above the first and far below the third: three times
the measured figure, so runner noise and a legitimate n-log-n term cannot
reach it, and a third of quadratic, so the regression it exists for cannot
hide under it.
"""

MAX_DEEP_OFFSET_FACTOR = 4.0
"""How much more the last page of an export may cost than the first.

Measured at 1.1-1.2x. `OFFSET` is O(offset) in Postgres by nature, so this
is not asserting it is free; it is asserting that walking to the end of the
collection stays a small constant multiple rather than becoming the
dominant cost of a backup.
"""

MAX_POSTINGS_PAGE_SECONDS = 3.0
"""Wall-clock ceiling for one 200-transaction page of `GET /postings` over a 10k-transaction ledger.

Measured locally at 0.28 s. The ceiling is roughly 10x that. That multiple
is not timidity: the GitHub runners this executes on are two-core
containers with no I/O isolation, and the honest spread between a quiet one
and a loaded one is several-fold. A gate that flaps gets deleted, which
would leave less protection than a loose one. The scaling assertion above
is the sensitive instrument; this one catches a collapse.
"""

MAX_EXPORT_PAGE_SECONDS = 2.0
"""Wall-clock ceiling for one 5,000-posting page of `GET /ledger/export`.

Measured locally at 0.17 s, same runner reasoning. The export is the
cheaper of the two by construction — it applies no overlay — so its ceiling
is lower even though its page covers more rows.
"""


def _median_seconds(client: TestClient, path: str, **params: int) -> float:
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


def test_the_ledger_export_page_stays_within_its_wall_clock_budget(request_as: Callable[[str], TestClient]) -> None:
    """The same backstop for the export path."""
    elapsed = _median_seconds(request_as("big"), _EXPORT, limit=PAGE_LIMIT_MAX)

    assert elapsed < MAX_EXPORT_PAGE_SECONDS, (
        f"one {PAGE_LIMIT_MAX}-posting export page took {elapsed:.2f} s, budget {MAX_EXPORT_PAGE_SECONDS} s"
    )
