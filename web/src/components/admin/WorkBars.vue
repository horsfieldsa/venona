<script setup lang="ts">
import { computed, ref } from 'vue'

/**
 * How much model work this instance did, by day.
 *
 * Calls, not dollars. On a flat-rate subscription the money is API list price
 * for tokens nothing is billed for, and charting it invites a reader to manage
 * a budget that does not exist -- while the shape a reader actually wants from
 * a month of bars, which days were busy, is carried just as well by the call
 * count. The window that genuinely binds is on the Runs page. ADR 0057.
 *
 * MEASURED, NEVER MANAGED. `llm/budget.py` and `ratelimit.py` both argue the
 * same history, so nothing here caps, alerts or colours a day red for being
 * busy. It is a read.
 *
 * Inline SVG on `LatencyBars`' conventions: a `viewBox` rather than a CSS
 * height (which is also how this avoids the arbitrary-dimension rule -- an
 * `h-[120px]` on a chart is exactly the literal `lint_tokens.py` bans), colours
 * as `var(--…)` and never a JS colour table, and a readout below rather than a
 * native `<title>` tooltip that waits a second and cannot be styled.
 *
 * A day that spent nothing is a GAP, not a missing bar: the endpoint returns
 * only days with rows, so the series is padded here. Without that, a quiet
 * fortnight would draw as a dense run of bars and read as continuous activity.
 */
export interface Day {
  day: string
  cost_usd: number
  calls: number
  cost_basis?: string
}

const props = defineProps<{ days: Day[]; span: number }>()

const W = 640
const H = 96
const GAP = 1

const hovered = ref<Day | null>(null)

/**
 * Oldest to newest, one slot per day in the window, zeros included.
 *
 * The window ends TODAY rather than on the last day that spent something.
 * Ending it on the newest row would slide a quiet week off the right-hand edge,
 * so "nothing has run since Tuesday" -- the fact worth seeing -- would render
 * identically to "it ran last night".
 */
const series = computed<Day[]>(() => {
  // SUMMED PER DAY, because the endpoint returns one row per day PER COST
  // BASIS -- a subscription's shadow price and a metered bill are different
  // quantities and it refuses to add them. This chart draws CALLS, which add
  // fine across both, so the split is collapsed here. Keying a Map straight
  // off the rows would have kept whichever basis sorted last and quietly
  // dropped the other service's calls from every day of a month that spanned
  // a switch.
  const byDay = new Map<string, Day>()
  for (const row of props.days) {
    const key = row.day.slice(0, 10)
    const seen = byDay.get(key)
    if (seen) {
      seen.calls += row.calls
      seen.cost_usd += row.cost_usd
    } else {
      byDay.set(key, { day: key, calls: row.calls, cost_usd: row.cost_usd })
    }
  }
  const today = Date.parse(`${new Date().toISOString().slice(0, 10)}T00:00:00Z`)
  const out: Day[] = []
  for (let i = props.span - 1; i >= 0; i--) {
    const day = new Date(today - i * 86_400_000).toISOString().slice(0, 10)
    out.push(byDay.get(day) ?? { day, cost_usd: 0, calls: 0 })
  }
  return out
})

const total = computed(() => series.value.reduce((sum, d) => sum + d.calls, 0))
const peak = computed(() => Math.max(1, ...series.value.map((d) => d.calls)))
const slot = computed(() => W / Math.max(1, series.value.length))

function calls(value: number): string {
  return `${value} call${value === 1 ? '' : 's'}`
}

/** The row the reader opened this for is the total; the hover names one day. */
const readout = computed(() => {
  const day = hovered.value
  if (!day) return `${calls(total.value)} over ${props.span} days`
  return `${day.day.slice(0, 10)} · ${calls(day.calls)}`
})
</script>

<template>
  <div class="spend">
    <!-- Sized by its own attributes and clamped with `max-w-full`, exactly as
         `LatencyBars` is. Stretching it with `w-full` scales the viewBox, so a
         wide window drew a 145px-tall band of mostly nothing -- and an explicit
         CSS height would be the `h-[120px]` the token lint bans. -->
    <svg
      :viewBox="`0 0 ${W} ${H}`"
      :width="W"
      :height="H"
      class="max-w-full"
      role="img"
      :aria-label="`model work over ${span} days, ${calls(total)} in total`"
    >
      <g
        v-for="(day, i) in series"
        :key="day.day"
        @pointerenter="hovered = day"
        @pointerleave="hovered = null"
      >
        <!-- Full-height hit area: aiming at a $0.02 bar is aiming at two
             pixels, and the empty days have no bar to aim at at all. -->
        <rect :x="i * slot" :width="slot" :height="H" fill="transparent" />
        <rect
          :x="i * slot"
          :y="H - Math.max(day.calls > 0 ? 1 : 0, (day.calls / peak) * H)"
          :width="Math.max(1, slot - GAP)"
          :height="Math.max(day.calls > 0 ? 1 : 0, (day.calls / peak) * H)"
          :fill="hovered === day ? 'var(--accent-primary)' : 'var(--accent-muted)'"
        />
      </g>
      <!-- The baseline, so a window with no spend at all still reads as a chart
           with nothing in it rather than as a region that failed to render. -->
      <rect :y="H - 1" :width="W" height="1" fill="var(--border-subtle)" />
    </svg>

    <p class="mono mt-2 text-xs text-text-secondary">{{ readout }}</p>
  </div>
</template>
