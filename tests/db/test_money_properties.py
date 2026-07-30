"""The money seam as properties: what has to hold for *every* amount, not just the chosen ones.

`test_money.py` next door pins the money policy with examples — the values
that famously break under IEEE-754 (`0.1 + 0.2`, a hundred cents), plus the
wire contract. Examples are the right tool for those: each one names a
specific historical failure. They are the wrong tool for the question this
module asks, which is not "does this value survive?" but "*which* values
survive, and by how much do the rest miss?".

That question is what `accounting.ledger.frame`'s docstring left open. It
declares a single, deliberate `Decimal -> float` crossing at the analytics
projection and calls the resulting imprecision "confined to aggregation,
bounded by `NUMERIC(18, 4)` inputs" — bounded, but nowhere stated. The
float slack the ledger already carries (`ledger.replay._ZERO_SUM_TOLERANCE`,
`ledger.transfers._AMOUNT_TOLERANCE`) is a number someone picked, and
nothing in the suite said it was the right number. So these tests state the
bound and then let hypothesis attack it, which is the only way a bound is
worth anything: a tolerance no adversarial search has tried to break is a
guess.

Three kinds of property live here, and they are deliberately different in
strength:

- **Exact, for quantization.** `quantize_money` is pure decimal
  arithmetic, so idempotence, scale, sign and ordering are asserted as
  equalities. Anything less would be hiding a bug.
- **Bounded, for the analytics crossing.** `to_analytics_float` is
  documented as lossy ("never compare it for exact equality"), so every
  property about it is an inequality against a named tolerance derived
  from the float64 format, never an equality. Asserting equality there
  would either be false or would be true only for the examples someone
  happened to pick.
- **A stated domain, for exactness.** The interesting finding is that
  "lossy" has a sharp edge: below a magnitude these tests compute rather
  than guess, the crossing is *exactly* reversible, and above it two
  distinct stored amounts collapse onto one float. Both halves are pinned
  — the property below the edge, and a witness above it — so the edge
  cannot move unnoticed.

Every strategy is bounded by what the column can actually hold, because a
property about values the schema rejects would be a property about nothing.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from accounting.ledger.replay import _ZERO_SUM_TOLERANCE
from db.money import (
    MONEY_QUANTUM,
    MONEY_SCALE,
    SHARES_QUANTUM,
    SHARES_SCALE,
    quantize_money,
    quantize_shares,
    to_analytics_float,
    to_decimal,
)

# --------------------------------------------------------------------------
# The domain: what the columns can actually hold.
# --------------------------------------------------------------------------

MONEY_MAX = Decimal("99999999999999.9999")
"""The largest value `db.base.MONEY` (`NUMERIC(18, 4)`) can store: 18 digits, 4 of them fractional."""

SHARES_MAX = Decimal("999999999999.99999999")
"""The largest value `db.base.SHARES` (`NUMERIC(20, 8)`) can store: 20 digits, 8 of them fractional."""

money = st.decimals(min_value=-MONEY_MAX, max_value=MONEY_MAX, allow_nan=False, allow_infinity=False)
"""Any amount a `MONEY` column could be asked to hold, at any precision.

NaN and infinity are excluded because `NUMERIC` in this schema cannot hold
them and `quantize` would raise on them — a property over them would be a
property about an error path, which `test_quantize_money_only_raises_outside_the_column_domain`
covers explicitly instead.
"""

shares = st.decimals(min_value=-SHARES_MAX, max_value=SHARES_MAX, allow_nan=False, allow_infinity=False)
"""Any share count a `SHARES` column could be asked to hold, at any precision."""

# --------------------------------------------------------------------------
# The tolerances. Each is derived from the float64 format, not tuned until green.
# --------------------------------------------------------------------------

FLOAT_HALF_ULP = Decimal(2) ** -53
"""Half a unit in the last place, relatively: the most a single correctly-rounded float64 operation can be off.

A float64 has a 53-bit significand, so a value in `[2**e, 2**(e+1))` is
represented on a grid of spacing `2**(e - 52)`. Rounding to nearest lands
within half that spacing, and since `|x| >= 2**e`, the error is at most
`|x| * 2**-53`. Every tolerance below is a count of these.
"""

ROUND_TRIP_TOLERANCE = 2 * FLOAT_HALF_ULP
"""Two half-ulps: the error budget of one `Decimal -> float -> Decimal` trip.

One is spent going out (`float(value)` rounds to the nearest representable
double). One is spent coming back: `to_decimal` routes a float through
`str`, and `repr` produces the *shortest* decimal that reads back as that
same double — which is within half an ulp of it, but is not required to be
the decimal we started from. So the round trip is off by at most one full
ulp, relatively, and this is the bound to state instead of the equality
`db.money.to_analytics_float`'s docstring explicitly forbids.
"""

SUM_TOLERANCE_PER_TERM = 2 * FLOAT_HALF_ULP
"""Two half-ulps *per term*, applied to the sum of the magnitudes.

The textbook forward-error bound for recursive summation of `n` floats is
`(n - 1) * u * sum(|x_i|)` with `u = 2**-53`; each of the `n` inputs also
absorbs one rounding on the way from `Decimal` to `float`. Together that is
under `n * 2**-52 * sum(|x_i|)`, which is why the bound scales with list
length and with the magnitudes summed, not with the magnitude of the
answer. That last point is the one that matters for a ledger: a hundred
million-dollar legs cancelling to zero carry the error of a hundred
million-dollar legs, not the error of zero.
"""

MONEY_FLOAT_EXACT_LIMIT = Decimal(2**39)
"""Below this magnitude a `MONEY` value survives the analytics crossing exactly. Above it, it may not.

The crossing is reversible exactly while the float64 grid is finer than the
storage quantum. Spacing at `|x|` in `[2**e, 2**(e+1))` is `2**(e - 52)`,
so the condition `2**(e - 52) < 0.0001` holds up to `e = 38` and fails from
`e = 39` (spacing `2**-13` = 0.000122, wider than the 0.0001 quantum). So
`2**39` = 549,755,813,888 — about $550 billion — is the edge, and it is
computed from `MONEY_SCALE`, not chosen. `test_two_distinct_money_values_above_the_limit_collapse_onto_one_float`
pins the other side of it.
"""

SHARES_FLOAT_EXACT_LIMIT = Decimal(2**26)
"""The same edge for `SHARES`, and far nearer: about 67.1 million shares.

Same derivation with the eight-place quantum: `2**(e - 52) < 1e-8` holds up
to `e = 25` and fails from `e = 26`. The wider scale that makes `SHARES`
*more* precise in Postgres is exactly what makes it *less* survivable
through a float, four orders of magnitude sooner than money — worth
knowing before a share count that large is ever fed to the analytics
projection.
"""

ZERO_SUM_MAX_LEGS = 20
"""Legs in the balanced transactions generated below.

The schema allows any number >= 2 (see `docs/accounting/architecture.md`,
"The canonical schema"); a split of one deposit into twenty pieces is a
realistic upper end, and the bound this pins scales linearly, so twenty is
a fair stress of a constant used on real transactions.
"""

ZERO_SUM_MAX_LEG = Decimal(1_000_000)
"""Per-leg magnitude in those transactions: a million units, comfortably above any real posting."""


# --------------------------------------------------------------------------
# Quantization: exact properties, asserted as equalities.
# --------------------------------------------------------------------------


@given(money)
def test_quantize_money_is_idempotent(value: Decimal) -> None:
    """Rounding an already-rounded amount must change nothing.

    This is the property that makes `quantize_money` safe to call at every
    write boundary, which is what `db.money` tells callers to do. If it
    were not idempotent, a value that passed through two write paths (an
    import, then a correction) would differ from the same value that passed
    through one — and the ledger would depend on how many times a row had
    been touched rather than on what it says.
    """
    once = quantize_money(value)
    assert quantize_money(once) == once


@given(money)
def test_quantize_money_lands_exactly_on_the_storage_scale(value: Decimal) -> None:
    """The result's exponent is `-MONEY_SCALE`, for every input, at every precision.

    Not merely "close to four places": the exponent itself is pinned, so a
    value can never reach Postgres carrying a scale the column would
    silently truncate. That truncation is the failure `db.money` exists to
    prevent — the in-memory value and the stored value disagreeing until
    the next read.
    """
    assert quantize_money(value).as_tuple().exponent == -MONEY_SCALE


@given(st.integers(min_value=-(10**14) + 1, max_value=10**14 - 1))
def test_a_value_already_at_the_storage_scale_passes_through_untouched(quanta: int) -> None:
    """A `Decimal` built as a whole number of quanta is returned unchanged, digits and all.

    Generating the value as `quanta * MONEY_QUANTUM` rather than filtering a
    decimal strategy is deliberate: it means every example is exactly on the
    storage grid, so the assertion is about the pass-through path and not
    about whichever inputs happened to need no rounding. `compare_total`
    rather than `==` because `==` on `Decimal` compares numeric value and
    would accept a result that had been re-scaled to, say, two places.
    """
    value = quanta * MONEY_QUANTUM
    assert quantize_money(value).compare_total(value) == 0


@given(money)
def test_a_quantized_value_round_trips_through_its_own_text(value: Decimal) -> None:
    """`to_decimal(str(quantized))` is the same number: the DB scale carries no loss.

    This is the round trip the storage path actually performs — a
    `NUMERIC` value crosses the wire to Postgres and back as text, never as
    a float — so it is the one that must be exact. Contrast every
    `to_analytics_float` property below, which cannot be.
    """
    quantized = quantize_money(value)
    assert to_decimal(str(quantized)) == quantized


@given(money)
def test_quantize_money_never_flips_a_sign(value: Decimal) -> None:
    """Rounding may erase a sign, never reverse one.

    The obvious phrasing — "a positive amount stays positive" — is false,
    and stating it that way would be the kind of property that gets weakened
    once it fails. An amount smaller than half a quantum rounds to zero, and
    that is correct: the column cannot hold it. What must never happen is a
    credit becoming a debit, so the property is that the two never have
    opposite signs, which `quantized * value >= 0` says exactly.
    """
    assert quantize_money(value) * value >= 0


@given(money, money)
def test_quantize_money_preserves_ordering(left: Decimal, right: Decimal) -> None:
    """Rounding is monotone: it can merge two amounts, never reorder them.

    `ROUND_HALF_UP` is round-half-away-from-zero, and it would be easy to
    assume the discontinuity at zero breaks monotonicity. It does not, and
    that matters everywhere the app sorts or thresholds on a stored amount
    (largest expenses, budget over/under): a report ordered by the rounded
    value agrees with one ordered by the exact value, up to ties.
    """
    assume(left <= right)
    assert quantize_money(left) <= quantize_money(right)


def test_quantize_money_only_raises_outside_the_column_domain() -> None:
    """The undocumented cliff: past 28 significant digits, quantizing raises instead of rounding.

    Found by widening the strategy past the column's own bounds. `Decimal`
    quantize signals `InvalidOperation` when the result would need more
    digits than the active context allows (28 by default), so
    `quantize_money` has a domain, and it is silent about it. Nothing in the
    app can reach it — `NUMERIC(18, 4)` tops out at 18 digits, six orders of
    magnitude short — which is precisely why this is pinned as a boundary
    rather than reported as a bug: the properties above are entitled to
    assume the column domain, and this test is what makes that assumption
    explicit instead of accidental.
    """
    assert quantize_money(MONEY_MAX) == MONEY_MAX
    with pytest.raises(InvalidOperation):
        quantize_money(Decimal(10) ** 24)


# --------------------------------------------------------------------------
# The analytics crossing: bounded properties, never equalities.
# --------------------------------------------------------------------------


@given(money)
def test_the_analytics_crossing_is_lossy_within_one_ulp(value: Decimal) -> None:
    """A quantized amount comes back from the float projection within `ROUND_TRIP_TOLERANCE`, relatively.

    `db.money.to_analytics_float` says never to compare its output for
    exact equality, and `accounting.ledger.frame` explains why the loss is
    accepted at all. Neither says how large it is. This does: at most one
    ulp of the value, which is the strongest true statement available and
    the number any future tolerance elsewhere in the ledger should be
    derived from.

    The bound is relative, not absolute, and it has to be — a $10 posting
    round-trips to the femto-cent while a $10 billion one is only good to a
    hundredth of a cent, and a single absolute tolerance would be either
    vacuous for the first or wrong for the second.
    """
    quantized = quantize_money(value)
    returned = to_decimal(to_analytics_float(quantized))
    assert abs(returned - quantized) <= abs(quantized) * ROUND_TRIP_TOLERANCE


@given(money)
def test_the_analytics_crossing_is_exactly_reversible_below_the_ulp_limit(value: Decimal) -> None:
    """Under `MONEY_FLOAT_EXACT_LIMIT` the loss is not merely bounded — there is none.

    Worth pinning separately from the bound above because it is the fact
    the dashboard actually depends on. Every real balance, category total
    and budget actual is many orders of magnitude below $550 billion, so in
    practice the "lossy" boundary returns the value it was given; the
    imprecision the docs warn about is entirely an *aggregation* effect, as
    `accounting.ledger.frame` claims, and not something each individual
    amount suffers on the way in.

    Stated as a derived limit rather than a comfortable round number, so
    that if `MONEY_SCALE` ever widens, this test says where the new edge is
    instead of quietly passing at the old one.
    """
    quantized = quantize_money(value)
    assume(abs(quantized) < MONEY_FLOAT_EXACT_LIMIT)
    assert to_decimal(to_analytics_float(quantized)) == quantized


def test_two_distinct_money_values_above_the_limit_collapse_onto_one_float() -> None:
    """The other side of the edge, as a witness: above it, the projection is not injective.

    Two amounts a hundredth of a cent apart — both perfectly storable in
    `NUMERIC(18, 4)`, both distinct in Postgres — become the same float64
    the moment they cross into the analytics frame. This is the concrete
    reason the property above is bounded by a magnitude rather than being
    universal, and it exists so that nobody "simplifies" that bound away.
    """
    lower = MONEY_FLOAT_EXACT_LIMIT + 2 * MONEY_QUANTUM
    upper = lower + MONEY_QUANTUM
    assert lower != upper
    assert to_analytics_float(lower) == to_analytics_float(upper)


@given(money)
def test_the_analytics_crossing_never_changes_a_sign(value: Decimal) -> None:
    """Unlike quantization, the float crossing preserves sign exactly — including for the smallest amount stored.

    There is no underflow to worry about here and the test proves it: the
    smallest non-zero value a `MONEY` column can hold is one quantum,
    which is ~2**-13, nowhere near float64's subnormal range. So a balance
    that is negative in Postgres is negative in every chart derived from
    it, which is a stronger guarantee than the merely-bounded one above.
    """
    quantized = quantize_money(value)
    crossed = to_analytics_float(quantized)
    assert (crossed > 0) == (quantized > 0)
    assert (crossed < 0) == (quantized < 0)


@given(money, money)
def test_the_analytics_crossing_preserves_ordering_weakly(left: Decimal, right: Decimal) -> None:
    """Ordering survives the crossing, but only as `<=`, never as `<`.

    Rounding to nearest is monotone, so the projection can never swap two
    amounts — every "top spend" or "largest account" ordering computed on
    the frame agrees with the exact one. It can, however, tie two amounts
    that Postgres distinguishes, which is the same collapse the witness
    above demonstrates. Asserting strict ordering here would be a property
    that passes for years and then fails on a large enough number; the
    weak form is the one that is actually true.
    """
    assume(quantize_money(left) <= quantize_money(right))
    assert to_analytics_float(quantize_money(left)) <= to_analytics_float(quantize_money(right))


# --------------------------------------------------------------------------
# Aggregation: the error the frame docstring calls "residual", given a number.
# --------------------------------------------------------------------------


AGGREGATION_EXAMPLES = settings(max_examples=500)
"""A larger budget for the two aggregation properties than for the pointwise ones.

They are the only properties here whose counterexamples would live in a
*shape* rather than in a value — a long list of near-cancelling large legs —
and hypothesis needs room to find one. The pointwise properties are
one-argument and the default budget already covers their interesting
magnitudes.
"""


@AGGREGATION_EXAMPLES
@given(st.lists(money, min_size=1, max_size=50))
def test_a_float_sum_tracks_the_exact_sum_within_a_length_scaled_bound(values: list[Decimal]) -> None:
    """Summing as floats differs from summing as `Decimal` by at most `n` ulps of the magnitudes summed.

    This is the residual imprecision `accounting.ledger.frame` describes
    and does not quantify: every balance, net-worth figure and category
    total in the app is a Polars `Float64` sum over exactly this shape of
    input. The bound has to scale with *both* the number of terms and the
    sum of their magnitudes, because both are what recursive summation's
    error depends on — a longer ledger and a wealthier one each cost
    accuracy, independently.

    Note what is compared: the exact `Decimal` sum against the float sum,
    not two float sums against each other. That makes the exact answer the
    reference, which is the only framing in which the word "error" means
    anything.
    """
    quantized = [quantize_money(value) for value in values]
    exact = sum(quantized, Decimal(0))
    projected = to_decimal(sum(to_analytics_float(value) for value in quantized))
    magnitude = sum(abs(value) for value in quantized)
    assert abs(projected - exact) <= len(quantized) * SUM_TOLERANCE_PER_TERM * magnitude


@AGGREGATION_EXAMPLES
@given(
    st.lists(
        st.decimals(min_value=-ZERO_SUM_MAX_LEG, max_value=ZERO_SUM_MAX_LEG, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=ZERO_SUM_MAX_LEGS - 1,
    )
)
def test_a_balanced_transaction_stays_inside_the_replay_zero_sum_tolerance(legs: list[Decimal]) -> None:
    """A transaction that sums to exactly zero in `Decimal` sums to within `1e-6` of zero as floats.

    `docs/accounting/architecture.md` states the invariant — for every
    `transaction_id`, the postings sum to zero — and `ledger.replay`
    enforces it on the float projection with `_ZERO_SUM_TOLERANCE = 1e-6`.
    That constant is imported rather than repeated here, so this test is
    about the real threshold and fails if it is ever tightened past what
    the arithmetic supports.

    The transaction is built the way the importers build one: `n - 1`
    arbitrary legs plus a balancing leg, exact in `Decimal` by
    construction. The worst case generated — twenty legs at a million each
    — carries a theoretical error near `9e-8`, so `1e-6` is roughly an
    order of magnitude of headroom. That is the finding: the constant is
    not tight, it is not arbitrary either, and it now has a test that
    would notice if a future ledger of much larger transactions ate the
    margin.
    """
    quantized = [quantize_money(leg) for leg in legs]
    balancing = -sum(quantized, Decimal(0))
    transaction = [*quantized, balancing]
    assert sum(transaction, Decimal(0)) == 0

    drift = abs(sum(to_analytics_float(leg) for leg in transaction))
    assert drift < _ZERO_SUM_TOLERANCE


# --------------------------------------------------------------------------
# Shares: the same seam at a wider scale, which is not the same story.
# --------------------------------------------------------------------------


@given(shares)
def test_quantize_shares_is_idempotent_at_its_own_wider_scale(value: Decimal) -> None:
    """Shares round to eight places, not four, and rounding twice changes nothing.

    Kept separate from the money properties rather than parametrized over
    both: `db.money` names these scales differently on purpose (a share
    count feeds cost basis and realized gain), and a single generic test
    would let a future edit collapse the two scales into one without
    anything failing.
    """
    once = quantize_shares(value)
    assert quantize_shares(once) == once
    assert once.as_tuple().exponent == -SHARES_SCALE


@given(shares)
def test_the_analytics_crossing_is_exactly_reversible_for_realistic_share_counts(value: Decimal) -> None:
    """Share counts survive the crossing exactly, but only up to ~67.1 million.

    The limit is four orders of magnitude nearer than money's because the
    scale is four orders of magnitude finer: the finer the storage grid,
    the sooner float64's own grid becomes coarser than it. Any real
    position is far below this, so the guarantee holds in practice — but it
    holds for a reason that is worth having written down before someone
    stores a share count in the hundreds of millions.
    """
    quantized = quantize_shares(value)
    assume(abs(quantized) < SHARES_FLOAT_EXACT_LIMIT)
    assert to_decimal(to_analytics_float(quantized)) == quantized


def test_two_distinct_share_counts_above_the_limit_collapse_onto_one_float() -> None:
    """The witness for shares: adjacent storable counts, one float.

    Directly above the limit the collapse is total — consecutive multiples
    of the storage quantum, which Postgres holds apart, are indistinguishable
    once projected. Pinned for the same reason as the money witness: the
    exactness property above is bounded, and a bound with no counterexample
    on the far side invites someone to delete it.
    """
    lower = SHARES_FLOAT_EXACT_LIMIT + SHARES_QUANTUM
    upper = lower + SHARES_QUANTUM
    assert lower != upper
    assert to_analytics_float(lower) == to_analytics_float(upper)
