<script setup lang="ts">
/**
 * A square control whose content is a glyph rather than a word.
 *
 * Replaces `CurationView`'s `.help`, the rail collapse chevrons, and the
 * per-row actions in the run history -- all of which were bare `<button>`s with
 * their own `background: none; border: none` boilerplate.
 *
 * `label` is REQUIRED and is not decoration: a glyph-only control has no
 * accessible name, and docs/DESIGN.md's rule that meaning is never carried by
 * appearance alone applies to shape exactly as it does to colour. It becomes
 * both the `title` (hover) and the `aria-label` (assistive tech).
 *
 * `as="span"` exists for the one legitimate case of nesting inside another
 * interactive element -- the settings gear lives in a `RouterLink` -- where a
 * real `<button>` would be invalid HTML. `as="a"` is for an action that is
 * genuinely a navigation and must stay a real link: middle-click, "open in new
 * window" and the status-bar preview all ride on `href`, which falls through.
 *
 * `disabled` is deliberately NOT declared as a prop: undeclared attributes
 * fall through to the real `<button>`, so the browser handles semantics and
 * focus. Declaring it would swallow the attribute and leave a disabled-looking
 * control that still clicks.
 */
withDefaults(
  defineProps<{
    /** Accessible name and hover title. Required; there is no glyph fallback. */
    label: string
    /**
     * Overrides the hover title alone -- the accessible name stays `label`.
     * For a disabled control whose tooltip carries the REASON it is disabled.
     */
    title?: string
    as?: 'button' | 'span' | 'a'
    /** Pressed/on state, e.g. a collapsed rail's chevron. */
    active?: boolean
    size?: 'md' | 'sm'
    /**
     * Draws the button's own box: a border and an inset ground.
     *
     * For where it sits AMONG CHIPS AND TOGGLES -- previous/next in the
     * ContextBar -- so it reads as one of them rather than as a glyph floating
     * between two outlined controls. Leave it off inside table rows and rail
     * headers, where a box on every row is the forty-rectangles problem again.
     */
    bordered?: boolean
  }>(),
  { as: 'button', active: false, size: 'md', bordered: false },
)
</script>

<template>
  <component
    :is="as"
    class="icon-btn inline-flex items-center justify-center p-0 border rounded-sm leading-none cursor-pointer transition-colors duration-fast ease-standard [&:hover:not(:disabled)]:text-text-primary disabled:opacity-40 disabled:cursor-default"
    :class="[
      size === 'sm' ? 'icon-btn--sm w-(--control-h-sm) h-(--control-h-sm) text-sm' : 'w-control h-control text-md',
      // One branch per property: a utility and its conditional override in two
      // places tie on specificity and Tailwind's sort order decides, not you.
      bordered ? 'icon-btn--bordered border-border-default bg-surface-inset' : 'border-transparent bg-transparent',
      // Resting colour is SECONDARY, not tertiary. A glyph carries less shape
      // than a word, so at the rung a label sits on it reads as disabled.
      active ? 'icon-btn--on text-accent-primary' : 'text-text-secondary',
    ]"
    :type="as === 'button' ? 'button' : undefined"
    :aria-pressed="as === 'button' && active ? true : undefined"
    :aria-label="label"
    :title="title ?? label"
  >
    <slot />
  </component>
</template>
