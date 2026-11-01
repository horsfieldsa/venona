// @vitest-environment happy-dom
/**
 * The dunning ladder's top rung, and the clock underneath it.
 *
 * WHY THIS EXISTS. `blocking` was a named weight, documented in this repo and
 * in the hub as "the last retry is the only one that earns a modal", and read
 * by nothing: `useBillingNotice` returned only `notice`, and both consumers
 * branched on `weight === 'quiet'`. So the single moment a modal would recover
 * a card rendered exactly like the first decline, and no type, lint or test
 * could see it — the ladder had three rungs and two behaviours.
 *
 * The clock is the other half. `noticeFor` defaults its `now` to `Date.now()`,
 * which is not a reactive dependency, so a computed that called it cached the
 * answer for the life of the tab. The last-day trial escalation was moved to
 * this side specifically so that it could fire, and in a tab left open
 * overnight — the tab belonging to the reader about to be charged — it did not.
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { useBillingNotice } from './useBillingNotice'

import { useSessionStore } from '@/stores/session'

function billing(over: Record<string, unknown> = {}) {
  return {
    enforced: true,
    entitled: true,
    status: 'past_due',
    entitled_through: null,
    notice: 'final_notice',
    notice_at: '2026-09-20T00:00:00Z',
    manage_url: null,
    ...over,
  }
}

function signedIn(over: Record<string, unknown> = {}) {
  const session = useSessionStore()
  session.info = { authenticated: true, billing: billing(over) } as never
  return session
}

beforeEach(() => {
  setActivePinia(createPinia())
  // Guarded the same way the composable guards its own access: a private
  // window, cleared site data, or a browser set to block storage all throw
  // rather than returning null, and happy-dom simply does not define one.
  try {
    window.sessionStorage?.clear()
    window.localStorage?.clear()
  } catch {
    // The module-level refs below are what the assertions actually read.
  }
})

/** A date per test. `modalSeen` is module state by design -- dismissing in one
 *  place hides it everywhere -- so tests that share a date would share it. */
let day = 20

describe('the one notice that earns a modal', () => {
  it('raises it for the last retry', () => {
    signedIn({ notice_at: `2026-09-${++day}T00:00:00Z` })
    const { notice, modal } = useBillingNotice()
    expect(notice.value?.weight).toBe('blocking')
    expect(modal.value?.kind).toBe('final_notice')
  })

  it('does not raise one for a first decline', () => {
    signedIn({ notice: 'payment_failed', notice_at: `2026-09-${++day}T00:00:00Z` })
    const { notice, modal } = useBillingNotice()
    expect(notice.value?.weight).toBe('persistent')
    expect(modal.value).toBeNull()
  })

  /* A modal on every page view is a modal nobody reads, and the banner stays
   * either way -- closing this does not silence the notice. */
  it('raises it once per session and keeps the banner', () => {
    signedIn({ notice_at: `2026-09-${++day}T00:00:00Z` })
    const { notice, modal, closeModal } = useBillingNotice()
    expect(modal.value).not.toBeNull()
    closeModal()
    expect(modal.value).toBeNull()
    expect(notice.value?.kind).toBe('final_notice')
  })

  /* A NEW final notice -- a different date -- is a different fact, and the
   * reader has not seen this one. */
  it('raises it again when the date moves', () => {
    const session = signedIn({ notice_at: `2026-09-${++day}T00:00:00Z` })
    const { modal, closeModal } = useBillingNotice()
    closeModal()
    expect(modal.value).toBeNull()
    session.info = {
      authenticated: true,
      billing: billing({ notice_at: `2026-09-${++day}T00:00:00Z` }),
    } as never
    expect(modal.value).not.toBeNull()
  })
})
