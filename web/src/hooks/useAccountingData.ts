import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { accountingApi, type ImportAccountInfo } from '@/lib/accountingApi'
import type { Category, ManualOverride, OtherAsset, Rule, Tag } from '@/types/accounting'

const keys = {
  store: ['accounting', 'store'],
  postings: ['accounting', 'postings'],
  transferSuggestions: ['accounting', 'transfer-suggestions'],
  netWorth: (asOf?: string) => ['accounting', 'net-worth', asOf ?? {}],
}

function useInvalidateAccounting() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: ['accounting'] })
}

export const useAccountingStore = () => useQuery({ queryKey: keys.store, queryFn: accountingApi.store })

export const usePostings = () => useQuery({ queryKey: keys.postings, queryFn: accountingApi.postings })

export const useTransferSuggestions = () =>
  useQuery({ queryKey: keys.transferSuggestions, queryFn: accountingApi.transferSuggestions })

export const useNetWorth = (asOf?: string) =>
  useQuery({ queryKey: keys.netWorth(asOf), queryFn: () => accountingApi.netWorth(asOf) })

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

export function useImportCsv() {
  const invalidate = useInvalidateAccounting()
  return useMutation({
    mutationFn: ({ file, info }: { file: File; info: ImportAccountInfo }) => accountingApi.importCsv(file, info),
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
