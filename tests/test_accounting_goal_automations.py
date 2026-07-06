import pytest

from accounting.ledger.goal_automations import run_recurring_additions, run_withdrawal_automation
from accounting.models import RecurringAddition, WithdrawalPriorityEntry


def _addition(goal_id: str, mode: str, value: float, priority: int) -> RecurringAddition:
    return RecurringAddition(
        addition_id=f"auto:{goal_id}",
        goal_id=goal_id,
        schedule_day_of_month=1,
        mode=mode,
        value=value,
        priority=priority,
    )


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
