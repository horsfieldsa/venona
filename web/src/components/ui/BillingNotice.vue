<script setup lang="ts">
/**
 * A persistent, actionable banner about money, above the reading surface.
 *
 * WHY THIS IS NOT `StatusNote` OR `StateNotice`, both of which already exist.
 *
 * `StateNotice` is "a whole screen that is not the thing you asked for" and
 * occupies the page. That is exactly right for the paywall SCREEN, and this
 * component is not that: a reader with a failing card can still read, and a
 * full-page interruption would take the product away from somebody we are
 * trying to keep.
 *
 * `StatusNote` is deliberately borderless with a 2px left rule, and its docblock
 * says why: "a border means 'this floats, or you can press it' and a status note
 * is neither, it is an annotation on the thing above it." This one floats above
 * unrelated content and carries a button, so it is both of the things that rule
 * excludes. Its `kind` union is also `error | info | success`, with no amber for
 * the state that matters most here.
 *
 * Widening either would give one component two contradictory layout
 * commitments, which is the argument `StateNotice` itself makes for not being
 * folded into `EmptyState`.
 *
 * WEIGHT IS A LADDER, and that is the whole design. A modal on the first failed
 * payment gets dismissed reflexively and teaches people to ignore the next one,
 * so `quiet` may be dismissed, `persistent` may not, and only the last retry
 * before a subscription ends earns anything louder.
 */
import Button from '@/components/ui/Button.vue'
import type { BillingNotice } from '@/lib/billing'

const props = defineProps<{ notice: BillingNotice }>()
defineEmits<{ act: []; dismiss: [] }>()

/**
 * Static maps, never `bg-${tone}`. Tailwind scans source TEXT, so an
 * interpolated class is never generated and the failure renders as an element
 * with no colour, which looks like a data bug rather than a styling one
 * (CLAUDE.md). `StateNotice`'s own `TONE` map is the pattern.
 */
const FRAME = {
  ok: 'bg-surface-inset border-signal-ok',
  accent: 'bg-accent-wash border-accent-muted',
  high: 'bg-surface-inset border-signal-high',
  critical: 'bg-surface-inset border-signal-critical',
  neutral: 'bg-surface-inset border-border-default',
} as const

const TEXT = {
  ok: 'text-signal-ok',
  accent: 'text-text-primary',
  high: 'text-signal-high',
  critical: 'text-signal-critical',
  neutral: 'text-text-secondary',
} as const
</script>

<template>
  <!-- `alert` only on the two that are genuinely wrong. Announcing every state
       makes the live region useless, which is the discipline StatusNote sets. -->
  <div
    class="flex flex-wrap items-center gap-3 border-b px-4 py-2 sm:px-6"
    :class="FRAME[props.notice.tone]"
    :role="props.notice.urgent ? 'alert' : 'status'"
  >
    <p
      class="min-w-0 flex-1 font-prose text-sm leading-normal tracking-tight text-pretty"
      :class="TEXT[props.notice.tone]"
    >
      {{ props.notice.text }}
    </p>
    <div class="flex shrink-0 items-center gap-2">
      <Button v-if="props.notice.action" variant="outline" @click="$emit('act')">
        {{ props.notice.action }}
      </Button>
      <!-- Dismissal is keyed on the notice AND its date by the caller, so a NEW
           notice always returns. A permanently silenced payment failure is a
           cancellation nobody meant to make. -->
      <Button
        v-if="props.notice.weight === 'quiet'"
        variant="text"
        @click="$emit('dismiss')"
      >
        Dismiss
      </Button>
    </div>
  </div>
</template>
