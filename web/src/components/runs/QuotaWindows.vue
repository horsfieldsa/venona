<script setup lang="ts">
import { computed } from 'vue'

import StatusBar from '@/components/marks/StatusBar.vue'

/**
 * How much of the subscription this instance has used, and when it comes back.
 *
 * This replaces a dollar total that was actively misleading. Venona runs on a
 * flat-rate subscription, so the money figure was API list price for tokens
 * that cost nothing extra at the margin, while the thing that actually stops a
 * run -- the usage window -- was reported nowhere. ADR 0057 settled that
 * argument in the backend and this is the front of it.
 *
 * The reading rides the model calls the instance was already making: the CLI
 * reports it on the same stream the run is read from, so nothing here costs a
 * request.
 *
 * NOT a new bar primitive. `marks/StatusBar` already draws a proportion at
 * `--spine-h` and already insists on a worded label, which is the rule that a
 * meter carried by width and colour alone fails.
 */

export interface QuotaWindow {
  name: string
  /** 0 to 1, as the CLI reports it. Scaled to a percentage here and nowhere
   *  else, so two copies of the conversion cannot disagree. */
  used: number
  resets_at: string
  representative?: boolean
}

const props = withDefaults(
  defineProps<{
    windows: QuotaWindow[]
    available?: boolean
    billingMode?: string
    /** Whether the configured service reports windows at all. */
    reported?: boolean
    serviceLabel?: string
  }>(),
  { available: false, billingMode: 'subscription', reported: true, serviceLabel: 'This service' },
)

/**
 * Reader words for wire words. `five_hour` and `seven_day` are the CLI's
 * spelling of a thing a person already has a name for, and "the last five
 * hours" is not what the window means -- it is the allowance that refills.
 */
const LABEL: Record<string, string> = {
  five_hour: 'Session limit',
  seven_day: 'Weekly limit',
  seven_day_opus: 'Weekly limit, deeper model',
  seven_day_sonnet: 'Weekly limit, faster model',
}

/**
 * Weekly first, then session, then anything the CLI grows later.
 *
 * Not sorted by fullness: the order of the two bars would then change under the
 * reader as the day went on, and a strip whose rows move is one you have to
 * read twice.
 */
const ORDER = ['seven_day', 'seven_day_opus', 'seven_day_sonnet', 'five_hour']

const rows = computed(() =>
  [...props.windows]
    .sort((a, b) => {
      const ai = ORDER.indexOf(a.name)
      const bi = ORDER.indexOf(b.name)
      return (ai < 0 ? ORDER.length : ai) - (bi < 0 ? ORDER.length : bi)
    })
    .map((w) => {
      const pct = Math.min(100, Math.max(0, Math.round(w.used * 100)))
      return {
        key: w.name,
        label: LABEL[w.name] ?? w.name.replace(/_/g, ' '),
        pct,
        resets: resetText(w.resets_at),
        // Colour is the SECOND signal, never the only one: the percentage is
        // written out beside every bar. Thresholds match the CLI's own warning
        // point so this strip and the terminal do not disagree about whether
        // you are close.
        tone:
          pct >= 90
            ? 'var(--signal-critical)'
            : pct >= 75
              ? 'var(--signal-high)'
              : 'var(--accent-primary)',
      }
    }),
)

/**
 * "resets 21:00" today, "resets Sun 21:00" beyond it.
 *
 * A bare percentage raises exactly one question and this is the answer to it.
 * Local time, because the reader is deciding whether to wait.
 */
function resetText(iso: string): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return ''
  // 24-hour, matching how every other time on this page is set. A locale that
  // would render "02:00 PM" puts eight glyphs and a space where four belong,
  // in a line that has to survive 390px.
  const time = at.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  })
  const sameDay = at.toDateString() === new Date().toDateString()
  if (sameDay) return `resets ${time}`
  return `resets ${at.toLocaleDateString([], { weekday: 'short' })} ${time}`
}
</script>

<template>
  <!-- Stacks at every width. Two labelled bars side by side hit the 390px floor
       as four wrapped fragments, and the vertical form reads the same at both
       sizes, so there is no second layout to keep honest. -->
  <div v-if="available && rows.length" class="flex flex-col gap-(--space-3)">
    <div v-for="row in rows" :key="row.key" class="flex flex-col gap-(--space-1) min-w-0">
      <div class="flex items-baseline justify-between gap-(--space-2)">
        <span class="text-2xs tracking-label uppercase text-text-tertiary">{{ row.label }}</span>
        <span class="mono text-xs text-text-secondary whitespace-nowrap">
          {{ row.pct }}% used<template v-if="row.resets"> · {{ row.resets }}</template>
        </span>
      </div>
      <StatusBar
        :label="`${row.label}, ${row.pct}% used, ${row.resets}`"
        :segments="[
          { value: row.pct, color: row.tone },
          { value: 100 - row.pct, color: 'transparent' },
        ]"
      />
    </div>
  </div>

  <!-- Said plainly rather than drawn as an empty bar. A meter at zero reads as
       an untouched allowance, which is the opposite of no reading.

       Two sentences, not one, and the difference is a promise. Under a
       subscription the first run fills this in, so saying so is true. Under an
       API key there is no window to fill it with, and telling a reader to wait
       for one would be an assurance nothing can keep. -->
  <p
    v-else-if="billingMode === 'api'"
    class="max-w-hint font-prose text-xs leading-normal text-text-tertiary"
  >
    There is no usage window under an API key. What the runs have cost is on the Runs history.
  </p>
  <!-- A service that will never report one. Saying "the first run will fill it
       in" here is a promise nothing can keep, on the strip a reader consults to
       decide whether to start another run. -->
  <p
    v-else-if="!reported"
    class="max-w-hint font-prose text-xs leading-normal text-text-tertiary"
  >
    {{ serviceLabel }} does not report how much of the subscription is left. What the runs have
    taken is on the Runs history.
  </p>
  <p v-else class="max-w-hint font-prose text-xs leading-normal text-text-tertiary">
    Venona has not been told how much of the subscription is left. It learns this from the runs
    it makes, so the first run will fill it in.
  </p>
</template>
