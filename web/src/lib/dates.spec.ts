/**
 * One file, several formatters, and the thing worth testing is that they
 * render different things — and that none of them renders `Invalid Date` at a
 * reader.
 */
import { afterEach, describe, expect, it } from 'vitest'

import {
  count,
  dayLabel,
  dayTimeLabel,
  isoDayLabel,
  timeLabel,
  zone,
  zoneAbbrev,
} from './dates'

// `zone` is module state, so it is shared by every spec in this worker. A
// leaked override would re-zone assertions in files that never mention time
// zones and never explain the failure.
afterEach(() => {
  zone.value = null
})

describe('dates', () => {
  it('renders nothing for a missing or unparseable value', () => {
    for (const format of [dayTimeLabel, dayLabel, isoDayLabel, timeLabel]) {
      expect(format(null)).toBe('')
      expect(format(undefined)).toBe('')
      expect(format('')).toBe('')
      expect(format('not a date')).toBe('')
    }
  })

  it('keeps the renderings distinct', () => {
    const iso = '2026-03-04T14:15:00Z'
    expect(dayTimeLabel(iso)).not.toBe(dayLabel(iso))
    // The one with a time in it is the longer of the two.
    expect(dayTimeLabel(iso).length).toBeGreaterThan(dayLabel(iso).length)
  })

  it('separates thousands, and treats a missing count as zero', () => {
    expect(count(1499)).toBe((1499).toLocaleString(undefined))
    expect(count(null)).toBe('0')
    expect(count(undefined)).toBe('0')
  })
})

describe('the reader’s zone', () => {
  // 22:10 UTC: the same instant is the same day in London and the next day in
  // Auckland, which is exactly the case a UTC-sliced label got wrong.
  const LATE = '2026-08-06T22:10:00Z'

  it('renders one instant as a different clock time in each zone', () => {
    zone.value = 'UTC'
    expect(timeLabel(LATE)).toBe('22:10')

    zone.value = 'Asia/Tokyo'
    expect(timeLabel(LATE)).toBe('07:10')
  })

  it('rolls the date over for a reader far enough east', () => {
    zone.value = 'Europe/London'
    expect(isoDayLabel(LATE)).toBe('2026-08-06')

    zone.value = 'Pacific/Auckland'
    expect(isoDayLabel(LATE)).toBe('2026-08-07')
  })

  it('keeps isoDayLabel ISO-shaped, so a mono column stays aligned', () => {
    zone.value = 'America/Los_Angeles'
    expect(isoDayLabel(LATE)).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('names the zone, and names a different one when the reader moves', () => {
    zone.value = 'UTC'
    const utc = zoneAbbrev(LATE)
    zone.value = 'Asia/Tokyo'
    expect(zoneAbbrev(LATE)).not.toBe(utc)
    expect(zoneAbbrev(LATE)).not.toBe('')
  })

  it('follows the reader across a DST boundary rather than being appended', () => {
    zone.value = 'Europe/London'
    // Same zone, one instant in BST and one in GMT.
    expect(zoneAbbrev('2026-08-06T12:00:00Z')).not.toBe(zoneAbbrev('2026-01-06T12:00:00Z'))
  })

  it('falls back to the browser’s zone when there is no override', () => {
    zone.value = null
    expect(timeLabel(LATE)).toBe(
      new Date(LATE).toLocaleTimeString(undefined, {
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23',
      }),
    )
  })
})
