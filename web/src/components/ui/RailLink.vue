<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

/**
 * One row of a sidebar rail: an inset panel of `border-l-2` rows where the
 * current one carries the accent edge and the selected ground.
 *
 * WHY THIS EXISTS. That treatment had three implementations -- the settings
 * rail's top-level destinations, the same rail's indented document links, and
 * `GuideView`'s contents rail, whose comment already said "SAME SHAPE AS
 * `SettingsRail`" and then restated all six utilities. Splitting Settings and
 * Admin into two rails would have made it four. `docs/DESIGN.md`: a second
 * implementation of an existing primitive is a review failure.
 *
 * NOT `ListRail`. That one is full-bleed, bordered and collapsible, pinned to
 * the viewport edge for the graph's long lists. This is a row inside a panel
 * that ends where its content ends. They look adjacent and are not; merging
 * them would give the graph a rail it cannot collapse.
 *
 * A LINK, never a button. Every row here is a destination -- a route or an
 * anchor -- so it must carry an href a reader can copy, middle-click or open in
 * a new tab. `to` renders a `RouterLink`, `href` a plain `<a>`; the anchor form
 * also carries `aria-current`, because it is doing the work `RouterLink` does
 * for itself.
 *
 * The tone lives ENTIRELY in one `:class` ternary. A static `text-text-tertiary`
 * with a conditional `text-text-primary` split across two attributes ties on
 * specificity, and Tailwind's sort order picks the winner rather than this file.
 * The static `class` holds only properties the ternary never touches -- border
 * WIDTH, padding, font -- so the two cannot collide.
 */
const props = withDefaults(
  defineProps<{
    /** A route. Renders a `RouterLink`. */
    to?: string
    /** An in-page anchor. Renders an `<a>` and carries `aria-current`. */
    href?: string
    active?: boolean
    /** The nested form: a document under a group header, not a destination. */
    indent?: boolean
  }>(),
  { to: undefined, href: undefined, active: false, indent: false },
)

/**
 * The destination, bound as ONE object so that `href` is ABSENT rather than
 * undefined on the `RouterLink` form.
 *
 * `:to` and `:href` side by side looks harmless and is not. `RouterLink`
 * computes its own href and renders it; a fallthrough attribute worth
 * `undefined` then REMOVES the attribute it just wrote. Every row in the
 * Settings and Admin rails came out as an `<a>` with no href, which costs the
 * pointer cursor, keyboard focus, middle-click and "copy link address" -- while
 * still navigating on click, which is exactly what kept it invisible.
 */
const destination = computed(() =>
  props.to
    ? { to: props.to }
    : { href: props.href, 'aria-current': props.active ? 'true' : undefined },
)
</script>

<template>
  <component
    :is="to ? RouterLink : 'a'"
    v-bind="destination"
    class="border-l-2 no-underline hover:no-underline"
    :class="[
      indent
        ? 'file flex flex-col gap-(--space-half) py-2 pr-3 pl-6'
        : 'rail__item px-3 py-2 font-ui text-sm',
      active
        ? 'on border-l-accent-primary bg-surface-selected text-text-primary'
        : 'border-l-transparent text-text-tertiary hover:bg-surface-base hover:text-text-secondary',
    ]"
  >
    <slot />
  </component>
</template>
