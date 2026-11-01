import { computed } from 'vue'

import { dayTimeLabel, timeLabel, zoneAbbrev } from '@/lib/dates'
import { untilFrom } from '@/lib/duration'
import { useHealthStore } from '@/stores/health'

/**
 * The daily schedule, as one shared derivation off the health poll — the same
 * shape as `useServiceHealth`, and for the same reason: `useAppPolling`
 * refreshes health every 30s, so nothing here polls anything itself.
 *
 * `untilLabel` reads the clock at evaluation time and re-evaluates when the
 * poll replaces `health.data`, so its resolution is the poll interval. That is
 * the right coarseness on purpose: the label's units are minutes at their
 * finest, and a countdown ticking faster than the data behind it implies a
 * precision the page does not have.
 *
 * `enabled: false` with a non-null `next_run_at` is a real state, not a
 * contradiction: the in-process timer is off and an external cron drives the
 * trigger endpoint, so the timestamp is the yaml cron's stated cadence.
 */
export function useSchedule() {
  const health = useHealthStore()

  const schedule = computed(() => health.data?.schedule)

  const enabled = computed(() => schedule.value?.enabled === true)

  const nextRunAt = computed(() => schedule.value?.next_run_at ?? null)

  const nextRunLabel = computed(() => dayTimeLabel(nextRunAt.value))

  const untilLabel = computed(() => untilFrom(nextRunAt.value, Date.now()))

  const lastDailyAt = computed(() => schedule.value?.last_daily_at ?? null)

  /**
   * "Next brief in 7 hours, at 01:00 NZST" -- the reader-facing sentence.
   *
   * KEYED ON `next_run_at`, NEVER ON `enabled`. `enabled: false` with a
   * timestamp is a real deployment, not a contradiction: the in-process timer
   * is off and an external cron drives the trigger endpoint, which is the
   * documented pattern for a host that sleeps. Branching on `enabled` the way
   * the operator's Runs page does would blank this line on exactly the
   * deployments that have a cadence to report. `RunsView` says "off" because
   * an operator needs to know the timer is idle; a reader does not, and it is
   * an operator's word.
   *
   * The clock time comes from `next_run_at`, which is an absolute instant, and
   * NOT from `schedule.timezone` -- that is the SERVER's cron zone. A 06:00
   * cron in Los Angeles is 01:00 in Auckland, and naming the hour in somebody
   * else's city is worse than naming no hour at all.
   *
   * `untilFrom` answers 'imminent' rather than a count under 45 seconds, and
   * clamps an overdue timestamp to zero, so the countdown gets its own
   * sentence instead of being spliced into this one.
   */
  const nextBriefLine = computed(() => {
    const at = nextRunAt.value
    if (health.data?.offline || !at) return ''
    const until = untilFrom(at, Date.now())
    if (!until || until === 'imminent') return 'Next brief is due now.'
    const named = zoneAbbrev(at)
    return `Next brief ${until}, at ${timeLabel(at)}${named ? ` ${named}` : ''}.`
  })

  return {
    schedule,
    enabled,
    nextRunAt,
    nextRunLabel,
    untilLabel,
    lastDailyAt,
    nextBriefLine,
  }
}
