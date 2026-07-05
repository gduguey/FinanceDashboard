import { useMemo, useState } from 'react'
import { Trash2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatDate, formatUsd, signColor } from '@/lib/format'
import {
  useAccountingStore,
  usePostings,
  useSetCategories,
  useSetPostingOverride,
  useSetRules,
  useTransferSuggestions,
} from '@/hooks/useAccountingData'
import type { Account, Category, Posting, Rule } from '@/types/accounting'

const NO_CATEGORY = '__none__'

function CategorySelect({
  categories,
  classification,
  value,
  onChange,
}: {
  categories: Record<string, Category>
  classification: 'income' | 'expense'
  value: string | null
  onChange: (categoryId: string | null) => void
}) {
  const topLevel = Object.values(categories).filter(
    (category) => category.classification === classification && category.parent_category_id === null,
  )
  return (
    <Select value={value ?? NO_CATEGORY} onValueChange={(next) => onChange(next === NO_CATEGORY ? null : next)}>
      <SelectTrigger size="sm" className="w-44">
        <SelectValue placeholder="Uncategorized" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NO_CATEGORY}>Uncategorized</SelectItem>
        {topLevel.map((category) => (
          <SelectItem key={category.category_id} value={category.category_id}>
            {category.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function TransactionsTable({ postings, accounts, categories }: { postings: Posting[]; accounts: Record<string, Account>; categories: Record<string, Category> }) {
  const [search, setSearch] = useState('')
  const setOverride = useSetPostingOverride()

  const rows = useMemo(() => {
    const uncategorizedIds = new Set(['uncategorized:expense', 'uncategorized:income'])
    return postings
      .filter((posting) => !uncategorizedIds.has(posting.account_id))
      .filter((posting) => posting.description.toLowerCase().includes(search.toLowerCase()))
      .sort((a, b) => b.posted_at.localeCompare(a.posted_at))
  }, [postings, search])

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Transactions</CardTitle>
        <Input
          className="w-56"
          placeholder="Search description…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No transactions yet — import a CSV to start.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Account</TableHead>
                <TableHead>Description</TableHead>
                <TableHead className="text-right">Amount</TableHead>
                <TableHead>Category</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((posting) => (
                <TableRow key={posting.posting_id}>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {formatDate(posting.posted_at.slice(0, 10))}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {accounts[posting.account_id]?.name ?? posting.account_id}
                  </TableCell>
                  <TableCell className="max-w-xs truncate">{posting.description}</TableCell>
                  <TableCell className={`text-right tabular-nums ${signColor(posting.amount)}`}>
                    {formatUsd(posting.amount)}
                  </TableCell>
                  <TableCell>
                    <CategorySelect
                      categories={categories}
                      classification={posting.amount >= 0 ? 'income' : 'expense'}
                      value={posting.category_id}
                      onChange={(categoryId) =>
                        setOverride.mutate({ postingId: posting.posting_id, override: { category_id: categoryId } })
                      }
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}

function CategoriesPanel({ categories }: { categories: Record<string, Category> }) {
  const setCategories = useSetCategories()
  const [draft, setDraft] = useState<{ name: string; classification: 'income' | 'expense' }>({
    name: '',
    classification: 'expense',
  })

  const topLevel = Object.values(categories)
    .filter((category) => category.parent_category_id === null)
    .sort((a, b) => a.name.localeCompare(b.name))

  function addCategory() {
    if (!draft.name) return
    const id = `${draft.classification}:${draft.name.toLowerCase().replace(/\s+/g, '-')}`
    const color = '#6471eb'
    setCategories.mutate({ ...categories, [id]: { category_id: id, name: draft.name, classification: draft.classification, parent_category_id: null, color } })
    setDraft({ name: '', classification: draft.classification })
  }

  function removeCategory(categoryId: string) {
    const next = { ...categories }
    delete next[categoryId]
    for (const [id, category] of Object.entries(next)) {
      if (category.parent_category_id === categoryId) delete next[id]
    }
    setCategories.mutate(next)
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Categories</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-2">
          {topLevel.map((category) => (
            <Badge key={category.category_id} variant="outline" className="flex items-center gap-1.5">
              <span className="inline-block size-2 rounded-full" style={{ background: category.color }} />
              {category.name}
              <span className="text-muted-foreground/70">({category.classification})</span>
              <button onClick={() => removeCategory(category.category_id)} className="ml-1 text-muted-foreground/60 hover:text-destructive">
                <Trash2 className="size-3" />
              </button>
            </Badge>
          ))}
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Name
            <Input
              className="w-40"
              value={draft.name}
              onChange={(event) => setDraft((prev) => ({ ...prev, name: event.target.value }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Classification
            <Select
              value={draft.classification}
              onValueChange={(value) => value && setDraft((prev) => ({ ...prev, classification: value as 'income' | 'expense' }))}
            >
              <SelectTrigger size="sm" className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="expense">Expense</SelectItem>
                <SelectItem value="income">Income</SelectItem>
              </SelectContent>
            </Select>
          </label>
          <Button size="sm" onClick={addCategory}>
            Add category
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function RulesPanel({ rules }: { rules: Rule[] }) {
  const setRules = useSetRules()
  const [draft, setDraft] = useState({ descriptionContains: '', counterpartyAccountId: '', counterpartyAccountName: '' })

  function addRule() {
    if (!draft.descriptionContains || !draft.counterpartyAccountId) return
    const rule: Rule = {
      rule_id: `manual:${Date.now()}`,
      description_contains: draft.descriptionContains,
      account_id: null,
      category_id: null,
      subcategory_id: null,
      counterparty_account_id: draft.counterpartyAccountId,
      counterparty_account_name: draft.counterpartyAccountName || draft.counterpartyAccountId,
      counterparty_account_kind: 'expense_payee',
      counterparty_parent_account_id: null,
      priority: 100,
    }
    setRules.mutate([...rules, rule])
    setDraft({ descriptionContains: '', counterpartyAccountId: '', counterpartyAccountName: '' })
  }

  function removeRule(ruleId: string) {
    setRules.mutate(rules.filter((rule) => rule.rule_id !== ruleId))
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Rules</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>If description contains</TableHead>
              <TableHead>Counterparty</TableHead>
              <TableHead className="w-8" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rules.map((rule) => (
              <TableRow key={rule.rule_id}>
                <TableCell className="font-medium">{rule.description_contains}</TableCell>
                <TableCell className="text-muted-foreground">{rule.counterparty_account_name}</TableCell>
                <TableCell>
                  <Button variant="ghost" size="icon" onClick={() => removeRule(rule.rule_id)}>
                    <Trash2 className="size-3.5 text-muted-foreground" />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Description contains
            <Input
              className="w-56"
              value={draft.descriptionContains}
              onChange={(event) => setDraft((prev) => ({ ...prev, descriptionContains: event.target.value }))}
              placeholder="e.g. NETFLIX"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Counterparty account id
            <Input
              className="w-48"
              value={draft.counterpartyAccountId}
              onChange={(event) => setDraft((prev) => ({ ...prev, counterpartyAccountId: event.target.value }))}
              placeholder="e.g. payee:netflix"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Counterparty name
            <Input
              className="w-48"
              value={draft.counterpartyAccountName}
              onChange={(event) => setDraft((prev) => ({ ...prev, counterpartyAccountName: event.target.value }))}
              placeholder="e.g. Netflix"
            />
          </label>
          <Button size="sm" onClick={addRule}>
            Add rule
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function TransferSuggestionsPanel() {
  const { data } = useTransferSuggestions()
  if (!data?.length) return null
  return (
    <Card>
      <CardHeader>
        <CardTitle>Possible transfers not yet caught by a rule</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Account</TableHead>
              <TableHead>Date</TableHead>
              <TableHead>Other account</TableHead>
              <TableHead>Date</TableHead>
              <TableHead className="text-right">Amount</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((suggestion) => (
              <TableRow key={`${suggestion.posting_id}-${suggestion.other_posting_id}`}>
                <TableCell>{suggestion.account_id}</TableCell>
                <TableCell className="text-muted-foreground">{formatDate(suggestion.posted_at.slice(0, 10))}</TableCell>
                <TableCell>{suggestion.other_account_id}</TableCell>
                <TableCell className="text-muted-foreground">{formatDate(suggestion.other_posted_at.slice(0, 10))}</TableCell>
                <TableCell className={`text-right tabular-nums ${signColor(suggestion.amount)}`}>
                  {formatUsd(suggestion.amount)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <p className="mt-2 text-xs text-muted-foreground">
          Each pair looks like one transfer a rule hasn't resolved yet — add a rule, or categorize each side manually.
        </p>
      </CardContent>
    </Card>
  )
}

export function AccountingPage() {
  const { data: store, isLoading } = useAccountingStore()
  const { data: postings } = usePostings()

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="sticky top-0 z-10 border-b border-border bg-white/95 px-8 py-5 backdrop-blur-sm">
        <h1 className="text-lg font-semibold tracking-tight text-foreground">Accounting</h1>
      </div>

      <div className="mx-auto max-w-5xl space-y-6 px-8 py-8">
        {isLoading || !store ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <>
            <TransactionsTable postings={postings ?? []} accounts={store.accounts} categories={store.categories} />
            <TransferSuggestionsPanel />
            <CategoriesPanel categories={store.categories} />
            <RulesPanel rules={store.rules} />
          </>
        )}
      </div>
    </div>
  )
}
