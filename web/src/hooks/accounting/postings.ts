import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { keys, POSTINGS_PAGE_PREFIX } from '@/hooks/accounting/keys'
import {
  useAccountingMutation,
  useOptimisticPagesMutation,
  useOptimisticStoreMutation,
} from '@/hooks/accounting/mutations'
import { accountingApi, type PostingPageQuery } from '@/lib/accountingApi'
import type {
  DismissSuggestionRequest,
  ManualOverride,
  Posting,
  PostingFilters,
  PostingMergeUpsert,
  PostingPage,
  PostingSplitLeg,
  TransferLinkCreate,
} from '@/types/accounting'

export const usePostings = () => useQuery({ queryKey: keys.postings, queryFn: accountingApi.postings })

/**
 * One page of the filtered, sorted transactions collection.
 *
 * `keepPreviousData` holds the page already on screen while the next one is
 * in flight, so paging or re-sorting dims the table rather than replacing it
 * with a skeleton. It is a display choice with one consequence worth naming:
 * a request in flight means the rows shown are the *previous* query's, which
 * is why the caller reads `isPlaceholderData` and says so rather than letting
 * a stale count sit under fresh-looking rows.
 */
export const usePostingsPage = (query: PostingPageQuery) =>
  useQuery({
    queryKey: keys.postingsPage(query),
    queryFn: () => accountingApi.postingsPage(query),
    placeholderData: keepPreviousData,
  })

/**
 * What the whole, unfiltered ledger amounts to, without fetching any of it.
 *
 * `total` is how many transactions exist — all the sidebar's onboarding check
 * ever needed, and reading it off `usePostings` cost the whole history.
 * `counts` comes free with it, and carries the "Needs categorizing" badge:
 * that number describes the ledger rather than either tab's filter, so it
 * cannot be read off a page the filter bar has narrowed.
 */
export const usePostingSummary = () => useQuery({ queryKey: keys.postingCount, queryFn: accountingApi.postingSummary })

/**
 * Every month the user has a posting in, newest first — the month picker's options.
 *
 * One `GROUP BY` on the server (`GET /postings/months`), not a `Set` built
 * over a resident ledger. The distinction only became affordable once
 * something other than the whole posting list could answer it, which is why
 * this endpoint sat unconsumed until now (C2).
 */
export const usePostingMonths = () => useQuery({ queryKey: keys.postingMonths, queryFn: accountingApi.postingMonths })

/**
 * The real leg of each named transaction, for a screen that holds only ids.
 *
 * The Rules page's three list tabs each render "one transaction as a small
 * card" for a set the user authored — a rule's links, the manual pairs, a
 * rule's exclusions — and knew nothing but the transaction id. They used to
 * index the whole resolved ledger to turn one into a row.
 *
 * The ids are sorted into the query key so two callers asking for the same
 * set in a different order share one cache entry, and skipped entirely when
 * the set is empty — a fresh install has no rules and should make no request.
 */
export const useTransactionLegs = (transactionIds: string[]) => {
  const sorted = [...new Set(transactionIds)].sort()
  return useQuery({
    queryKey: keys.transactionLegs(sorted),
    queryFn: () => accountingApi.transactionLegs(sorted),
    enabled: sorted.length > 0,
  })
}

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

/**
 * Set one posting's manual override, painting the change before the server confirms it.
 *
 * Categorizing used to wait on the round trip plus a full posting refetch
 * before the cell showed what was picked. Matched on the exact posting id an
 * override is stored under, which for a split leg is the leg's own id — the
 * id the caller sent — not the posting it was split from.
 *
 * Every cached page is painted, not just the one on screen: the same row can
 * be cached under several filters and windows, and repainting only the
 * visible one would let a page the user returns to contradict it.
 *
 * The page's `counts` are deliberately left alone. Categorizing a row moves
 * `needs_categorizing`, and predicting that here means re-implementing
 * `projection._needs_categorizing` in the browser — a second copy of a
 * predicate, which is the duplication this whole cutover removed. The
 * `onSuccess` invalidation corrects the counts a beat later; a badge that is
 * briefly one behind is a smaller lie than one computed two ways.
 */
export const useSetPostingOverride = () =>
  useOptimisticPagesMutation<PostingPage, ManualOverride, { postingId: string; override: Partial<ManualOverride> }>({
    prefix: POSTINGS_PAGE_PREFIX,
    mutationFn: ({ postingId, override }) => accountingApi.putPostingOverride(postingId, override),
    changes: ['ledger'],
    edit: (page, { postingId, override }) => ({
      ...page,
      items: page.items.map((posting) =>
        posting.posting_id === postingId ? paintOverride(posting, override) : posting,
      ),
    }),
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

// Both bulk actions send the filter, never a list of ids: the table holds a
// page now, so it cannot enumerate the set it is showing, and the server
// resolves that set inside the same transaction as the write. Each returns
// `matched` — how many rows it actually acted on — which the caller reports
// rather than assuming the number it last rendered.
export const usePatternSuggestCategoryBulk = () =>
  useAccountingMutation({
    mutationFn: (filters: PostingFilters) => accountingApi.patternSuggestCategoryBulk(filters),
    changes: ['ledger'],
  })

export const useValidatePending = () =>
  useAccountingMutation({
    mutationFn: (filters: PostingFilters) => accountingApi.validatePending(filters),
    changes: ['ledger'],
  })
