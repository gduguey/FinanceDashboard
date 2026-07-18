"""SQLAlchemy ORM models for the `accounting` Postgres schema.

Importing this package registers every accounting table on `db.base.Base`'s
shared metadata — `migration/env.py` and any test fixture that calls
`Base.metadata.create_all` must import `accounting.db` (directly or
transitively) before doing so, or these tables silently won't exist yet.
"""

from __future__ import annotations

from accounting.db.automation import CategoryPattern, TransferRule, TransferRuleExclusion
from accounting.db.budgets import Budget, GeneralBudget
from accounting.db.concurrency import StoreVersion
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
    PostingMerge,
    PostingMergeDuplicate,
    PostingOverride,
    PostingOverrideTag,
    PostingPendingSuggestion,
    PostingSplit,
    PostingSplitLeg,
)
from accounting.db.goals import Goal, GoalContribution, RecurringAddition, WithdrawalPriorityEntry
from accounting.db.llm import LLMUsage
from accounting.db.simulator import SimulatorScenario
from accounting.db.transfers import TransferLink, TransferLinkedTransaction

__all__ = [
    "Account",
    "Budget",
    "Category",
    "CategoryPattern",
    "DismissedSuggestion",
    "GeneralBudget",
    "Goal",
    "GoalContribution",
    "LLMUsage",
    "ManualTransfer",
    "OpeningBalance",
    "OtherAsset",
    "Posting",
    "PostingMerge",
    "PostingMergeDuplicate",
    "PostingOverride",
    "PostingOverrideTag",
    "PostingPendingSuggestion",
    "PostingSplit",
    "PostingSplitLeg",
    "PostingTag",
    "RecurringAddition",
    "SimulatorScenario",
    "StoreVersion",
    "Tag",
    "Transaction",
    "TransferLink",
    "TransferLinkedTransaction",
    "TransferRule",
    "TransferRuleExclusion",
    "WithdrawalPriorityEntry",
]
