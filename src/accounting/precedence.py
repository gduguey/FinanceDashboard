"""The order the interpretation overlays are applied in, declared as data instead of as call order.

Every table in the interpretation aggregate (see
`repositories.interpretation`) layers something on top of the immutable
posting ledger, and the layers are not commutative: an override has to see
the account a rule resolved, a merge has to see the rows a split produced.
Until this module existed, that ordering lived nowhere but the top-to-bottom
line order of `api.dependencies._resolved_postings_and_store` — reading it
meant reading a function body, changing it meant editing a hard-coded
sequence, and nothing stopped a row from being written by a stage that had
already run.

`OverlayStage` is that ordering, written down once. Its declaration order
*is* the precedence order (`OVERLAY_PRECEDENCE` freezes it into a tuple),
every overlay table carries a `stage` column CHECK-pinned to the value(s)
legal for it, and the resolver walks `OVERLAY_PRECEDENCE` dispatching to a
registered applier per stage rather than calling six functions in a row.
Adding a stage is therefore adding a vocabulary entry plus its applier, not
finding the right line of a function to insert a call at.

The order, and why each stage sits where it does:

1. `counterparty` — a transfer-effect `categorization_rules` row repoints a
   posting's placeholder counterparty at a real account
   (`ledger.categorization.apply_rules`). First, because every later stage
   may read the account it resolved: an override that clears a category is
   only meaningful relative to what the rule produced, and the transfer
   detector downstream matches on resolved accounts.
2. `split` — `posting_splits` turns one posting into N legs
   (`ledger.categorization.apply_posting_splits`). After `counterparty` so
   every leg inherits the resolved account, before `override` so the user's
   own last word can land on the split's result.
3. `override` — `posting_overrides` (plus the pending half of `suggestions`,
   folded into the same `ManualOverride` per posting) is the user's direct
   edit, and by definition wins over anything a rule or a pattern produced
   (`ledger.categorization.apply_manual_overrides`). Hence: after both
   automated stages, before the two that only ever *remove* or *add* whole
   rows.
4. `merge` — `posting_merges` drops whole duplicate transactions
   (`ledger.categorization.apply_posting_merges`). After `override` because
   dropping a row first would throw away the override on it and change what
   an untouched override resolves to; merging is a decision about rows, not
   about a row's fields, so it is applied to fields that are already final.
5. `manual_transfer` — `manual_transfers` contributes postings that were
   never imported at all (`ledger.manual_transfers.postings_for_manual_transfers`).
   Late because these rows are generated whole and correct: no rule, split,
   override or merge has anything to say about them.
6. `link` — `transfer_links` marks two transactions as the two sides of one
   transfer (`ledger.transfers.apply_transfer_links`). Deliberately last,
   and not only by preference: `apply_posting_splits`/`apply_manual_overrides`
   rebuild the frame through `ledger.frame.LEDGER_FRAME_SCHEMA`, which would
   silently drop the `is_linked_transfer`/`linked_transaction_id`/
   `transfer_link_source` columns this stage adds if it ran any earlier.

`suggestions` deliberately carries no `stage` column. A suggestion is a
*proposal*, not an overlay: a dismissed one only filters what the detectors
propose and never reaches the resolved ledger at all, and a pending one
reaches it only by being folded into its posting's `ManualOverride` — at the
`override` stage that `posting_overrides` already declares. Giving it a
stage would be declaring a precedence it does not have.
"""

from __future__ import annotations

from typing import Literal, get_args

OverlayStage = Literal["counterparty", "split", "override", "merge", "manual_transfer", "link"]
"""Which stage of the resolution pipeline an overlay row is applied at.

Declared in precedence order — see this module's docstring for what each
stage does and why it sits where it does. Every overlay table restates the
subset of these values it is allowed to hold as a `CheckConstraint` (via
`db.base.check_in_sql`), so a row can never claim a stage its own table is
not applied at.
"""

OVERLAY_PRECEDENCE: tuple[OverlayStage, ...] = get_args(OverlayStage)
"""The stages in the order the resolver applies them — `OverlayStage`'s own declaration order.

`typing.get_args` on a `Literal` preserves the order the values were
written in, so this is the declaration itself rather than a second,
hand-maintained copy of it that could drift.
"""
