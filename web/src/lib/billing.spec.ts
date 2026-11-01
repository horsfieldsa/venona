import { describe, expect, it } from 'vitest'

import {
  billingUrl,
  hasSubscription,
  noticeFor,
  planAction,
  planLabel,
  planSentence,
  screenFor,
  statusLabel,
  statusTone,
} from '@/lib/billing'
import { subscriptionLabel, subscriptionTone } from '@/lib/people'
import type { components } from '@/api/schema'

type BillingState = components['schemas']['BillingState']

function state(over: Partial<BillingState> = {}): BillingState {
  return {
    status: 'active',
    entitled: true,
    entitled_through: null,
    notice: null,
    notice_at: null,
    enforced: true,
    manage_url: 'https://hub.example.com/billing/portal?instance=defense',
    ...over,
  }
}

/* The Account page and the Subscription page describe the same six states. Two
 * surfaces wording them independently is how one ends up contradicting the
 * other about money, to the person paying it -- so both read from here. */
describe('planSentence', () => {
  it('names the next charge when there is a date', () => {
    const text = planSentence(state({ status: 'active', entitled_through: '2026-10-01T00:00:00Z' }))
    expect(text).toContain('subscribed')
    expect(text).toMatch(/\d{4}/)
  })

  it('does not invent a date when there is none', () => {
    expect(planSentence(state({ status: 'active' }))).not.toMatch(/\d{4}/)
  })

  it('does not tell somebody who never subscribed that theirs ended', () => {
    const text = planSentence(state({ status: 'none', entitled: false })).toLowerCase()
    expect(text).not.toContain('ended')
    expect(text).not.toContain('already had')
  })

  it('reassures a lapsed reader that the archive is theirs', () => {
    const text = planSentence(state({ status: 'lapsed', entitled: false, entitled_through: '2026-08-20T00:00:00Z' }))
    expect(text.toLowerCase()).toContain('still here')
  })

  it('says a failing payment has not cost them access', () => {
    expect(planSentence(state({ status: 'past_due' })).toLowerCase()).toContain('full access')
  })

  it('has a sentence for every status the wire can carry', () => {
    for (const status of ['none', 'trialing', 'active', 'past_due', 'lapsed', 'comped']) {
      const text = planSentence(state({ status }))
      expect(text.length, status).toBeGreaterThan(10)
      expect(text.endsWith('.'), status).toBe(true)
    }
  })

  it('carries no em or en dash, on any status', () => {
    for (const status of ['none', 'trialing', 'active', 'past_due', 'lapsed', 'comped']) {
      expect(planSentence(state({ status })), status).not.toMatch(/[—–]/)
    }
  })
})

/* `manage_url` already carries `?instance=`, so appending a second `?` produces
 * a link that goes nowhere useful -- and the banner's button is the one click
 * that recovers a failed payment. */
describe('billingUrl', () => {
  it('keeps the instance and adds the flow', () => {
    const url = new URL(billingUrl(state().manage_url, 'portal', { flow: 'payment_method_update' })!)
    expect(url.pathname).toBe('/billing/portal')
    expect(url.searchParams.get('instance')).toBe('defense')
    expect(url.searchParams.get('flow')).toBe('payment_method_update')
  })

  it('swaps the path for checkout and keeps the instance', () => {
    const url = new URL(billingUrl(state().manage_url, 'checkout', { plan: 'annual' })!)
    expect(url.pathname).toBe('/billing/checkout')
    expect(url.searchParams.get('instance')).toBe('defense')
    expect(url.searchParams.get('plan')).toBe('annual')
  })

  it('is null with no hub, so a caller can hide the control', () => {
    expect(billingUrl(null, 'portal')).toBeNull()
    expect(billingUrl('not a url', 'portal')).toBeNull()
  })
})

describe('screenFor', () => {
  it('separates never-started from lapsed', () => {
    expect(screenFor(state({ status: 'none', entitled: false }))).toBe('never')
    expect(screenFor(state({ status: 'lapsed', entitled: false }))).toBe('lapsed')
  })

  it('shows the product to anyone entitled, and to everyone when nobody is charged', () => {
    expect(screenFor(state({ status: 'past_due' }))).toBe('reading')
    expect(screenFor(state({ status: 'none', entitled: false, enforced: false }))).toBe('reading')
  })
})

describe('noticeFor', () => {
  it('says nothing to a subscriber who is fine', () => {
    expect(noticeFor(state({ status: 'active' }))).toBeNull()
  })

  it('says nothing at all on an instance that takes no money', () => {
    expect(noticeFor(state({ notice: 'payment_failed', enforced: false }))).toBeNull()
  })

  it('sends a declined card to the card form and marks it urgent', () => {
    const notice = noticeFor(state({ notice: 'payment_failed', notice_at: '2026-09-12T00:00:00Z' }))!
    expect(notice.flow).toBe('payment_method_update')
    expect(notice.urgent).toBe(true)
    expect(notice.weight).toBe('persistent')
  })

  it('does NOT send an SCA hold to the card form', () => {
    /* Their bank wants a tap and the card is fine. Telling them to replace it
     * sends them to re-enter a card that works, and they conclude the product
     * is broken. */
    const notice = noticeFor(state({ notice: 'action_required', notice_at: '2026-09-12T00:00:00Z' }))!
    expect(notice.flow).toBeNull()
    expect(notice.urgent).toBe(false)
    expect(notice.text.toLowerCase()).not.toContain('declined')
  })

  it('treats a chosen cancellation calmly, not as an alarm', () => {
    const notice = noticeFor(state({ notice: 'canceling', notice_at: '2026-10-01T00:00:00Z' }))!
    expect(notice.urgent).toBe(false)
    expect(notice.tone).toBe('neutral')
  })

  it('stays silent on a kind it has never heard of', () => {
    /* A hub ahead of this instance. The reader loses a banner, not access. */
    expect(noticeFor(state({ notice: 'invented_next_year' }))).toBeNull()
  })
})

describe('planAction', () => {
  /* "is where" after a FACT answers nothing. Somebody with a subscription is
   * looking for how to change it; somebody without one, how to begin. */
  it('offers to change it when there is one', () => {
    expect(planAction(state({ status: 'active' }))).toContain('change or cancel')
  })

  it('offers to start when there is not', () => {
    expect(planAction(state({ status: 'none', entitled: false }))).toContain('start')
    expect(planAction(state({ status: 'lapsed', entitled: false }))).toContain('start')
  })

  /* An administrator and a complimentary account are ENTITLED and have no card
   * behind them. Keying on access told them "Subscription is where you change
   * or cancel it" about a subscription that does not exist, and the page they
   * land on says there is nothing to change. */
  it('does not offer to change a subscription nobody has', () => {
    expect(planAction(state({ status: 'comped', entitled: true }))).toContain('start')
    expect(planAction(state({ status: 'none', entitled: true }))).toContain('start')
  })

  it('reads as a whole sentence after the link, on every status', () => {
    for (const [status, entitled] of [
      ['none', false], ['trialing', true], ['active', true],
      ['past_due', true], ['lapsed', false], ['comped', true],
    ] as const) {
      const billing = state({ status, entitled })
      const line = `${planSentence(billing)} Subscription ${planAction(billing)}`
      expect(line, status).not.toMatch(/\s\s/)
      expect(line.endsWith('.'), status).toBe(true)
      expect(line, status).not.toMatch(/[—–]/)
    }
  })
})

/* Stripe sends NOTHING in the final day of a trial -- `trial_will_end` fires 72
 * hours out, which for a three-day trial is the moment it starts. Verified
 * against a Stripe test clock: the stored kind stayed `trial_active` right up
 * to the charge. Only this side knows what time it is when the page renders. */
describe('the last day of a trial', () => {
  const NOW = Date.parse('2026-09-12T00:00:00Z')

  it('names the charge once it is inside a day', () => {
    const billing = state({
      status: 'trialing',
      notice: 'trial_active',
      notice_at: '2026-09-12T12:00:00Z',
    })
    const notice = noticeFor(billing, NOW)!
    expect(notice.kind).toBe('trial_ending')
    expect(notice.text.toLowerCase()).toContain('cancel')
  })

  it('stays calm while there is still time', () => {
    const billing = state({
      status: 'trialing',
      notice: 'trial_active',
      notice_at: '2026-09-14T12:00:00Z',
    })
    expect(noticeFor(billing, NOW)!.kind).toBe('trial_active')
  })

  it('escalates nothing else', () => {
    for (const kind of ['payment_failed', 'action_required', 'canceling', 'lapsed']) {
      const billing = state({ notice: kind, notice_at: '2026-09-12T01:00:00Z' })
      expect(noticeFor(billing, NOW)!.kind, kind).toBe(kind)
    }
  })

  it('does not crash on an unparseable date', () => {
    const billing = state({ status: 'trialing', notice: 'trial_active', notice_at: 'not a date' })
    expect(noticeFor(billing, NOW)!.kind).toBe('trial_active')
  })
})

/* Entitled is not the same as paying. An administrator has access by role and
 * no subscription, and the page offered them Change plan, Cancel and Update
 * card -- three buttons the hub refuses, because there is nothing behind them. */
describe('hasSubscription', () => {
  it('is true only where Stripe has something to act on', () => {
    for (const status of ['trialing', 'active', 'past_due']) {
      expect(hasSubscription(state({ status })), status).toBe(true)
    }
  })

  it('is false for an admin or a complimentary account, who are entitled', () => {
    for (const status of ['none', 'comped']) {
      const billing = state({ status, entitled: true })
      expect(screenFor(billing), status).toBe('reading')
      expect(hasSubscription(billing), status).toBe(false)
    }
  })

  it('is false once it has ended', () => {
    expect(hasSubscription(state({ status: 'lapsed', entitled: false }))).toBe(false)
  })

  it('is false with no billing at all', () => {
    expect(hasSubscription(null)).toBe(false)
  })
})

/* One vocabulary for two audiences. `web/src/lib/people.ts` renders the same
 * six states for an operator and had its own copy, which had already drifted:
 * "payment failed" against the reader's own "Payment problem", on a screen
 * that also tells them they still have full access. */
describe('the status vocabulary', () => {
  it('is the same words the operator table renders', () => {
    expect(subscriptionLabel).toBe(statusLabel)
    expect(subscriptionTone).toBe(statusTone)
  })

  it('has a word and a tone for every status the wire can carry', () => {
    for (const status of ['none', 'trialing', 'active', 'past_due', 'lapsed', 'comped']) {
      expect(statusLabel(status), status).toBeTruthy()
      expect(statusLabel(status), status).not.toBe(status)
      expect(statusTone(status), status).toBeTruthy()
    }
  })

  it('says the same thing about past_due as the reader’s own screen', () => {
    expect(statusLabel('past_due')).toBe(planLabel(state({ status: 'past_due' })))
  })
})
