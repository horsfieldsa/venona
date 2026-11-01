/**
 * A subscription, in one phrase and one tone.
 *
 * THE ONLY PLACE a billing state becomes words. Every surface that mentions
 * money reads from here, for the reason `runOutcome.ts` exists one file over:
 * four surfaces once decided the wording of a run independently and one of them
 * was wrong, telling a curator the web held no definition when the truth was
 * that we had run out of budget. The same mistake here would be wrong about
 * somebody's money, which is the version that produces a chargeback.
 *
 * So the wire carries a KIND (`billing_notice`) and a DATE (`billing_notice_at`)
 * and nothing else. The hub decides which kind applies, because it is the only
 * side that can: `action_required` and `final_notice` come from invoice events
 * and cannot be recovered from a subscription's status at all.
 *
 * Reader language throughout (CLAUDE.md): no "entitlement", no "instance", no
 * "lapsed". And no em or en dashes, which `tests/test_copy_style.py` holds on
 * the server side and review holds here.
 */

import type { components } from '@/api/schema'
import * as dates from '@/lib/dates'

export type BillingState = components['schemas']['BillingState']

/** The tones `Chip` and the notice take. Same four meanings as elsewhere. */
export type BillingTone = 'ok' | 'high' | 'critical' | 'accent' | 'neutral'

/**
 * How loudly a notice appears, and it is deliberately a ladder.
 *
 * A modal on the first failed payment gets dismissed reflexively and teaches
 * people to ignore the next one. `quiet` may be dismissed and comes back when
 * the date changes; `persistent` may not; `blocking` adds one modal per session
 * and is reserved for the last retry before the subscription ends.
 */
export type NoticeWeight = 'quiet' | 'persistent' | 'blocking'

export interface BillingNotice {
  kind: string
  /** The whole sentence, dates already resolved into the reader's zone. */
  text: string
  tone: BillingTone
  weight: NoticeWeight
  /** What the button says, or null when there is nothing to do. */
  action: string | null
  /**
   * Which Stripe portal flow the button opens. A dunning banner that lands
   * somebody on a portal home page has lost most of the people it just
   * persuaded, so "update your card" goes straight to the card form.
   */
  flow: 'payment_method_update' | 'subscription_update' | null
  /** True for the two that are genuinely wrong, so the markup can use
   *  `role="alert"`. Announcing every state makes a live region useless --
   *  `StatusNote`'s docblock sets that discipline. */
  urgent: boolean
}

/** A date in the reader's own zone. A billing date shown a day out is a support
 *  email, or a dispute -- `dates.zone` carries `user_preferences.timezone`. */
function on(at: string | null | undefined): string {
  return at ? dates.longDate(at) : 'soon'
}

/**
 * Build a link into the hub's billing routes from the one URL the session
 * carries, preserving the query it already holds.
 *
 * `manage_url` arrives as `<hub>/billing/portal?instance=<slug>` -- the slug is
 * there so the hub never has to guess which seat a reader means, which it
 * cannot do for somebody who belongs to two. That trailing query is exactly why
 * this is URL parsing rather than string surgery: appending `?flow=...` to a
 * URL that already has a `?` produces a link that silently goes nowhere useful,
 * and stripping a suffix with a `$`-anchored regex stops matching the moment a
 * parameter is added.
 *
 * Returns null when there is no hub, so a caller can hide the control rather
 * than draw one that does nothing.
 */
export function billingUrl(
  manageUrl: string | null | undefined,
  path: 'portal' | 'checkout',
  params: Record<string, string> = {},
): string | null {
  if (!manageUrl) return null
  try {
    const url = new URL(manageUrl)
    url.pathname = url.pathname.replace(/\/billing\/.*$/, `/billing/${path}`)
    for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value)
    return url.toString()
  } catch {
    return null
  }
}

/** A day. Inside it, a running trial becomes the one that names the charge. */
const TRIAL_ENDING_WINDOW_MS = 24 * 60 * 60 * 1000

function escalate(billing: BillingState, now: number): string {
  if (billing.notice !== 'trial_active' || !billing.notice_at) return billing.notice as string
  const ends = Date.parse(billing.notice_at)
  if (Number.isNaN(ends)) return billing.notice
  return ends - now <= TRIAL_ENDING_WINDOW_MS ? 'trial_ending' : 'trial_active'
}

/**
 * What to say, or null for "say nothing".
 *
 * Null is the correct answer for a paying subscriber who is fine, and most
 * readers most of the time. A product that reminds people they are paying for
 * it every day is a product they cancel.
 */
export function noticeFor(
  billing: BillingState | null | undefined,
  now: number = Date.now(),
): BillingNotice | null {
  if (!billing || !billing.enforced || !billing.notice) return null
  const at = on(billing.notice_at)
  // THE LAST-DAY ESCALATION HAPPENS HERE, not on the hub, because only this
  // side knows what time it is when the page renders.
  //
  // The hub can only recompute a notice when an event arrives, and Stripe sends
  // NOTHING in the final day of a trial: `trial_will_end` fires 72 hours out,
  // which for a three-day trial is the moment it starts. So a stored
  // `trial_ending` was unreachable, and the reader saw the same sentence on day
  // one and on the morning of the charge. Verified against a Stripe test clock.
  //
  // The words still live in this file and nowhere else, which is the rule that
  // matters; what moves here is a comparison against the clock.
  const kind = escalate(billing, now)
  switch (kind) {
    case 'trial_active':
      // Runs the whole trial, not just the last day. Surprise is what produces
      // chargebacks, and somebody who has seen the date every visit is not
      // surprised when the charge lands.
      return {
        kind,
        text: `Your free trial runs until ${at}. Your card will be charged then.`,
        tone: 'accent',
        weight: 'quiet',
        action: 'See your plan',
        flow: null,
        urgent: false,
      }
    case 'trial_ending':
      return {
        kind,
        text: `Your free trial ends on ${at} and your subscription starts. Cancel any time before then.`,
        tone: 'high',
        weight: 'quiet',
        action: 'See your plan',
        flow: null,
        urgent: false,
      }
    case 'action_required':
      // NOT a failure, and saying so would send this reader to re-enter a card
      // that works perfectly. Their bank wants a tap; the card is fine.
      return {
        kind,
        text: `Your bank needs to confirm this payment before ${at}. Nothing is wrong with your card.`,
        tone: 'accent',
        weight: 'persistent',
        action: 'Confirm with your bank',
        flow: null,
        urgent: false,
      }
    case 'payment_failed':
      return {
        kind,
        text: `Your card was declined. We will try again on ${at}.`,
        tone: 'high',
        weight: 'persistent',
        action: 'Update your card',
        flow: 'payment_method_update',
        urgent: true,
      }
    case 'final_notice':
      return {
        kind,
        text: `Your card was declined and we cannot try again. New briefs stop on ${at}.`,
        tone: 'critical',
        weight: 'blocking',
        action: 'Update your card',
        flow: 'payment_method_update',
        urgent: true,
      }
    case 'canceling':
      // They chose this. Red-alert treatment here reads as a product arguing
      // with somebody who has already decided, and it loses the win-back.
      return {
        kind,
        text: `Your subscription ends on ${at}. You can keep reading until then.`,
        tone: 'neutral',
        weight: 'quiet',
        action: 'Keep my subscription',
        flow: 'subscription_update',
        urgent: false,
      }
    case 'lapsed':
      return {
        kind,
        text: `Your subscription ended on ${at}. The briefs you already had are still here.`,
        tone: 'neutral',
        weight: 'persistent',
        action: 'Start again',
        flow: null,
        urgent: false,
      }
    default:
      // A kind this build has never heard of, from a hub that is ahead of this
      // instance. Silence beats guessing: the reader loses a banner, not access.
      return null
  }
}

/**
 * The three screens a reader can be on, which are NOT the same screen.
 *
 * `never` has nothing to freeze and must be offered the trial; `lapsed` has an
 * archive and must be shown it. Conflating them is how somebody who just paid
 * to get in is met with an empty page.
 */
export type BillingScreen = 'reading' | 'never' | 'lapsed'

export function screenFor(billing: BillingState | null | undefined): BillingScreen {
  if (!billing || !billing.enforced || billing.entitled) return 'reading'
  return billing.status === 'none' ? 'never' : 'lapsed'
}

/**
 * Whether there is a SUBSCRIPTION to manage, which is not the same question as
 * whether this reader is entitled.
 *
 * An administrator is entitled by role and pays nothing, so `screenFor` calls
 * them `reading` and the page offered them Change plan, Cancel and Update card
 * -- three buttons with no subscription behind them, every one of which the hub
 * refuses. `comped` is the same shape: real access, nothing to bill.
 *
 * Only these three states have something Stripe can act on.
 */
export function hasSubscription(billing: BillingState | null | undefined): boolean {
  return ['trialing', 'active', 'past_due'].includes(billing?.status ?? '')
}

/**
 * The status word, for ANY surface that renders one.
 *
 * Takes the bare status so the operator's people table can use it too. That
 * table had its own copy, and the copy disagreed: it said "payment failed"
 * where the reader's own screen says "Payment problem" and reassures them they
 * still have full access. An operator reading one and a reader reading the
 * other is how somebody is told the wrong thing about their own account.
 */
export function statusLabel(status: string | null | undefined): string {
  switch (status) {
    case 'trialing':
      return 'Free trial'
    case 'active':
      return 'Subscribed'
    case 'past_due':
      return 'Payment problem'
    case 'lapsed':
      return 'Ended'
    case 'comped':
      return 'Complimentary'
    default:
      return 'Not started'
  }
}

/** The status word, for the plan summary. Reader language, not the wire's. */
export function planLabel(billing: BillingState | null | undefined): string {
  return statusLabel(billing?.status)
}

/**
 * The one-line summary for the Account page: what you are on, and the date.
 *
 * HERE rather than in `AccountView`, for the reason the whole file exists. The
 * Account page and the Subscription page describe the same six states, and two
 * surfaces wording them independently is how one of them ends up saying
 * something the other contradicts -- about money, to the person paying it.
 *
 * Ends without naming the destination, because every caller follows it with the
 * link. Dates come through `dates.longDate`, so they land in the reader's own
 * zone rather than UTC.
 */
export function planSentence(billing: BillingState | null | undefined): string {
  const on = billing?.entitled_through ? dates.longDate(billing.entitled_through) : null
  switch (billing?.status) {
    case 'trialing':
      return on
        ? `You are on a free trial until ${on}, when your subscription starts.`
        : 'You are on a free trial.'
    case 'active':
      return on ? `You are subscribed. Your next charge is ${on}.` : 'You are subscribed.'
    case 'past_due':
      return 'There is a problem with your payment, and you still have full access.'
    case 'lapsed':
      return on
        ? `Your subscription ended on ${on}. The briefs you already had are still here.`
        : 'Your subscription has ended. The briefs you already had are still here.'
    case 'comped':
      return 'You have complimentary access, with nothing to pay.'
    default:
      return 'You have not subscribed yet.'
  }
}

/**
 * The clause that FOLLOWS the link on the Account page.
 *
 * `AccountView` mimics the brief-delivery section, which reads "...ask to be
 * told when it lands. Notifications is where." That works because its first
 * sentence describes an ACTION, so "is where" answers a question the reader is
 * already holding. `planSentence` states a fact instead, and "is where" after a
 * fact answers nothing -- so the clause has to finish the thought itself.
 *
 * Two forms, because there are two situations: somebody with a subscription is
 * looking for how to change it, and somebody without one is looking for how to
 * begin.
 */
export function planAction(billing: BillingState | null | undefined): string {
  // `hasSubscription`, NOT `entitled`. An administrator and a complimentary
  // account are both entitled and have no card behind them, so keying on
  // access told them "Subscription is where you change or cancel it" about a
  // subscription that does not exist -- and the page they would land on says
  // there is nothing to change. Two surfaces disagreeing about one account.
  return hasSubscription(billing) ? 'is where you change or cancel it.' : 'is where you start.'
}

/** The signal colour for a status, shared with the operator's table for the
 *  same reason `statusLabel` is. */
export function statusTone(status: string | null | undefined): BillingTone {
  switch (status) {
    case 'active':
    case 'comped':
      return 'ok'
    case 'trialing':
      return 'accent'
    case 'past_due':
      return 'high'
    case 'lapsed':
      return 'critical'
    default:
      return 'neutral'
  }
}

export function planTone(billing: BillingState | null | undefined): BillingTone {
  return statusTone(billing?.status)
}
