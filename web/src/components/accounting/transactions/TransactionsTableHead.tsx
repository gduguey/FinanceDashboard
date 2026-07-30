import type { Ref } from 'react'
import { SortableTableHead } from '@/components/shared/SortableTableHead'
import { TableHead, TableHeader, TableRow } from '@/components/ui/table'
import type { Posting } from '@/types/accounting'

// The table's own header row: the select-all checkbox for pending
// suggestions, and one sortable heading per column. Split out because it is
// the one region of the table that only reads the sort state and the pending
// tally — nothing here touches a posting, a mutation, or the virtualizer.
export function TransactionsTableHead({
  sort,
  toggleSort,
  selectAllRef,
  pendingInViewCount,
  allPendingSelected,
  onToggleSelectAllPending,
}: {
  sort: { key: keyof Posting; desc: boolean }
  toggleSort: (key: keyof Posting) => void
  selectAllRef: Ref<HTMLInputElement>
  pendingInViewCount: number
  allPendingSelected: boolean
  onToggleSelectAllPending: () => void
}) {
  return (
    <TableHeader>
      <TableRow>
        <TableHead className="w-8">
          {pendingInViewCount > 0 && (
            <input
              ref={selectAllRef}
              type="checkbox"
              className="size-3.5 accent-current"
              checked={allPendingSelected}
              onChange={onToggleSelectAllPending}
              aria-label="Select all pending suggestions in view"
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
