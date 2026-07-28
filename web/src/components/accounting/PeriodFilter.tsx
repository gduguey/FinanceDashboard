import { RotateCcw } from 'lucide-react'
import { MonthSelect } from '@/components/accounting/MonthSelect'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { PeriodGranularity, usePeriodFilter } from '@/hooks/usePeriodFilter'
import { availableMonths } from '@/lib/months'
import type { Account, Posting, Tag } from '@/types/accounting'

const GRANULARITY_ITEMS = { month: 'Month', range: 'Date range', year: 'Year' }

export function PeriodFilterBar({
  filter,
  accounts,
  tags,
  postings,
}: {
  filter: ReturnType<typeof usePeriodFilter>
  accounts: Record<string, Account>
  tags: Record<string, Tag>
  postings: Posting[]
}) {
  const accountOptions = Object.values(accounts)
    .filter((account) => !['income_source', 'expense_payee'].includes(account.kind))
    .sort((a, b) => a.name.localeCompare(b.name))
  const tagOptions = Object.values(tags).sort((a, b) => a.name.localeCompare(b.name))
  const months = availableMonths(postings)

  const accountItems = {
    __all__: 'All accounts',
    ...Object.fromEntries(accountOptions.map((a) => [a.account_id, a.name])),
  }
  const tagItems = { __all__: 'All tags', ...Object.fromEntries(tagOptions.map((t) => [t.tag_id, t.name])) }

  return (
    <div className="flex flex-wrap items-end gap-2">
      <Select
        value={filter.granularity}
        onValueChange={(value) => value && filter.setGranularity(value as PeriodGranularity)}
      >
        <SelectTrigger size="sm" className="min-w-28">
          <SelectValue items={GRANULARITY_ITEMS} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="month">Month</SelectItem>
          <SelectItem value="range">Date range</SelectItem>
          <SelectItem value="year">Year</SelectItem>
        </SelectContent>
      </Select>

      {filter.granularity === 'month' && (
        <MonthSelect value={filter.month} onChange={filter.setMonth} months={months} />
      )}
      {filter.granularity === 'year' && (
        <Input
          type="number"
          className="w-24"
          value={filter.year}
          onChange={(event) => filter.setYear(event.target.value)}
        />
      )}
      {filter.granularity === 'range' && (
        <>
          <Input
            type="date"
            className="w-36"
            value={filter.rangeStart}
            onChange={(event) => filter.setRangeStart(event.target.value)}
          />
          <Input
            type="date"
            className="w-36"
            value={filter.rangeEnd}
            onChange={(event) => filter.setRangeEnd(event.target.value)}
          />
        </>
      )}

      <Select
        value={filter.accountId ?? '__all__'}
        onValueChange={(value) => filter.setAccountId(value === '__all__' ? null : value)}
      >
        <SelectTrigger size="sm" className="min-w-40">
          <SelectValue items={accountItems} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">All accounts</SelectItem>
          {accountOptions.map((account) => (
            <SelectItem key={account.account_id} value={account.account_id}>
              {account.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={filter.tagId ?? '__all__'}
        onValueChange={(value) => filter.setTagId(value === '__all__' ? null : value)}
      >
        <SelectTrigger size="sm" className="min-w-36">
          <SelectValue items={tagItems} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__all__">All tags</SelectItem>
          {tagOptions.map((tag) => (
            <SelectItem key={tag.tag_id} value={tag.tag_id}>
              {tag.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Button variant="ghost" size="sm" onClick={filter.reset}>
        <RotateCcw className="size-3.5" />
        Reset filters
      </Button>
    </div>
  )
}
