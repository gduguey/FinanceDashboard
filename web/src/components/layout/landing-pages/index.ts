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

function isLandingPageKey(key: string): key is LandingPageKey {
  return key in LANDING_PAGES
}

// The pages that go into the random rotation — every signed-out visitor
// gets one of these, picked at random on each page load. classicDashboard
// is deliberately left out: it's the sober fallback kept in reserve, not
// part of the rotation. Add a new variant's key here once it's ready to
// go live alongside the others.
const ROTATION: LandingPageKey[] = ['summitPiggyBank', 'bergeriePastoral', 'synthwaveChase']

function randomLandingPage(): LandingPageKey {
  return ROTATION[Math.floor(Math.random() * ROTATION.length)]
}

// VITE_LANDING_PAGE (web/.env.local for local dev; LANDING_PAGE in
// .env.docker/.env.staging on the VM — see deploy/deploy.sh) pins one
// specific page instead of randomizing — e.g. to force classicDashboard
// back, or to preview a single variant. Falls back to the random rotation
// when unset or invalid. Picked once per page load, not per re-render.
const requested = import.meta.env.VITE_LANDING_PAGE
export const ACTIVE_LANDING_PAGE: LandingPageKey =
  requested && isLandingPageKey(requested) ? requested : randomLandingPage()
