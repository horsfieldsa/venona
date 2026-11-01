<script setup lang="ts">
import { computed } from 'vue'

/**
 * Small labelled token: entity types, statuses, counts, filters.
 *
 * `tone` maps to a semantic token rather than a literal color, so a chip is
 * themed by the same layer as everything else. Note that a chip ALWAYS carries
 * a text label -- meaning is never encoded by color alone (see docs/DESIGN.md).
 */
const props = withDefaults(
  defineProps<{
    tone?: 'neutral' | 'accent' | 'high' | 'critical' | 'ok' | 'info'
    /** Draws the dot marker; useful for status rather than classification. */
    dot?: boolean
    /**
     * Renders a <button> instead of a <span> and adds the pressed states.
     *
     * This exists because two views had grown a `.chip-btn` class — a second
     * implementation of this component, which CLAUDE.md prohibits outright.
     * Both default to false, so the existing 23 usages are provably inert.
     */
    interactive?: boolean
    selected?: boolean
    /** `sm` matches the denser relationship filters in the graph rail. */
    size?: 'md' | 'sm'
    /**
     * `bare` drops the border: a dot and coloured text, nothing else.
     *
     * Bordered-and-rounded has to mean something, and it had stopped: at one
     * point `/runs` carried nine identical outlined status pills, five outlined
     * stage pills, two outlined buttons, two outlined panels and three outlined
     * footer chips — forty-odd rectangles, none out-ranking any other. A border
     * now means "you can press this"; a status, a count or a stage marker is
     * not pressable, so it goes bare.
     */
    variant?: 'outline' | 'bare'
  }>(),
  {
    tone: 'neutral',
    dot: false,
    interactive: false,
    selected: false,
    size: 'md',
    variant: 'outline',
  },
)

const TONE = {
  neutral: 'text-text-tertiary',
  accent: 'text-accent-primary',
  high: 'text-signal-high',
  critical: 'text-signal-critical',
  ok: 'text-signal-ok',
  info: 'text-signal-info',
} as const

/* `--chip-dot` defaults on the ROOT, unconditionally, and `.chip__dot` consumes
 * it. Both halves stay unconditional and neither is scoped to a variant: a
 * default declared on the DOT instead of the root shadows the caller's inline
 * `:style="{ '--chip-dot': ... }"`, and every entity-type dot silently fell
 * back to grey while still looking plausible. See docs/adr/, "One button,
 * one chip". Scoping either half re-opens that bug for whichever variant misses
 * out -- which is why this is an arbitrary PROPERTY utility rather than a
 * conditional class. */
const classes = computed(() => {
  const bare = props.variant === 'bare'
  return [
    'chip',
    `chip--${props.tone}`,
    `chip--${props.variant}`,
    'inline-flex items-center gap-1 font-ui border rounded-sm leading-tight whitespace-nowrap',
    props.size === 'sm' ? 'chip--sm text-2xs' : 'text-xs',
    // Every property that varies is decided ONCE. Two utilities for the same
    // property have equal specificity, so listing `px-2` and `px-0` together
    // leaves Tailwind's sort order to pick -- and it picked `px-2`, which put
    // the border back on the borderless variant's footprint.
    bare ? 'px-0' : 'px-2',
    props.interactive ? 'py-(--space-half)' : 'py-(--space-half)',
    bare
      ? 'border-transparent'
      : props.interactive
        ? props.selected
          ? 'border-accent-primary'
          : 'border-border-default'
        : 'border-current',
    props.interactive
      ? [
          // A filter chip reads as a control, so colour means "off" or "on",
          // not a signal level.
          'chip--interactive h-control bg-transparent font-normal tracking-normal cursor-pointer',
          '[--chip-dot:var(--text-tertiary)]',
          props.selected
            ? 'chip--selected text-text-primary'
            : 'text-text-tertiary hover:text-text-secondary',
        ]
      : ['font-medium tracking-label [--chip-dot:currentColor]', TONE[props.tone]],
  ]
})
</script>

<template>
  <component
    :is="interactive ? 'button' : 'span'"
    :class="classes"
    :type="interactive ? 'button' : undefined"
    :aria-pressed="interactive ? selected : undefined"
  >
    <span
      v-if="dot"
      class="chip__dot w-(--dot-sm) h-(--dot-sm) rounded-[50%] bg-(--chip-dot)"
      aria-hidden="true"
    />
    <slot />
  </component>
</template>
