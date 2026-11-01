/**
 * The reader-facing "when is the next brief" sentence.
 *
 * The cases that earn a spec are the ones nothing else would catch: a schedule
 * that is DISABLED but still has a fire time is a real production deployment
 * (external cron), so blanking the line on `enabled` would hide it on exactly
 * the hosts that have a cadence to report; and `untilFrom` answers the bare
 * word 'imminent', which is ungrammatical spliced into a sentence.
 */
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useSchedule } from './useSchedule'

import { zone } from '@/lib/dates'
import { useHealthStore } from '@/stores/health'
import type { Health } from '@/types/health'

const NOW = '2026-08-06T12:00:00.000Z'

function withSchedule(schedule: unknown, offline = false): void {
  useHealthStore().data = {
    api: 'ok',
    offline,
    neo4j: { ok: true },
    claude: { ok: true },
    schedule,
  } as unknown as Health
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
  vi.setSystemTime(new Date(NOW))
  zone.value = 'UTC'
})

afterEach(() => {
  vi.useRealTimers()
  zone.value = null
})

describe('nextBriefLine', () => {
  it('says nothing before health has answered', () => {
    expect(useSchedule().nextBriefLine.value).toBe('')
  })

  it('says nothing when there is no fire time to report', () => {
    withSchedule({ enabled: true, cron: '0 6 * * *', timezone: 'UTC', next_run_at: null })
    expect(useSchedule().nextBriefLine.value).toBe('')
  })

  it('says nothing in offline replay, where no timer will fire', () => {
    withSchedule(
      { enabled: true, cron: '0 6 * * *', timezone: 'UTC', next_run_at: '2026-08-06T19:00:00Z' },
      true,
    )
    expect(useSchedule().nextBriefLine.value).toBe('')
  })

  it('names the countdown and the clock time', () => {
    withSchedule({
      enabled: true,
      cron: '0 6 * * *',
      timezone: 'America/Los_Angeles',
      next_run_at: '2026-08-06T19:00:00Z',
    })
    expect(useSchedule().nextBriefLine.value).toBe('Next brief in 7 hours, at 19:00 UTC.')
  })

  it('STILL reports when the in-process timer is off but a cron drives it', () => {
    withSchedule({
      enabled: false,
      cron: '0 6 * * *',
      timezone: 'America/Los_Angeles',
      next_run_at: '2026-08-06T19:00:00Z',
    })
    expect(useSchedule().nextBriefLine.value).toContain('Next brief in 7 hours')
  })

  it('renders the hour in the READER’S zone, not the server’s cron zone', () => {
    withSchedule({
      enabled: true,
      cron: '0 6 * * *',
      timezone: 'America/Los_Angeles',
      next_run_at: '2026-08-06T13:00:00Z',
    })
    zone.value = 'Pacific/Auckland'
    // 06:00 in Los Angeles is 01:00 the next day in Auckland. Naming the
    // server's hour would be naming an hour in somebody else's city.
    expect(useSchedule().nextBriefLine.value).toContain('at 01:00')
    expect(useSchedule().nextBriefLine.value).not.toContain('06:00')
  })

  it('gives a due brief its own sentence rather than splicing in “imminent”', () => {
    withSchedule({
      enabled: true,
      cron: '0 6 * * *',
      timezone: 'UTC',
      next_run_at: '2026-08-06T12:00:20Z',
    })
    expect(useSchedule().nextBriefLine.value).toBe('Next brief is due now.')
  })

  it('does the same for a fire time the poll has already fallen behind', () => {
    withSchedule({
      enabled: true,
      cron: '0 6 * * *',
      timezone: 'UTC',
      next_run_at: '2026-08-06T11:59:00Z',
    })
    expect(useSchedule().nextBriefLine.value).toBe('Next brief is due now.')
  })
})
