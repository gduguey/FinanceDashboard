import { useQuery } from '@tanstack/react-query'
import { keys } from '@/hooks/accounting/keys'
import {
  patchEntry,
  useAccountingMutation,
  useOptimisticStoreMutation,
  usePreviewMutation,
  withoutEntry,
} from '@/hooks/accounting/mutations'
import { accountingApi } from '@/lib/accountingApi'
import type {
  GoalAutomation,
  GoalAutomationCreate,
  GoalAutomationUpdate,
  GoalContributionCreate,
  GoalContributionUpdate,
  GoalCreate,
  GoalUpdate,
} from '@/types/accounting'

export const useGoalsSummary = (asOf?: string, displayCurrency?: string) =>
  useQuery({
    queryKey: keys.goalsSummary(asOf, displayCurrency),
    queryFn: () => accountingApi.goalsSummary(asOf, displayCurrency),
  })

// Each call carries and checks its own `update.expected_version`, so editing
// two different goals can never clobber each other regardless of how the
// requests interleave.
export const usePatchGoal = () =>
  useOptimisticStoreMutation({
    mutationFn: ({ goalId, update }: { goalId: string; update: GoalUpdate }) => accountingApi.patchGoal(goalId, update),
    changes: ['store', 'goals'],
    edit: (store, { goalId, update }) => ({ ...store, goals: patchEntry(store.goals, goalId, update) }),
  })

export const useDeleteGoal = () =>
  useOptimisticStoreMutation({
    mutationFn: (goalId: string) => accountingApi.deleteGoal(goalId),
    changes: ['store', 'goals'],
    edit: (store, goalId) => ({ ...store, goals: withoutEntry(store.goals, goalId) }),
  })

export const useCreateGoal = () =>
  useAccountingMutation({
    mutationFn: (goal: GoalCreate) => accountingApi.createGoal(goal),
    changes: ['store', 'goals'],
  })

export const useCreateGoalContribution = () =>
  useAccountingMutation({
    mutationFn: (contribution: GoalContributionCreate) => accountingApi.createGoalContribution(contribution),
    changes: ['store', 'goals'],
  })

// A full replace of one row, so the patch is the row.
export const useUpdateGoalContribution = () =>
  useOptimisticStoreMutation({
    mutationFn: ({ contributionId, contribution }: { contributionId: string; contribution: GoalContributionUpdate }) =>
      accountingApi.updateGoalContribution(contributionId, contribution),
    changes: ['store', 'goals'],
    edit: (store, { contributionId, contribution }) => ({
      ...store,
      goal_contributions: patchEntry(store.goal_contributions, contributionId, contribution),
    }),
  })

export const useRemoveGoalContribution = () =>
  useOptimisticStoreMutation({
    mutationFn: (contributionId: string) => accountingApi.removeGoalContribution(contributionId),
    changes: ['store', 'goals'],
    edit: (store, contributionId) => ({
      ...store,
      goal_contributions: withoutEntry(store.goal_contributions, contributionId),
    }),
  })

// Drag-to-reorder, and nothing else: the body is the automation ids in the
// wanted order, so this can no longer express a field edit, an insert or a
// delete even by accident — those go through the scoped hooks below.
export const useReorderContributionAutomations = () =>
  useOptimisticStoreMutation({
    mutationFn: (automationIds: string[]) => accountingApi.reorderContributionAutomations(automationIds),
    changes: ['store', 'goals'],
    edit: (store, automationIds) => ({ ...store, goal_automations: repriced(store.goal_automations, automationIds) }),
  })

// Same narrowing as `useReorderContributionAutomations`; joining or leaving the
// drawdown order is `useCreateWithdrawalAutomation` / `useDeleteGoalAutomation`.
export const useReorderWithdrawalAutomations = () =>
  useOptimisticStoreMutation({
    mutationFn: (automationIds: string[]) => accountingApi.reorderWithdrawalAutomations(automationIds),
    changes: ['store', 'goals'],
    edit: (store, automationIds) => ({ ...store, goal_automations: repriced(store.goal_automations, automationIds) }),
  })

export const usePatchGoalAutomation = () =>
  useOptimisticStoreMutation({
    mutationFn: ({ automationId, update }: { automationId: string; update: GoalAutomationUpdate }) =>
      accountingApi.patchGoalAutomation(automationId, update),
    changes: ['store', 'goals'],
    edit: (store, { automationId, update }) => ({
      ...store,
      goal_automations: store.goal_automations.map((automation) =>
        automation.automation_id === automationId ? { ...automation, ...update } : automation,
      ),
    }),
  })

export const useDeleteGoalAutomation = () =>
  useOptimisticStoreMutation({
    mutationFn: (automationId: string) => accountingApi.deleteGoalAutomation(automationId),
    changes: ['store', 'goals'],
    edit: (store, automationId) => ({
      ...store,
      goal_automations: store.goal_automations.filter((automation) => automation.automation_id !== automationId),
    }),
  })

export const useCreateContributionAutomation = () =>
  useAccountingMutation({
    mutationFn: (automation: GoalAutomationCreate) => accountingApi.createContributionAutomation(automation),
    changes: ['store', 'goals'],
  })

export const useCreateWithdrawalAutomation = () =>
  useAccountingMutation({
    mutationFn: (goalId: string) => accountingApi.createWithdrawalAutomation(goalId),
    changes: ['store', 'goals'],
  })

export const useRunRecurringAdditions = () =>
  useAccountingMutation({
    mutationFn: (asOf?: string) => accountingApi.runRecurringAdditions(asOf),
    changes: ['store', 'goals'],
  })

export const useRunWithdrawalAutomation = () =>
  useAccountingMutation({
    mutationFn: (asOf?: string) => accountingApi.runWithdrawalAutomation(asOf),
    changes: ['store', 'goals'],
  })

export const useSimulateContribution = () =>
  usePreviewMutation(({ goalId, date, amount }: { goalId: string; date: string; amount: number }) =>
    accountingApi.simulateContribution(goalId, date, amount),
  )

/**
 * Reassign the named automations' own priorities in the order they were dragged into.
 *
 * The panel renders each direction sorted by `priority` ascending, so painting
 * a reorder means moving the numbers, not the list positions. Permuting the
 * priorities the named automations already hold — rather than numbering them
 * 0..n-1 — gets the dragged order right without this file having to know the
 * server's numbering convention: whatever values were in play stay in play,
 * and the reconciling refetch brings back the server's own.
 *
 * The store keeps contributions and withdrawals in one list and a reorder only
 * ever names one direction's ids, so an automation not named keeps its
 * priority untouched.
 *
 * @param automations - The store's automations.
 * @param automationIds - The named automations, in their wanted order.
 * @returns A new list; the original is untouched.
 */
function repriced(automations: GoalAutomation[], automationIds: string[]): GoalAutomation[] {
  const priorities = automations
    .filter((automation) => automationIds.includes(automation.automation_id))
    .map((automation) => automation.priority)
    .sort((a, b) => a - b)
  const wanted = new Map(automationIds.map((id, index) => [id, priorities[index]]))
  return automations.map((automation) => {
    const priority = wanted.get(automation.automation_id)
    return priority === undefined ? automation : { ...automation, priority }
  })
}
