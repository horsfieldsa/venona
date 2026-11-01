<script setup lang="ts">
/**
 * A number, and what it counts.
 *
 * This is the app's central typographic motif, because this is an instrument:
 * the counts, costs, ages and scores ARE the content, and setting them at the
 * same size and colour as the words around them buries that. A figure in mono
 * tabular at `--text-xl` over a `--text-2xs` uppercase label reads as an
 * instrument panel; the same figure at 13px inline reads as a footnote.
 *
 * Three hand-rolled variants of this existed — the brief's freshness ledger,
 * the run counters, the run-detail facts — with different sizes, colours and
 * label casing.
 */
withDefaults(
  defineProps<{
    value: string | number
    label: string
    /** `ok` for a reassuring figure, `high`/`critical` for one that wants
     *  attention. Neutral by default: most numbers are just numbers. */
    tone?: 'neutral' | 'ok' | 'high' | 'critical' | 'accent'
    size?: 'md' | 'lg'
  }>(),
  { tone: 'neutral', size: 'md' },
)

/** One hero figure per page, at most -- `lg` is the exception, not a size. */
const TONE = {
  ok: 'text-signal-ok',
  high: 'text-signal-high',
  critical: 'text-signal-critical',
  accent: 'text-accent-primary',
} as const
</script>

<template>
  <div class="stat flex flex-col gap-(--space-half) min-w-0">
    <!-- `.mono` in base.css carries `font-variant-numeric: tabular-nums`, which
         is what stops a column of these shifting as the digits change. -->
    <span
      class="value mono leading-tight"
      :class="[
        TONE[tone as keyof typeof TONE] ?? 'text-text-primary',
        `value--${size}`,
        size === 'lg' ? 'text-2xl' : 'text-xl',
      ]"
      >{{ value }}</span
    >
    <span
      class="label text-2xs tracking-label uppercase text-text-tertiary whitespace-nowrap"
      >{{ label }}</span
    >
  </div>
</template>
