import { Fragment, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { useSortableRows } from '@/hooks/useSortableRows'
import { usePersistedState } from '@/hooks/usePersistedState'
import { useSetTransferRules, useTransferSuggestions } from '@/hooks/useAccountingData'
import { formatCurrency, formatDate, signColor } from '@/lib/format'
import type { Account, TransferRule, TransferSuggestion } from '@/types/accounting'

function suggestionKey(suggestion: TransferSuggestion): string {
  return `${suggestion.posting_id}-${suggestion.other_posting_id}`
}

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
        On <span className="font-medium text-foreground">{accountName(accounts, draft.account_id)}</span>, if description contains…
      </p>
      <Input
        value={draft.description_contains}
        disabled={alreadyAdded}
        onChange={(event) => onChange({ ...draft, description_contains: event.target.value })}
      />
      <p className="text-xs text-muted-foreground">…repoint it to {accountName(accounts, draft.counterparty_account_id)}.</p>
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
}: {
  suggestion: TransferSuggestion
  accounts: Record<string, Account>
  existingRules: TransferRule[]
  onAdd: (rules: TransferRule[]) => void
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
      <p className="text-xs text-muted-foreground">
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
      <Button size="sm" disabled={allAdded} onClick={handleAddBoth}>
        {allAdded ? 'Both rules added' : 'Add both rules'}
      </Button>
    </div>
  )
}

// Suggests — never applies — likely internal transfers no transfer rule
// has caught yet: two postings, on two real accounts, still pointing at a
// placeholder counterparty, whose amounts are equal and opposite within
// `windowDays` of each other. Clicking a row shows both sides plus both
// proposed rules, editable before adding.
export function TransferSuggestionsPanel({ accounts, rules }: { accounts: Record<string, Account>; rules: TransferRule[] }) {
  const [windowDays, setWindowDays] = usePersistedState('accounting.transfer-suggestions.window-days', 3)
  const [windowDaysDraft, setWindowDaysDraft] = useState(String(windowDays))
  const { data } = useTransferSuggestions(windowDays)
  const [expandedKey, setExpandedKey] = useState<string | null>(null)
  const setRules = useSetTransferRules()
  const { sorted, sort, toggleSort } = useSortableRows(data ?? [], 'posted_at')

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

  if (!data) return null

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
            <Table>
              <TableHeader>
                <TableRow>
                  <SortableTableHead active={sort.key === 'account_id'} desc={sort.desc} onClick={() => toggleSort('account_id')}>
                    Account
                  </SortableTableHead>
                  <SortableTableHead active={sort.key === 'posted_at'} desc={sort.desc} onClick={() => toggleSort('posted_at')}>
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
                  <SortableTableHead align="right" active={sort.key === 'amount'} desc={sort.desc} onClick={() => toggleSort('amount')}>
                    Amount
                  </SortableTableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sorted.map((suggestion) => {
                  const key = suggestionKey(suggestion)
                  const isExpanded = expandedKey === key
                  return (
                    <Fragment key={key}>
                      <TableRow className="cursor-pointer" onClick={() => setExpandedKey(isExpanded ? null : key)}>
                        <TableCell>{accountName(accounts, suggestion.account_id)}</TableCell>
                        <TableCell className="text-muted-foreground">{formatDate(suggestion.posted_at.slice(0, 10))}</TableCell>
                        <TableCell>{accountName(accounts, suggestion.other_account_id)}</TableCell>
                        <TableCell className="text-muted-foreground">{formatDate(suggestion.other_posted_at.slice(0, 10))}</TableCell>
                        <TableCell className={`text-right tabular-nums ${signColor(suggestion.amount)}`}>
                          {formatCurrency(suggestion.amount, 'USD')}
                        </TableCell>
                      </TableRow>
                      {isExpanded && (
                        <TableRow>
                          <TableCell colSpan={5} className="bg-muted/30">
                            <div className="grid gap-4 py-2 sm:grid-cols-2">
                              <div className="space-y-1 text-sm">
                                <p className="font-medium">{accountName(accounts, suggestion.account_id)}</p>
                                <p className="text-muted-foreground">{formatDate(suggestion.posted_at.slice(0, 10))}</p>
                                <p>{suggestion.description}</p>
                                <p className={`tabular-nums ${signColor(suggestion.amount)}`}>{formatCurrency(suggestion.amount, 'USD')}</p>
                              </div>
                              <div className="space-y-1 text-sm">
                                <p className="font-medium">{accountName(accounts, suggestion.other_account_id)}</p>
                                <p className="text-muted-foreground">{formatDate(suggestion.other_posted_at.slice(0, 10))}</p>
                                <p>{suggestion.other_description}</p>
                                <p className={`tabular-nums ${signColor(-suggestion.amount)}`}>
                                  {formatCurrency(-suggestion.amount, 'USD')}
                                </p>
                              </div>
                            </div>
                            <div className="mt-3">
                              <SuggestedRulePair suggestion={suggestion} accounts={accounts} existingRules={rules} onAdd={addRules} />
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  )
                })}
              </TableBody>
            </Table>
            <p className="mt-2 text-xs text-muted-foreground">
              Click a row to see both sides and add both rules needed to resolve it — each pair looks like one
              transfer a rule hasn't resolved yet.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  )
}
