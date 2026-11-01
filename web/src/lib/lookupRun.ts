import { errorText } from '@/api/client'
import { waitForRun, type WaitedRun } from '@/lib/waitForRun'
import { useRunsStore } from '@/stores/runs'
import { useToastsStore } from '@/stores/toasts'

/**
 * Starting a lookup and following it, in the two places a reader can start one.
 *
 * The hover card (`stores/lookups.ts`) and the curation queue
 * (`stores/curation.ts`) launch the same run type against different surfaces,
 * and every sentence a reader saw along the way was written twice: the queued
 * notice with its contract-C11 explanation, the failure toast, the 300s
 * timeout and the reason for it. Two copies of a run's vocabulary is the
 * failure `run_types.RunType.outcomes` exists to stop at the other end of the
 * wire, and it had grown here instead.
 *
 * What the two do NOT share stays with them: the hover card keys pending state
 * by entity id and repaints a card, curation keys by surface and splices one
 * queue row. Neither is passed in here.
 */

/** How long to follow a lookup before giving up.
 *
 * 300s where `waitForRun` defaults to 180: a run queued behind three full
 * light-lane slots can legitimately wait longer than it runs, and a wait that
 * expires first reports a working run as a lost one. */
const LOOKUP_TIMEOUT_MS = 300_000

/** Announce a lookup that has just been accepted.
 *
 * `queued` is a real answer and not a failure: the light lane was full
 * (contract C11) and the run starts when a slot frees. Saying so is the
 * difference between a reader waiting and a reader pressing the button again.
 */
export function pushLookupStarted(surface: string, status?: string): void {
  useToastsStore().push({
    kind: 'running',
    text: status === 'queued' ? 'lookup queued; it starts when a slot frees' : 'looking it up',
    subject: surface,
    to: '/admin/runs',
    toLabel: 'watch',
  })
}

/** The Runs page has to see a lookup start.
 *
 * Both stores poll their own run rather than streaming it, so nothing else
 * refreshes the health probe that `In flight` reads. */
export function noteLookupLaunched(): void {
  void useRunsStore().launched()
}

/** A finished lookup, or the sentence explaining why there is not one.
 *
 * The error is RETURNED as well as toasted because the two callers keep it
 * differently: the hover card holds it on the term until the next attempt, so
 * a card closed and reopened still shows what happened, while curation lets
 * the toast be the whole record. */
export type LookupResult = { run: WaitedRun; error?: undefined } | { run?: undefined; error: string }

/**
 * Follow a lookup run to its end, toasting the failures on the way.
 *
 * A `cancelled` run returns as a run, not an error: nothing failed, and the
 * caller decides whether a cancelled lookup is worth refreshing for.
 */
export async function awaitLookup(
  runId: string,
  surface: string,
  onStatus?: (status: string) => void,
): Promise<LookupResult> {
  const toasts = useToastsStore()
  try {
    const run = await waitForRun(runId, { timeoutMs: LOOKUP_TIMEOUT_MS, onStatus })
    if (run.status === 'failed') {
      // Two different fallbacks on purpose. The toast is a sentence and reads
      // `lookup failed: unknown error`; the returned string is stored ON the
      // term and stands alone, where `unknown error` names nothing.
      toasts.push({
        kind: 'error',
        text: `lookup failed: ${run.error ?? 'unknown error'}`,
        subject: surface,
      })
      return { error: run.error ?? 'lookup failed' }
    }
    return { run }
  } catch (err) {
    const error = errorText(err, 'lookup failed')
    toasts.push({ kind: 'error', text: error, subject: surface })
    return { error }
  }
}
