<script setup lang="ts">
/**
 * A page that is a rail beside a pane.
 *
 * Nine views wrote this grid out by hand and only three of them agreed: three
 * carried a class hook nothing referenced, one appended a frame's height
 * chain, and one applied the whole thing behind a condition.
 *
 * IT WRAPS NEITHER SLOT, and that is the constraint that makes it usable at
 * all. Both children have to be direct GRID children or the two columns
 * collapse into one; and under `ViewShell`'s `scroll="none"` the rule
 * `.view--scroll-none > :deep(:last-child)` needs this to stay the direct last
 * child of the shell. `ViewShell`'s own docblock records the same failure for
 * `CurationView`.
 */
withDefaults(
  defineProps<{
    /**
     * Take the remaining height and let the grid scroll on a phone, for a page
     * that is a fixed frame rather than a document. `CurationView` is the
     * caller: under `ViewShell`'s `scroll="none"` this grid is the shell's
     * direct last child and takes its `flex:1;min-height:0`, and `fill` is what
     * carries that height down to the `Panel` whose inner region scrolls.
     *
     * At `max-md` the grid is one column, so the rail and the pane stack and
     * `align-content: stretch` divides the frame between them -- measured 2026-08-31 on
     * curation, the pane goes from 669px to 300px with the rail above it, and
     * the pane's own action bar stays inside the frame either way. An explicit
     * `auto minmax(0,1fr)` row list was tried and moved neither number, so what
     * is left is `max-md:overflow-y-auto`: the escape hatch for the case where
     * the stack genuinely does not fit, since this element is the only thing
     * between a fixed frame and content with nowhere to go.
     *
     * Named for `Panel`'s prop of the same job.
     */
    fill?: boolean
  }>(),
  { fill: false },
)
</script>

<template>
  <div
    class="rail-layout grid grid-cols-1 gap-6 pt-4 md:grid-cols-[var(--field-lg)_minmax(0,1fr)]"
    :class="fill ? 'h-full min-h-0 max-md:overflow-y-auto' : ''"
  >
    <slot name="rail" />
    <slot />
  </div>
</template>
