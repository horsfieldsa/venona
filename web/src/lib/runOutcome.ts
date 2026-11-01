/**
 * A finished run, in one phrase and one tone.
 *
 * Every surface that reports a run reads from here. Before this there were
 * four decoders: `StatusChip`'s tone map, `RunsView`'s `finishedText`,
 * `RunDetailView`'s `lookupResult`/`ingestResult`, and `stores/curation.ts`,
 * which guessed the words out of a status field that did not carry them:
 *
 *     runStatus === 'succeeded' ? 'lookup finished' : 'lookup found nothing new'
 *
 * That told a reader "found nothing new" whenever a lookup had actually run
 * out of budget, because `succeeded_partial` was the only word available for
 * both. ADR 0183 split the field; this is the one place the two halves are
 * turned back into a sentence.
 *
 * THE RULE, and it is the whole design: **an execution problem wins, otherwise
 * show the answer.** A reader scanning the history wants to know what a run
 * found; a reader whose run broke needs to know that first and nothing else.
 */

import type { RunStatus } from '@/types/run'

/** The tones `Chip` takes. Deliberately narrow: four meanings, four colours. */
export type RunTone = 'ok' | 'high' | 'critical' | 'accent' | 'neutral'

/**
 * What a run row shows.
 *
 * `text` is already in the reader's words — the server resolves it per kind
 * (`run_types.RunType.outcomes`, shipped as `outcome_text`), so "not found"
 * and "nothing new" are the same stored value seen by two kinds of reader.
 */
export interface RunVerdict {
  text: string
  tone: RunTone
  /** True when this is reporting execution rather than the answer. The detail
   *  page uses it to decide whether the warnings block leads. */
  isProblem: boolean
}

/**
 * Statuses that are not `succeeded`, in the words and tone they get.
 *
 * `degraded` rather than a softer word: `succeeded_partial` means a real
 * degradation and nothing else, so it is rare enough to be named plainly. A
 * run that simply found nothing reports that through its outcome and never
 * reaches this map. ADR 0183.
 */
const PROBLEM: Partial<Record<RunStatus, { text: string; tone: RunTone }>> = {
  succeeded_partial: { text: 'degraded', tone: 'high' },
  failed: { text: 'failed', tone: 'critical' },
  cancelled: { text: 'cancelled', tone: 'neutral' },
  running: { text: 'running', tone: 'accent' },
  queued: { text: 'queued', tone: 'neutral' },
}

/**
 * Outcome tones. Only `delivered` is green.
 *
 * Grey for the other three on purpose: a lookup that found nothing and a
 * sweep that landed forty of two hundred are both correct, completed work, and
 * colouring them as warnings would rebuild the exact false alarm this split
 * removed. Green means we got it, grey means it ran and there is nothing to
 * open, amber means something broke, red means it did not run.
 */
const OUTCOME_TONE: Record<string, RunTone> = {
  delivered: 'ok',
  some: 'neutral',
  nothing_found: 'neutral',
  nothing_to_do: 'neutral',
}

/** A run row, as much of one as this needs. */
export interface VerdictInput {
  status: string
  outcome?: string | null
  outcome_text?: string | null
  /**
   * The evidence behind a `succeeded_partial`. See `degradedWithoutEvidence`.
   *
   * Only `level` is read here, and the index signature keeps this structural:
   * a caller passing a whole `RunMessage` and a test passing the two fields it
   * cares about must both fit, and neither should have to widen a cast.
   */
  warnings?: { level?: string; [key: string]: unknown }[] | null
  error?: string | null
}

/**
 * A `succeeded_partial` row that carries NO degradation and NO error did not
 * degrade, whatever its status says.
 *
 * The invariant is structural: `ctx.warn` appends a `warning`-level message
 * and THEN sets `ctx.degraded`, and `ctx.degraded` is the only route to
 * `succeeded_partial`. So a degraded run always has at least one of them to
 * show, and a partial row with none is a row from before `runs.outcome`
 * existed -- when a lookup that simply found nothing had no other field to say
 * so in. 44 of them are in the defence history.
 *
 * It tests the LEVEL rather than the length, so it also covers a partial row
 * whose messages are all notes or todos: such a row claims a degradation it
 * never had. Safe because the one other writer of `succeeded_partial`,
 * `reap_interrupted`, always sets an `error`, which the guard below catches
 * first. comment-history: ok -- the pre-0018 rows named above are live data
 * this function exists to reconcile, not an earlier version of the code.
 *
 * Their outcome was recovered by migration 0018; their status was deliberately
 * left as written, since rewriting what a row said at the time is not this
 * split's business. This is where the two are reconciled for a reader: trust
 * the evidence on the row over a label that contradicts it. ADR 0183.
 */
export function degradedWithoutEvidence(run: VerdictInput): boolean {
  // `Array.isArray` and not `?.length ?? 0`: a caller that did not SUPPLY
  // warnings has told us nothing, and treating "not supplied" as "there are
  // none" would strip the degradation off every row that reaches this without
  // the field. Only an explicitly empty list is evidence of absence.
  return (
    run.status === 'succeeded_partial' &&
    !run.error &&
    Array.isArray(run.warnings) &&
    !run.warnings.some((m) => m.level === 'warning') &&
    !!run.outcome
  )
}

export function verdictFor(run: VerdictInput): RunVerdict {
  const problem = degradedWithoutEvidence(run) ? undefined : PROBLEM[run.status as RunStatus]
  if (problem) return { ...problem, isProblem: true }
  const tone = OUTCOME_TONE[run.outcome ?? ''] ?? 'neutral'
  if (run.outcome_text) return { text: run.outcome_text, tone, isProblem: false }
  // No phrase: an older row, a kind that declares none for what it found, or a
  // caller reached before `runTypes` finished loading. Open the underscores on
  // the stored value rather than showing a word that says nothing -- `nothing
  // found` is raw but TRUE, where a generic "finished" is neither. Same
  // philosophy as `runCopy.counterLabel`: a new value should read as slightly
  // raw, not vanish.
  if (run.outcome) return { text: run.outcome.replace(/_/g, ' '), tone, isProblem: false }
  // No outcome at all: a row written before the column existed.
  return { text: 'finished', tone: 'neutral', isProblem: false }
}

/**
 * The same verdict, with a count when the run has one.
 *
 * A sweep that landed forty of two hundred says so as a fraction rather than
 * as "some defined", because the fraction is the number anyone reading the
 * history is actually after. Only for `some`: on the other three outcomes the
 * phrase already says everything a count would.
 */
/**
 * Whether anything actually went wrong, for a surface deciding what to SHOW
 * rather than what to say.
 *
 * The same question `verdictFor` answers to pick between a status word and an
 * answer, exported so a page cannot arrive at a different one -- which is how
 * a run came to show `not found` in its header and `degraded` in its
 * breadcrumb, on the same screen, a centimetre apart.
 */
export function hasTrouble(run: VerdictInput): boolean {
  if (degradedWithoutEvidence(run)) return false
  return run.status !== 'succeeded' || !!run.error
}

export function verdictWithCount(
  run: VerdictInput,
  counts: { done?: number; total?: number },
): RunVerdict {
  const verdict = verdictFor(run)
  if (run.outcome !== 'some' || verdict.isProblem) return verdict
  const { done, total } = counts
  if (typeof done !== 'number' || typeof total !== 'number' || !total) return verdict
  return { ...verdict, text: `${done} of ${total}` }
}
