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

export const useUpdateGoalContribution = () =>
  useAccountingMutation({
    mutationFn: ({ contributionId, contribution }: { contributionId: string; contribution: GoalContributionUpdate }) =>
      accountingApi.updateGoalContribution(contributionId, contribution),
    changes: ['store', 'goals'],
  })

export const useRemoveGoalContribution = () =>
  useAccountingMutation({
    mutationFn: (contributionId: string) => accountingApi.removeGoalContribution(contributionId),
    changes: ['store', 'goals'],
  })

// Drag-to-reorder, and nothing else: the body is the automation ids in the
// wanted order, so this can no longer express a field edit, an insert or a
// delete even by accident — those go through the scoped hooks below.
export const useReorderContributionAutomations = () =>
  useAccountingMutation({
    mutationFn: (automationIds: string[]) => accountingApi.reorderContributionAutomations(automationIds),
    changes: ['store', 'goals'],
  })

// Same narrowing as `useReorderContributionAutomations`; joining or leaving the
// drawdown order is `useCreateWithdrawalAutomation` / `useDeleteGoalAutomation`.
export const useReorderWithdrawalAutomations = () =>
  useAccountingMutation({
    mutationFn: (automationIds: string[]) => accountingApi.reorderWithdrawalAutomations(automationIds),
    changes: ['store', 'goals'],
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
