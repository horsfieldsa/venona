<script setup lang="ts">
import { computed } from 'vue'

/**
 * The app's `Button`, class for class.
 *
 * ONE SIZE, and it is `text-xs`. `web/src/components/ui/Button.vue` records why
 * the `size` prop was removed: two values that changed the font size and
 * nothing else, applied by no rule anyone could state, at twenty of fifty-four
 * call sites. A scale nobody enforces is a suggestion.
 *
 * Every property that varies is chosen in ONE branch. A static
 * `text-text-secondary` with a conditional override in a second attribute ties
 * on specificity, and Tailwind's sort order picks the winner rather than this
 * file -- which is why `uppercase` lives in the case ternary rather than in
 * `BASE` beside a `normal-case` override.
 *
 * `disabled` is deliberately not declared: an undeclared attribute falls
 * through to the real element, so `<Button disabled>` reaches the `<button>`
 * and the browser handles semantics and focus. Declaring it would swallow the
 * attribute and leave a disabled-LOOKING control that still takes clicks.
 */
const props = withDefaults(
  defineProps<{
    /** `primary` fills, `outline` draws a border, `text` is a bare link. */
    variant?: 'primary' | 'outline' | 'text'
    tone?: 'default' | 'critical'
    /** For a label that is DATA rather than a word: a domain's own name. */
    case?: 'label' | 'literal'
    /** Renders an `<a>` with the same everything else. */
    href?: string
    /**
     * Submits the `<form>` around it instead of firing a click handler.
     *
     * The app's Button has no such prop and does not need one: nothing there
     * posts a form. This surface has one that must -- signing out clears a
     * cookie, and it has to work whether or not this bundle is still healthy,
     * so it is a real form post rather than a `fetch`. The default stays
     * `button`, because a bare `<button>` inside a form submits it by accident
     * and that is the failure this prop exists to make deliberate.
     */
    submit?: boolean
  }>(),
  {
    variant: 'primary',
    tone: 'default',
    case: 'label',
    href: undefined,
    submit: false,
  },
)

const BASE =
  'inline-flex flex-none whitespace-nowrap items-center justify-center gap-2 h-control rounded-sm font-ui text-xs leading-[1.4] cursor-pointer transition-[opacity,color,background-color,border-color] duration-fast ease-standard disabled:opacity-40 disabled:cursor-default'

const VARIANT = {
  primary: 'px-3 py-1 border-0 font-medium',
  outline: 'px-3 py-1 bg-transparent border border-border-default',
  text: 'p-0 bg-transparent border-0 [&:hover:not(:disabled)]:underline',
} as const

const HOVER_PRIMARY = '[&:hover:not(:disabled)]:opacity-90'

const COLOUR = {
  primary: {
    default: `bg-accent-primary text-text-inverse ${HOVER_PRIMARY}`,
    critical: `bg-signal-critical text-text-primary ${HOVER_PRIMARY}`,
  },
  outline: {
    default:
      'text-text-secondary [&:hover:not(:disabled)]:text-text-primary [&:hover:not(:disabled)]:border-border-strong',
    critical:
      'text-signal-critical border-signal-critical [&:hover:not(:disabled)]:bg-signal-critical [&:hover:not(:disabled)]:text-text-primary',
  },
  text: { default: 'text-accent-primary', critical: 'text-signal-critical' },
} as const

const classes = computed(() => [
  'btn',
  `btn--${props.variant}`,
  BASE,
  VARIANT[props.variant],
  COLOUR[props.variant][props.tone],
  props.case === 'literal' ? 'normal-case' : 'uppercase tracking-label',
])
</script>

<template>
  <a v-if="href" class="btn no-underline" :class="classes" :href="href"><slot /></a>
  <button v-else :type="submit ? 'submit' : 'button'" class="btn" :class="classes">
    <slot />
  </button>
</template>
