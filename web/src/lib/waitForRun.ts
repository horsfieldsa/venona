import { CancelledError, apiGet } from '@/api/client'
import type { Run } from '@/types/run'

/**
 * Poll one short-lived run to completion.
 *
 * Seconds, not minutes: a lookup or a definition takes 25-60s, and polling beats
 * opening an SSE stream for a single run that will be over before the page
 * changes. The long pipeline runs get the stream; these get this.
 *
 * THE RULE THAT IS EASY TO GET WRONG, and which both copies of this carried in a
 * comment because it had already been got wrong once: `queued` is IN FLIGHT.
 * Contract C11 made several runs possible at once, so a run can sit queued
 * behind a full lane for a while. Testing `!== 'running'` alone takes a run that
 * has not started yet for a finished one — and the caller then reports "no
 * definition found" before anything has been looked up.
 *
 * There were two near-identical copies, in `stores/curation.ts` and
 * `components/entity/EntityHoverCard.vue`. This is the hover card's, which is
 * the richer of the two: it reports status transitions and returns the finished
 * run. Curation discarded both and only awaited.
 */
const POLL_MS = 1500
const DEFAULT_TIMEOUT_MS = 180_000
/**
 * How many polls in a row may fail before the wait gives up.
 *
 * Four at `POLL_MS` is about six seconds of outage — long enough to ride out a
 * dropped connection or an API restart, short enough that a genuinely broken
 * server does not hold the caller to the full timeout. One failed poll says
 * nothing about the run: the run is on the server, and the thing that broke was
 * the question, not the work.
 *
 * The backend probe's retry-once is the same reasoning at a different scale
 * (ADR 0155).
 */
const MAX_CONSECUTIVE_FAILURES = 4

/** Terminal states. Everything else means "still going". */
function settled(status: string): boolean {
  return status !== 'running' && status !== 'queued'
}

export interface WaitForRunOptions {
  timeoutMs?: number
  /** Called on every poll, so a caller can show `queued` and `running` as
   *  different things rather than one undifferentiated spinner. */
  onStatus?: (status: string) => void
  /**
   * Give up early.
   *
   * Neither copy could be cancelled, so a hover card closed two seconds into a
   * lookup kept polling for the full three minutes. The loop exits quietly
   * rather than throwing: a caller that aborted is not waiting for an answer.
   */
  signal?: AbortSignal
}

/**
 * What a caller gets back.
 *
 * `outcome` and `outcome_text` because the settled branch already returns the
 * whole `Run` and callers needed them: without them a caller could only ask
 * `status === 'succeeded'` and had to GUESS the words for everything else,
 * which is how `stores/curation.ts` came to tell a curator "found nothing new"
 * about a lookup that had run out of budget. Both are absent on the synthetic
 * cancelled/timed-out returns below, which have no run to report.
 */
export interface WaitedRun {
  status: string
  error?: string | null
  outcome?: string | null
  outcome_text?: string | null
}

export async function waitForRun(
  runId: string,
  { timeoutMs = DEFAULT_TIMEOUT_MS, onStatus, signal }: WaitForRunOptions = {},
): Promise<WaitedRun> {
  const deadline = Date.now() + timeoutMs
  let consecutiveFailures = 0

  while (Date.now() < deadline) {
    if (signal?.aborted) return { status: 'cancelled', error: null }
    await new Promise((resolve) => setTimeout(resolve, POLL_MS))
    if (signal?.aborted) return { status: 'cancelled', error: null }

    try {
      const run = await apiGet<Run>(`/runs/${encodeURIComponent(runId)}`, { signal })
      onStatus?.(run.status)
      if (settled(run.status)) return run
      consecutiveFailures = 0
    } catch (err) {
      // A cancelled poll is not a failed run.
      if (err instanceof CancelledError) return { status: 'cancelled', error: null }
      consecutiveFailures += 1
      if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) throw err
      // The deadline still governs: failures burn wall-clock like any other
      // poll, so a flapping server cannot extend the wait past `timeoutMs`.
    }
  }

  return { status: 'failed', error: 'timed out' }
}
