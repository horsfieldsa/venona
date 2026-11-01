<script setup lang="ts">
/**
 * A page that is a rail beside a pane.
 *
 * IT WRAPS NEITHER SLOT: both children have to be direct grid children or the
 * two columns collapse into one.
 *
 * Below `md` the grid is one column, so the rail becomes a full-width block of
 * rows above the pane. That is what the app does, so both surfaces degrade the
 * same way rather than each inventing a phone layout.
 */
</script>

<template>
  <div class="rail-layout grid grid-cols-1 gap-6 pt-4 md:grid-cols-[var(--field-lg)_minmax(0,1fr)]">
    <slot name="rail" />
    <slot />
  </div>
</template>
