<script setup lang="ts">
/**
 * The operator log: what a run said about itself, as it went.
 *
 * `log`, `warning` and `error` events only -- `stores/runs.ts` peels
 * `model_message` out before this ever sees it, so this stays a short list of
 * decisions rather than the firehose the transcript is.
 *
 * Takes its events as a prop rather than reading the store, so the live panel
 * and a finished run's page render the same component from the same data. They
 * differ only in whether it starts open: watching this is the point of a live
 * run, and it is a thing you go looking for afterwards.
 */
import type { RunEvent } from '@/types/run'

defineProps<{ events: RunEvent[] }>()

const LINE_TONE = { error: 'text-signal-critical', warning: 'text-signal-high' } as const

function timeOf(iso: string | null) {
  return iso ? iso.slice(11, 19) : ''
}

function bodyOf(event: { data: Record<string, unknown> }): string {
  const message = event.data?.message
  if (typeof message === 'string') return message
  if (!event.data || !Object.keys(event.data).length) return ''
  return JSON.stringify(event.data).slice(0, 160)
}
</script>

<template>
  <div class="log max-h-(--scroll-md) overflow-y-auto">
    <div
      v-for="event in events"
      :key="event.seq"
      class="line grid grid-cols-[4.5rem_6rem_1fr] gap-2 py-(--space-half) text-text-secondary"
    >
      <span class="text-text-tertiary mono text-xs">{{ timeOf(event.ts) }}</span>
      <span
        class="mono text-xs"
        :class="[`t-${event.type}`, LINE_TONE[event.type as keyof typeof LINE_TONE] ?? '']"
        >{{ (event.stage || event.type).replace(/_/g, ' ') }}</span
      >
      <!-- `JSON.stringify` only when there is something to stringify: an event
           with an empty payload rendered as the literal `{}`. -->
      <span class="text-xs">{{ bodyOf(event) }}</span>
    </div>
  </div>
</template>
