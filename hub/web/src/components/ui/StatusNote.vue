<script setup lang="ts">
/**
 * An inline notice. `role="alert"` on the error tone and nowhere else: a note
 * that is merely informative should not interrupt a screen reader mid-sentence.
 */
withDefaults(defineProps<{ kind?: 'info' | 'error' | 'success' }>(), { kind: 'info' })

const BODY_TONE = {
  info: 'text-text-secondary',
  error: 'text-signal-critical',
  success: 'text-signal-ok',
} as const

const FRAME = {
  info: 'bg-accent-wash border-l-accent-muted',
  error: 'bg-surface-inset border-l-signal-critical',
  success: 'bg-surface-inset border-l-signal-ok',
} as const
</script>

<template>
  <div
    class="note border-l-2 px-3 py-2"
    :class="FRAME[kind]"
    :role="kind === 'error' ? 'alert' : undefined"
  >
    <p
      class="note__body font-prose text-sm leading-normal tracking-tight text-pretty"
      :class="BODY_TONE[kind]"
    >
      <slot />
    </p>
  </div>
</template>
