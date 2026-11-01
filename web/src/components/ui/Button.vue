<script setup lang="ts">
import { computed } from 'vue'

import Icon from '@/components/ui/Icon.vue'

/**
 * The one button. Thirteen button CSS blocks across six files collapse into it,
 * because four states each is where drift regenerates; ADR 0048 carries that
 * argument and the measurements that closed it.
 *
 * `disabled` is deliberately NOT declared as a prop. Undeclared attributes fall
 * through to the root element, so `<Button disabled>` reaches the real
 * `<button>` and the browser handles semantics and focus for us. Declaring it
 * would swallow the attribute and leave a disabled-looking button that still
 * clicks.
 */
const props = withDefaults(
  defineProps<{
    /** `primary` fills, `outline` draws a border, `text` is a bare link. */
    variant?: 'primary' | 'outline' | 'text'
    /**
     * Where the label sits when the button is WIDER than its content.
     *
     * A prop rather than a `justify-start` at the call site: `ALIGN` is then
     * the one place the property is set, and a second opinion in a `class`
     * attribute would tie on specificity, leaving Tailwind's sort order to
     * decide rather than the author.
     *
     * `start` is for a button stretched by a column -- a menu item, where every
     * label has to begin on the same vertical line or the icons stagger.
     */
    align?: 'center' | 'start'
    tone?: 'default' | 'critical'
    /**
     * `literal` opts out of the uppercase label treatment, for a button whose
     * text is DATA rather than a label -- the focus breadcrumb renders an
     * entity's own name, and "LOCKHEED MARTIN" is not that name.
     *
     * A prop rather than a `normal-case` class at the call site: `uppercase` is
     * the other branch of this one ternary, which is the only way the same
     * property gets chosen once, for the reason `align` gives.
     */
    case?: 'label' | 'literal'
    /**
     * Renders an `<a target="_blank">` instead of a `<button>`, with the same
     * everything else, for an action that is genuinely a NAVIGATION: a
     * `<button>` with a click handler loses middle-click, "open in new window"
     * and the status-bar preview. Hand-rolling the control instead is the other
     * half of the scar -- the extension's own footer buttons drifted to 20px
     * and lower case against the app's 28px and upper (2026-08-16).
     */
    href?: string
    /**
     * Name of a leading `Icon`, drawn before the label at a fixed 12px.
     *
     * A prop rather than a slot convention: one binding point keeps every
     * leading glyph the same size in the same order, which is why `Dialog` and
     * `RelevanceTailor` take their labels by prop too. A keyboard-first row
     * carries a `Kbd` cap and no glyph at all instead, never both (ADR 0159,
     * `docs/DESIGN.md`).
     */
    icon?: string
    /**
     * The name this button keeps when its words are too wide to show.
     *
     * Set it WITH `icon` and the label collapses below `sm`: the glyph stays,
     * the slot is hidden, and this string becomes the accessible name and the
     * tooltip. Without an icon it is only the accessible name, because a
     * button collapsed to nothing is not a control. The icon REPLACES the
     * words rather than joining them, cap and all: a `Kbd` lives in the slot,
     * so it collapses with the words it belongs to, and on a tablet there is no
     * keyboard to press `L` on anyway.
     *
     * Below `sm` (720) rather than `md` (860) deliberately: `ContextBar` wraps
     * at `md`, so a tablet in portrait gets a second row of full words, which
     * is more readable than a row of glyphs. Collapsing is the answer when
     * there is no room to wrap into. `ContextBar` scopes a second band of its
     * own on top of this one, reaching in through `btn-label`; its comment
     * carries why that band is scoped there rather than widened here.
     */
    label?: string
  }>(),
  { variant: 'primary', tone: 'default', case: 'label', align: 'center' },
)

/** Icon-only below `sm` needs both halves: something to show, something to call it. */
const collapses = computed(() => Boolean(props.icon && props.label))

/* The variant matrix, as data: every property that varies is chosen once, since
 * two utilities for the same property have equal specificity and listing both
 * hoping the later one wins is not a thing that works. Tone pairs with variant
 * because critical sets only the BACKGROUND -- measured 2026-08-06, before ADR
 * 0133 retuned the palette, the destructive confirm inside `Dialog` inherited
 * `--text-inverse` and landed at 3.66:1 on rust, the highest-stakes control in
 * the app failing AA. */
/* ONE SIZE, and it is `text-xs`: 12px, because the labels are uppercase and
 * every other uppercase label in this app -- section heads, table headers, stat
 * labels, the context bar -- is `text-2xs` or `text-xs` with `tracking-label`.
 * At 13px the buttons would be the largest label type in the interface, which
 * is the opposite of what a secondary control should be. Every Button is
 * `h-control` tall, and a `size` prop is not the missing half: the last one
 * changed the font size and nothing else, applied by no rule anyone could state
 * -- `DefinitionQueue` had `sm` Save/Cancel beside `md` Accept/Reject -- across
 * twenty of fifty-four call sites (2026-08-16). A scale nobody enforces is a
 * suggestion. */
const BASE =
  // The transition names its properties explicitly rather than stacking
  // `transition-opacity` and `transition-colors`: both set `transition-property`
  // and would tie on specificity, leaving Tailwind's sort order to decide which
  // one the button actually animates. Opacity is the disabled and primary-hover
  // channel; the colours are the outline-hover channel. Both, or the hover is
  // instant on one variant and smooth on the other.
  // `whitespace-nowrap` and `flex-none`, both load-bearing on a fixed-height
  // control: a label allowed to wrap renders two lines inside `--control-h`'s
  // 28px and clips them both, which is what "Run the pipeline" and "Review
  // sources first" did sharing a flex row. `flex-none` stops the row shrinking
  // them into that state at all.
  'inline-flex flex-none whitespace-nowrap items-center gap-2 h-control rounded-sm font-ui text-xs leading-[1.4] cursor-pointer transition-[opacity,color,background-color,border-color] duration-fast ease-standard disabled:opacity-40 disabled:cursor-default'

const ALIGN = { center: 'justify-center', start: 'justify-start' } as const

const VARIANT = {
  primary: 'px-3 py-1 border-0 font-medium',
  outline: 'px-3 py-1 bg-transparent border border-border-default',
  // A bare link, so no box -- but it keeps the shared height so it sits on the
  // same optical line as the boxed controls beside it in the bar. The underline
  // on hover is what tells a bare-coloured word it is clickable.
  text: 'p-0 bg-transparent border-0 [&:hover:not(:disabled)]:underline',
} as const

/* HOVER, chosen per variant AND tone.
 *
 * Every interactive primitive brightens under the pointer -- `IconButton` to
 * `--text-primary`, `Toggle`, `Chip interactive` and `SegmentedControl` a step
 * to `--text-secondary` -- and a boxed Button owes the same, most visibly on
 * the cards, where the footer is two outlined buttons over a panel.
 *
 * Not one rule for all four cells. `border-border-strong` would overwrite the
 * critical outline's red border, and brightening critical text to
 * `--text-primary` would drop the warning colour on the way in -- so critical
 * fills instead, with the same `bg-signal-critical` / `text-text-inverse` pair
 * the primary variant uses, for the contrast reason recorded there.
 *
 * `:not(:disabled)` on every one. `reset` on the graph sits disabled at rest
 * and must not light up under the pointer; that is the whole point of drawing
 * it disabled rather than hiding it.
 */
const HOVER_PRIMARY = '[&:hover:not(:disabled)]:opacity-90'

const COLOUR = {
  // The primary action should be the most legible thing on screen: measured
  // 2026-08-06, `--accent-muted` read washed out at 5.09:1 against 6.5:1 here.
  //
  // BOTH FILLS TAKE `--text-inverse`, and the critical one is the reason to
  // say so. Measured 2026-09-14 from `tokens.css`: on `--signal-critical`,
  // `--text-primary` is 3.53:1 on paper and 3.92:1 at night, both under AA,
  // while `--text-inverse` is 5.52 and 4.57. The 2026-08-06 reading above
  // predates ADR 0133's palette rework, which is why the two disagree.
  // `tests/test_contracts.py` holds the pair.
  primary: {
    default: `bg-accent-primary text-text-inverse ${HOVER_PRIMARY}`,
    critical: `bg-signal-critical text-text-inverse ${HOVER_PRIMARY}`,
  },
  outline: {
    default:
      'text-text-secondary [&:hover:not(:disabled)]:text-text-primary [&:hover:not(:disabled)]:border-border-strong',
    critical:
      'text-signal-critical border-signal-critical [&:hover:not(:disabled)]:bg-signal-critical [&:hover:not(:disabled)]:text-text-inverse',
  },
  text: { default: 'text-accent-primary', critical: 'text-signal-critical' },
} as const

const classes = computed(() => [
  'btn',
  `btn--${props.variant}`,
  `btn--${props.tone}`,
  BASE,
  ALIGN[props.align],
  VARIANT[props.variant],
  COLOUR[props.variant][props.tone],
  props.case === 'literal' ? 'normal-case' : 'uppercase tracking-label',
])
</script>

<template>
  <!-- `no-underline` on the link form only: `text` variant sets
       `hover:underline`, and a link would otherwise arrive underlined at rest
       from the browser's own stylesheet, which reads as a different control. -->
  <a
    v-if="href"
    class="btn no-underline"
    :class="classes"
    :href="href"
    :aria-label="label"
    :title="collapses ? label : undefined"
    target="_blank"
    rel="noopener noreferrer"
  >
    <Icon v-if="icon" :name="icon" :size="12" />
    <!-- One wrapper, not a class on the slot: the slot holds the words AND
         any `Kbd` cap, and both go together or the cap is left floating
         beside a glyph. `max-sm:hidden` rather than removing the node, so
         the accessible name and every text-matching probe still see it. -->
    <span v-if="collapses" class="btn-label sm:contents max-sm:hidden">
      <slot />
    </span>
    <slot v-else />
  </a>
  <button
    v-else
    type="button"
    class="btn"
    :class="classes"
    :aria-label="label"
    :title="collapses ? label : undefined"
  >
    <Icon v-if="icon" :name="icon" :size="12" />
    <span v-if="collapses" class="btn-label sm:contents max-sm:hidden">
      <slot />
    </span>
    <slot v-else />
  </button>
</template>
