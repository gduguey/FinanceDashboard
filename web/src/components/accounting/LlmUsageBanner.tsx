import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useLlmUsage } from '@/hooks/useAccountingData'
import type { LlmProviderUsage } from '@/types/accounting'

const PROVIDER_LABELS: Record<string, string> = {
  gemini: 'Gemini',
  mistral: 'Mistral',
}

const PERIOD_LABELS: Record<LlmProviderUsage['period'], string> = {
  daily: 'today',
  monthly: 'this month',
}

function ProviderUsageCard({ name, usage }: { name: string; usage: LlmProviderUsage }) {
  const label = PROVIDER_LABELS[name] ?? name

  if (!usage.configured) {
    return (
      <div className="flex items-center gap-1.5 rounded-lg border border-dashed px-3 py-1.5 text-xs text-muted-foreground">
        <span className="font-medium">{label}</span>
        <span>not configured</span>
      </div>
    )
  }

  const card = (
    <div
      className={
        usage.is_limited
          ? 'flex items-center gap-1.5 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-1.5 text-xs text-destructive'
          : 'flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs text-muted-foreground'
      }
    >
      <span className="font-medium">{label}</span>
      <span>
        {usage.used_count} used {PERIOD_LABELS[usage.period]}
      </span>
      {usage.is_limited && <span className="font-medium">— limit reached</span>}
    </div>
  )

  if (!usage.is_limited || !usage.last_error) return card

  return (
    <Tooltip>
      <TooltipTrigger>{card}</TooltipTrigger>
      <TooltipContent>{usage.last_error}</TooltipContent>
    </Tooltip>
  )
}

export function LlmUsageBanner() {
  const { data } = useLlmUsage()
  if (!data) return null

  return (
    <div className="flex flex-wrap gap-2">
      {Object.entries(data).map(([name, usage]) => (
        <ProviderUsageCard key={name} name={name} usage={usage} />
      ))}
    </div>
  )
}
