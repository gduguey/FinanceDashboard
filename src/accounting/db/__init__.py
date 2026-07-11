"""SQLAlchemy ORM models for the `accounting` Postgres schema.

Importing this package registers every accounting table on `db.base.Base`'s
shared metadata — `migration/env.py` and any test fixture that calls
`Base.metadata.create_all` must import `accounting.db` (directly or
transitively) before doing so, or these tables silently won't exist yet.
"""

from __future__ import annotations

from accounting.db.automation import CategoryPattern, TransferRule
from accounting.db.budgets import Budget, GeneralBudget
from accounting.db.core import (
    Account,
    Category,
    ManualTransfer,
    OpeningBalance,
    OtherAsset,
    Posting,
    PostingTag,
    Tag,
    Transaction,
)
from accounting.db.corrections import (
    DismissedSuggestion,
    ManualOverride,
    PostingMerge,
    PostingMergeDuplicate,
    PostingSplit,
    PostingSplitLeg,
)
from accounting.db.goals import Goal, GoalContribution, RecurringAddition, WithdrawalPriorityEntry
from accounting.db.simulator import SimulatorScenario

__all__ = [
    "Account",
    "Budget",
    "Category",
    "CategoryPattern",
    "DismissedSuggestion",
    "GeneralBudget",
    "Goal",
    "GoalContribution",
    "ManualOverride",
    "ManualTransfer",
    "OpeningBalance",
    "OtherAsset",
    "Posting",
    "PostingMerge",
    "PostingMergeDuplicate",
    "PostingSplit",
    "PostingSplitLeg",
    "PostingTag",
    "RecurringAddition",
    "SimulatorScenario",
    "Tag",
    "Transaction",
    "TransferRule",
    "WithdrawalPriorityEntry",
]
