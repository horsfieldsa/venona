/**
 * The dunning banner: which notice to show, and whether it has been dismissed.
 *
 * DISMISSAL IS PER NOTICE AND PER DATE, keyed in `localStorage`. That is the
 * whole subtlety: a permanently silenced payment failure is a cancellation
 * nobody meant to make, so the key carries `notice_at` and a NEW notice always
 * comes back. Only `quiet` notices can be dismissed at all -- a failed card
 * stays on screen until it is fixed.
 *
 * `localStorage` rather than a column, because this is a per-viewer convenience
 * with no consequence if it is lost: the worst case is seeing a banner twice.
 * Wrapped in try/catch because a private window, cleared site data or a browser
 * set to block storage all throw on access rather than returning null.
 */

import { computed, ref } from 'vue'

import { type BillingNotice, billingUrl, noticeFor } from '@/lib/billing'
import { useSessionStore } from '@/stores/session'

const KEY = 'venona.billing.dismissed'
/** The modal is once per SESSION, not per date: `sessionStorage` clears itself
 *  when the tab closes, which is exactly the lifetime wanted. */
const MODAL_KEY = 'venona.billing.modal'

/** Module-level so dismissing in one place hides it everywhere at once, rather
 *  than per component instance. */
const dismissed = ref<string>(read(() => window.localStorage.getItem(KEY)))
const modalSeen = ref<string>(read(() => window.sessionStorage.getItem(MODAL_KEY)))

/**
 * The clock the escalation reads, as a ref rather than `Date.now()` inside the
 * computed.
 *
 * A computed caches until a dependency changes, and `Date.now()` is not one --
 * so the last-day trial escalation, which was moved to this side specifically
 * so it could fire, never fired in a tab left open overnight. That tab is the
 * one belonging to the reader about to be charged.
 */
const now = ref(Date.now())

if (typeof window !== 'undefined') {
  const tick = () => {
    now.value = Date.now()
  }
  // Both, because they answer different cases: the interval covers a tab left
  // open and untouched, and the events cover a laptop that was asleep for two
  // days and whose interval never ran.
  window.setInterval(tick, 15 * 60 * 1000)
  window.addEventListener('focus', tick)
  document.addEventListener('visibilitychange', tick)
}

function read(get: () => string | null): string {
  try {
    return get() ?? ''
  } catch {
    return ''
  }
}

function stamp(notice: BillingNotice, at: string | null | undefined): string {
  return `${notice.kind}:${at ?? ''}`
}

export function useBillingNotice() {
  const session = useSessionStore()

  const notice = computed<BillingNotice | null>(() => {
    const billing = session.info?.billing ?? null
    const found = noticeFor(billing, now.value)
    if (!found) return null
    if (found.weight !== 'quiet') return found
    return dismissed.value === stamp(found, billing?.notice_at) ? null : found
  })

  /**
   * The one notice that earns a modal, and only the first time this session.
   *
   * `blocking` is the top of a three-rung ladder -- quiet, persistent, blocking
   * -- and it was named, documented in two files and then read by nothing, so
   * the last retry before somebody loses their subscription looked exactly like
   * the first decline. That is the single moment a modal is worth spending, and
   * the ladder exists so that it is spent only there.
   */
  const modal = computed<BillingNotice | null>(() => {
    const found = notice.value
    if (found?.weight !== 'blocking') return null
    return modalSeen.value === stamp(found, session.info?.billing?.notice_at) ? null : found
  })

  function closeModal(): void {
    const found = modal.value
    if (!found) return
    const value = stamp(found, session.info?.billing?.notice_at)
    modalSeen.value = value
    try {
      window.sessionStorage.setItem(MODAL_KEY, value)
    } catch {
      // Shown once for this page either way; persistence is a nicety.
    }
  }

  function dismiss(): void {
    const found = notice.value
    if (!found) return
    const value = stamp(found, session.info?.billing?.notice_at)
    dismissed.value = value
    try {
      window.localStorage.setItem(KEY, value)
    } catch {
      // The banner is hidden for this page either way; persistence is a nicety.
    }
  }

  /**
   * Where the button goes.
   *
   * A notice with a `flow` deep-links into Stripe's portal, because a banner
   * saying "update your card" that lands somebody on a portal home page has
   * lost most of the people it just persuaded. Everything else goes to the
   * billing page, which is ours.
   */
  function target(): string {
    const found = notice.value
    if (!found?.flow) return '/settings/billing'
    // `billingUrl` rather than appending: `manage_url` already carries an
    // `?instance=` query, so a second `?` would produce a link that goes
    // nowhere useful -- and the banner's button is the one click that recovers
    // a failed payment.
    return billingUrl(session.info?.billing?.manage_url, 'portal', { flow: found.flow })
      ?? '/settings/billing'
  }

  return { notice, dismiss, target, modal, closeModal }
}
