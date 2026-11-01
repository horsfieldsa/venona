<script setup lang="ts">
/**
 * A keyboard hint.
 *
 * Declared as a primitive because it had already been implemented three times —
 * identically — in `MergeReview`, `DefinitionQueue` and `CurationView`. That is
 * exactly the multiplication `Button.vue` was written to stop, and it regrows
 * the moment a fourth surface wants one.
 *
 * `muted` is for hints sitting inside a filled `primary` button, where the
 * default border reads as a second element rather than an annotation.
 */
withDefaults(defineProps<{ muted?: boolean }>(), { muted: false })
</script>

<template>
  <!-- `align-baseline` is not decoration: a hint must never change the height of
       the row it annotates. -->
  <kbd
    class="kbd font-mono text-2xs leading-none align-baseline border rounded-sm px-1 py-(--space-half)"
    :class="
      muted ? 'kbd--muted border-current text-inherit opacity-60' : 'border-border-default text-text-tertiary'
    "
    ><slot
  /></kbd>
</template>
