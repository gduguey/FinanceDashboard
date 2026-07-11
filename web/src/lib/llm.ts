import type { LlmUsage } from '@/types/accounting'

// Whether at least one AI categorization provider can actually be called
// right now — configured, and not currently rate-limited. `LlmUsageBanner`
// already reads this same per-provider `configured`/`is_limited` state to
// show "Gemini — not configured"; this is the gate that keeps the AI
// suggest buttons from being clickable when that banner already says
// there's nothing to suggest with.
export function anyLlmProviderAvailable(usage: LlmUsage | undefined): boolean {
  return Object.values(usage ?? {}).some((provider) => provider.configured && !provider.is_limited)
}
