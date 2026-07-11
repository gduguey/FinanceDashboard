import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { type ConnectionState, useLlmConnectionStatus, useLlmUsage } from '@/hooks/useAccountingData'
import type { LlmProviderUsage } from '@/types/accounting'

const PROVIDER_LABELS: Record<string, string> = {
  gemini: 'Gemini',
  mistral: 'Mistral',
}

const PERIOD_LABELS: Record<LlmProviderUsage['period'], string> = {
  daily: 'today',
  monthly: 'this month',
}

function ProviderUsageCard({
  name,
  usage,
  connectionState,
  connectionError,
}: {
  name: string
  usage: LlmProviderUsage
  connectionState: ConnectionState
  connectionError: string | null
}) {
  const label = PROVIDER_LABELS[name] ?? name

  // `configured` alone doesn't catch a wrong/expired key — this now
  // mirrors what Settings actually verified, not just "a value is set".
  if (connectionState === 'none') {
    return (
      <div className="flex items-center gap-1.5 rounded-lg border border-dashed px-3 py-1.5 text-xs text-muted-foreground">
        <span className="font-medium">{label}</span>
        <span>not configured</span>
      </div>
    )
  }

  if (connectionState === 'checking') {
    return (
      <div className="flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs text-muted-foreground">
        <span className="font-medium">{label}</span>
        <span>checking…</span>
      </div>
    )
  }

  if (connectionState === 'invalid') {
    const card = (
      <div className="flex items-center gap-1.5 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-1.5 text-xs text-destructive">
        <span className="font-medium">{label}</span>
        <span>can't authenticate</span>
      </div>
    )
    if (!connectionError) return card
    return (
      <Tooltip>
        <TooltipTrigger>{card}</TooltipTrigger>
        <TooltipContent>{connectionError}</TooltipContent>
      </Tooltip>
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
  // Exactly two providers, always — called unconditionally here (not
  // inside the map below) since hooks can't run in a loop.
  const gemini = useLlmConnectionStatus('gemini')
  const mistral = useLlmConnectionStatus('mistral')
  const connections: Record<string, { state: ConnectionState; error: string | null }> = { gemini, mistral }

  if (!data) return null

  return (
    <div className="flex flex-wrap gap-2">
      {Object.entries(data).map(([name, usage]) => (
        <ProviderUsageCard
          key={name}
          name={name}
          usage={usage}
          connectionState={connections[name]?.state ?? 'none'}
          connectionError={connections[name]?.error ?? null}
        />
      ))}
    </div>
  )
}
