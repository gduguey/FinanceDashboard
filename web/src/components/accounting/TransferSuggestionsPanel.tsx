import { useVirtualizer } from '@tanstack/react-virtual'
import { Archive } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { SuggestionArchive } from '@/components/accounting/SuggestionArchive'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import {
  useCreateTransferLink,
  useCreateTransferRule,
  useDismissSuggestion,
  useTransferSuggestions,
} from '@/hooks/useAccountingData'
import { usePersistedState } from '@/hooks/usePersistedState'
import { useSortableRows } from '@/hooks/useSortableRows'
import { formatCurrency, formatDate, signColor } from '@/lib/format'
import { hasAnyRealAccount } from '@/lib/postingClassification'
import type { Account, TransferRule, TransferSuggestion } from '@/types/accounting'

function suggestionKey(suggestion: TransferSuggestion): string {
  return suggestion.suggestion_id
}

const ESTIMATED_ROW_HEIGHT = 44
// The expanded detail block's real height varies a lot (description
// lengths, whether both draft rules wrap to two lines) — this is just the
// virtualizer's starting guess before `measureElement` corrects it against
// the actual rendered element.
const ESTIMATED_DETAIL_HEIGHT = 260

// One entry per rendered `<tr>` — a suggestion contributes a "main" row
// always, plus a "detail" row only while expanded — so the virtualizer
// sees a flat list matching what's actually in the DOM instead of the
// nested/Fragment shape the data comes in as.
type VirtualEntry =
  | { kind: 'main'; suggestion: TransferSuggestion }
  | { kind: 'detail'; suggestion: TransferSuggestion }

// One suggestion resolves into TWO independent one-directional transfer
// rule drafts — a transfer is really two separate transactions (each
// imported against its own placeholder counterparty), so fully resolving
// it means writing one rule per side: "on account A, if description
// contains X, repoint to B" and the mirror for B. Adding only one leaves
// the other transaction exactly as it was, so both are always added
// together as one action.
interface RuleDraft {
  description_contains: string
  account_id: string | null
  counterparty_account_id: string | null
}

function suggestedRuleDrafts(suggestion: TransferSuggestion): RuleDraft[] {
  return [
    {
      description_contains: suggestion.description,
      account_id: suggestion.account_id,
      counterparty_account_id: suggestion.other_account_id,
    },
    {
      description_contains: suggestion.other_description,
      account_id: suggestion.other_account_id,
      counterparty_account_id: suggestion.account_id,
    },
  ]
}

// A rule's identity is its matching criteria (see `post_transfer_rule`'s
// content-derived id), so "already added" means an existing rule with the
// same criteria — never an id comparison, since the draft has no id of its
// own until the server mints one.
function draftAlreadyAdded(rules: TransferRule[], draft: RuleDraft): boolean {
  return rules.some(
    (rule) =>
      rule.description_contains === draft.description_contains &&
      (rule.account_id ?? null) === draft.account_id &&
      (rule.counterparty_account_id ?? null) === draft.counterparty_account_id,
  )
}

function accountName(accounts: Record<string, Account>, accountId: string | null): string {
  return (accountId && accounts[accountId]?.name) || accountId || '—'
}

function DraftRuleCard({
  draft,
  accounts,
  alreadyAdded,
  onChange,
}: {
  draft: RuleDraft
  accounts: Record<string, Account>
  alreadyAdded: boolean
  onChange: (draft: RuleDraft) => void
}) {
  return (
    <div className="space-y-2 rounded-md border p-3">
      <p className="text-xs text-muted-foreground">
        On <span className="font-medium text-foreground">{accountName(accounts, draft.account_id ?? null)}</span>, if
        description contains…
      </p>
      <Input
        value={draft.description_contains}
        disabled={alreadyAdded}
        onChange={(event) => onChange({ ...draft, description_contains: event.target.value })}
      />
      <p className="text-xs text-muted-foreground">
        …repoint it to {accountName(accounts, draft.counterparty_account_id ?? null)}.
      </p>
      {alreadyAdded && <p className="text-xs text-emerald-600">Already added</p>}
    </div>
  )
}

// The two proposed transfer rules for one suggestion, added together as a
// single action — see `suggestedRuleDrafts`'s own comment for why adding
// only one would leave the other transaction unresolved.
function SuggestedRulePair({
  suggestion,
  accounts,
  existingRules,
  transactionIds,
  onAdd,
  onLink,
  onDismiss,
  linkPending,
}: {
  suggestion: TransferSuggestion
  accounts: Record<string, Account>
  existingRules: TransferRule[]
  transactionIds: { transactionId: string; otherTransactionId: string } | null
  onAdd: (drafts: RuleDraft[]) => void
  onLink: (transactionId: string, otherTransactionId: string) => void
  onDismiss: () => void
  linkPending: boolean
}) {
  const [drafts, setDrafts] = useState(() => suggestedRuleDrafts(suggestion))
  const alreadyAdded = drafts.map((draft) => draftAlreadyAdded(existingRules, draft))
  const allAdded = alreadyAdded.every(Boolean)

  function updateDraft(index: number, next: RuleDraft) {
    setDrafts((prev) => prev.map((draft, draftIndex) => (draftIndex === index ? next : draft)))
  }

  function handleAddBoth() {
    onAdd(drafts.filter((_, index) => !alreadyAdded[index]))
  }

  return (
    <div className="space-y-3">
      <p className="max-w-xl text-xs break-words text-muted-foreground">
        Two ways to resolve this: link just this one pair (a one-off fact about these two transactions), or add rules
        that also catch every future occurrence of this same description.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {drafts.map((draft, index) => (
          <DraftRuleCard
            key={`${draft.account_id ?? 'none'}:${draft.counterparty_account_id ?? 'none'}`}
            draft={draft}
            accounts={accounts}
            alreadyAdded={alreadyAdded[index]}
            onChange={(next) => updateDraft(index, next)}
          />
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={!transactionIds || linkPending}
          title={
            transactionIds
              ? 'Link just this pair, without adding an ongoing rule'
              : "Couldn't resolve these postings to transactions"
          }
          onClick={() => transactionIds && onLink(transactionIds.transactionId, transactionIds.otherTransactionId)}
        >
          Link this pair
        </Button>
        <Button size="sm" disabled={allAdded} onClick={handleAddBoth}>
          {allAdded ? 'Both rules added' : 'Add both rules'}
        </Button>
        <Button variant="ghost" size="sm" className="gap-1.5" onClick={onDismiss}>
          <Archive className="size-3.5" />
          Not relevant
        </Button>
      </div>
    </div>
  )
}

// Suggests — never applies — likely internal transfers no transfer rule
// has caught yet: two postings, on two real accounts, still pointing at a
// placeholder counterparty, whose amounts are equal and opposite within
// `windowDays` of each other. Clicking a row shows both sides plus both
// proposed rules, editable before adding.
export function TransferSuggestionsPanel({
  accounts,
  rules,
}: {
  accounts: Record<string, Account>
  rules: TransferRule[]
}) {
  const [windowDays, setWindowDays] = usePersistedState('accounting.transfer-suggestions.window-days', 3)
  const [windowDaysDraft, setWindowDaysDraft] = useState(String(windowDays))
  const { data, isLoading, isError, error } = useTransferSuggestions(windowDays)
  const [expandedKey, setExpandedKey] = useState<string | null>(null)
  const createRule = useCreateTransferRule()
  const dismissSuggestion = useDismissSuggestion()
  const createTransferLink = useCreateTransferLink()
  const { sorted, sort, toggleSort } = useSortableRows(data ?? [], 'posted_at')
  // A suggestion pairs two postings, never transactions directly — but it
  // carries both transaction ids, which is what `POST /transfer-links` takes.
  // This used to hold the entire resolved ledger to build a two-entry lookup.
  function transactionIdsFor(suggestion: TransferSuggestion) {
    return { transactionId: suggestion.transaction_id, otherTransactionId: suggestion.other_transaction_id }
  }

  function linkPair(transactionId: string, otherTransactionId: string) {
    createTransferLink.mutate({ transaction_id_a: transactionId, transaction_id_b: otherTransactionId })
  }

  function dismiss(suggestion: TransferSuggestion) {
    dismissSuggestion.mutate({
      suggestionId: suggestion.suggestion_id,
      kind: 'transfer',
      description: `${suggestion.description} <-> ${suggestion.other_description}`,
    })
    if (expandedKey === suggestion.suggestion_id) setExpandedKey(null)
  }

  // Kept as free-text while typing (rather than coercing on every
  // keystroke) so backspacing to clear the field and type a new number
  // doesn't get immediately snapped back to a minimum value, which would
  // otherwise make every second keystroke land on the tail of the old
  // number instead of a blank field.
  function handleWindowDaysChange(value: string) {
    setWindowDaysDraft(value)
    const parsed = Number.parseInt(value, 10)
    if (value !== '' && Number.isFinite(parsed) && parsed >= 1) {
      setWindowDays(parsed)
    }
  }

  function handleWindowDaysBlur() {
    const parsed = Number.parseInt(windowDaysDraft, 10)
    if (windowDaysDraft === '' || !Number.isFinite(parsed) || parsed < 1) {
      setWindowDaysDraft(String(windowDays))
    }
  }

  // Fired in parallel: each draft creates its own independent rule, and a
  // rule create has no version to conflict on, so nothing here can lose a
  // race against a sibling. `allSettled`, not `all`, so one failure
  // doesn't discard the outcome of the others — every request is issued
  // regardless, and the toast reports how many actually landed.
  async function addRules(newDrafts: RuleDraft[]) {
    const results = await Promise.allSettled(
      newDrafts.map((draft) =>
        createRule.mutateAsync({
          description_contains: draft.description_contains,
          account_id: draft.account_id,
          counterparty_account_id: draft.counterparty_account_id,
          priority: 100,
          description: '',
        }),
      ),
    )
    const added = results.filter((result) => result.status === 'fulfilled').length
    if (added === newDrafts.length) return
    toast.error(
      added > 0
        ? `Added ${added} of ${newDrafts.length} rules — the rest failed, try again.`
        : 'Could not add the rule — try again.',
    )
  }

  const virtualEntries = useMemo(
    () =>
      sorted.flatMap((suggestion): VirtualEntry[] =>
        expandedKey === suggestionKey(suggestion)
          ? [
              { kind: 'main', suggestion },
              { kind: 'detail', suggestion },
            ]
          : [{ kind: 'main', suggestion }],
      ),
    [sorted, expandedKey],
  )

  const scrollParentRef = useRef<HTMLDivElement>(null)
  const rowVirtualizer = useVirtualizer({
    count: virtualEntries.length,
    getScrollElement: () => scrollParentRef.current,
    estimateSize: (index) =>
      virtualEntries[index]?.kind === 'detail' ? ESTIMATED_DETAIL_HEIGHT : ESTIMATED_ROW_HEIGHT,
    overscan: 12,
  })
  const virtualRows = rowVirtualizer.getVirtualItems()
  const paddingTop = virtualRows.length > 0 ? virtualRows[0].start : 0
  const paddingBottom =
    virtualRows.length > 0 ? rowVirtualizer.getTotalSize() - virtualRows[virtualRows.length - 1].end : 0

  if (isLoading) return null

  if (isError || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Suggested transfer matches</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            {error?.message || "Couldn't check for transfer matches — try again in a moment."}
          </p>
        </CardContent>
      </Card>
    )
  }

  if (!hasAnyRealAccount(Object.values(accounts))) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Suggested transfer matches</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">No accounts yet — import a statement to get started.</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
        <CardTitle>Possible transfers not yet caught by a rule</CardTitle>
        {/* `text-xs text-muted-foreground` sits on the wrapper, not just the
            label, because the trailing "days" is outside the caption. */}
        <Field label="Search within" className="flex-row items-center gap-2 text-xs text-muted-foreground">
          {(id) => (
            <>
              <Input
                id={id}
                type="number"
                min={1}
                className="w-16"
                value={windowDaysDraft}
                onChange={(event) => handleWindowDaysChange(event.target.value)}
                onBlur={handleWindowDaysBlur}
              />
              days
            </>
          )}
        </Field>
      </CardHeader>
      <CardContent>
        {sorted.length === 0 ? (
          <p className="text-sm text-muted-foreground">No unresolved transfer pairs within {windowDays} days.</p>
        ) : (
          <>
            <div ref={scrollParentRef} className="max-h-[70vh] overflow-y-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <SortableTableHead
                      active={sort.key === 'account_id'}
                      desc={sort.desc}
                      onClick={() => toggleSort('account_id')}
                    >
                      Account
                    </SortableTableHead>
                    <SortableTableHead
                      active={sort.key === 'posted_at'}
                      desc={sort.desc}
                      onClick={() => toggleSort('posted_at')}
                    >
                      Date
                    </SortableTableHead>
                    <SortableTableHead
                      active={sort.key === 'other_account_id'}
                      desc={sort.desc}
                      onClick={() => toggleSort('other_account_id')}
                    >
                      Other account
                    </SortableTableHead>
                    <SortableTableHead
                      active={sort.key === 'other_posted_at'}
                      desc={sort.desc}
                      onClick={() => toggleSort('other_posted_at')}
                    >
                      Date
                    </SortableTableHead>
                    <SortableTableHead
                      align="right"
                      active={sort.key === 'amount'}
                      desc={sort.desc}
                      onClick={() => toggleSort('amount')}
                    >
                      Amount
                    </SortableTableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {paddingTop > 0 && (
                    <TableRow>
                      <TableCell colSpan={5} style={{ height: paddingTop, padding: 0 }} />
                    </TableRow>
                  )}
                  {virtualRows.map((virtualRow) => {
                    const entry = virtualEntries[virtualRow.index]
                    const { suggestion } = entry
                    const key = suggestionKey(suggestion)
                    if (entry.kind === 'main') {
                      return (
                        <TableRow
                          key={`${key}-main`}
                          data-index={virtualRow.index}
                          ref={rowVirtualizer.measureElement}
                          className="cursor-pointer"
                          onClick={() => setExpandedKey(expandedKey === key ? null : key)}
                        >
                          <TableCell>{accountName(accounts, suggestion.account_id)}</TableCell>
                          <TableCell className="text-muted-foreground">
                            {formatDate(suggestion.posted_at.slice(0, 10))}
                          </TableCell>
                          <TableCell>{accountName(accounts, suggestion.other_account_id)}</TableCell>
                          <TableCell className="text-muted-foreground">
                            {formatDate(suggestion.other_posted_at.slice(0, 10))}
                          </TableCell>
                          <TableCell className={`text-right tabular-nums ${signColor(suggestion.amount)}`}>
                            {formatCurrency(suggestion.amount, 'USD')}
                          </TableCell>
                        </TableRow>
                      )
                    }
                    return (
                      <TableRow key={`${key}-detail`} data-index={virtualRow.index} ref={rowVirtualizer.measureElement}>
                        <TableCell colSpan={5} className="whitespace-normal bg-muted/30">
                          <div className="grid gap-4 py-2 sm:grid-cols-2">
                            <div className="min-w-0 space-y-1 text-sm">
                              <p className="font-medium">{accountName(accounts, suggestion.account_id)}</p>
                              <p className="text-muted-foreground">{formatDate(suggestion.posted_at.slice(0, 10))}</p>
                              <p className="break-words">{suggestion.description}</p>
                              <p className={`tabular-nums ${signColor(suggestion.amount)}`}>
                                {formatCurrency(suggestion.amount, 'USD')}
                              </p>
                            </div>
                            <div className="min-w-0 space-y-1 text-sm">
                              <p className="font-medium">{accountName(accounts, suggestion.other_account_id)}</p>
                              <p className="text-muted-foreground">
                                {formatDate(suggestion.other_posted_at.slice(0, 10))}
                              </p>
                              <p className="break-words">{suggestion.other_description}</p>
                              <p className={`tabular-nums ${signColor(-suggestion.amount)}`}>
                                {formatCurrency(-suggestion.amount, 'USD')}
                              </p>
                            </div>
                          </div>
                          <div className="mt-3">
                            <SuggestedRulePair
                              suggestion={suggestion}
                              accounts={accounts}
                              existingRules={rules}
                              transactionIds={transactionIdsFor(suggestion)}
                              onAdd={addRules}
                              onLink={linkPair}
                              onDismiss={() => dismiss(suggestion)}
                              linkPending={createTransferLink.isPending}
                            />
                          </div>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                  {paddingBottom > 0 && (
                    <TableRow>
                      <TableCell colSpan={5} style={{ height: paddingBottom, padding: 0 }} />
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              Click a row to see both sides and add both rules needed to resolve it — each pair looks like one transfer
              a rule hasn't resolved yet.
            </p>
          </>
        )}
        <div className="mt-4">
          <SuggestionArchive kind="transfer" />
        </div>
      </CardContent>
    </Card>
  )
}
