"""SQLAlchemy ORM models for the `accounting` Postgres schema.

Importing this package registers every accounting table on `db.base.Base`'s
shared metadata — `migration/env.py` and any test fixture that calls
`Base.metadata.create_all` must import `accounting.db` (directly or
transitively) before doing so, or these tables silently won't exist yet.
"""

from __future__ import annotations

from accounting.db.automation import CategorizationRule, CategorizationRuleExclusion
from accounting.db.budgets import Budget
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
    PostingMerge,
    PostingMergeDuplicate,
    PostingOverride,
    PostingOverrideTag,
    PostingSplit,
    PostingSplitLeg,
    Suggestion,
)
from accounting.db.goals import Goal, GoalAutomation, GoalContribution
from accounting.db.llm import LLMUsage
from accounting.db.simulator import SimulatorScenario
from accounting.db.transfers import TransferLink, TransferLinkedTransaction
from db.base import Base
from db.indexes import ensure_foreign_key_indexes

__all__ = [
    "Account",
    "Budget",
    "CategorizationRule",
    "CategorizationRuleExclusion",
    "Category",
    "Goal",
    "GoalAutomation",
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
    "PostingSplit",
    "PostingSplitLeg",
    "PostingTag",
    "SimulatorScenario",
    "Suggestion",
    "Tag",
    "Transaction",
    "TransferLink",
    "TransferLinkedTransaction",
]


# Every foreign key in this schema gets its `(user_id, <fk>)` index here rather
# than in each model, so adding a foreign key cannot ship without one.
# See `db.indexes` for the reasoning. This is the only module where every
# model in the schema is guaranteed to be loaded, so it is the only place the
# walk can run.
ensure_foreign_key_indexes(Base.metadata, schema="accounting")  # noqa: RUF067 — see the comment above
