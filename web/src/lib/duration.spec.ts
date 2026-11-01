import { describe, expect, it } from 'vitest'

import { agoFrom, humanBetween, humanMs, untilFrom } from './duration'

describe('humanMs', () => {
  it('never renders sixty seconds', () => {
    // ⭐ The regression this file exists for. `Math.round` on the remainder
    // put "82m 60s" on the run detail page for a real 83-minute stage.
    expect(humanMs(4_979_864)).toBe('82m 59s')
    expect(humanMs(119_600)).toBe('1m 59s')
    expect(humanMs(120_000)).toBe('2m 0s')
  })

  it('reports sub-second work in milliseconds', () => {
    // "0.0s" is not an answer, and every fast stage would render as one.
    expect(humanMs(199)).toBe('199ms')
  })

  it('keeps a tenth of a second under a minute and drops it above', () => {
    expect(humanMs(45_148)).toBe('45.1s')
    expect(humanMs(5_858_507)).toBe('97m 38s')
  })

  it('is a dash rather than NaN for a value it cannot render', () => {
    expect(humanMs(Number.NaN)).toBe('—')
    expect(humanMs(-1)).toBe('—')
  })
})

describe('humanBetween', () => {
  it('is empty when either end is missing', () => {
    // A stage still running has no `finished_at`, and "NaN" on screen reads as
    // a broken page rather than as work in progress.
    expect(humanBetween('2026-01-01T00:00:00Z', null)).toBe('')
    expect(humanBetween(null, '2026-01-01T00:00:00Z')).toBe('')
  })

  it('measures across the two ISO timestamps', () => {
    expect(humanBetween('2026-01-01T00:00:00Z', '2026-01-01T00:01:30Z')).toBe('1m 30s')
  })
})

describe('agoFrom', () => {
  const NOW = new Date('2026-08-16T12:00:00Z').getTime()

  it('does not count seconds at people', () => {
    // "just now" rather than "12 seconds ago": nobody acts on the difference,
    // and a number that changes every second on a polled page reads as noise.
    expect(agoFrom('2026-08-16T11:59:48Z', NOW)).toBe('just now')
  })

  it('walks up to the coarsest unit that is still true', () => {
    expect(agoFrom('2026-08-16T11:55:00Z', NOW)).toBe('5 minutes ago')
    expect(agoFrom('2026-08-16T11:00:00Z', NOW)).toBe('1 hour ago')
    expect(agoFrom('2026-08-14T12:00:00Z', NOW)).toBe('2 days ago')
  })

  it('is empty rather than "NaN ago" for a missing or unparseable stamp', () => {
    expect(agoFrom(null, NOW)).toBe('')
    expect(agoFrom('not a date', NOW)).toBe('')
  })

  it('never reports the future as elapsed', () => {
    // Clock skew between the server's stamp and the browser is real; a run
    // that reads "-3 minutes ago" looks like a bug in the run, not the clock.
    expect(agoFrom('2026-08-16T12:05:00Z', NOW)).toBe('just now')
  })
})

describe('untilFrom', () => {
  const NOW = new Date('2026-08-16T12:00:00Z').getTime()

  it('walks up to the coarsest unit that is still true', () => {
    expect(untilFrom('2026-08-16T12:05:00Z', NOW)).toBe('in 5 minutes')
    expect(untilFrom('2026-08-16T13:00:00Z', NOW)).toBe('in 1 hour')
    expect(untilFrom('2026-08-18T12:00:00Z', NOW)).toBe('in 2 days')
  })

  it('never reports the past as upcoming', () => {
    // The fire time slips behind a stale poll for up to 30 seconds; "in -2
    // minutes" would read as a broken schedule rather than a stale page.
    expect(untilFrom('2026-08-16T11:58:00Z', NOW)).toBe('imminent')
    expect(untilFrom('2026-08-16T12:00:30Z', NOW)).toBe('imminent')
  })

  it('is empty rather than "in NaN" for a missing or unparseable stamp', () => {
    expect(untilFrom(null, NOW)).toBe('')
    expect(untilFrom('not a date', NOW)).toBe('')
  })
})
