from datetime import date

import pytest

from accounting.ledger.goal_automations import (
    next_recurring_occurrence,
    run_recurring_additions,
    run_withdrawal_automation,
)
from accounting.models import RecurringAddition, WithdrawalPriorityEntry


def _addition(goal_id: str, mode: str, value: float, priority: int) -> RecurringAddition:
    return RecurringAddition(
        addition_id=f"auto:{goal_id}",
        goal_id=goal_id,
        start_date=date(2000, 1, 1),
        frequency="monthly",
        mode=mode,
        value=value,
        priority=priority,
    )


def _schedule(frequency: str, start_date: date, end_date: date | None = None) -> RecurringAddition:
    return RecurringAddition(
        addition_id="auto:a",
        goal_id="g",
        start_date=start_date,
        frequency=frequency,
        end_date=end_date,
        mode="fixed_amount",
        value=10.0,
        priority=0,
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
    assert funded == [("emergency-fund", 500.0)]


def test_run_recurring_additions_funds_in_priority_order_top_first() -> None:
    additions = [
        _addition("vacation", "fixed_amount", 800.0, priority=1),
        _addition("emergency-fund", "fixed_amount", 500.0, priority=0),
    ]
    funded = run_recurring_additions(additions, unallocated=1000.0)
    # emergency-fund (priority 0) is funded in full first; vacation only gets what's left.
    assert funded == [("emergency-fund", 500.0), ("vacation", 500.0)]


def test_run_recurring_additions_gives_a_lower_priority_addition_nothing_once_funds_run_out() -> None:
    additions = [
        _addition("emergency-fund", "fixed_amount", 500.0, priority=0),
        _addition("vacation", "fixed_amount", 800.0, priority=1),
    ]
    funded = run_recurring_additions(additions, unallocated=500.0)
    assert funded == [("emergency-fund", 500.0)]


def test_run_recurring_additions_percent_of_unallocated_uses_the_starting_balance() -> None:
    additions = [_addition("emergency-fund", "percent_of_unallocated", 10.0, priority=0)]
    funded = run_recurring_additions(additions, unallocated=2000.0)
    assert funded == [("emergency-fund", 200.0)]


def test_run_recurring_additions_remainder_gets_whatever_is_left() -> None:
    additions = [
        _addition("emergency-fund", "fixed_amount", 500.0, priority=0),
        _addition("vacation", "remainder", 0.0, priority=1),
    ]
    funded = run_recurring_additions(additions, unallocated=2000.0)
    assert funded == [("emergency-fund", 500.0), ("vacation", 1500.0)]


def test_run_recurring_additions_with_no_unallocated_money_funds_nothing() -> None:
    additions = [_addition("emergency-fund", "fixed_amount", 500.0, priority=0)]
    assert run_recurring_additions(additions, unallocated=0.0) == []


def test_run_withdrawal_automation_draws_down_the_top_priority_goal_first() -> None:
    priorities = [
        WithdrawalPriorityEntry(goal_id="vacation", priority=1),
        WithdrawalPriorityEntry(goal_id="emergency-fund", priority=0),
    ]
    withdrawals = run_withdrawal_automation(
        priorities, goal_balances={"emergency-fund": 1000.0, "vacation": 500.0}, shortfall=300.0
    )
    assert withdrawals == [("emergency-fund", -300.0)]


def test_run_withdrawal_automation_moves_to_the_next_goal_once_one_is_exhausted() -> None:
    priorities = [
        WithdrawalPriorityEntry(goal_id="emergency-fund", priority=0),
        WithdrawalPriorityEntry(goal_id="vacation", priority=1),
    ]
    withdrawals = run_withdrawal_automation(
        priorities, goal_balances={"emergency-fund": 200.0, "vacation": 500.0}, shortfall=300.0
    )
    assert withdrawals == [("emergency-fund", -200.0), ("vacation", -100.0)]


def test_run_withdrawal_automation_leaves_a_residual_shortfall_when_every_goal_is_exhausted() -> None:
    priorities = [WithdrawalPriorityEntry(goal_id="emergency-fund", priority=0)]
    withdrawals = run_withdrawal_automation(priorities, goal_balances={"emergency-fund": 100.0}, shortfall=300.0)
    assert withdrawals == [("emergency-fund", -100.0)]
    total_withdrawn = sum(-amount for _, amount in withdrawals)
    assert total_withdrawn == pytest.approx(100.0)  # caller sees this doesn't cover the full 300 shortfall
