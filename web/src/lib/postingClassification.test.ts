import { describe, expect, it } from 'vitest'
import type { Account, Posting } from '@/types/accounting'
import { hasAnyRealAccount, realIncomeExpensePostingIds } from '@/lib/postingClassification'

function makeAccount(overrides: Partial<Account> & Pick<Account, 'account_id' | 'kind'>): Account {
  return { name: overrides.account_id, institution: 'Test Bank', currency: 'USD', closed: false, ...overrides }
}

function makePosting(
  overrides: Partial<Posting> & Pick<Posting, 'posting_id' | 'transaction_id' | 'account_id'>,
): Posting {
  return {
    posted_at: '2026-01-01T00:00:00',
    amount: 100,
    currency: 'USD',
    description: '',
    pending_selected: true,
    ...overrides,
  }
}

describe('hasAnyRealAccount', () => {
  it('is false with no accounts at all', () => {
    expect(hasAnyRealAccount([])).toBe(false)
  })

  it('is false when every account is a virtual placeholder', () => {
    const accounts = [{ kind: 'income_source' }, { kind: 'expense_payee' }]
    expect(hasAnyRealAccount(accounts)).toBe(false)
  })

  it('is true once at least one real account exists alongside the virtual placeholders', () => {
    const accounts = [{ kind: 'income_source' }, { kind: 'checking' }]
    expect(hasAnyRealAccount(accounts)).toBe(true)
  })
})

describe('realIncomeExpensePostingIds', () => {
  it('excludes both legs of an internal transfer between two real accounts', () => {
    const accounts: Record<string, Account> = {
      checking: makeAccount({ account_id: 'checking', kind: 'checking' }),
      savings: makeAccount({ account_id: 'savings', kind: 'savings' }),
    }
    const postings = [
      makePosting({ posting_id: 'p1', transaction_id: 't1', account_id: 'checking' }),
      makePosting({ posting_id: 'p2', transaction_id: 't1', account_id: 'savings' }),
    ]
    expect(realIncomeExpensePostingIds(postings, accounts)).toEqual(new Set())
  })

  it('includes the real-account leg of a transaction that also touches a virtual placeholder account', () => {
    const accounts: Record<string, Account> = {
      checking: makeAccount({ account_id: 'checking', kind: 'checking' }),
      incomeSource: makeAccount({ account_id: 'uncategorized:income', kind: 'income_source' }),
    }
    const postings = [
      makePosting({ posting_id: 'p1', transaction_id: 't1', account_id: 'checking' }),
      makePosting({ posting_id: 'p2', transaction_id: 't1', account_id: 'uncategorized:income' }),
    ]
    expect(realIncomeExpensePostingIds(postings, accounts)).toEqual(new Set(['p1']))
  })

  it('never includes a posting on a virtual placeholder account, even when its sibling is also virtual', () => {
    const accounts: Record<string, Account> = {
      incomeSource: makeAccount({ account_id: 'uncategorized:income', kind: 'income_source' }),
      expensePayee: makeAccount({ account_id: 'uncategorized:expense', kind: 'expense_payee' }),
    }
    const postings = [
      makePosting({ posting_id: 'p1', transaction_id: 't1', account_id: 'uncategorized:income' }),
      makePosting({ posting_id: 'p2', transaction_id: 't1', account_id: 'uncategorized:expense' }),
    ]
    expect(realIncomeExpensePostingIds(postings, accounts)).toEqual(new Set())
  })

  it("resolves a leg's virtuality from the full unscoped posting list, not just the accounts touched here", () => {
    const accounts: Record<string, Account> = {
      checking: makeAccount({ account_id: 'checking', kind: 'checking' }),
      incomeSource: makeAccount({ account_id: 'uncategorized:income', kind: 'income_source' }),
    }
    // Same transaction id split across two calls, mimicking an account-filtered
    // subset that only ever sees one leg — the virtual sibling still counts.
    const allPostings = [
      makePosting({ posting_id: 'p1', transaction_id: 't1', account_id: 'checking' }),
      makePosting({ posting_id: 'p2', transaction_id: 't1', account_id: 'uncategorized:income' }),
    ]
    expect(realIncomeExpensePostingIds(allPostings, accounts)).toEqual(new Set(['p1']))
  })
})
