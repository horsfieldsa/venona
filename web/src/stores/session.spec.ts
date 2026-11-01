import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useSessionStore } from '@/stores/session'

/**
 * Two questions that look alike and want opposite defaults.
 *
 * `isAdmin` is for NAV and answers optimistically, so an operator's own menu
 * does not blank for a round trip on every reload. `isKnownAdmin` is for
 * FETCHING and waits, because an admin-only request fired before the probe
 * lands is a 403 the reader never asked for -- `main.ts` had to key the global
 * toast because three of them arrived in the same tick at mount.
 */
function payload(role: string | null) {
  return {
    authenticated: true,
    email: 'a@example.com',
    role,
    user_id: 'user:1',
    via: 'session',
    setup_required: false,
    relevance_tailored: true,
    timezone: null,
    billing: null,
    created_at: null,
    display_name: null,
    expires_at: null,
    logout_url: null,
    picture_url: null,
    provider: null,
  } as never
}

beforeEach(() => setActivePinia(createPinia()))

describe('before the probe answers', () => {
  it('offers the nav optimistically', () => {
    expect(useSessionStore().isAdmin()).toBe(true)
  })

  it('but fetches nothing', () => {
    /* THE BUG. Every admin-only request fired here is a refusal on a page
     * somebody just opened -- and coming back from Stripe Checkout is exactly
     * that page. */
    expect(useSessionStore().isKnownAdmin()).toBe(false)
  })
})

describe('once it has', () => {
  it('agrees for an admin', () => {
    const session = useSessionStore()
    session.apply(payload('admin'))
    expect(session.isAdmin()).toBe(true)
    expect(session.isKnownAdmin()).toBe(true)
  })

  it('agrees for a reader', () => {
    const session = useSessionStore()
    session.apply(payload('reader'))
    expect(session.isAdmin()).toBe(false)
    expect(session.isKnownAdmin()).toBe(false)
  })

  it('still admits an admin with no users row', () => {
    /* A null role is the root bearer, the env break-glass admin, and every
     * local `make dev` with auth off. Testing `role === 'admin'` would take the
     * admin surfaces away from all three. */
    const session = useSessionStore()
    session.apply(payload(null))
    expect(session.isKnownAdmin()).toBe(true)
  })
})
