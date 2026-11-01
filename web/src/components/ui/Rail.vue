<script setup lang="ts">
/**
 * The frame every vertical rail sits in.
 *
 * There are three -- Settings, Admin, and the guide's contents column, whose
 * markup already carried the comment "SAME SHAPE AS THE ADMIN AND SETTINGS
 * RAILS" above six utilities spelled out a third time. `RailLink` was extracted
 * because that treatment had three implementations; this is the same argument
 * one level out, for the box around the rows rather than the rows.
 *
 * It holds the ground, the radius and the inset, and nothing else. Width,
 * stickiness and any breakpoint at which a rail disappears belong to the page
 * that places it, and arrive as ordinary classes -- the guide's rail is a
 * sticky fixed-width column, the other two are grid children.
 *
 * `name` IS NOT `label` LOWERCASED, and must stay its own prop:
 * `scripts/ui_smoke.py` selects on `[data-rail=settings]` and
 * `[data-rail=admin]`, and both checks read `?.innerText || ''`, so an
 * attribute that stopped matching would make them pass against an empty string
 * rather than fail.
 */
defineProps<{
  /** Names the rail for assistive tech: "Settings", "Admin", "Contents". */
  label: string
  /** The stable lowercase identifier behind `data-rail`. */
  name: string
}>()
</script>

<template>
  <nav
    :aria-label="label"
    :data-rail="name"
    class="rail flex flex-col self-start p-1 rounded-md bg-surface-inset"
  >
    <slot name="header" />
    <slot />
  </nav>
</template>
