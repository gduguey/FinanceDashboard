import { BergeriePastoralLanding } from '@/components/layout/landing-pages/bergerie-pastoral'
import { ClassicDashboardLanding } from '@/components/layout/landing-pages/classic-dashboard'
import { SummitPiggyBankLanding } from '@/components/layout/landing-pages/summit-piggy-bank'
import { SynthwaveChaseLanding } from '@/components/layout/landing-pages/synthwave-chase'

// One entry per landing page variant we keep on hand — e.g. for seasonal
// happenings — even when it isn't the one currently shown. Add a new file
// next to these and register it here to make it selectable.
export const LANDING_PAGES = {
  classicDashboard: ClassicDashboardLanding,
  summitPiggyBank: SummitPiggyBankLanding,
  bergeriePastoral: BergeriePastoralLanding,
  synthwaveChase: SynthwaveChaseLanding,
} as const

export type LandingPageKey = keyof typeof LANDING_PAGES

const DEFAULT_LANDING_PAGE: LandingPageKey = 'summitPiggyBank'

function isLandingPageKey(key: string): key is LandingPageKey {
  return key in LANDING_PAGES
}

// Set via VITE_LANDING_PAGE (web/.env.local for local dev; LANDING_PAGE in
// .env.docker/.env.staging on the VM — see deploy/deploy.sh) so switching
// which page every signed-out visitor sees doesn't require a code change.
const requested = import.meta.env.VITE_LANDING_PAGE
export const ACTIVE_LANDING_PAGE: LandingPageKey =
  requested && isLandingPageKey(requested) ? requested : DEFAULT_LANDING_PAGE
