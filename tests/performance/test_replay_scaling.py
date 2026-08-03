"""The gate over `ledger.replay.replay_ledger`'s cost curve.

Nothing measured this, and that is precisely why a quadratic survived in it
from the first release to `v1.12.0` (item C4b). The latency gate next door
watches the `accounting` read paths and would never have seen it: the
`trades` replay touches no database, so no CI job timed it, and every
functional test replays a ledger of a dozen events where quadratic and
linear are the same number.

What it is defending
--------------------
The same thing `test_read_path_latency.py` defends, in the same currency:
the **shape** of the cost curve, not a wall clock. A replay whose per-event
work grows with how many lots are already open is quadratic, and that is
the exact regression this exists for — a `sort` reintroduced inside the
`SELL` branch, a dividend that touches every open lot again, a lot list
rebuilt through a polars frame per event. All three are invisible at a
dozen events and unmistakable at twenty thousand.

**The scaling assertion** replays the same generated ledger shape at two
sizes ten times apart and asserts the ratio. It divides the runner's speed
out of the answer: a slow runner makes both numbers larger and leaves the
ratio alone. Linear work scales at 10x; quadratic work scales at 100x.

**The wall-clock assertion** is the coarse backstop for what a ratio cannot
see — a constant-factor collapse that is equally bad at both sizes.

The ledger shape is load-bearing
--------------------------------
`generated_ledger`'s default sells a random slice of up to a third of the
position, which makes the open-lot book **self-limiting**: sells scale with
holdings, the book stops growing, and per-event work proportional to the
book is therefore *constant*. Measured on that shape, the retired quadratic
implementation scales at 4.83x for 5x the events — indistinguishable from
linear. A gate built on it would have passed the very defect it exists to
catch.

So this measures `sell_shares=1.0`, which lets the book grow in proportion
to the ledger (5,439 open lots at 10k events against 1,113 at 2k), and
`split_p=0.0`, because a `SPLIT` genuinely is O(open lots) — every lot's
share count and cost basis change — so a ledger that both accumulates and
splits at a fixed rate per event cannot be replayed in linear time by any
implementation, and asserting otherwise would be asserting something false.
Accumulating without splitting is also the shape a real buy-and-hold
ledger has, which is the one the cost mattered on.

Where the bounds come from
--------------------------
Local measurement, quoted on each constant below, and local-only where
`test_read_path_latency.py`'s bounds come from six CI runs. There is no
data-dependent variance to characterise here — no query planner, no
statistics, no second tenant, no I/O — so what is left is the runner's own
scheduling, which the ratio divides out.

Every run prints its median and its ratio, for the reason item C8 exists:
that item had to generate six CI runs by hand because the figures it needed
to re-derive a bound from had never been recorded anywhere. From here they
accumulate in the job log.

The one honest caveat is that the *small* measurement is close to the noise
floor — 5 to 9 ms across eight local runs, against 57 to 68 ms for the big
one — so the ratio's spread is wider than the underlying cost curve's:
7.29x to 13.11x for the same code on the same two ledgers. The bound
absorbs that rather than pretending it away. The figures to beat are the
retired implementation's own, measured through
`test_replay_equivalence._reference_replay` on the same ledgers: **36.77x,
41.85x and 49.67x**.
"""

from __future__ import annotations

import statistics
import time

import pytest

from tests.support.generated_ledger import generated_ledger
from trades.config import AppConfig
from trades.ledger.replay import replay_ledger

pytestmark = pytest.mark.perf

CONFIG = AppConfig()

SMALL_EVENTS = 2_001
BIG_EVENTS = 20_001
EVENT_RATIO = BIG_EVENTS / SMALL_EVENTS
"""10.0 — how many times longer the big ledger is."""

_SEED = 7
_SELL_PROBABILITY = 0.15
_ACCUMULATING = {"sell_shares": 1.0, "split_p": 0.0}
"""The ledger shape whose open-lot book grows with its length — see this module's docstring."""

_REPEATS = 7
"""Replays per measurement. The median of seven, following the latency gate's median-of-five for the same reason."""

_WARMUP = 1
"""Replays made and thrown away first, so Polars' frame construction warm-up is not in the sample."""

MAX_REPLAY_SCALING_FACTOR = 22.0
"""How much dearer replaying the big ledger may be than the small one, for 10x the events.

Linear is 10.0 and quadratic is 100.0. Worst observed locally across eight
runs and three seeds: **13.11x**, in a band of 7.29-13.11. Twenty-two is
1.68x that, matching `MAX_SCALING_FACTOR`'s own 1.6x margin next door
rather than tightening on a distribution this small.

Two things sit between the healthy figure and the nominal 10.0, and neither
is a hidden super-linearity in the walk. Most of it is the small
measurement's noise floor: 5-9 ms is a handful of scheduler quanta, so its
own spread lands almost entirely in the ratio. The rest is the live object
set growing — the fold holds every open lot and every closed lot in memory,
so CPython's generational collector walks proportionally more tracked
objects on each pass, which moves per-event cost from about 2.6 to about
3.0 microseconds and flattens rather than compounding.

What it rejects: the implementation this replaced measures 36.77x, 41.85x
and 49.67x on these same three ledgers, so the nearest broken figure is
1.67x above this bound and the worst healthy one 1.68x below it. It would
also reject a partial regression — a `sort` restored to the `SELL` branch
alone, without the per-`DIVIDEND` rebuild.
"""

MAX_BIG_REPLAY_SECONDS = 2.0
"""Wall-clock ceiling for one replay of a 20,000-event accumulating ledger.

Worst observed locally: **88 ms**. Two seconds is 23x that, deliberately
looser than the 8-12x the latency gate's wall clocks carry: those have six
CI runs behind them and this has none, and a replay runs on whatever core
the runner gives a single Python thread with no server to amortize against.

It is the backstop, not the instrument. What it catches is a collapse the
ratio cannot see — the same constant factor at both sizes — and the number
that matters for it is the one the product cares about: ten call sites
replay per request, four of them once per event date, so a replay of this
ledger costing seconds rather than tens of milliseconds is a broken screen
whatever its scaling looks like. The retired implementation took **26.8 s**
on this ledger.
"""


def _median_replay_seconds(n_events: int) -> float:
    """Median wall-clock seconds for one replay of a generated ledger of `n_events` events.

    Returns
    -------
    float
    """
    ledger = generated_ledger(n_events, _SEED, _SELL_PROBABILITY, **_ACCUMULATING)
    for _ in range(_WARMUP):
        replay_ledger(ledger, CONFIG)
    samples = []
    for _ in range(_REPEATS):
        started = time.perf_counter()
        replay_ledger(ledger, CONFIG)
        samples.append(time.perf_counter() - started)
    median = statistics.median(samples)
    # Printed for the same reason `test_read_path_latency.py` prints its own
    # medians: the CI job runs `pytest -m perf -v -s`, so every run leaves its
    # figures in the log, and re-deriving a bound from a distribution of runs
    # is only possible if the runs recorded one. Item C8 had to generate six
    # of them by hand because the numbers were not there to read.
    print(f"  replay {n_events} events -> {median * 1000:.1f} ms median of {_REPEATS}")  # noqa: T201
    return median


def test_the_measured_ledgers_accumulate_open_lots_in_proportion_to_their_length() -> None:
    """Guard the guard: on a self-limiting ledger the quadratic this gate exists for scales linearly.

    The two assertions below are the premise the scaling case rests on, and
    both used to be false of the default shape. If a change to
    `generated_ledger` ever makes the book stop growing, the scaling case
    keeps passing and stops testing anything — which is the shape of hole
    PR H found twice in this repo's own gates.
    """
    small = replay_ledger(generated_ledger(SMALL_EVENTS, _SEED, _SELL_PROBABILITY, **_ACCUMULATING), CONFIG)
    big = replay_ledger(generated_ledger(BIG_EVENTS, _SEED, _SELL_PROBABILITY, **_ACCUMULATING), CONFIG)

    assert not small.closed_lots.is_empty(), "no SELL was replayed, so the FIFO path is not measured at all"
    assert len(big.open_lots) > 4 * len(small.open_lots), (
        f"the open-lot book is not growing with the ledger — {len(small.open_lots)} lots at {SMALL_EVENTS} events "
        f"and {len(big.open_lots)} at {BIG_EVENTS}. Per-event work proportional to the book is invisible on a "
        "ledger whose book has a ceiling, so the scaling assertion below would admit a quadratic."
    )


def test_a_replay_scales_with_the_ledger_and_not_with_the_lots_it_has_open() -> None:
    """The real detector: 10x the events must cost roughly 10x, not 100x (item C4b)."""
    small = _median_replay_seconds(SMALL_EVENTS)
    big = _median_replay_seconds(BIG_EVENTS)
    ratio = big / small
    print(f"  scaling {SMALL_EVENTS} -> {BIG_EVENTS} events: {ratio:.2f}x for {EVENT_RATIO:.0f}x the events")  # noqa: T201

    assert ratio < MAX_REPLAY_SCALING_FACTOR, (
        f"replaying {BIG_EVENTS} events cost {ratio:.2f}x replaying {SMALL_EVENTS} "
        f"({big * 1000:.0f} ms against {small * 1000:.0f} ms) for {EVENT_RATIO:.0f}x the events. "
        "Some per-event work is proportional to the open-lot book again."
    )


def test_a_replay_of_a_long_ledger_stays_within_its_wall_clock_budget() -> None:
    """The backstop: a constant-factor collapse that leaves the ratio alone."""
    elapsed = _median_replay_seconds(BIG_EVENTS)

    assert elapsed < MAX_BIG_REPLAY_SECONDS, (
        f"one replay of {BIG_EVENTS} events took {elapsed:.2f} s; ten call sites replay per request and four of "
        "them replay once per event date"
    )
