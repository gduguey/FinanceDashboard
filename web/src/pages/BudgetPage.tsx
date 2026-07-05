export function BudgetPage() {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="sticky top-0 z-10 border-b border-border bg-white/95 px-8 py-5 backdrop-blur-sm">
        <h1 className="text-lg font-semibold tracking-tight text-foreground">Budget</h1>
      </div>
      <div className="mx-auto max-w-4xl px-8 py-8">
        <p className="text-sm text-muted-foreground">
          Not built yet — see Phase 5 of <code>ACCOUNTING_PLAN.md</code> for what's planned: a monthly budget per
          category, and the actual-vs-budgeted cash-flow comparison.
        </p>
      </div>
    </div>
  )
}
