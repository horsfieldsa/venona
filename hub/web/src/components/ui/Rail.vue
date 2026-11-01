<script setup lang="ts">
/**
 * The frame a vertical rail sits in: the inset ground, the radius, the inset,
 * and nothing else. Width, stickiness and any breakpoint at which it
 * disappears belong to the page that places it.
 *
 * `name` is not `label` lowercased and must stay its own prop: the smoke check
 * selects on `[data-rail=admin]`, and an attribute that stopped matching would
 * make it pass against an empty string rather than fail.
 */
defineProps<{ label: string; name: string }>()
</script>

<template>
  <nav
    :aria-label="label"
    :data-rail="name"
    class="rail flex flex-col self-start p-1 rounded-md bg-surface-inset"
  >
    <slot />
  </nav>
</template>
