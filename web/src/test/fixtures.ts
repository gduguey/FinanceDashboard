import type { AccountingStore } from '@/types/accounting'

/**
 * An `AccountingStore` with every collection present and empty.
 *
 * The shape matters more than the contents: the optimistic `onMutate`
 * handlers reach straight into `previous.goal_automations`, `previous.goals`
 * and friends, so a test seeding the store cache with a bare `{}` fails inside
 * the handler rather than on the assertion it meant to make.
 *
 * @param overrides - Collections to replace, for a test that needs rows.
 * @returns A complete store, safe to hand to `queryClient.setQueryData`.
 */
export function emptyStore(overrides: Partial<AccountingStore> = {}): AccountingStore {
  return {
    accounts: {},
    account_ids_with_postings: [],
    categories: {},
    tags: {},
    transfer_rules: [],
    other_assets: [],
    budgets: [],
    simulator_scenarios: [],
    transfer_links: [],
    category_patterns: {},
    goals: {},
    goal_contributions: {},
    goal_automations: [],
    ...overrides,
  }
}
