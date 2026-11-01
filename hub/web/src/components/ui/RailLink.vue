<script setup lang="ts">
import { RouterLink } from 'vue-router'

/**
 * One row of a sidebar rail: the accent left edge and selected ground that mean
 * "you are here".
 *
 * `border-l-transparent` on the inactive row rather than no border at all, so
 * nothing shifts by two pixels when the selection moves.
 *
 * The tone lives ENTIRELY in one `:class` ternary. A static `text-text-tertiary`
 * with a conditional `text-text-primary` split across two attributes ties on
 * specificity, and Tailwind's sort order picks the winner rather than this file.
 * The static `class` holds only properties the ternary never touches.
 *
 * A `RouterLink`, never a button: every row is a destination, so it carries an
 * href a reader can copy, middle-click or open in a new tab. `aria-current` is
 * `RouterLink`'s own, which is why it is not written here.
 */
defineProps<{ to: string; active: boolean }>()
</script>

<template>
  <RouterLink
    :to="to"
    class="rail__item border-l-2 px-3 py-2 font-ui text-sm no-underline hover:no-underline"
    :class="
      active
        ? 'on border-l-accent-primary bg-surface-selected text-text-primary'
        : 'border-l-transparent text-text-tertiary hover:bg-surface-base hover:text-text-secondary'
    "
  >
    <slot />
  </RouterLink>
</template>
