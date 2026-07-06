import { Fragment, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { useSortableRows } from '@/hooks/useSortableRows'
import { usePersistedState } from '@/hooks/usePersistedState'
import { useSetRules, useTransferSuggestions } from '@/hooks/useAccountingData'
import { formatCurrency, formatDate, signColor } from '@/lib/format'
import type { Account, Rule, TransferSuggestion } from '@/types/accounting'

function suggestionKey(suggestion: TransferSuggestion): string {
  return `${suggestion.posting_id}-${suggestion.other_posting_id}`
}

// One suggestion resolves into TWO independent one-directional rule
// drafts — a transfer is really two separate transactions (each imported
// against its own placeholder counterparty), so fully resolving it means
// writing one rule per side: "on account A, if description contains X,
// repoint to B" and the mirror for B. A user might only want one
// direction (or neither), so both are offered, each with its own Add button.
function suggestedRuleDrafts(suggestion: TransferSuggestion): Rule[] {
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

function SuggestedRuleCard({
  draft,
  accounts,
  alreadyAdded,
  onAdd,
}: {
  draft: Rule
  accounts: Record<string, Account>
  alreadyAdded: boolean
  onAdd: (rule: Rule) => void
}) {
  const [rule, setRule] = useState(draft)
  return (
    <div className="space-y-2 rounded-md border p-3">
      <p className="text-xs text-muted-foreground">
        On <span className="font-medium text-foreground">{accountName(accounts, rule.account_id)}</span>, if description contains…
      </p>
      <Input
        value={rule.description_contains}
        disabled={alreadyAdded}
        onChange={(event) => setRule((prev) => ({ ...prev, description_contains: event.target.value }))}
      />
      <p className="text-xs text-muted-foreground">…repoint it to {accountName(accounts, rule.counterparty_account_id)}.</p>
      <Button size="sm" disabled={alreadyAdded} onClick={() => onAdd(rule)}>
        {alreadyAdded ? 'Rule added' : 'Add rule'}
      </Button>
    </div>
  )
}

// Suggests — never applies — likely internal transfers no `Rule` has
// caught yet: two postings, on two real accounts, still pointing at a
// placeholder counterparty, whose amounts are equal and opposite within
// `windowDays` of each other. Clicking a row shows both sides plus a
// proposed rule for each direction, editable before adding.
export function TransferSuggestionsPanel({ accounts, rules }: { accounts: Record<string, Account>; rules: Rule[] }) {
  const [windowDays, setWindowDays] = usePersistedState('accounting.transfer-suggestions.window-days', 3)
  const { data } = useTransferSuggestions(windowDays)
  const [expandedKey, setExpandedKey] = useState<string | null>(null)
  const setRules = useSetRules()
  const { sorted, sort, toggleSort } = useSortableRows(data ?? [], 'posted_at')
  const existingRuleIds = new Set(rules.map((rule) => rule.rule_id))

  function addRule(rule: Rule) {
    setRules.mutate([...rules, rule])
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
            value={windowDays}
            onChange={(event) => setWindowDays(Math.max(1, Number(event.target.value) || 1))}
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
                            <div className="mt-3 grid gap-3 sm:grid-cols-2">
                              {suggestedRuleDrafts(suggestion).map((draft) => (
                                <SuggestedRuleCard
                                  key={draft.rule_id}
                                  draft={draft}
                                  accounts={accounts}
                                  alreadyAdded={existingRuleIds.has(draft.rule_id)}
                                  onAdd={addRule}
                                />
                              ))}
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
              Click a row to see both sides and propose a rule for either direction — each pair looks like one
              transfer a rule hasn't resolved yet.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  )
}
