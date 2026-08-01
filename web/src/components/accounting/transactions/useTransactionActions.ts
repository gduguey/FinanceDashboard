import { useCallback, useMemo, useState } from 'react'
import { toast } from 'sonner'
import {
  useAiSuggestCategory,
  useCreateTransferLink,
  useDeletePostingSplit,
  useLlmUsage,
  usePatchTransferRule,
  usePatternSuggestCategory,
  usePatternSuggestCategoryBulk,
  useRemoveTransferLink,
  useSetPostingOverride,
  useValidatePending,
} from '@/hooks/useAccountingData'
import { accountingApi } from '@/lib/accountingApi'
import { safeDirectRepointOptions } from '@/lib/counterpartyAccounts'
import { anyLlmProviderAvailable } from '@/lib/llm'
import { PLACEHOLDER_ACCOUNT_IDS } from '@/lib/transactionFilters'
import { addedExcludedTransactionIds, ruleUpdateFromRule } from '@/lib/transferRules'
import type { Account, ManualOverride, Posting, PostingFilters, TransferRule } from '@/types/accounting'

/**
 * Every write the transactions table can make, plus the transient state each one needs.
 *
 * Split out of the table itself so the component below is layout and the
 * mutations are here: eleven hooks, three pieces of in-flight progress state,
 * and the table-wide "picking a transfer partner" mode, none of which is about
 * how a row looks.
 *
 * The three bulk actions take a `PostingFilters` rather than a list of ids.
 * The table holds one page, so it cannot enumerate the set it is showing —
 * and rebuilding one by walking the collection would mean the client deciding
 * what the filter selects, a second implementation of the thirteen predicates
 * the server already evaluates. Two of the three resolve the set entirely
 * server-side; the AI loop asks for the ids because it makes one request per
 * posting on purpose.
 *
 * @param postings - This page's postings, every leg included. Placeholder legs
 *   ride along on the page and are what a "mark as transfer" actually targets.
 * @param accounts - The store's accounts, for the direct-repoint fallback list.
 * @param rules - The store's transfer rules, for the "exclude this one" action.
 * @returns The handlers the rows and toolbar call, and the state they read.
 */
export function useTransactionActions(postings: Posting[], accounts: Record<string, Account>, rules: TransferRule[]) {
  const [suggestMessages, setSuggestMessages] = useState<Record<string, string>>({})
  const [bulkSuggesting, setBulkSuggesting] = useState(false)
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null)
  const [bulkPatternSuggesting, setBulkPatternSuggesting] = useState(false)
  const setOverride = useSetPostingOverride()
  const deleteSplit = useDeletePostingSplit()
  const aiSuggest = useAiSuggestCategory()
  const patternSuggest = usePatternSuggestCategory()
  const patternSuggestBulk = usePatternSuggestCategoryBulk()
  const validatePending = useValidatePending()
  const patchTransferRule = usePatchTransferRule()
  const createTransferLink = useCreateTransferLink()
  const removeTransferLink = useRemoveTransferLink()
  const { data: llmUsage } = useLlmUsage()
  const aiAvailable = anyLlmProviderAvailable(llmUsage)
  // Only ever a fallback now — the primary "flag as transfer" path links to
  // a specific other transaction instead (see `pickHintByPostingId` below).
  // Repointing straight onto an `IMPORTABLE_ACCOUNT_KINDS` account (the
  // ones this list used to include) risks double-counting against that
  // account's own independently-imported statement.
  const safeCounterpartyAccounts = useMemo(() => safeDirectRepointOptions(accounts), [accounts])
  // The transaction currently being matched against, or `null` when no pick
  // is in progress — set by clicking "Link to another transaction…" on a
  // row, cleared by picking a target (or Cancel). Table-wide, not per-row
  // local state, since every OTHER row's own styling/clickability depends
  // on it (see `pickHintByPostingId`).
  const [pickingSource, setPickingSource] = useState<Posting | null>(null)
  // The placeholder leg of each transaction — never rendered as its own
  // row (see `filtered` below) but needed here to know *which* posting a
  // "mark as transfer" override actually has to target: the visible row is
  // always the real leg, but repointing a transfer's counterparty means
  // overriding the still-placeholder sibling, not the real leg itself.
  const placeholderPostingIdByTransactionId = useMemo(() => {
    const lookup = new Map<string, string>()
    for (const posting of postings) {
      if (PLACEHOLDER_ACCOUNT_IDS.has(posting.account_id)) lookup.set(posting.transaction_id, posting.posting_id)
    }
    return lookup
  }, [postings])
  // The page's rows by id, for the AI loop: it holds a list of ids the server
  // resolved and needs each row's own current category as that call's lock.
  const postingById = useMemo(() => new Map(postings.map((posting) => [posting.posting_id, posting])), [postings])
  const runAiSuggest = useCallback(
    async (posting: Posting) => {
      const postingId = posting.posting_id
      setSuggestMessages((prev) => ({ ...prev, [postingId]: 'Asking the AI…' }))
      try {
        const result = await aiSuggest.mutateAsync({ postingId, lockCategoryId: posting.category_id })
        setSuggestMessages((prev) => {
          if (!result.applied) return { ...prev, [postingId]: 'No confident suggestion' }
          const { [postingId]: _removed, ...rest } = prev
          return rest
        })
      } catch (error) {
        setSuggestMessages((prev) => ({
          ...prev,
          [postingId]: error instanceof Error ? error.message : 'AI suggestion failed',
        }))
      }
    },
    [aiSuggest],
  )

  // Only ever run one at a time — the LLM call has real latency, and this
  // avoids hammering the provider with the whole filtered set at once. The
  // ids are fetched rather than read off the page, because the set this walks
  // is the filter's and not the page's; `POST /postings/matching-ids` exists
  // for exactly this one caller.
  async function runBulkAiSuggest(filters: PostingFilters) {
    setBulkSuggesting(true)
    setBulkProgress({ done: 0, total: 0 })
    try {
      const postingIds = await accountingApi.matchingPostingIds(filters)
      const targets = postingIds
        .map((postingId) => postingById.get(postingId))
        .filter((posting): posting is Posting => posting !== undefined)
      // Only rows on this page carry the category a suggestion is locked
      // against, so ids the page does not hold are skipped rather than sent
      // with a guessed lock. Paging on picks them up.
      setBulkProgress({ done: 0, total: targets.length })
      for (const [index, posting] of targets.entries()) {
        await runAiSuggest(posting)
        setBulkProgress({ done: index + 1, total: targets.length })
      }
    } catch (error) {
      console.error('Bulk AI suggestion failed', error)
    } finally {
      setBulkSuggesting(false)
      setBulkProgress(null)
    }
  }

  // The backend matches every posting the filter resolves to in a single
  // vectorized pass (see `ledger.patterns.match_patterns_bulk`) — one request
  // regardless of how many, instead of one per posting.
  async function runBulkPatternSuggest(
    filters: PostingFilters,
    onDone?: (result: { matched: number; applied: number }) => void,
  ) {
    setBulkPatternSuggesting(true)
    try {
      onDone?.(await patternSuggestBulk.mutateAsync(filters))
    } catch (error) {
      // Already surfaced via the global mutation-error toast (see App.tsx) —
      // logged here too so a failure is distinguishable from "nothing needed
      // suggesting" when debugging.
      console.error('Bulk pattern suggestion failed', error)
    } finally {
      setBulkPatternSuggesting(false)
    }
  }

  // Reports `matched` back to the caller rather than letting it assume the
  // action covered what the page had rendered. It does not: the server
  // resolves the filter, which spans every page.
  async function validateFiltered(
    filters: PostingFilters,
    onDone?: (result: { matched: number; accepted: number; reverted: number }) => void,
  ) {
    try {
      onDone?.(await validatePending.mutateAsync(filters))
    } catch (error) {
      console.error('Validating pending suggestions failed', error)
    }
  }

  const handleOverride = useCallback(
    (postingId: string, override: Partial<ManualOverride>) => setOverride.mutate({ postingId, override }),
    [setOverride],
  )
  const handleMarkAsTransfer = useCallback(
    (transactionId: string, counterpartyAccountId: string) => {
      const placeholderPostingId = placeholderPostingIdByTransactionId.get(transactionId)
      if (!placeholderPostingId) return
      setOverride.mutate({ postingId: placeholderPostingId, override: { account_id: counterpartyAccountId } })
    },
    [setOverride, placeholderPostingIdByTransactionId],
  )
  // Clears just the manual account override, restoring this posting to
  // whatever it would resolve to without it (a rule, a link, or plain
  // uncategorized) — the same "explicit null clears just this one field"
  // merge semantics `put_posting_override` already has for every other field.
  const handleUndoManualOverride = useCallback(
    (postingId: string) => setOverride.mutate({ postingId, override: { account_id: null } }),
    [setOverride],
  )
  // Takes every transaction id to exclude in one call (both sides of a
  // rule-found transfer link, or just the one transaction for a direct
  // single-account repoint) so this rule only gets patched once — a
  // second, separate `PATCH` for the same rule fired before this one's
  // response lands would be a genuine conflict on that row, caught (409)
  // by the rule's own `expected_version`; there's no reason to invite it.
  const handleExcludeFromRule = useCallback(
    (transactionIds: string[], ruleId: string) => {
      const rule = rules.find((r) => r.rule_id === ruleId)
      if (!rule) return
      const ruleLabel = rule.description || rule.description_contains || ruleId
      patchTransferRule.mutate(
        {
          ruleId,
          update: ruleUpdateFromRule(rule, {
            excluded_transaction_ids: addedExcludedTransactionIds(rule, transactionIds),
          }),
        },
        {
          onSuccess: () =>
            toast.success(
              transactionIds.length > 1
                ? `Excluded from "${ruleLabel}" — both transactions fall back to the next-matching rule, or stay uncategorized. Manage exclusions from the Rules page.`
                : `Excluded from "${ruleLabel}" — this transaction falls back to the next-matching rule, or stays uncategorized. Manage exclusions from the Rules page.`,
            ),
        },
      )
    },
    [rules, patchTransferRule],
  )
  // Excluding a rule-found link must also delete the `TransferLink` itself
  // so both transactions actually revert to normal (see
  // `TransferDetailDialog`'s "Exclude this specific transfer…" button).
  // The two calls touch disjoint rows — the rule patch governed by its own
  // `expected_version` (see `usePatchTransferRule`), the unlink naming one
  // link by id — so they can't conflict with each other. Awaited in
  // sequence purely so the success toast only fires once both have landed.
  const handleExcludeAndUnlinkFromRule = useCallback(
    async (transactionIds: string[], ruleId: string, linkId: string) => {
      const rule = rules.find((r) => r.rule_id === ruleId)
      if (!rule) return
      const ruleLabel = rule.description || rule.description_contains || ruleId
      await patchTransferRule.mutateAsync({
        ruleId,
        update: ruleUpdateFromRule(rule, {
          excluded_transaction_ids: addedExcludedTransactionIds(rule, transactionIds),
        }),
      })
      await removeTransferLink.mutateAsync(linkId)
      toast.success(`Excluded from "${ruleLabel}" — both transactions are back to being normal transactions.`)
    },
    [rules, patchTransferRule, removeTransferLink],
  )
  const handleLinkTransfer = useCallback(
    (transactionIdA: string, transactionIdB: string) => {
      createTransferLink.mutate({ transaction_id_a: transactionIdA, transaction_id_b: transactionIdB })
    },
    [createTransferLink],
  )
  const handleUnlinkTransfer = useCallback((linkId: string) => removeTransferLink.mutate(linkId), [removeTransferLink])
  const handleStartPicking = useCallback((posting: Posting) => setPickingSource(posting), [])
  const handleCancelPicking = useCallback(() => setPickingSource(null), [])
  const handlePickTarget = useCallback(
    (target: Posting) => {
      if (!pickingSource) return
      handleLinkTransfer(pickingSource.transaction_id, target.transaction_id)
      setPickingSource(null)
    },
    [pickingSource, handleLinkTransfer],
  )
  const handleUndoSplit = useCallback(
    (originalPostingId: string) => deleteSplit.mutate(originalPostingId),
    [deleteSplit],
  )
  const handleToggleSelected = useCallback(
    (postingId: string, selected: boolean) =>
      setOverride.mutate({ postingId, override: { pending_selected: selected } }),
    [setOverride],
  )

  return {
    suggestMessages,
    bulkSuggesting,
    bulkProgress,
    bulkPatternSuggesting,
    aiAvailable,
    aiPending: aiSuggest.isPending || bulkSuggesting || patternSuggest.isPending || bulkPatternSuggesting,
    validatePendingIsPending: validatePending.isPending,
    safeCounterpartyAccounts,
    pickingSource,
    runAiSuggest,
    runBulkAiSuggest,
    runBulkPatternSuggest,
    validateFiltered,
    handleOverride,
    handleMarkAsTransfer,
    handleUndoManualOverride,
    handleExcludeFromRule,
    handleExcludeAndUnlinkFromRule,
    handleUnlinkTransfer,
    handleStartPicking,
    handleCancelPicking,
    handlePickTarget,
    handleUndoSplit,
    handleToggleSelected,
  }
}
