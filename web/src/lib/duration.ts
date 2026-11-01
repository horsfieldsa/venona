/**
 * One duration formatter.
 *
 * There were three, written within an hour of each other, and they disagreed:
 * the run detail page's `seconds()` took two ISO strings, and the timeline and
 * latency chart each grew their own `human(ms)`. Two of them carried the same
 * bug — `Math.round` on the seconds remainder, so 4_979_864ms rendered as
 * **"82m 60s"** on screen. A minute that reads as sixty seconds is the kind of
 * defect that makes every other number on the page look untrustworthy.
 */

/**
 * `ms` as the coarsest unit that still says something.
 *
 * Sub-second work is reported in milliseconds because "0.0s" is not an answer;
 * anything over a minute drops the decimal, because tenths of a second are
 * noise next to eighty minutes.
 */
export function humanMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  // FLOOR, not round. Rounding the remainder lets 59.6s become "60s", so a
  // duration renders one minute short of itself with a sixty in it.
  const minutes = Math.floor(ms / 60_000)
  const seconds = Math.floor((ms % 60_000) / 1000)
  return `${minutes}m ${seconds}s`
}

/** The same, from two ISO-8601 timestamps. Empty when either is missing, so a
 *  stage still running renders nothing rather than "NaN". */
export function humanBetween(from: string | null | undefined, to: string | null | undefined): string {
  if (!from || !to) return ''
  const ms = new Date(to).getTime() - new Date(from).getTime()
  return Number.isNaN(ms) ? '' : humanMs(ms)
}

/**
 * "4 minutes ago". The coarsest unit that is still true.
 *
 * A run page showed `2026-08-16T07:04:24.023Z`, which is precise and answers
 * the wrong question: what a reader wants from a run's start time is how long
 * ago it was, and converting an ISO timestamp to that in their head is work.
 * The exact value is kept as a `title` at the call site for anyone matching a
 * run against a log.
 */
export function agoFrom(iso: string | null | undefined, now: number): string {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const seconds = Math.max(0, Math.round((now - then) / 1000))
  if (seconds < 45) return 'just now'
  const units: [number, string][] = [
    [60, 'minute'],
    [3600, 'hour'],
    [86_400, 'day'],
  ]
  // Walk up while the next unit still divides into at least one whole.
  let label = 'second'
  let size = 1
  for (const [unitSeconds, unitLabel] of units) {
    if (seconds >= unitSeconds) {
      size = unitSeconds
      label = unitLabel
    }
  }
  const n = Math.floor(seconds / size)
  return `${n} ${label}${n === 1 ? '' : 's'} ago`
}

/**
 * "in 4 hours" -- `agoFrom`'s mirror, for timestamps ahead of now.
 *
 * A separate function rather than a sign branch in `agoFrom` because the two
 * clamp in opposite directions: a past timestamp here (the fire time slipping
 * behind a stale poll) renders 'imminent', exactly as a future one there
 * renders 'just now'.
 */
export function untilFrom(iso: string | null | undefined, now: number): string {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  const seconds = Math.max(0, Math.round((then - now) / 1000))
  if (seconds < 45) return 'imminent'
  const units: [number, string][] = [
    [60, 'minute'],
    [3600, 'hour'],
    [86_400, 'day'],
  ]
  let label = 'second'
  let size = 1
  for (const [unitSeconds, unitLabel] of units) {
    if (seconds >= unitSeconds) {
      size = unitSeconds
      label = unitLabel
    }
  }
  const n = Math.floor(seconds / size)
  return `in ${n} ${label}${n === 1 ? '' : 's'}`
}
