<script setup lang="ts">
import { computed, ref, useAttrs } from 'vue'

/**
 * The one text field.
 *
 * `Button.vue` records what this file is for: thirteen CSS blocks across six
 * files, three byte-identical, five of six `.link` rules already diverged. Text
 * inputs were at that stage and no lint rule was watching them, so the divergence
 * was further along than it looked. Five fields that all mean "a boxed field in a
 * control bar" were written five different ways:
 *
 *   RequestAccessView  boxed h-control w-full             px-2 … font-ui   text-sm
 *   LookupView         boxed h-control w-full             px-2 … font-ui   text-sm
 *   SearchView         boxed h-control w-full max-w-measure px-2 … font-ui text-sm
 *   DataView           boxed mt-2 h-control w-full        px-2 … font-mono text-sm
 *   ArchiveView        filter w-(--field-lg) px-2 py-(--space-half) … text-xs
 *
 * Two of five identical, one differing only in font, one in width, and one
 * sharing no structural class at all -- not even `h-control`, in a ContextBar
 * `#controls` slot where `tokens.css` says every control shares `--control-h`.
 *
 * TWO VARIANTS, because `base.css` already defines two and this expresses them
 * rather than replacing them. `bare` is the instrument treatment -- underlined,
 * mono, no box -- which `base.css` applies to any input NOT carrying `.boxed`.
 * `boxed` is the filled field. Adding `.boxed` is the whole of what this
 * component does about the box; the rest is the shared metrics.
 *
 * THE FIELD FILLS ITS BOX; THE CALLER SIZES THE BOX. `BASE` is `w-full`, so a
 * `w-(--field-lg)` passed as a class here would be a utility and its override
 * in two different attributes -- they tie on specificity and Tailwind's sort
 * order decides, which is the prohibition CLAUDE.md states. Width belongs on a
 * wrapper around this, which is what `FilterField` does.
 *
 * NO SCOPED CSS, and there cannot be any: `lint_tokens.py` fails a component
 * outside its two-entry allowlist that carries a style block. (Phrased without
 * the literal tag, because that check greps the whole file and would flag this
 * sentence as the violation -- exactly what it already does to `LoginView`'s
 * docstring about buttons.)
 */
const props = withDefaults(
  defineProps<{
    modelValue: string | number | null | undefined
    /** `boxed` fills; `bare` is `base.css`'s underlined instrument. */
    variant?: 'boxed' | 'bare'
    /**
     * Chosen HERE rather than at the call site, for the reason `Button`'s `case`
     * prop exists: `font-mono` and `font-ui` are the same property, so a base
     * utility and a call-site override tie and Tailwind picks the winner. Two
     * real cases -- DataView's confirm phrase is mono because it is a literal
     * string being copied, RequestAccess's paragraph field is prose.
     *
     * Named `family` rather than `font`: it selects a FAMILY token, and
     * `lint_tokens.py`'s CSS `font:` shorthand check reads a `font:` prop
     * declaration as the shorthand it bans.
     */
    family?: 'ui' | 'mono' | 'prose'
    type?: string
    /** Reserve room inside the box for a leading glyph. See `PAD`. */
    leading?: boolean
    /** Reserve room inside the box for a trailing control. See `PAD`. */
    trailing?: boolean
  }>(),
  { variant: 'boxed', family: 'ui', type: 'text', leading: false, trailing: false },
)

const emit = defineEmits<{ (e: 'update:modelValue', value: string | number): void }>()

/* ONE HEIGHT: `--control-h`, rising to `--control-h-touch` under the phone
 * breakpoint.
 *
 * Same argument as `Button`'s one size -- a control bar holds chips, buttons
 * and fields together, and `tokens.css` gives them one height precisely so the
 * bar cannot show four optical lines. The touch rung is in `VARIANT.boxed`
 * rather than at a call site for the reason stated above about width: a height
 * utility here and a responsive override there tie, and the tie is silent. */
const BASE = 'w-full font-normal text-text-primary placeholder:text-text-tertiary'

const VARIANT = {
  boxed:
    'boxed h-control max-xs:h-(--control-h-touch) bg-surface-inset border border-border-default rounded-sm text-sm',
  // Nothing at all: `base.css` styles a bare input by element selector, and
  // adding utilities here would be a second opinion about the same properties.
  bare: '',
} as const

/* Horizontal padding, a branch of its own rather than part of `VARIANT`,
 * because `FilterField` insets the box on one or both sides to make room for a
 * search glyph and a clear button. A call site cannot add that padding: `px`
 * here and `pl` there are the same property twice, and the tie is silent.
 *
 * The room is reserved whether or not the clear button is on screen, so the
 * caret does not jump sideways on the first keystroke.
 */
const PAD = {
  none: 'px-2',
  leading: 'pl-5 pr-2',
  trailing: 'pl-2 pr-5',
  both: 'px-5',
} as const

const FAMILY = { ui: 'font-ui', mono: 'font-mono', prose: 'font-prose' } as const

const padding = computed(() => {
  if (props.leading && props.trailing) return PAD.both
  if (props.leading) return PAD.leading
  if (props.trailing) return PAD.trailing
  return PAD.none
})

const classes = computed(() => [
  BASE,
  VARIANT[props.variant],
  // The bare variant is styled by element selector in `base.css`, which owns
  // its padding too; a value here would be a second opinion about it.
  props.variant === 'boxed' ? padding.value : '',
  // The bare variant takes its family from `base.css` (mono), so a font class
  // here would fight it. One branch per property.
  props.variant === 'boxed' ? FAMILY[props.family] : '',
])

/**
 * `type="number"` emits a Number.
 *
 * Chosen inside the component because `v-model.number` does not survive a
 * component boundary -- the modifier applies to the native element, and
 * `PathFinder`'s depth field relies on it.
 */
function onInput(event: Event) {
  const el = event.target as HTMLInputElement
  emit('update:modelValue', props.type === 'number' ? el.valueAsNumber : el.value)
}

/**
 * An accessible name is the caller's job, but not having one is this
 * component's business to notice.
 *
 * It does not render a `<label>`: half the call sites have no visible label by
 * design (a search box in a control bar), and the two that do -- RequestAccess
 * and DataView -- already pair `for`/`id` with two different label treatments.
 * So this warns in dev instead. A PLACEHOLDER IS NOT A NAME: it disappears on
 * first keystroke and is not exposed as one by every screen reader.
 */
const attrs = useAttrs()
if (import.meta.env.DEV) {
  if (!attrs['aria-label'] && !attrs['aria-labelledby'] && !attrs.id) {
    console.warn('[Venona] <Input> with no accessible name', attrs.placeholder ?? '')
  }
}
/**
 * The element, and a `focus()` for callers that need it.
 *
 * A `<script setup>` component is CLOSED: a template ref on `<Input ref="x">`
 * yields the component instance, not the `<input>`, so `x.value?.focus()`
 * optional-chains into a silent no-op. `DefinitionQueue`'s edit field was
 * annotated `ref<HTMLInputElement | null>` and did exactly that -- it never took
 * focus when you pressed `E`, and nothing type-checked it, because the
 * annotation asserted the very thing it was wrong about.
 *
 * Exposing `focus` rather than the element keeps the seam one verb wide: a
 * caller that wants the field to have focus says so, and cannot reach past this
 * component into the DOM to do anything else.
 */
const el = ref<HTMLInputElement | null>(null)
defineExpose({ focus: () => el.value?.focus() })
</script>

<template>
  <!-- `disabled` is deliberately NOT a declared prop, exactly as on `Button`:
       undeclared attributes fall through to the real element, so `<Input
       disabled>` reaches the `<input>` and the browser handles semantics and
       focus. Declaring it would swallow the attribute and leave a
       disabled-LOOKING field that still accepts typing. -->
  <input
    ref="el"
    :type="type"
    :value="modelValue"
    :class="classes"
    @input="onInput"
  />
</template>
