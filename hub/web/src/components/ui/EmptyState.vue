<script setup lang="ts">
/**
 * Left-aligned and compact, never centred in a void: this sits under a heading
 * that already said what the region is, so it only has to say there is nothing
 * in it yet.
 */
defineProps<{ title: string }>()
</script>

<template>
  <div class="empty py-3">
    <p class="title text-sm text-text-secondary">{{ title }}</p>
    <p
      v-if="$slots.default"
      class="hint mt-1 max-w-hint font-prose text-xs tracking-tight text-pretty text-text-tertiary leading-normal"
    >
      <slot />
    </p>
  </div>
</template>
