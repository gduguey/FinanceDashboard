import {
  patchEntry,
  useAccountingMutation,
  useOptimisticStoreMutation,
  usePreviewMutation,
  withoutEntry,
} from '@/hooks/accounting/mutations'
import { accountingApi } from '@/lib/accountingApi'
import type {
  AccountCreate,
  AccountUpdate,
  Budget,
  BudgetUpsert,
  CategoryCreate,
  CategoryPatternCreate,
  CategoryPatternUpdate,
  ManualTransfer,
  OpeningBalance,
  OtherAssetCreate,
  SubcategoryCreate,
  TagCreate,
  TransferRuleCreate,
  TransferRuleUpdate,
} from '@/types/accounting'

// Unlike a whole-tree replace, this refuses a same-classification, same-name
// duplicate server-side (409) instead of silently overwriting whatever
// already had that computed id.
export const useCreateCategory = () =>
  useAccountingMutation({
    mutationFn: (category: CategoryCreate) => accountingApi.createCategory(category),
    changes: ['store'],
  })

export const useCreateSubcategory = () =>
  useAccountingMutation({
    mutationFn: ({ parentId, subcategory }: { parentId: string; subcategory: SubcategoryCreate }) =>
      accountingApi.createSubcategory(parentId, subcategory),
    changes: ['store'],
  })

export const useCategoryRenamePreview = () =>
  usePreviewMutation(({ categoryId, name }: { categoryId: string; name: string }) =>
    accountingApi.categoryRenamePreview(categoryId, name),
  )

// Renaming to an existing category's (or, for a subcategory, an existing
// sibling's) name merges into it — repointing postings, rules, budgets, and
// manual overrides — so this moves the ledger too, not just the category tree.
export const useRenameCategory = () =>
  useAccountingMutation({
    mutationFn: ({ categoryId, name }: { categoryId: string; name: string }) =>
      accountingApi.renameCategory(categoryId, name),
    changes: ['store', 'ledger'],
  })

export const useCategoryDeletePreview = () =>
  usePreviewMutation((categoryId: string) => accountingApi.categoryDeletePreview(categoryId))

// Uncategorizes every posting (and clears/drops every rule, budget, pattern,
// and split leg) referencing the deleted category or, for a top-level one, any
// of its subcategories — so this moves the ledger, same as a merge does.
export const useDeleteCategory = () =>
  useAccountingMutation({
    mutationFn: (categoryId: string) => accountingApi.deleteCategory(categoryId),
    changes: ['store', 'ledger'],
  })

export const useDeleteTag = () =>
  useOptimisticStoreMutation({
    mutationFn: (tagId: string) => accountingApi.deleteTag(tagId),
    changes: ['store', 'ledger'],
    edit: (store, tagId) => ({ ...store, tags: withoutEntry(store.tags, tagId) }),
  })

// Unlike a whole-list replace, this refuses a same-name (case-insensitive)
// duplicate server-side (409) instead of silently overwriting whatever already
// had that computed id.
export const useCreateTag = () =>
  useAccountingMutation({ mutationFn: (tag: TagCreate) => accountingApi.createTag(tag), changes: ['store'] })

export const useTagRenamePreview = () =>
  usePreviewMutation(({ tagId, name }: { tagId: string; name: string }) => accountingApi.tagRenamePreview(tagId, name))

// Renaming to an existing tag's name merges into it — repointing
// `posting_tags` rows and `tag_ids_override` arrays — so this moves the ledger
// too, not just the tag list.
export const useRenameTag = () =>
  useOptimisticStoreMutation({
    mutationFn: ({ tagId, name }: { tagId: string; name: string }) => accountingApi.renameTag(tagId, name),
    changes: ['store', 'ledger'],
    // Paints the plain rename. A rename that turns out to be a *merge* repoints
    // rows this cannot see, and the refetch corrects the chip's name and the
    // tag it collapsed into together.
    edit: (store, { tagId, name }) => ({ ...store, tags: patchEntry(store.tags, tagId, { name }) }),
  })

// Scoped to the one rule being edited (toggling active, editing fields,
// excluding/un-excluding a transaction) rather than the whole `transfer_rules`
// array — each call carries and checks its own `update.expected_version`, so
// firing several back-to-back can never silently clobber a sibling rule's edit.
export const usePatchTransferRule = () =>
  useOptimisticStoreMutation({
    mutationFn: ({ ruleId, update }: { ruleId: string; update: TransferRuleUpdate }) =>
      accountingApi.patchTransferRule(ruleId, update),
    changes: ['store', 'ledger'],
    edit: (store, { ruleId, update }) => ({
      ...store,
      transfer_rules: store.transfer_rules.map((rule) => (rule.rule_id === ruleId ? { ...rule, ...update } : rule)),
    }),
  })

export const useDeleteTransferRule = () =>
  useOptimisticStoreMutation({
    mutationFn: (ruleId: string) => accountingApi.deleteTransferRule(ruleId),
    changes: ['store', 'ledger'],
    edit: (store, ruleId) => ({
      ...store,
      transfer_rules: store.transfer_rules.filter((rule) => rule.rule_id !== ruleId),
    }),
  })

export const useCreateTransferRule = () =>
  useAccountingMutation({
    mutationFn: (rule: TransferRuleCreate) => accountingApi.createTransferRule(rule),
    changes: ['store', 'ledger'],
  })

export const useDeleteOtherAsset = () =>
  useOptimisticStoreMutation({
    mutationFn: (assetId: string) => accountingApi.deleteOtherAsset(assetId),
    changes: ['store', 'netWorth'],
    edit: (store, assetId) => ({
      ...store,
      other_assets: store.other_assets.filter((asset) => asset.asset_id !== assetId),
    }),
  })

export const useCreateOtherAsset = () =>
  useAccountingMutation({
    mutationFn: (asset: OtherAssetCreate) => accountingApi.createOtherAsset(asset),
    changes: ['store', 'netWorth'],
  })

// Single-item budget mutations — only send the one budget being changed over
// the wire, not the user's entire budget history for every edit (see
// accounting.api.routers.budgets.post_budget). A `month: null` upsert is the
// general, every-month-alike target; both go through the same endpoint.
//
// The paint covers the *replace* half of the upsert only. A budget's id is
// derived server-side from its `(month, category_id, subcategory_id)`
// (`repositories.planning.budget_row_key`), so painting a cell that does not
// exist yet would mean reimplementing that key format client-side and
// inventing a row the reconciling refetch then replaces — a create, which
// stays non-optimistic. Retyping an amount already set is the reversible edit
// this is for, and it matches on the natural key rather than the derived id.
export const useSetBudget = () =>
  useOptimisticStoreMutation({
    mutationFn: (budget: BudgetUpsert) => accountingApi.setBudget(budget),
    changes: ['store', 'budgets'],
    edit: (store, budget) => ({
      ...store,
      budgets: store.budgets.map((existing) =>
        sameBudgetCell(existing, budget) ? { ...existing, ...budget } : existing,
      ),
    }),
  })

/**
 * Whether two budget rows target the same cell of the budget grid.
 *
 * @param existing - A budget already in the store.
 * @param upsert - The budget being written.
 * @returns `true` when the write replaces `existing` rather than adding a row.
 */
function sameBudgetCell(existing: Budget, upsert: BudgetUpsert): boolean {
  return (
    (existing.month ?? null) === (upsert.month ?? null) &&
    existing.category_id === upsert.category_id &&
    (existing.subcategory_id ?? null) === (upsert.subcategory_id ?? null)
  )
}

export const useRemoveBudget = () =>
  useOptimisticStoreMutation({
    mutationFn: (budgetId: string) => accountingApi.removeBudget(budgetId),
    changes: ['store', 'budgets'],
    edit: (store, budgetId) => ({
      ...store,
      budgets: store.budgets.filter((budget) => budget.budget_id !== budgetId),
    }),
  })

export const useCreateAccount = () =>
  useAccountingMutation({
    mutationFn: (account: AccountCreate) => accountingApi.postAccount(account),
    changes: ['store'],
  })

// An account's kind and currency decide whether its legs are real
// income/expense and whether a rule may repoint onto it, so an edit moves the
// resolved ledger as well as the store.
export const useUpdateAccount = () =>
  useOptimisticStoreMutation({
    mutationFn: ({ accountId, update }: { accountId: string; update: AccountUpdate }) =>
      accountingApi.putAccount(accountId, update),
    changes: ['store', 'ledger'],
    edit: (store, { accountId, update }) => ({ ...store, accounts: patchEntry(store.accounts, accountId, update) }),
  })

export const useDeleteAccount = () =>
  useOptimisticStoreMutation({
    mutationFn: (accountId: string) => accountingApi.deleteAccount(accountId),
    changes: ['store', 'ledger'],
    edit: (store, accountId) => ({ ...store, accounts: withoutEntry(store.accounts, accountId) }),
  })

// An opening balance is the balance held the day before the first posting. It
// creates no posting and appears in no store collection, so nothing but the
// balances move — and, for the same reason, there is nothing cached to paint
// optimistically: its only visible effect is a server-computed net worth. The
// form field is write-only; no query reads an opening balance back.
export const useSetOpeningBalance = () =>
  useAccountingMutation({
    mutationFn: ({ accountId, openingBalance }: { accountId: string; openingBalance: OpeningBalance }) =>
      accountingApi.putOpeningBalance(accountId, openingBalance),
    changes: ['netWorth'],
  })

export const useCloseAccount = () =>
  useAccountingMutation({
    mutationFn: ({ accountId, transfers }: { accountId: string; transfers: ManualTransfer[] }) =>
      accountingApi.closeAccount(accountId, transfers),
    changes: ['store', 'ledger'],
  })

export const useReopenAccount = () =>
  useAccountingMutation({
    mutationFn: (accountId: string) => accountingApi.reopenAccount(accountId),
    changes: ['store', 'ledger'],
  })

// A pattern is only ever applied by an explicit suggest call, so storing one
// changes nothing about any posting.
export const usePatchCategoryPattern = () =>
  useOptimisticStoreMutation({
    mutationFn: ({ patternId, update }: { patternId: string; update: CategoryPatternUpdate }) =>
      accountingApi.patchCategoryPattern(patternId, update),
    changes: ['store'],
    edit: (store, { patternId, update }) => ({
      ...store,
      category_patterns: patchEntry(store.category_patterns, patternId, update),
    }),
  })

export const useDeleteCategoryPattern = () =>
  useOptimisticStoreMutation({
    mutationFn: (patternId: string) => accountingApi.deleteCategoryPattern(patternId),
    changes: ['store'],
    edit: (store, patternId) => ({
      ...store,
      category_patterns: withoutEntry(store.category_patterns, patternId),
    }),
  })

export const useCreateCategoryPattern = () =>
  useAccountingMutation({
    mutationFn: (pattern: CategoryPatternCreate) => accountingApi.createCategoryPattern(pattern),
    changes: ['store'],
  })
