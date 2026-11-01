<script setup lang="ts">
import { computed } from 'vue'

import SectionHead from '@/components/ui/SectionHead.vue'

/**
 * Surface primitive. Every boxed region in the app is a Panel -- there is no
 * second implementation, and adding one is a review failure (see CLAUDE.md).
 *
 * Flat by design: hairline border, no shadow, no elevation. The interface
 * should read as an instrument, not a card-based dashboard.
 */
const props = withDefaults(
  defineProps<{
    title?: string
    /**
     * `bare` is the default shape for in-page grouping: a label, a hairline,
     * and the content. No border, no radius, no fill.
     *
     * A border means "this floats, or you can press it". It had stopped meaning
     * anything — Settings was four stacked grey boxes with uppercase titles,
     * one of them 350px tall to hold a sentence and an empty state, which is
     * the card-based-dashboard register docs/DESIGN.md exists to reject.
     *
     * `raised`/`inset`/`overlay` survive for things that genuinely float.
     */
    variant?: 'bare' | 'raised' | 'inset' | 'overlay'
    padded?: boolean
    /**
     * Take the parent's remaining height and let the BODY scroll rather than
     * the page.
     *
     * This is the review-queue shape: a queue that runs off the bottom of the
     * screen loses its action bar, which is the one thing that must always be
     * reachable. A prop rather than three `:deep()` overrides reaching in from
     * `CurationView` -- CLAUDE.md asks for a prop when a primitive nearly
     * fits.
     */
    fill?: boolean
  }>(),
  { variant: 'raised', padded: true, fill: false },
)

/* The variant matrix. `--bare` is "a rule, not a box", so it drops the border
 * and radius but its HEADER keeps the bottom rule -- that asymmetry was three
 * separate descendant selectors and is the whole point of the variant.
 *
 * The header and body ELEMENTS survive in every variant: `--fill`'s three
 * coupled rules are keyed on them existing, and `CurationView` passes its
 * kind-filter chips through the header slot. Dropping either takes the merge
 * queue's action bar off screen. */
const SURFACE = {
  bare: 'border-0 rounded-none bg-transparent',
  raised: 'border border-border-subtle rounded-lg bg-surface-raised',
  inset: 'border border-border-subtle rounded-md bg-surface-inset',
  overlay: 'border border-border-default rounded-md bg-surface-overlay',
} as const

const panelClass = computed(() => [
  'panel',
  `panel--${props.variant}`,
  'min-w-0',
  SURFACE[props.variant],
  props.padded ? 'panel--padded' : '',
  // `min-h-0` is what actually lets the body scroll -- a flex item defaults to
  // `min-height: auto`, so without it the body grows to fit and never overflows.
  props.fill ? 'panel--fill flex flex-col min-h-0' : '',
])

const headerClass = computed(() => [
  'panel__header flex items-center justify-between gap-3 border-b border-b-border-subtle',
  props.variant === 'bare' ? 'px-0 pt-0 pb-2' : 'px-4 py-3',
  props.fill ? 'flex-none' : '',
])

const bodyClass = computed(() => [
  'panel__body',
  props.padded ? (props.variant === 'bare' ? 'pt-4 px-0 pb-0' : 'p-4') : '',
  props.fill ? 'grow min-h-0 flex flex-col' : '',
])
</script>

<template>
  <section :class="panelClass">
    <header v-if="title || $slots.header" :class="headerClass">
      <SectionHead v-if="title" as="h2" class="panel__title grow" :label="title" />
      <slot name="header" />
    </header>
    <div :class="bodyClass">
      <slot />
    </div>
  </section>
</template>
