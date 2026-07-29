from datetime import date

import pytest
from pydantic import ValidationError

from accounting.ledger.goal_automations import (
    next_recurring_occurrence,
    run_recurring_additions,
    run_withdrawal_automation,
)
from accounting.models import GoalAutomation


def _addition(goal_id: str, mode: str, value: float, priority: int, automation_id: str | None = None) -> GoalAutomation:
    """One contribution automation.

    `automation_id` is overridable because a goal may legitimately carry
    several contribution schedules — deriving it from `goal_id` alone made
    that state inexpressible here, which is why the collision
    `run_recurring_additions` used to have went unnoticed.
    """
    return GoalAutomation(
        automation_id=automation_id or f"auto:{goal_id}",
        goal_id=goal_id,
        direction="contribution",
        start_date=date(2000, 1, 1),
        frequency="monthly",
        mode=mode,
        value=value,
        currency="USD",
        priority=priority,
    )


def _schedule(frequency: str, start_date: date, end_date: date | None = None) -> GoalAutomation:
    return GoalAutomation(
        automation_id="auto:a",
        goal_id="g",
        direction="contribution",
        start_date=start_date,
        frequency=frequency,
        end_date=end_date,
        mode="fixed_amount",
        value=10.0,
        currency="USD",
        priority=0,
    )


def _withdrawal(goal_id: str, priority: int) -> GoalAutomation:
    return GoalAutomation(
        automation_id=f"withdrawal:{goal_id}", goal_id=goal_id, direction="withdrawal", priority=priority
    )


def test_next_recurring_occurrence_is_none_before_the_start_date() -> None:
    addition = _schedule("daily", start_date=date(2026, 6, 10))
    assert next_recurring_occurrence(addition, as_of=date(2026, 6, 9)) is None


def test_next_recurring_occurrence_daily_is_every_day_from_the_start() -> None:
    addition = _schedule("daily", start_date=date(2026, 6, 10))
    assert next_recurring_occurrence(addition, as_of=date(2026, 6, 10)) == date(2026, 6, 10)
    assert next_recurring_occurrence(addition, as_of=date(2026, 6, 15)) == date(2026, 6, 15)


def test_next_recurring_occurrence_weekly_lands_every_seventh_day() -> None:
    addition = _schedule("weekly", start_date=date(2026, 6, 1))
    assert next_recurring_occurrence(addition, as_of=date(2026, 6, 1)) == date(2026, 6, 1)
    assert next_recurring_occurrence(addition, as_of=date(2026, 6, 7)) == date(2026, 6, 1)
    assert next_recurring_occurrence(addition, as_of=date(2026, 6, 8)) == date(2026, 6, 8)
    assert next_recurring_occurrence(addition, as_of=date(2026, 6, 13)) == date(2026, 6, 8)


def test_next_recurring_occurrence_biweekly_lands_every_fourteenth_day() -> None:
    addition = _schedule("biweekly", start_date=date(2026, 6, 1))
    assert next_recurring_occurrence(addition, as_of=date(2026, 6, 14)) == date(2026, 6, 1)
    assert next_recurring_occurrence(addition, as_of=date(2026, 6, 15)) == date(2026, 6, 15)


def test_next_recurring_occurrence_monthly_uses_the_start_days_day_of_month() -> None:
    addition = _schedule("monthly", start_date=date(2026, 1, 5))
    assert next_recurring_occurrence(addition, as_of=date(2026, 6, 4)) == date(2026, 5, 5)
    assert next_recurring_occurrence(addition, as_of=date(2026, 6, 5)) == date(2026, 6, 5)
    assert next_recurring_occurrence(addition, as_of=date(2026, 6, 20)) == date(2026, 6, 5)


def test_next_recurring_occurrence_monthly_caps_the_day_of_month_at_28() -> None:
    addition = _schedule("monthly", start_date=date(2026, 1, 30))
    assert next_recurring_occurrence(addition, as_of=date(2026, 2, 28)) == date(2026, 2, 28)


def test_next_recurring_occurrence_monthly_before_the_first_occurrence_in_the_start_month() -> None:
    addition = _schedule("monthly", start_date=date(2026, 6, 15))
    assert next_recurring_occurrence(addition, as_of=date(2026, 6, 10)) is None


def test_next_recurring_occurrence_is_none_past_the_end_date() -> None:
    addition = _schedule("daily", start_date=date(2026, 6, 1), end_date=date(2026, 6, 5))
    assert next_recurring_occurrence(addition, as_of=date(2026, 6, 5)) == date(2026, 6, 5)
    assert next_recurring_occurrence(addition, as_of=date(2026, 6, 10)) == date(2026, 6, 5)


def test_run_recurring_additions_funds_a_fixed_amount_addition() -> None:
    additions = [_addition("emergency-fund", "fixed_amount", 500.0, priority=0)]
    funded = run_recurring_additions(additions, unallocated=2000.0)
    assert funded == [("auto:emergency-fund", 500.0)]


def test_run_recurring_additions_funds_in_priority_order_top_first() -> None:
    additions = [
        _addition("vacation", "fixed_amount", 800.0, priority=1),
        _addition("emergency-fund", "fixed_amount", 500.0, priority=0),
    ]
    funded = run_recurring_additions(additions, unallocated=1000.0)
    # emergency-fund (priority 0) is funded in full first; vacation only gets what's left.
    assert funded == [("auto:emergency-fund", 500.0), ("auto:vacation", 500.0)]


def test_run_recurring_additions_reports_two_schedules_on_one_goal_separately() -> None:
    """Keyed by automation, not by goal — only a *withdrawal* is one-per-goal.

    Returning `goal_id` made the result ambiguous the moment a goal had two
    contribution schedules, and the caller keyed its contributions off it —
    so the second funded schedule overwrote the first.
    """
    additions = [
        _addition("emergency-fund", "fixed_amount", 300.0, priority=0, automation_id="auto:a"),
        _addition("emergency-fund", "fixed_amount", 200.0, priority=1, automation_id="auto:b"),
    ]

    assert run_recurring_additions(additions, unallocated=2000.0) == [("auto:a", 300.0), ("auto:b", 200.0)]


def test_run_recurring_additions_gives_a_lower_priority_addition_nothing_once_funds_run_out() -> None:
    additions = [
        _addition("emergency-fund", "fixed_amount", 500.0, priority=0),
        _addition("vacation", "fixed_amount", 800.0, priority=1),
    ]
    funded = run_recurring_additions(additions, unallocated=500.0)
    assert funded == [("auto:emergency-fund", 500.0)]


def test_run_recurring_additions_percent_of_unallocated_uses_the_starting_balance() -> None:
    additions = [_addition("emergency-fund", "percent_of_unallocated", 10.0, priority=0)]
    funded = run_recurring_additions(additions, unallocated=2000.0)
    assert funded == [("auto:emergency-fund", 200.0)]


def test_run_recurring_additions_remainder_gets_whatever_is_left() -> None:
    additions = [
        _addition("emergency-fund", "fixed_amount", 500.0, priority=0),
        _addition("vacation", "remainder", 0.0, priority=1),
    ]
    funded = run_recurring_additions(additions, unallocated=2000.0)
    assert funded == [("auto:emergency-fund", 500.0), ("auto:vacation", 1500.0)]


def test_run_recurring_additions_with_no_unallocated_money_funds_nothing() -> None:
    additions = [_addition("emergency-fund", "fixed_amount", 500.0, priority=0)]
    assert run_recurring_additions(additions, unallocated=0.0) == []


def test_run_withdrawal_automation_draws_down_the_top_priority_goal_first() -> None:
    priorities = [
        _withdrawal("vacation", priority=1),
        _withdrawal("emergency-fund", priority=0),
    ]
    withdrawals = run_withdrawal_automation(
        priorities, goal_balances={"emergency-fund": 1000.0, "vacation": 500.0}, shortfall=300.0
    )
    assert withdrawals == [("emergency-fund", -300.0)]


def test_run_withdrawal_automation_moves_to_the_next_goal_once_one_is_exhausted() -> None:
    priorities = [
        _withdrawal("emergency-fund", priority=0),
        _withdrawal("vacation", priority=1),
    ]
    withdrawals = run_withdrawal_automation(
        priorities, goal_balances={"emergency-fund": 200.0, "vacation": 500.0}, shortfall=300.0
    )
    assert withdrawals == [("emergency-fund", -200.0), ("vacation", -100.0)]


def test_run_withdrawal_automation_leaves_a_residual_shortfall_when_every_goal_is_exhausted() -> None:
    priorities = [_withdrawal("emergency-fund", priority=0)]
    withdrawals = run_withdrawal_automation(priorities, goal_balances={"emergency-fund": 100.0}, shortfall=300.0)
    assert withdrawals == [("emergency-fund", -100.0)]
    total_withdrawn = sum(-amount for _, amount in withdrawals)
    assert total_withdrawn == pytest.approx(100.0)  # caller sees this doesn't cover the full 300 shortfall


def test_run_recurring_additions_skips_a_withdrawal_automation_that_reached_the_list() -> None:
    # A withdrawal carries no mode/value, so there is nothing for the
    # contribution pass to fund — it must be passed over, not crash.
    automations = [_withdrawal("vacation", priority=0), _addition("emergency-fund", "fixed_amount", 500.0, priority=1)]
    assert run_recurring_additions(automations, unallocated=2000.0) == [("auto:emergency-fund", 500.0)]


def test_next_recurring_occurrence_is_none_for_a_withdrawal_automation() -> None:
    assert next_recurring_occurrence(_withdrawal("vacation", priority=0), as_of=date(2026, 6, 10)) is None


def test_a_contribution_automation_without_a_schedule_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GoalAutomation(automation_id="a", goal_id="g", direction="contribution", priority=0)


def test_a_withdrawal_automation_carrying_a_schedule_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GoalAutomation(
            automation_id="a",
            goal_id="g",
            direction="withdrawal",
            priority=0,
            start_date=date(2026, 1, 1),
            frequency="monthly",
            mode="fixed_amount",
            value=10.0,
        )
