import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { accountingApi, type AccountUpdate, type ImportAccountInfo } from '@/lib/accountingApi'
import type { Account, Category, ManualOverride, OtherAsset, Rule, Tag } from '@/types/accounting'

const keys = {
  store: ['accounting', 'store'],
  currencies: ['accounting', 'currencies'],
  postings: ['accounting', 'postings'],
  transferSuggestions: ['accounting', 'transfer-suggestions'],
  netWorth: (asOf?: string, displayCurrency?: string) => ['accounting', 'net-worth', asOf ?? {}, displayCurrency ?? {}],
  netWorthHistory: (start: string, end: string, intervalDays?: number, displayCurrency?: string) => [
    'accounting',
    'net-worth-history',
    start,
    end,
    intervalDays ?? {},
    displayCurrency ?? {},
  ],
  categoryTotals: (start: string, end: string, accountIds?: string[], displayCurrency?: string) => [
    'accounting',
    'category-totals',
    start,
    end,
    accountIds ?? [],
    displayCurrency ?? {},
  ],
  monthlyIncomeExpense: (start: string, end: string, displayCurrency?: string) => [
    'accounting',
    'monthly-income-expense',
    start,
    end,
    displayCurrency ?? {},
  ],
  spendCurve: (month: string, lookbackMonths?: number, displayCurrency?: string) => [
    'accounting',
    'spend-curve',
    month,
    lookbackMonths ?? {},
    displayCurrency ?? {},
  ],
}

function useInvalidateAccounting() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: ['accounting'] })
}

export const useAccountingStore = () => useQuery({ queryKey: keys.store, queryFn: accountingApi.store })

export const useCurrencies = () => useQuery({ queryKey: keys.currencies, queryFn: accountingApi.currencies })

export const usePostings = () => useQuery({ queryKey: keys.postings, queryFn: accountingApi.postings })

export const useTransferSuggestions = () =>
  useQuery({ queryKey: keys.transferSuggestions, queryFn: accountingApi.transferSuggestions })

export const useNetWorth = (asOf?: string, displayCurrency?: string) =>
  useQuery({ queryKey: keys.netWorth(asOf, displayCurrency), queryFn: () => accountingApi.netWorth(asOf, displayCurrency) })

export const useNetWorthHistory = (start: string, end: string, intervalDays?: number, displayCurrency?: string) =>
  useQuery({
    queryKey: keys.netWorthHistory(start, end, intervalDays, displayCurrency),
    queryFn: () => accountingApi.netWorthHistory(start, end, intervalDays, displayCurrency),
  })

export const useCategoryTotals = (start: string, end: string, accountIds?: string[], displayCurrency?: string) =>
  useQuery({
    queryKey: keys.categoryTotals(start, end, accountIds, displayCurrency),
    queryFn: () => accountingApi.categoryTotals(start, end, accountIds, displayCurrency),
  })

export const useMonthlyIncomeExpense = (start: string, end: string, displayCurrency?: string) =>
  useQuery({
    queryKey: keys.monthlyIncomeExpense(start, end, displayCurrency),
    queryFn: () => accountingApi.monthlyIncomeExpense(start, end, displayCurrency),
  })

export const useSpendCurve = (month: string, lookbackMonths?: number, displayCurrency?: string) =>
  useQuery({
    queryKey: keys.spendCurve(month, lookbackMonths, displayCurrency),
    queryFn: () => accountingApi.spendCurve(month, lookbackMonths, displayCurrency),
  })

export function useSetExchangeRate() {
  const invalidate = useInvalidateAccounting()
  return useMutation({ mutationFn: (rate: number) => accountingApi.setExchangeRate(rate), onSuccess: invalidate })
}

export function useSetCategories() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (categories: Record<string, Category>) => accountingApi.putCategories(categories),
    onSuccess: invalidate,
  })
}

export function useSetTags() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (tags: Record<string, Tag>) => accountingApi.putTags(tags),
    onSuccess: invalidate,
  })
}

export function useSetRules() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (rules: Rule[]) => accountingApi.putRules(rules),
    onSuccess: invalidate,
  })
}

export function useSetOtherAssets() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (otherAssets: OtherAsset[]) => accountingApi.putOtherAssets(otherAssets),
    onSuccess: invalidate,
  })
}

export function useCreateAccount() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (account: Account) => accountingApi.postAccount(account),
    onSuccess: invalidate,
  })
}

export function useUpdateAccount() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ accountId, update }: { accountId: string; update: AccountUpdate }) =>
      accountingApi.putAccount(accountId, update),
    onSuccess: invalidate,
  })
}

export function useDeleteAccount() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (accountId: string) => accountingApi.deleteAccount(accountId),
    onSuccess: invalidate,
  })
}

export function useImportCsv() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ file, info }: { file: File; info: ImportAccountInfo }) => accountingApi.importCsv(file, info),
    onSuccess: invalidate,
  })
}

export function useImportSofiStatementPdf() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: (file: File) => accountingApi.importSofiStatementPdf(file),
    onSuccess: invalidate,
  })
}

export function useRebuildLedger() {
  const invalidate = useInvalidateAccounting()
  return useMutation({ mutationFn: accountingApi.rebuild, onSuccess: invalidate })
}

export function useSetPostingOverride() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ postingId, override }: { postingId: string; override: ManualOverride }) =>
      accountingApi.putPostingOverride(postingId, override),
    onSuccess: invalidate,
  })
}
