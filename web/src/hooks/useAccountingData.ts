import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { accountingApi, type AccountUpdate, type ImportAccountInfo } from '@/lib/accountingApi'
import type { Account, Category, CurrencyCode, ManualOverride, OtherAsset, Rule, Tag } from '@/types/accounting'

const BASE_CURRENCY: CurrencyCode = 'USD'

const keys = {
  store: ['accounting', 'store'],
  currencies: ['accounting', 'currencies'],
  currentExchangeRate: (currency: string) => ['accounting', 'exchange-rate', 'current', currency],
  exchangeRateHistory: (currency: string) => ['accounting', 'exchange-rate', 'history', currency],
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
  categoryTotals: (start: string, end: string, accountIds?: string[], tagId?: string, displayCurrency?: string) => [
    'accounting',
    'category-totals',
    start,
    end,
    accountIds ?? [],
    tagId ?? {},
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

export const useCurrentExchangeRate = (currency: string) =>
  useQuery({
    queryKey: keys.currentExchangeRate(currency),
    queryFn: () => accountingApi.currentExchangeRate(currency),
    retry: false,
  })

export const useExchangeRateHistory = (currency: string) =>
  useQuery({
    queryKey: keys.exchangeRateHistory(currency),
    queryFn: () => accountingApi.exchangeRateHistory(currency),
    retry: false,
  })

// A client-side rates-to-base table, for the one place this app converts
// currencies outside a backend response — mixing accounts and other
// assets into one allocation pie. Every non-base `CurrencyCode` needs its
// own smoothed rate synced first; a currency with none just contributes
// no rate (see `convertCurrency`, which would then leave its amounts
// unconverted rather than throwing mid-render).
export function useRatesToBase(nonBaseCurrencies: CurrencyCode[]) {
  const results = useQueries({
    queries: nonBaseCurrencies.map((code) => ({
      queryKey: keys.currentExchangeRate(code),
      queryFn: () => accountingApi.currentExchangeRate(code),
      retry: false,
    })),
  })
  const ratesToBase: Record<string, number> = { [BASE_CURRENCY]: 1 }
  for (const result of results) {
    if (result.data) ratesToBase[result.data.currency] = result.data.rate_to_base
  }
  return ratesToBase
}

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

export const useCategoryTotals = (
  start: string,
  end: string,
  accountIds?: string[],
  tagId?: string,
  displayCurrency?: string,
) =>
  useQuery({
    queryKey: keys.categoryTotals(start, end, accountIds, tagId, displayCurrency),
    queryFn: () => accountingApi.categoryTotals(start, end, accountIds, tagId, displayCurrency),
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

export function useSyncExchangeRates() {
  const invalidate = useInvalidateAccounting()
  return useMutation({ mutationFn: accountingApi.syncExchangeRates, onSuccess: invalidate })
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
