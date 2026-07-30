import { describe, expect, it } from 'vitest'
import { routes } from '@/routeTable'

// Every page the app serves. Spelled out rather than derived, so that dropping
// a route file — or a glob pattern that quietly stops matching one, which is
// the failure mode lazy globbing introduces — fails here instead of turning a
// page into a blank screen in production.
const EXPECTED_PATHS = [
  '/',
  '/accounts',
  '/budget',
  '/categories',
  '/goals',
  '/guide',
  '/import',
  '/insights',
  '/investments',
  '/investments/allocation',
  '/investments/glossary',
  '/investments/taxes',
  '/net-worth',
  '/onboarding',
  '/rules',
  '/settings',
  '/simulator',
  '/tags',
  '/transactions',
]

describe('routes', () => {
  it('serves every expected path', () => {
    expect(routes.map((route) => route.path)).toEqual(EXPECTED_PATHS)
  })

  it('maps each path to exactly one component', () => {
    expect(new Set(routes.map((route) => route.path)).size).toBe(routes.length)
  })

  it('gives every route a component', () => {
    for (const route of routes) {
      expect(route.Component, route.path).toBeTruthy()
    }
  })
})
