# Planning: budgets and goals

Budgets and goals are both laid over the same categorized postings
`categorization.md` describes — neither one maintains its own copy of
transaction data, and neither changes how a posting is categorized or
which charts it appears in.

## Budgets

A `Budget` (`accounting.models.Budget`) assigns a target amount to one
expense category for one calendar month — `category_id` is always the
top-level category; an optional `subcategory_id` scopes the target to just
that one subcategory's own actual spend instead of the whole category's,
so a category and one of its subcategories can each carry their own
independent target for the same month. A `GeneralBudget` mirrors this same
`category_id`/`subcategory_id` shape but assigns a target that applies to
every month alike, stored completely separately from per-month budgets —
switching between "general" and "per-month" mode in the UI never merges or
silently overwrites the other; each is its own independent set of numbers.

A budget's *actual* spend is never stored anywhere — `dashboard.budgets`
computes it fresh from `dashboard.income_statement.category_totals` for
whichever month is being viewed, the same replay-not-cache principle as
everything else in this module. A top-level budget's actual spend sums
every posting in that category regardless of subcategory; a
subcategory-scoped budget's actual is just that one subcategory's own
postings. `dashboard.budgets.suggested_budget_amount` offers a starting
number pulled from trailing months' actual average spend in that category
(or subcategory, when one is given), purely as a suggestion the user can
accept or overwrite.

## Goals

A goal is somewhere money is deliberately being set aside — an emergency
fund, a vacation, a big purchase — tracked separately from ordinary
categorized spending, but still derived entirely from a plain, append-only
list of entries rather than any stored running total.

### The core data model

- **`Goal`** (`accounting.models.Goal`) — a target: `name`, `target_amount`,
  `target_currency`, `target_date`, `color`. A goal never stores its own
  balance.
- **`GoalContribution`** (`accounting.models.GoalContribution`) — the
  single object everything else derives from: `goal_id`, a real `date`
  (never a month bucket — "this month's contributions" is a filter applied
  at display time, never a separately-stored figure), a signed `amount`
  (positive = money allocated into the goal, negative = a withdrawal),
  `currency`, an optional `note`, and an optional `source_posting_id` that
  links to a real bank transfer purely for traceability — it's never read
  by any balance or unallocated computation.

### Derived quantities — always computed, never stored

- **A goal's balance at any date `T`** is the sum of that goal's own
  contributions dated on or before `T` (`dashboard.goals.goal_balance`).
- **Unallocated money at date `T`** is `(real income − real expense,
  cumulative through T)` minus `(every goal's contributions, cumulative
  through T)` (`dashboard.goals.unallocated_balance`). This is
  deliberately *never* written as if it were a goal's own contribution
  row — it's the residual left over after every real goal's contributions
  are subtracted out, recomputed fresh every time, with no contributions
  of its own to keep in sync. It's shown as its own slice in a few charts
  purely for display convenience.

### Automations

Two independent, optional automations build on top of the contribution
model — both only ever write ordinary, dated `GoalContribution` rows; the
decision logic (`ledger.goal_automations`) is pure and separate from the
part that actually persists a row
(`api.routers.goals.post_run_recurring_additions`/
`post_run_withdrawal_automation`), the same decide/apply split
`dashboard.paystub.propose_posting_splits` uses for paystub splits.

- **Recurring additions** (`accounting.models.RecurringAddition`) — an
  ordered list of rules that move unallocated money into a goal on a
  monthly schedule, in priority order. Each is either a fixed amount or a
  percentage of the unallocated balance at the moment it runs; only the
  single lowest-priority rule may instead be `"remainder"` — take
  whatever's left after every rule above it. `ledger.goal_automations.run_recurring_additions`
  funds each rule from a shrinking pool in priority order, so if there
  isn't enough unallocated money to satisfy every rule, higher-priority
  ones are funded first and lower-priority ones get a partial amount, or
  nothing. There is no background scheduler in this app — due-and-not-yet-run
  additions are caught up whenever the Goals page loads, using a
  deterministic contribution id per (rule, month) pair so re-running it
  within the same month is a no-op.
- **Withdrawal automation** (`accounting.models.WithdrawalPriorityEntry`) —
  a separate, ordered list with no schedule of its own; it only triggers
  when unallocated money is found to be negative (checked the same way,
  whenever the Goals page loads). `ledger.goal_automations.run_withdrawal_automation`
  draws down goals in priority order, never taking any single goal below
  zero, moving to the next goal in the list once one is exhausted. If
  every listed goal is exhausted and unallocated is still negative, it's
  left negative with a warning shown, rather than the app inventing money
  that isn't there.
