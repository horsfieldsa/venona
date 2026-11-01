<script setup lang="ts">
/**
 * Nothing here, and why.
 *
 * Replaces a `<td colspan="7">no runs yet</td>`, a `Panel title="Nothing
 * selected"`, and the several places a view rendered nothing at all. The
 * `hint` slot is the part that matters: "no runs yet" is a fact, "trigger one
 * with Run now" is an answer, and an empty screen with neither reads as broken.
 *
 * No illustration, no icon. This is an instrument.
 */
defineProps<{ title: string }>()
</script>

<template>
  <!-- Left-aligned and compact, not centred in a void. Centring made "no source
       is disabled" -- a one-line fact -- occupy 200px in the middle of a 350px
       region, which reads as a failure to load rather than as an answer. It
       hangs off the same left edge as everything around it. -->
  <div class="empty py-3">
    <p class="title text-sm text-text-secondary">{{ title }}</p>
    <p
      v-if="$slots.default"
      class="hint mt-1 max-w-hint font-prose text-xs tracking-tight text-pretty text-text-tertiary leading-normal"
    >
      <slot />
    </p>
    <div v-if="$slots.action" class="action mt-3">
      <slot name="action" />
    </div>
  </div>
</template>
