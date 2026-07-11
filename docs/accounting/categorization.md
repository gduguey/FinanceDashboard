# Categorization, tags, and splitting

This covers everything that happens to a posting after its counterparty is
resolved: assigning it a category, tagging it, the several different ways
a category can get suggested or assigned automatically, and splitting one
deposit into several independently-categorized pieces.

## Categories and tags

A `Category` (`accounting.models.Category`) is two levels deep at most — a
top-level category, optionally one subcategory beneath it
(`parent_category_id`) — and always belongs to exactly one classification,
`"income"` or `"expense"`, matching the sign of the postings it's used on.
The default taxonomy a fresh install seeds itself with lives in
`store._EXPENSE_TAXONOMY`/`_INCOME_TAXONOMY` (also shown for reference on
the Categories page in the app) — it's a starting point, not a fixed list;
renaming, merging, or deleting any of it is a normal data edit; nothing
about the schema treats it as special.

A `Tag` (`accounting.models.Tag`) is unrelated to the category tree — it
cuts across it. Where a category answers "what kind of spend or income is
this," a tag answers "what was this part of": a trip, a move, a one-off
event. A posting can carry any number of tags independent of its category.

## Three ways a posting's counterparty gets resolved, and one way it gets categorized automatically without a rule

### Rules — resolve a counterparty automatically, no confirmation step

A `TransferRule` (`accounting.models.TransferRule`) is a trigger/action
pair: `description_contains` (a case-insensitive substring match,
optionally scoped to one `account_id`) triggers repointing a posting's
placeholder counterparty to `counterparty_account_id`, and optionally
setting a category. `ledger.categorization.apply_rules` evaluates every
*active* rule against every still-unresolved transaction each time the
ledger is read — nothing is written back into the ledger cache; a deleted
rule's effect disappears the very next time postings are read, with no
separate "undo" step needed. `active: bool` (default `True`) lets a rule be
switched off temporarily without deleting it — an inactive rule is skipped
during matching entirely, as if it weren't in the list at all.

The mechanic worth being explicit about: a transfer between two of your
own accounts is imported as **two separate transactions**, one on each
account's own statement — neither statement's import process knows the
other transaction exists. A `TransferRule` only ever resolves the transaction whose
account and description it matches; it never automatically creates or
triggers the mirror rule for the other side. Fully resolving a two-sided
transfer into two real-to-real legs (and out of income/expense tracking)
takes two rules, one per direction — `ledger.transfers` (below) exists
specifically to make finding and writing that second rule easier.

### Auto-detected transfer suggestions

`ledger.transfers.find_unmatched_transfer_candidates` scans every posting
still pointed at a placeholder for pairs that look like the same transfer:
equal and opposite amounts, on two different real accounts, within a
configurable number of days of each other. Every match is surfaced as a
read-only suggestion (with a proposed rule for both directions, editable
before either is added) — never applied automatically, since the heuristic
can be wrong (two unrelated transactions that happen to share an amount).
A suggestion that isn't a real transfer can be archived instead of kept in
the list forever — see "Dismissing a suggestion" below.

### Duplicate detection and merging

A transfer suggestion is two *different* transactions, one per account, that
belong together. A duplicate is the opposite problem: the *same* real-world
purchase imported twice into the *same* account, because it reached this
ledger through two different sources (say, a CSV export and a statement PDF)
that each gave it their own transaction id. `ledger.duplicates.find_duplicate_candidates`
scans for groups of two or more transactions on one account with the same
amount, within a configurable number of days of each other, whose
descriptions are similar enough — `description_similarity` requires the
shorter description's words to (near-)match words in the longer one *and*
for those matched words to cover a large share of the longer description,
so "JP Morgan Chase" only counts as similar to "JP Morgan Chase Transfer
Out" because it captures both criteria, not just the first. Each group gets
a `certainty` score (blending description similarity with how close the
dates are) and groups are returned least-certain-first, since those need
the closest human review.

Nothing is merged automatically. Confirming a merge writes a
`PostingMerge` (`accounting.models`) — which transaction to keep, which
transaction(s) to drop, and an optional description override —
`ledger.categorization.apply_posting_merges` then drops every dropped
transaction's both legs from the resolved ledger and applies the
description override to the kept one, fresh on every read, the same
non-destructive way rules and overrides are applied. A group that isn't
actually a duplicate can be archived the same way a transfer suggestion
can — see "Dismissing a suggestion" below.

### Dismissing a suggestion

Both suggestion sources above share one dismiss-and-archive mechanism
(`models.DismissedSuggestion`, `POST`/`GET`/`DELETE
/dismissed-suggestions`). `suggestion_id` is a stable key derived from the
suggestion's own content (`api._transfer_suggestion_id`/
`_duplicate_suggestion_id`), not a random id — the same real-world pair or
group always dismisses and restores under the same key, regardless of how
many times the detector recomputes it on a later read. Dismissing never
touches a transfer rule, a posting, or a merge; it only removes one entry
from the list of things still being proposed, so restoring it (deleting
the `DismissedSuggestion` record) is always lossless. Both suggestion
endpoints filter out anything already dismissed; the frontend's
`SuggestionArchive` component lists what's currently archived, with a
restore action per entry.

### Category patterns and AI suggestions — propose a category, never apply it silently

Two independent, optional tools each suggest a category for an
already-resolved posting, and both share the exact same confirm-before-it-
sticks lifecycle:

- **AI suggestions** (`llm/`) — a pluggable-provider LLM call, given a
  posting's description and a sample of already-categorized postings on
  the same side (income/expense), returns a category and (optionally)
  subcategory guess.
- **Category patterns** (`accounting.models.CategoryPattern`,
  `ledger.patterns.matching_pattern`) — the same kind of description
  substring match a `TransferRule` uses, but authored by the user
  specifically to *suggest* a category rather than resolve a counterparty.
  Deliberately a separate model from `TransferRule`: a `TransferRule` acts
  with no confirmation step; a category pattern never does. Also carries
  its own `active` flag, applied the same way — `ledger.patterns.matching_pattern`/
  `match_patterns_bulk` both skip an inactive pattern entirely.

Both write through the same staging mechanism
(`ledger.pending.stage_pending_suggestion`): applying a suggestion sets the
posting's category optimistically but also records `pending_source`
(`"ai"` or `"pattern"`) and a snapshot of what the category was
immediately before, so the row can render as "still needs confirmation"
(colored by source) until it's explicitly validated
(`ledger.pending.resolve_pending_suggestion`) — which either keeps the
suggested category for good, or restores the pre-suggestion snapshot
exactly, never a best-effort guess at what it "should" revert to.

## Splitting

Nothing above requires a transaction to have exactly two postings — only
that they sum to zero. A plain deposit ordinarily becomes two postings (a
negative leg against a placeholder, a positive leg into the real account),
but `PostingSplit`/`PostingSplitLeg` (`accounting.models`) let one of those
two postings be replaced with several independently-categorized legs that
still sum to the original amount — `ledger.categorization.apply_posting_splits`
does this replacement fresh on every read, the same non-destructive way
rules and overrides are applied, never baked into the ledger cache.

This is what the paystub import tool automates end to end
(`importers.paystub` + `dashboard.paystub`): it extracts structured figures
from an uploaded paystub PDF (gross pay, net pay, per-destination-account
deposit amounts, named reimbursement line items), matches those deposits
against the real bank postings that landed on or near pay day, and — when
a paystub is split across more than one account — proposes which
reimbursement lines rode along with which deposit, greedily assigning the
largest reimbursement lines to the largest deposit first. The proposal is
always editable and never applied until confirmed.
