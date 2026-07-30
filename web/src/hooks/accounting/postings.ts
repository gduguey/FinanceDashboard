import { useQuery } from '@tanstack/react-query'
import { keys } from '@/hooks/accounting/keys'
import { useAccountingMutation, useOptimisticMutation, useOptimisticStoreMutation } from '@/hooks/accounting/mutations'
import { accountingApi } from '@/lib/accountingApi'
import type {
  DismissSuggestionRequest,
  ManualOverride,
  Posting,
  PostingMergeUpsert,
  PostingSplitLeg,
  TransferLinkCreate,
} from '@/types/accounting'

export const usePostings = () => useQuery({ queryKey: keys.postings, queryFn: accountingApi.postings })

/**
 * How many transactions the user has, without fetching any of them.
 *
 * For callers that only need to know whether the ledger is empty. Reading that
 * off `usePostings` costs the whole history; this costs one page of one.
 */
export const usePostingCount = () => useQuery({ queryKey: keys.postingCount, queryFn: accountingApi.postingCount })

export const useTransferSuggestions = (windowDays?: number) =>
  useQuery({
    queryKey: [...keys.transferSuggestions, windowDays ?? {}],
    queryFn: () => accountingApi.transferSuggestions(windowDays),
  })

export const useDuplicateSuggestions = (windowDays?: number) =>
  useQuery({
    queryKey: [...keys.duplicateSuggestions, windowDays ?? {}],
    queryFn: () => accountingApi.duplicateSuggestions(windowDays),
  })

export const useDismissedSuggestions = () =>
  useQuery({ queryKey: keys.dismissedSuggestions, queryFn: accountingApi.dismissedSuggestions })

export const useDismissSuggestion = () =>
  useAccountingMutation({
    mutationFn: ({ suggestionId, ...body }: DismissSuggestionRequest & { suggestionId: string }) =>
      accountingApi.dismissSuggestion(suggestionId, body),
    changes: ['suggestions'],
  })

export const useRestoreSuggestion = () =>
  useAccountingMutation({
    mutationFn: (suggestionId: string) => accountingApi.restoreSuggestion(suggestionId),
    changes: ['suggestions'],
  })

export const useCreatePostingMerge = () =>
  useAccountingMutation({
    mutationFn: (merge: PostingMergeUpsert) => accountingApi.createPostingMerge(merge),
    changes: ['ledger'],
  })

export const useRemovePostingMerge = () =>
  useAccountingMutation({
    mutationFn: (mergeId: string) => accountingApi.removePostingMerge(mergeId),
    changes: ['ledger'],
  })

export const useCreateTransferLink = () =>
  useAccountingMutation({
    mutationFn: (link: TransferLinkCreate) => accountingApi.createTransferLink(link),
    changes: ['store', 'ledger'],
  })

// Drops the link from the cached store the instant "unmark as transfer" /
// "exclude this transfer" fires, so the badge disappears without waiting on
// the round trip.
export const useRemoveTransferLink = () =>
  useOptimisticStoreMutation({
    mutationFn: (linkId: string) => accountingApi.removeTransferLink(linkId),
    changes: ['store', 'ledger'],
    edit: (store, linkId) => ({
      ...store,
      transfer_links: store.transfer_links.filter((link) => link.link_id !== linkId),
    }),
  })

/**
 * The fields of an override that map straight onto the posting row on screen.
 *
 * `account_id` is deliberately absent. Repointing a placeholder leg is what
 * turns a transaction into a transfer, and the badge, the category cell and
 * the sibling-leg lookups that follow from it are the output of the whole
 * server-side resolution pipeline — not something four lines here can predict.
 * That one field waits for the refetch; the four below are the ones a
 * categorizing click actually changes, and they are a straight copy.
 */
const PAINTABLE_OVERRIDE_FIELDS = ['category_id', 'subcategory_id', 'tag_ids', 'pending_selected'] as const

/**
 * Apply an override's directly-displayable fields to one posting row.
 *
 * @param posting - The row as cached.
 * @param override - The partial override being sent.
 * @returns The row as it should read immediately, or the row itself when the
 *   override changes nothing this function can predict.
 */
function paintOverride(posting: Posting, override: Partial<ManualOverride>): Posting {
  const painted: Partial<Posting> = {}
  for (const field of PAINTABLE_OVERRIDE_FIELDS) {
    if (field in override) Object.assign(painted, { [field]: override[field] })
  }
  return { ...posting, ...painted }
}

// Categorizing used to wait on the round trip plus a full posting refetch
// before the cell showed what was picked. Matched on the exact posting id an
// override is stored under, which for a split leg is the leg's own id — the
// id the caller sent — not the posting it was split from.
export const useSetPostingOverride = () =>
  useOptimisticMutation<Posting[], ManualOverride, { postingId: string; override: Partial<ManualOverride> }>({
    queryKey: keys.postings,
    mutationFn: ({ postingId, override }) => accountingApi.putPostingOverride(postingId, override),
    changes: ['ledger'],
    edit: (postings, { postingId, override }) =>
      postings.map((posting) => (posting.posting_id === postingId ? paintOverride(posting, override) : posting)),
  })

export const useSetPostingSplit = () =>
  useAccountingMutation({
    mutationFn: ({ postingId, legs }: { postingId: string; legs: PostingSplitLeg[] }) =>
      accountingApi.putPostingSplit(postingId, legs),
    changes: ['ledger'],
  })

export const useDeletePostingSplit = () =>
  useAccountingMutation({
    mutationFn: (postingId: string) => accountingApi.deletePostingSplit(postingId),
    changes: ['ledger'],
  })

// The suggestion lands as a *pending* override, which every dashboard
// aggregation deliberately ignores until it is validated (see
// `api.dependencies._resolved_postings_for_aggregation`) — so nothing but the
// posting list and, for the AI path, the provider's own call counter moves.
export const useAiSuggestCategory = () =>
  useAccountingMutation({
    mutationFn: ({ postingId, lockCategoryId }: { postingId: string; lockCategoryId?: string | null }) =>
      accountingApi.aiSuggestCategory(postingId, lockCategoryId),
    changes: ['ledger', 'llm'],
  })

export const usePatternSuggestCategory = () =>
  useAccountingMutation({
    mutationFn: ({ postingId, lockCategoryId }: { postingId: string; lockCategoryId?: string | null }) =>
      accountingApi.patternSuggestCategory(postingId, lockCategoryId),
    changes: ['ledger'],
  })

export const usePatternSuggestCategoryBulk = () =>
  useAccountingMutation({
    mutationFn: (postingIds: string[]) => accountingApi.patternSuggestCategoryBulk(postingIds),
    changes: ['ledger'],
  })

export const useValidatePending = () =>
  useAccountingMutation({
    mutationFn: (postingIds: string[]) => accountingApi.validatePending(postingIds),
    changes: ['ledger'],
  })
