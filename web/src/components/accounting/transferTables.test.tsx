import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ExcludedTransactionsTable } from '@/components/accounting/ExcludedTransactionsTable'
import { LinkedTransactionsTable } from '@/components/accounting/LinkedTransactionsTable'
import type { LinkedPairRow, TransferRowInfo } from '@/lib/transferRowInfo'

function excludedRow(transactionId: string, description: string, amount: number): TransferRowInfo {
  return {
    transactionId,
    accountName: 'Everyday',
    description,
    postedAt: '2026-01-15T00:00:00',
    amount,
    currency: 'USD',
  }
}

function linkedRow(linkId: string, fromDescription: string, toDescription: string): LinkedPairRow {
  return {
    linkId,
    fromTransactionId: `${linkId}:from`,
    fromAccountName: 'Everyday',
    fromDescription,
    fromPostedAt: '2026-01-15T00:00:00',
    fromAmount: -85,
    fromCurrency: 'USD',
    toTransactionId: `${linkId}:to`,
    toAccountName: 'Rainy Day',
    toDescription,
    toPostedAt: '2026-01-16T00:00:00',
    toAmount: 85,
    toCurrency: 'USD',
  }
}

/**
 * Body rows only. Both tables render each virtualized row as its own
 * `<tbody>`, plus spacer `<tbody>`s holding a single `<tr>` for the
 * unrendered space above and below — so the header row is dropped by role
 * and the spacers are dropped by having no cells.
 */
function bodyRowDescriptions(columnIndex: number): string[] {
  return screen
    .getAllByRole('row')
    .map((row) => within(row).queryAllByRole('cell'))
    .filter((cells) => cells.length > 1)
    .map((cells) => cells[columnIndex]?.textContent ?? '')
}

// These two tables are virtualized, so "does a row render at all" is a real
// question and not a given — the row-height and container-height shims in
// `src/test/setup.ts` are what let the virtualizer conclude that any row
// fits. They also declare their column widths through a `<colgroup>`, whose
// `<col>` count has to keep matching the number of `<th>`s and the `colSpan`
// the expanded detail row uses; a test that counts both catches a column
// being added to one and not the other.
describe('ExcludedTransactionsTable', () => {
  it('renders one row per excluded transaction', () => {
    render(
      <ExcludedTransactionsTable
        rows={[excludedRow('t:a', 'Corner Store', -25), excludedRow('t:b', 'Rent', -1200)]}
        onRemoveExclusion={vi.fn()}
      />,
    )

    expect(bodyRowDescriptions(2)).toEqual(['Corner Store', 'Rent'])
  })

  it('declares exactly one column width per header cell', () => {
    const { container } = render(
      <ExcludedTransactionsTable rows={[excludedRow('t:a', 'Corner Store', -25)]} onRemoveExclusion={vi.fn()} />,
    )

    expect(container.querySelectorAll('colgroup col')).toHaveLength(screen.getAllByRole('columnheader').length)
  })

  it('says so when the rule excludes nothing', () => {
    render(<ExcludedTransactionsTable rows={[]} onRemoveExclusion={vi.fn()} />)

    expect(screen.getByText(/No transactions excluded from this rule/)).toBeInTheDocument()
  })

  it('offers to remove the exclusion once a row is expanded', async () => {
    const user = userEvent.setup()
    const onRemoveExclusion = vi.fn()
    render(
      <ExcludedTransactionsTable
        rows={[excludedRow('t:a', 'Corner Store', -25)]}
        onRemoveExclusion={onRemoveExclusion}
      />,
    )

    await user.click(screen.getByText('Corner Store'))
    await user.click(screen.getByRole('button', { name: 'Remove exclusion' }))

    expect(onRemoveExclusion).toHaveBeenCalledWith('t:a')
  })
})

describe('LinkedTransactionsTable', () => {
  it('renders one row per linked pair', () => {
    render(
      <LinkedTransactionsTable rows={[linkedRow('l1', 'Moved out', 'Moved in'), linkedRow('l2', 'Paid', 'Got')]} />,
    )

    expect(bodyRowDescriptions(2)).toEqual(['Moved out', 'Paid'])
  })

  it('declares exactly one column width per header cell', () => {
    const { container } = render(<LinkedTransactionsTable rows={[linkedRow('l1', 'Moved out', 'Moved in')]} />)

    expect(container.querySelectorAll('colgroup col')).toHaveLength(screen.getAllByRole('columnheader').length)
  })

  it('uses the caller-supplied empty message', () => {
    render(<LinkedTransactionsTable rows={[]} emptyMessage="Nothing linked by hand yet." />)

    expect(screen.getByText('Nothing linked by hand yet.')).toBeInTheDocument()
  })
})
