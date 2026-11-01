import { describe, expect, it } from 'vitest'

import { verdictFor, verdictWithCount } from '@/lib/runOutcome'

/**
 * The rule every surface that reports a finished run renders through: an
 * execution problem wins, otherwise show the answer.
 *
 * Pinned here because it is the rule four surfaces render through, and a rule
 * that lives in four heads is a rule one of them gets wrong: a curator was
 * once told "found nothing new" about a lookup that had run out of budget.
 * ADR 0183.
 */
describe('verdictFor', () => {
  it('shows what a clean run found, in that kind of run’s words', () => {
    expect(
      verdictFor({ status: 'succeeded', outcome: 'nothing_found', outcome_text: 'not found' }),
    ).toEqual({ text: 'not found', tone: 'neutral', isProblem: false })
  })

  it('greens only a delivery', () => {
    expect(
      verdictFor({ status: 'succeeded', outcome: 'delivered', outcome_text: 'defined' }).tone,
    ).toBe('ok')
    // A sweep that landed forty of two hundred is correct, completed work.
    // Colouring it as a warning would rebuild the false alarm the split
    // removed.
    expect(
      verdictFor({ status: 'succeeded', outcome: 'some', outcome_text: 'some defined' }).tone,
    ).toBe('neutral')
  })

  it('lets an execution problem win over any answer', () => {
    // ⭐ A run that degraded AND delivered shows the degradation: a reader
    // whose machinery broke needs that first and nothing else. The other half
    // is stated beside it on the run's own page.
    const verdict = verdictFor({
      status: 'succeeded_partial',
      outcome: 'delivered',
      outcome_text: 'brief written',
    })
    expect(verdict).toEqual({ text: 'degraded', tone: 'high', isProblem: true })
  })

  it('never calls a run that found nothing degraded', () => {
    const verdict = verdictFor({
      status: 'succeeded',
      outcome: 'nothing_to_do',
      outcome_text: 'nothing new',
    })
    expect(verdict.isProblem).toBe(false)
    expect(verdict.tone).not.toBe('high')
  })

  it('opens the underscores on a value it has no phrase for', () => {
    // A row from before the phrases existed, or one read before `/runs/types`
    // has loaded. Slightly raw and TRUE beats a generic word that says nothing.
    expect(verdictFor({ status: 'succeeded', outcome: 'nothing_found' }).text).toBe('nothing found')
  })

  it('falls back to a bare word only when there is no outcome at all', () => {
    expect(verdictFor({ status: 'succeeded' }).text).toBe('finished')
  })

  it('reports queued and running rather than guessing an answer', () => {
    // Contract C11: `queued` is IN FLIGHT. Anything treating it as terminal
    // reports "found nothing" before anything has been looked up.
    expect(verdictFor({ status: 'queued' }).text).toBe('queued')
    expect(verdictFor({ status: 'running' }).tone).toBe('accent')
  })
})

describe('verdictWithCount', () => {
  it('prefers the fraction on a batch that landed part of its queue', () => {
    const verdict = verdictWithCount(
      { status: 'succeeded', outcome: 'some', outcome_text: 'some defined' },
      { done: 40, total: 182 },
    )
    expect(verdict.text).toBe('40 of 182')
  })

  it('leaves every other outcome its phrase', () => {
    expect(
      verdictWithCount(
        { status: 'succeeded', outcome: 'delivered', outcome_text: 'all defined' },
        { done: 182, total: 182 },
      ).text,
    ).toBe('all defined')
  })

  it('never lets a count outrank an execution problem', () => {
    expect(
      verdictWithCount(
        { status: 'failed', outcome: null, outcome_text: null },
        { done: 40, total: 182 },
      ).text,
    ).toBe('failed')
  })
})

describe('a partial row with no evidence behind it', () => {
  it('shows what it found rather than a degradation it cannot support', () => {
    // ⭐ 44 rows in the defence history look exactly like this: the status says
    // degraded, and there is not one warning to say what degraded. `ctx.warn`
    // appends BEFORE setting `ctx.degraded`, and that is the only route to
    // `succeeded_partial`, so this combination cannot be produced any more --
    // it can only have come from a lookup that found nothing back when the
    // status field was the only place to say so.
    const verdict = verdictFor({
      status: 'succeeded_partial',
      outcome: 'nothing_found',
      outcome_text: 'not found',
      warnings: [],
      error: null,
    })
    expect(verdict).toEqual({ text: 'not found', tone: 'neutral', isProblem: false })
  })

  it('still trusts a partial that brought its evidence', () => {
    const verdict = verdictFor({
      status: 'succeeded_partial',
      outcome: 'nothing_found',
      outcome_text: 'not found',
      warnings: [
        { level: 'warning', text: 'Tried 4 source(s) and read none: every host refused us.' },
      ],
    })
    expect(verdict.text).toBe('degraded')
  })

  it('does not call a run degraded on the strength of a note or a todo', () => {
    // The widening. A partial row whose only messages are the curation
    // backlog and a routine remark degraded in no way at all -- that
    // combination is what the level split exists to stop being written, and
    // this is where an already-written one stops being believed.
    const verdict = verdictFor({
      status: 'succeeded_partial',
      outcome: 'nothing_to_do',
      outcome_text: 'nothing new',
      warnings: [
        { level: 'todo', text: '42 name(s) to decide on: 42 containment' },
        { level: 'note', text: 'MOSA: folded duplicate readings into one' },
      ],
      error: null,
    })
    expect(verdict).toEqual({ text: 'nothing new', tone: 'neutral', isProblem: false })
  })

  it('still believes a partial that carries one degradation among its notes', () => {
    const verdict = verdictFor({
      status: 'succeeded_partial',
      outcome: 'delivered',
      outcome_text: 'brief written',
      warnings: [
        { level: 'note', text: 'nothing long enough to write about' },
        { level: 'warning', text: 'two feeds refused to fetch' },
      ],
    })
    expect(verdict.text).toBe('degraded')
  })

  it('never second-guesses a failure', () => {
    expect(verdictFor({ status: 'failed', outcome: null, warnings: [] }).text).toBe('failed')
  })
})

it('treats an absent warnings field as unknown, not as none', () => {
  // A caller that did not supply warnings has told us nothing. Reading that as
  // "there are none" would strip the degradation off every row that reaches
  // this without the field, which is the opposite of the point.
  const verdict = verdictFor({
    status: 'succeeded_partial',
    outcome: 'nothing_found',
    outcome_text: 'not found',
  })
  expect(verdict.text).toBe('degraded')
})
