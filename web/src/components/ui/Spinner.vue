<script setup lang="ts">
/** Indeterminate progress. Honours prefers-reduced-motion via base.css. */
withDefaults(defineProps<{ size?: number; label?: string }>(), {
  size: 14,
  label: 'Loading',
})
</script>

<template>
  <span
    class="spinner inline-block border-2 border-border-default border-t-accent-primary rounded-pill animate-spin-slow"
    role="status"
    :aria-label="label"
    :style="{ width: `${size}px`, height: `${size}px` }"
  />
</template>
