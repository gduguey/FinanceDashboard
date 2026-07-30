import { useQuery } from '@tanstack/react-query'
import { keys } from '@/hooks/accounting/keys'
import { useAccountingMutation, useOptimisticStoreMutation } from '@/hooks/accounting/mutations'
import { accountingApi } from '@/lib/accountingApi'
import type { SimulatorScenario, SimulatorScenarioCreate } from '@/types/accounting'

export const useNetWorth = (asOf?: string, displayCurrency?: string) =>
  useQuery({
    queryKey: keys.netWorth(asOf, displayCurrency),
    queryFn: () => accountingApi.netWorth(asOf, displayCurrency),
  })

export const useNetWorthHistory = (start: string, end: string, intervalDays?: number, displayCurrency?: string) =>
  useQuery({
    queryKey: keys.netWorthHistory(start, end, intervalDays, displayCurrency),
    queryFn: () => accountingApi.netWorthHistory(start, end, intervalDays, displayCurrency),
  })

export const useNetWorthHistoryByAccount = (
  start: string,
  end: string,
  intervalDays?: number,
  displayCurrency?: string,
  enabled = true,
) =>
  useQuery({
    queryKey: keys.netWorthHistoryByAccount(start, end, intervalDays, displayCurrency),
    queryFn: () => accountingApi.netWorthHistoryByAccount(start, end, intervalDays, displayCurrency),
    enabled,
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

export const useBudgetComparison = (month: string, displayCurrency?: string) =>
  useQuery({
    queryKey: keys.budgetComparison(month, displayCurrency),
    queryFn: () => accountingApi.budgetComparison(month, displayCurrency),
  })

export const useSuggestedBudgetAmount = (
  categoryId: string,
  month: string,
  lookbackMonths?: number,
  subcategoryId?: string,
  displayCurrency?: string,
) =>
  useQuery({
    queryKey: keys.suggestedBudgetAmount(categoryId, month, lookbackMonths, subcategoryId, displayCurrency),
    queryFn: () =>
      accountingApi.suggestedBudgetAmount(categoryId, month, lookbackMonths, subcategoryId, displayCurrency),
  })

export const useInterestSummary = (asOf?: string) =>
  useQuery({ queryKey: keys.interestSummary(asOf), queryFn: () => accountingApi.interestSummary(asOf) })

export const useSimulatorProjection = (
  initialCapital: number,
  monthlyContribution: number,
  horizonYears: number,
  annualRatePct: number,
  compoundingFrequency: SimulatorScenario['compounding_frequency'],
) =>
  useQuery({
    queryKey: keys.simulatorProject(
      initialCapital,
      monthlyContribution,
      horizonYears,
      annualRatePct,
      compoundingFrequency,
    ),
    queryFn: () =>
      accountingApi.simulatorProject(
        initialCapital,
        monthlyContribution,
        horizonYears,
        annualRatePct,
        compoundingFrequency,
      ),
  })

export const useDeleteSimulatorScenario = () =>
  useOptimisticStoreMutation({
    mutationFn: (scenarioId: string) => accountingApi.deleteSimulatorScenario(scenarioId),
    changes: ['store'],
    edit: (store, scenarioId) => ({
      ...store,
      simulator_scenarios: store.simulator_scenarios.filter((scenario) => scenario.scenario_id !== scenarioId),
    }),
  })

export const useCreateSimulatorScenario = () =>
  useAccountingMutation({
    mutationFn: (scenario: SimulatorScenarioCreate) => accountingApi.createSimulatorScenario(scenario),
    changes: ['store'],
  })
