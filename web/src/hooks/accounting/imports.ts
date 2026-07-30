import { useAccountingMutation, usePreviewMutation } from '@/hooks/accounting/mutations'
import { accountingApi } from '@/lib/accountingApi'
import type { CanonicalCategoryOverrides, CurrencyCode } from '@/types/accounting'

export const useImportCsv = () =>
  useAccountingMutation({
    mutationFn: ({ file, accountId }: { file: File; accountId: string }) => accountingApi.importCsv(file, accountId),
    changes: ['store', 'ledger', 'sync'],
  })

export const useImportCanonicalCsv = () =>
  useAccountingMutation({
    mutationFn: ({
      file,
      accountId,
      separator,
      dateOrder,
      categoryOverrides,
    }: {
      file: File
      accountId: string
      separator?: string
      dateOrder?: string
      categoryOverrides?: CanonicalCategoryOverrides
    }) => accountingApi.importCanonicalCsv(file, accountId, separator, dateOrder, categoryOverrides),
    changes: ['store', 'ledger', 'sync'],
  })

export const useCanonicalImportPreview = () =>
  usePreviewMutation(
    ({
      file,
      accountId,
      currency,
      separator,
      dateOrder,
    }: {
      file: File
      accountId: string
      currency: CurrencyCode
      separator?: string
      dateOrder?: string
    }) => accountingApi.previewCanonicalImport(file, accountId, currency, separator, dateOrder),
  )

export const useCategorizeFromFilePreview = () =>
  usePreviewMutation(({ file, separator, dateOrder }: { file: File; separator?: string; dateOrder?: string }) =>
    accountingApi.previewCategorizeFromFile(file, separator, dateOrder),
  )

// Writes overrides, and additively registers any category the uploaded file's
// own Category/Subcategory columns introduced — never a posting.
export const useApplyCategorizeFromFile = () =>
  useAccountingMutation({
    mutationFn: ({
      file,
      confirmedRowNumbers,
      separator,
      dateOrder,
    }: {
      file: File
      confirmedRowNumbers: number[]
      separator?: string
      dateOrder?: string
    }) => accountingApi.applyCategorizeFromFile(file, confirmedRowNumbers, separator, dateOrder),
    changes: ['store', 'ledger'],
  })

export const useImportPaystub = () => usePreviewMutation((file: File) => accountingApi.importPaystub(file))

export const useRebuildLedger = () =>
  useAccountingMutation({ mutationFn: accountingApi.rebuild, changes: ['store', 'ledger', 'sync'] })
