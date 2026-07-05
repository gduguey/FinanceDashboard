import { useMemo, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHeader, TableRow } from '@/components/ui/table'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { CategorySelect, SubcategorySelect } from '@/components/accounting/CategorySelect'
import { TagsCell } from '@/components/accounting/TagsCell'
import { formatCurrency, formatDate } from '@/lib/format'
import { useSortableRows } from '@/hooks/useSortableRows'
import { useSetPostingOverride } from '@/hooks/useAccountingData'
import type { Account, Category, Posting, Tag } from '@/types/accounting'

const ALL = '__all__'
const PLACEHOLDER_ACCOUNT_IDS = new Set(['uncategorized:expense', 'uncategorized:income'])

export function TransactionsTab({
  postings,
  accounts,
  categories,
  tags,
}: {
  postings: Posting[]
  accounts: Record<string, Account>
  categories: Record<string, Category>
  tags: Record<string, Tag>
}) {
  const [search, setSearch] = useState('')
  const [accountFilter, setAccountFilter] = useState(ALL)
  const [categoryFilter, setCategoryFilter] = useState(ALL)
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const setOverride = useSetPostingOverride()

  const realAccounts = Object.values(accounts)
    .filter((account) => !PLACEHOLDER_ACCOUNT_IDS.has(account.account_id))
    .sort((a, b) => a.name.localeCompare(b.name))
  const topLevelCategories = Object.values(categories)
    .filter((category) => category.parent_category_id === null)
    .sort((a, b) => a.name.localeCompare(b.name))

  const filtered = useMemo(() => {
    return postings
      .filter((posting) => !PLACEHOLDER_ACCOUNT_IDS.has(posting.account_id))
      .filter((posting) => posting.description.toLowerCase().includes(search.toLowerCase()))
      .filter((posting) => accountFilter === ALL || posting.account_id === accountFilter)
      .filter((posting) => categoryFilter === ALL || posting.category_id === categoryFilter)
      .filter((posting) => !startDate || posting.posted_at.slice(0, 10) >= startDate)
      .filter((posting) => !endDate || posting.posted_at.slice(0, 10) <= endDate)
  }, [postings, search, accountFilter, categoryFilter, startDate, endDate])

  const { sorted, sort, toggleSort } = useSortableRows(filtered, 'posted_at')

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-end justify-between gap-3">
        <CardTitle>Transactions</CardTitle>
        <div className="flex flex-wrap items-end gap-2">
          <Input
            className="w-48"
            placeholder="Search description…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <Select value={accountFilter} onValueChange={(value) => value && setAccountFilter(value)}>
            <SelectTrigger size="sm" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All accounts</SelectItem>
              {realAccounts.map((account) => (
                <SelectItem key={account.account_id} value={account.account_id}>
                  {account.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={categoryFilter} onValueChange={(value) => value && setCategoryFilter(value)}>
            <SelectTrigger size="sm" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All categories</SelectItem>
              {topLevelCategories.map((category) => (
                <SelectItem key={category.category_id} value={category.category_id}>
                  {category.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input type="date" className="w-36" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
          <Input type="date" className="w-36" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
        </div>
      </CardHeader>
      <CardContent>
        {sorted.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No transactions match — import a statement to start.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <SortableTableHead active={sort.key === 'posted_at'} desc={sort.desc} onClick={() => toggleSort('posted_at')}>
                  Date
                </SortableTableHead>
                <SortableTableHead active={sort.key === 'account_id'} desc={sort.desc} onClick={() => toggleSort('account_id')}>
                  Account
                </SortableTableHead>
                <SortableTableHead active={sort.key === 'description'} desc={sort.desc} onClick={() => toggleSort('description')}>
                  Description
                </SortableTableHead>
                <SortableTableHead align="right" active={sort.key === 'amount'} desc={sort.desc} onClick={() => toggleSort('amount')}>
                  Amount
                </SortableTableHead>
                <SortableTableHead active={sort.key === 'category_id'} desc={sort.desc} onClick={() => toggleSort('category_id')}>
                  Category
                </SortableTableHead>
                <SortableTableHead active={sort.key === 'subcategory_id'} desc={sort.desc} onClick={() => toggleSort('subcategory_id')}>
                  Subcategory
                </SortableTableHead>
                <SortableTableHead active={sort.key === 'tag_ids'} desc={sort.desc} onClick={() => toggleSort('tag_ids')}>
                  Tags
                </SortableTableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((posting) => (
                <TableRow key={posting.posting_id}>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {formatDate(posting.posted_at.slice(0, 10))}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground">
                    {accounts[posting.account_id]?.name ?? posting.account_id}
                  </TableCell>
                  <TableCell className="max-w-xs truncate">{posting.description}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(posting.amount, posting.currency)}
                  </TableCell>
                  <TableCell>
                    <CategorySelect
                      categories={categories}
                      classification={posting.amount >= 0 ? 'income' : 'expense'}
                      value={posting.category_id}
                      onChange={(categoryId) =>
                        setOverride.mutate({
                          postingId: posting.posting_id,
                          override: { category_id: categoryId, subcategory_id: null },
                        })
                      }
                    />
                  </TableCell>
                  <TableCell>
                    <SubcategorySelect
                      categories={categories}
                      categoryId={posting.category_id}
                      value={posting.subcategory_id}
                      onChange={(subcategoryId) =>
                        setOverride.mutate({ postingId: posting.posting_id, override: { subcategory_id: subcategoryId } })
                      }
                    />
                  </TableCell>
                  <TableCell>
                    <TagsCell
                      tagIds={posting.tag_ids}
                      tags={tags}
                      onChange={(tagIds) => setOverride.mutate({ postingId: posting.posting_id, override: { tag_ids: tagIds } })}
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
