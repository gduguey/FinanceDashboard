import { useVirtualizer } from '@tanstack/react-virtual'
import { Archive } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { SuggestionArchive } from '@/components/accounting/SuggestionArchive'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { useDismissSuggestion, useSetTransferRules, useTransferSuggestions } from '@/hooks/useAccountingData'
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
function suggestedRuleDrafts(suggestion: TransferSuggestion): TransferRule[] {
  return [
    {
      rule_id: `transfer:${suggestion.posting_id}`,
      description_contains: suggestion.description,
      account_id: suggestion.account_id,
      category_id: null,
      subcategory_id: null,
      counterparty_account_id: suggestion.other_account_id,
      priority: 100,
      description: '',
      active: true,
    },
    {
      rule_id: `transfer:${suggestion.other_posting_id}`,
      description_contains: suggestion.other_description,
      account_id: suggestion.other_account_id,
      category_id: null,
      subcategory_id: null,
      counterparty_account_id: suggestion.account_id,
      priority: 100,
      description: '',
      active: true,
    },
  ]
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
  draft: TransferRule
  accounts: Record<string, Account>
  alreadyAdded: boolean
  onChange: (draft: TransferRule) => void
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
  onAdd,
  onDismiss,
}: {
  suggestion: TransferSuggestion
  accounts: Record<string, Account>
  existingRules: TransferRule[]
  onAdd: (rules: TransferRule[]) => void
  onDismiss: () => void
}) {
  const [drafts, setDrafts] = useState(() => suggestedRuleDrafts(suggestion))
  const existingRuleIds = new Set(existingRules.map((rule) => rule.rule_id))
  const alreadyAdded = drafts.map((draft) => existingRuleIds.has(draft.rule_id))
  const allAdded = alreadyAdded.every(Boolean)

  function updateDraft(index: number, next: TransferRule) {
    setDrafts((prev) => prev.map((draft, draftIndex) => (draftIndex === index ? next : draft)))
  }

  function handleAddBoth() {
    const toAdd = drafts.filter((_, index) => !alreadyAdded[index])
    onAdd(toAdd)
  }

  return (
    <div className="space-y-3">
      <p className="max-w-xl text-xs break-words text-muted-foreground">
        Both rules below are needed to fully resolve this transfer — each one only fixes the transaction on its own
        account; the other side stays exactly as it is until its own rule is added too.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {drafts.map((draft, index) => (
          <DraftRuleCard
            key={draft.rule_id}
            draft={draft}
            accounts={accounts}
            alreadyAdded={alreadyAdded[index]}
            onChange={(next) => updateDraft(index, next)}
          />
        ))}
      </div>
      <div className="flex items-center gap-2">
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
  const setRules = useSetTransferRules()
  const dismissSuggestion = useDismissSuggestion()
  const { sorted, sort, toggleSort } = useSortableRows(data ?? [], 'posted_at')

  function dismiss(suggestion: TransferSuggestion) {
    dismissSuggestion.mutate({
      suggestion_id: suggestion.suggestion_id,
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

  function addRules(newRules: TransferRule[]) {
    if (newRules.length === 0) return
    setRules.mutate([...rules, ...newRules])
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
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          Search within
          <Input
            type="number"
            min={1}
            className="w-16"
            value={windowDaysDraft}
            onChange={(event) => handleWindowDaysChange(event.target.value)}
            onBlur={handleWindowDaysBlur}
          />
          days
        </label>
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
                              onAdd={addRules}
                              onDismiss={() => dismiss(suggestion)}
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
