<script setup lang="ts">
/**
 * A labelled region header: the quiet uppercase label that leads a rail, a
 * panel, a card section or a settings group, plus whatever control belongs on
 * the right of it.
 *
 * There were SIX typographic treatments of this one role across 27 sites in 21
 * files -- `text-2xs font-medium` (Panel, the brief rail, settings groups,
 * table headers), `text-xs font-semibold` (ListRail, the entity card, the hover
 * card, the keyboard map, PathFinder), `text-sm` with no weight
 * (ModelTranscript), and three more carrying no weight at all. Thirteen sites
 * at 10px against thirteen at 11px is not a density decision, it is a coin
 * flip, and it is exactly the kind of drift a four-state control multiplies.
 *
 * The one treatment is Panel's, because Panel is the container this role
 * appears in most and its title is the canonical instance. 10px and 11px at
 * label tracking are not meaningfully different sizes; having both is the
 * problem, not the choice between them.
 *
 * NOT a heading by default. `as` keeps a real `<h2>`/`<h3>`/`<h4>` where the
 * markup already had one -- the label is chrome, but a section heading is
 * document structure, and collapsing every one of them to a `<span>` would
 * flatten the outline for anything reading it.
 *
 * Owns no outer margin. Separation belongs to the parent, which is where it
 * legitimately varies -- a page masthead needs more air than a rail head -- and
 * every caller already has a `gap` or a margin doing that job.
 */
withDefaults(
  defineProps<{
    label?: string
    /** Draws the hairline that marks the region boundary. A rule means "this is
     *  where the region starts", per docs/DESIGN.md; a border would mean the
     *  header floats or is pressable, which it is not. */
    rule?: boolean
    as?: 'span' | 'h2' | 'h3' | 'h4'
  }>(),
  { rule: false, as: 'span' },
)
</script>

<template>
  <div
    class="section-head flex items-center justify-between gap-3"
    :class="rule ? 'pb-2 border-b border-b-border-subtle' : ''"
  >
    <component
      :is="as"
      v-if="label || $slots.label"
      class="section-head__label text-2xs font-medium tracking-label uppercase text-text-tertiary"
    >
      <slot name="label">{{ label }}</slot>
    </component>
    <slot />
  </div>
</template>
