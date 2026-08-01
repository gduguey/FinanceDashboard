import type { Ref } from 'react'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { TableHead, TableHeader, TableRow } from '@/components/ui/table'
import type { PostingSortField } from '@/types/accounting'

// The table's own header row: the select-all checkbox for pending
// suggestions, and one sortable heading per column. Split out because it is
// the one region of the table that only reads the sort state and the pending
// tally — nothing here touches a posting, a mutation, or the virtualizer.
//
// The checkbox is page-scoped and labelled as such, while "Validate matching"
// beside it acts on the whole filter. Two different scopes, so they are named
// differently rather than sharing a vague "in view": a checkbox can only
// toggle rows it can see, since each toggle is a write to that row's own
// override.
export function TransactionsTableHead({
  sort,
  toggleSort,
  selectAllRef,
  pendingOnPageCount,
  allPendingOnPageSelected,
  onToggleSelectAllOnPage,
}: {
  sort: { key: PostingSortField; desc: boolean }
  toggleSort: (key: PostingSortField) => void
  selectAllRef: Ref<HTMLInputElement>
  pendingOnPageCount: number
  allPendingOnPageSelected: boolean
  onToggleSelectAllOnPage: () => void
}) {
  return (
    <TableHeader>
      <TableRow>
        <TableHead className="w-8">
          {pendingOnPageCount > 0 && (
            <input
              ref={selectAllRef}
              type="checkbox"
              className="size-3.5 accent-current"
              checked={allPendingOnPageSelected}
              onChange={onToggleSelectAllOnPage}
              aria-label="Select all pending suggestions on this page"
              title="Checks every suggestion on this page. Other pages keep whatever they were set to."
            />
          )}
        </TableHead>
        <SortableTableHead active={sort.key === 'posted_at'} desc={sort.desc} onClick={() => toggleSort('posted_at')}>
          Date
        </SortableTableHead>
        <SortableTableHead active={sort.key === 'account_id'} desc={sort.desc} onClick={() => toggleSort('account_id')}>
          Account
        </SortableTableHead>
        <SortableTableHead
          active={sort.key === 'description'}
          desc={sort.desc}
          onClick={() => toggleSort('description')}
        >
          Description
        </SortableTableHead>
        <SortableTableHead
          align="right"
          active={sort.key === 'amount'}
          desc={sort.desc}
          onClick={() => toggleSort('amount')}
        >
          Amount
        </SortableTableHead>
        <SortableTableHead
          active={sort.key === 'category_id'}
          desc={sort.desc}
          onClick={() => toggleSort('category_id')}
        >
          Category
        </SortableTableHead>
        <SortableTableHead
          active={sort.key === 'subcategory_id'}
          desc={sort.desc}
          onClick={() => toggleSort('subcategory_id')}
        >
          Subcategory
        </SortableTableHead>
        <SortableTableHead active={sort.key === 'tag_ids'} desc={sort.desc} onClick={() => toggleSort('tag_ids')}>
          Tags
        </SortableTableHead>
        <TableHead className="w-10" />
      </TableRow>
    </TableHeader>
  )
}
