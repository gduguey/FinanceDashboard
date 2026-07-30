import { RotateCcw } from 'lucide-react'
import { FilterPanel, FilterRow } from '@/components/shared/FilterPanel'
import { FilterSelect } from '@/components/shared/FilterSelect'
import { MultiSelectFilter } from '@/components/shared/MultiSelectFilter'
import { OptionalDateInput } from '@/components/shared/OptionalDateInput'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { FILTER_ALL as ALL } from '@/lib/filters'
import {
  activeFilterCount as countActiveFilters,
  DATE_MODE_MONTH,
  DATE_MODE_RANGE,
  defaultFilterState,
  PENDING_OPTIONS,
  type PersistedFilters,
  TRANSFER_FLAG_OPTIONS,
} from '@/lib/transactionFilters'

const INCOME_EXPENSE_ITEMS: Record<string, string> = { [ALL]: 'All', income: 'Income', expense: 'Expense' }
const CATEGORIZED_ITEMS: Record<string, string> = {
  [ALL]: 'All',
  categorized: 'Categorized',
  uncategorized: 'Uncategorized',
}

/** One choice a picker offers, as `MultiSelectFilter` and the month `Select` both want it. */
export interface FilterOption {
  id: string
  name: string
}

// The whole right-hand side of the table's header: the search box, the
// "Reset filters" shortcut, and the panel holding the other nine rows. Takes
// the whole `FilterState` and one setter rather than nine callbacks — every
// control here writes the same persisted object, and splitting that into a
// prop per row would only relocate the spread.
export function TransactionsFilterBar({
  filters,
  setFilters,
  search,
  onSearchChange,
  monthItems,
  accountItems,
  categoryOptions,
  subcategoryOptions,
  tagFilterOptions,
}: {
  filters: PersistedFilters
  setFilters: (next: PersistedFilters) => void
  search: string
  onSearchChange: (next: string) => void
  monthItems: Record<string, string>
  accountItems: Record<string, string>
  categoryOptions: FilterOption[]
  subcategoryOptions: FilterOption[]
  tagFilterOptions: FilterOption[]
}) {
  const activeFilterCount = countActiveFilters(filters)
  return (
    <div className="flex flex-wrap items-end gap-2">
      {activeFilterCount > 0 && (
        <Button variant="ghost" size="sm" onClick={() => setFilters(defaultFilterState())}>
          <RotateCcw className="size-3.5" />
          Reset filters
        </Button>
      )}
      <Input
        className="w-48"
        placeholder="Search description…"
        value={search}
        onChange={(event) => onSearchChange(event.target.value)}
      />
      <FilterPanel activeCount={activeFilterCount}>
        <FilterRow label="Date">
          <div className="flex items-center gap-1.5">
            <Button
              type="button"
              variant={filters.dateMode === DATE_MODE_MONTH ? 'default' : 'outline'}
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => setFilters({ ...filters, dateMode: DATE_MODE_MONTH })}
            >
              Month
            </Button>
            <Button
              type="button"
              variant={filters.dateMode === DATE_MODE_RANGE ? 'default' : 'outline'}
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => setFilters({ ...filters, dateMode: DATE_MODE_RANGE })}
            >
              Range
            </Button>
          </div>
          {filters.dateMode === DATE_MODE_MONTH ? (
            <Select value={filters.month} onValueChange={(month) => month && setFilters({ ...filters, month })}>
              <SelectTrigger size="sm" className="mt-1.5 w-full">
                <SelectValue items={monthItems} />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(monthItems).map(([id, name]) => (
                  <SelectItem key={id} value={id}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <div className="mt-1.5 flex flex-col gap-1.5">
              <OptionalDateInput
                value={filters.startDate}
                onChange={(startDate) => setFilters({ ...filters, startDate })}
                placeholder="Any start date"
              />
              <OptionalDateInput
                value={filters.endDate}
                onChange={(endDate) => setFilters({ ...filters, endDate })}
                placeholder="Any end date"
              />
            </div>
          )}
        </FilterRow>
        <FilterRow label="Account">
          <FilterSelect
            value={filters.accountFilter}
            exclude={filters.accountExclude}
            items={accountItems}
            width="w-full"
            onValueChange={(value) => setFilters({ ...filters, accountFilter: value })}
            onExcludeChange={(exclude) => setFilters({ ...filters, accountExclude: exclude })}
          />
        </FilterRow>
        <FilterRow label="Category">
          <MultiSelectFilter
            label="Category"
            options={categoryOptions}
            selected={filters.categoryFilter}
            exclude={filters.categoryExclude}
            onSelectedChange={(next) => setFilters({ ...filters, categoryFilter: next })}
            onExcludeChange={(exclude) => setFilters({ ...filters, categoryExclude: exclude })}
          />
        </FilterRow>
        <FilterRow label="Subcategory">
          <MultiSelectFilter
            label="Subcategory"
            options={subcategoryOptions}
            selected={filters.subcategoryFilter}
            exclude={filters.subcategoryExclude}
            onSelectedChange={(next) => setFilters({ ...filters, subcategoryFilter: next })}
            onExcludeChange={(exclude) => setFilters({ ...filters, subcategoryExclude: exclude })}
          />
        </FilterRow>
        <FilterRow label="Tag">
          <MultiSelectFilter
            label="Tag"
            options={tagFilterOptions}
            selected={filters.tagFilter}
            exclude={filters.tagExclude}
            onSelectedChange={(next) => setFilters({ ...filters, tagFilter: next })}
            onExcludeChange={(exclude) => setFilters({ ...filters, tagExclude: exclude })}
          />
        </FilterRow>
        <FilterRow label="AI/pattern suggestion status">
          <MultiSelectFilter
            label="Status"
            options={PENDING_OPTIONS}
            selected={filters.pendingFilter}
            exclude={filters.pendingExclude ?? false}
            onSelectedChange={(next) => setFilters({ ...filters, pendingFilter: next })}
            onExcludeChange={(exclude) => setFilters({ ...filters, pendingExclude: exclude })}
          />
        </FilterRow>
        <FilterRow label="Filter transfers">
          <MultiSelectFilter
            label="Filter transfers"
            options={TRANSFER_FLAG_OPTIONS}
            selected={filters.transferFlagFilter}
            exclude={filters.transferFlagExclude}
            onSelectedChange={(next) => setFilters({ ...filters, transferFlagFilter: next })}
            onExcludeChange={(exclude) => setFilters({ ...filters, transferFlagExclude: exclude })}
          />
        </FilterRow>
        <FilterRow label="Income / expense">
          <Select
            value={filters.incomeExpenseFilter ?? ALL}
            onValueChange={(value) => value && setFilters({ ...filters, incomeExpenseFilter: value })}
          >
            <SelectTrigger size="sm" className="w-full">
              <SelectValue items={INCOME_EXPENSE_ITEMS} />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(INCOME_EXPENSE_ITEMS).map(([id, name]) => (
                <SelectItem key={id} value={id}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FilterRow>
        <FilterRow label="Categorized">
          <Select
            value={filters.categorizedFilter ?? ALL}
            onValueChange={(value) => value && setFilters({ ...filters, categorizedFilter: value })}
          >
            <SelectTrigger size="sm" className="w-full">
              <SelectValue items={CATEGORIZED_ITEMS} />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(CATEGORIZED_ITEMS).map(([id, name]) => (
                <SelectItem key={id} value={id}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FilterRow>
      </FilterPanel>
    </div>
  )
}
