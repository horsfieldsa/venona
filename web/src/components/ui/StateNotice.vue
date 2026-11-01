<script setup lang="ts">
/**
 * A whole screen that is not the thing you asked for: 403, offline, 404.
 *
 * WHY THIS IS NOT `EmptyState`, WHICH ALREADY EXISTS.
 *
 * `EmptyState` answers "this region has nothing in it" and its own comment
 * commits to being "left-aligned and compact, not centred in a void" -- because
 * centring a one-line fact in 200px of nothing reads as a failure to load. That
 * is exactly right for "no source is disabled" inside a settings panel.
 *
 * These three are the opposite case. They ARE the failure to load, they occupy
 * the page rather than a region, and the void is the honest shape for them.
 * Widening `EmptyState` with a `variant` to cover both would give one component
 * two contradictory layout commitments and lose the reason either was written.
 *
 * Every one of the three names what happened, in the signal colour that says how
 * bad it is, and offers EXACTLY ONE thing to do. A screen that reports a failure
 * and offers three options is asking the reader to diagnose it.
 */
import Panel from '@/components/ui/Panel.vue'

withDefaults(
  defineProps<{
    /** Maps to a signal colour. `neutral` is for the states that are not a
     *  fault -- a 404 on a date with no brief is a fact, not an error. */
    tone?: 'critical' | 'high' | 'neutral'
    /** The machine-readable half: `403 · not allow-listed`. */
    status: string
    heading: string
  }>(),
  { tone: 'neutral' },
)

/**
 * Static, never `text-signal-${tone}`. Tailwind scans source TEXT, so an
 * interpolated class is never generated and the failure renders as an element
 * with no colour -- which looks like a data bug, not a styling one (CLAUDE.md).
 */
const TONE = {
  critical: { text: 'text-signal-critical', border: 'border-signal-critical' },
  high: { text: 'text-signal-high', border: 'border-signal-high' },
  neutral: { text: 'text-text-tertiary', border: 'border-border-strong' },
} as const
</script>

<template>
  <Panel variant="raised" class="notice p-5">
    <!-- An outlined square, not an icon. The icon set is a UI vocabulary and a
         glyph here would have to mean "bad" in general, which none of them do.
         A bordered box in the signal colour carries the severity and nothing
         else. Sized from `--control-h-sm` rather than a literal: it sits on the
         same optical scale as the smallest control in the app. -->
    <div
      class="badge w-(--control-h-sm) h-(--control-h-sm) border rounded-sm"
      :class="TONE[tone].border"
      aria-hidden="true"
    />

    <p class="status mt-3 font-ui text-2xs uppercase tracking-label" :class="TONE[tone].text">
      {{ status }}
    </p>

    <h1 class="mt-2 font-ui text-xl font-medium tracking-tight leading-tight text-text-primary">
      {{ heading }}
    </h1>

    <p class="mt-3 max-w-hint font-prose text-base leading-normal text-text-tertiary">
      <slot />
    </p>

    <div v-if="$slots.action" class="action mt-5">
      <slot name="action" />
    </div>
  </Panel>
</template>
