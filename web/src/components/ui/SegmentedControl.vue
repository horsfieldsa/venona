<script setup lang="ts" generic="T extends string | number">
/**
 * One of N, as one object.
 *
 * Three places were spelling this out with `Chip interactive :selected` in a
 * flex row: Curation's three queues, the graph's scope, the graph's spacing.
 * `GraphModeControl` even documented the intent -- "ONE OF N, so chips in a
 * group rather than a row of buttons -- a segmented control" -- and then drew
 * three separate boxes, which is a row of buttons. Nothing said the options
 * belonged to each other, or that picking one un-picked the rest; `merges`,
 * `definitions` and `history` read as three things you could press rather than
 * three states the page could be in.
 *
 * So: ONE frame, segments inside it. The border encloses the whole set, which is
 * the part that carries the meaning -- these are alternatives, and exactly one
 * of them is true.
 *
 * SQUARE, NOT A PILL. This was `rounded-pill` on both the frame and the
 * segments, which made the app's most-used control the one shape nothing else
 * in the interface has -- a lozenge on a page whose every other control is a
 * 3px rectangle. `--radius-pill` is for things that are round BECAUSE they are
 * round (the spinner, the switch track); a segmented control is not one of
 * them. The design bundle draws it square, and `docs/DESIGN.md`'s "drawn on a
 * character grid" is the same argument.
 *
 * THE SELECTED SEGMENT IS NOT FILLED WITH THE ACCENT. A solid accent control
 * means "primary action" and lives in the ContextBar's `actions` zone; these
 * live in `controls`, where `ui_smoke` fails any accent-filled element over
 * 12px. Selection is `--surface-selected` and brighter text -- the same
 * accent-TINTED ground a selected table row and a selected graph node use, so
 * "this one" means one thing everywhere. It was `--surface-overlay`, a neutral
 * grey that said "raised" rather than "chosen" and shared no vocabulary with
 * any other selection in the app.
 *
 * `radiogroup`, not `tablist`. Curation's three ARE tabs in the loose sense,
 * but ARIA tabs owe their panels `id`/`aria-controls` wiring, and the graph's
 * spacing has no panel at all. One of N with `aria-checked` is true of all
 * three and needs nothing from the caller.
 */
/* Generic over the value so a caller keeps its own union: `CurationView`'s tab
 * stays `'merges' | 'definitions' | 'history'` and the graph's scope stays
 * `GraphMode`, rather than both widening to `string` at the boundary and losing
 * the exhaustiveness that makes adding a fourth queue a typecheck error. */
import Spinner from '@/components/ui/Spinner.vue'

defineProps<{
  modelValue: T
  /**
   * `hint` becomes `title`, for a choice whose consequence is not obvious.
   *
   * `busy` marks a segment whose CONTENT is being prepared while the reader is
   * on another one. It is not a disabled state and must not read as one: the
   * segment stays selectable, because what it shows while it waits (the ranked
   * list, and the sentence saying so) is a surface worth landing on.
   */
  options: readonly { value: T; label: string; hint?: string; busy?: boolean }[]
  /** Names the set for a screen reader: "graph scope", "curation queue". */
  ariaLabel: string
  /**
   * Rendering for a set that outruns the space it is given: ONE row, scrolling
   * inside its own container -- the same answer the design system gives every
   * wide thing, and the reason it is not `flex-wrap`: wrapped segments read as
   * ragged blocks with dead space, and a wrapped container collapses `h-full`
   * segments to text height. Off by default: a set short enough to fit should
   * stay put everywhere.
   *
   * The scroll is not gated to a breakpoint, and that is the point. The frame
   * is `min-w-0 max-w-full` so it shrinks under flex pressure at ANY width,
   * while the segments are `flex-none whitespace-nowrap` and never do. Whether
   * the set overruns is therefore a question about the container, not about
   * the device: on an iPad in portrait this strip overflowed a shrunken frame
   * with `overflow: visible` and painted over the actions beside it. Only the
   * TAP RUNG is a phone question, and that stays on `max-xs:`.
   */
  scrollable?: boolean
}>()

defineEmits<{ (e: 'update:modelValue', value: T): void }>()
</script>

<template>
  <div
    role="radiogroup"
    :aria-label="ariaLabel"
    class="segmented inline-flex items-center h-control p-(--space-half) gap-(--space-half) rounded-sm border border-border-default"
    :class="
      scrollable ? 'min-w-0 max-w-full overflow-x-auto max-xs:h-auto' : ''
    "
  >
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      role="radio"
      :aria-checked="modelValue === option.value"
      :title="option.hint"
      class="segmented__seg inline-flex items-center px-3 rounded-sm border-0 font-ui text-xs uppercase tracking-label leading-none whitespace-nowrap cursor-pointer transition-colors duration-fast ease-standard"
      :class="[
        modelValue === option.value
          ? 'is-selected bg-surface-selected text-text-primary'
          : 'bg-transparent text-text-tertiary hover:text-text-secondary',
        // One height branch per mode: the scroll container goes h-auto
        // under xs, and `h-full` of auto collapses a segment to text
        // height — a label-tall tap target. Scrollable segments take the
        // touch rung instead; everything else keeps filling the rail.
        scrollable ? 'xs:h-full max-xs:h-(--control-h-touch) flex-none' : 'h-full',
      ]"
      @click="$emit('update:modelValue', option.value)"
    >
      {{ option.label }}
      <!-- Work happening BEHIND a choice the reader has not made yet. The
           spinner rides the segment rather than the panel because the panel is
           not on screen: the whole point is that they are reading the other
           one. `aria-hidden` because `busyLabel` on the segment already says
           it, and a screen reader should hear the reason, not a graphic. -->
      <Spinner v-if="option.busy" :size="10" class="ml-2 shrink-0" aria-hidden="true" />
    </button>
  </div>
</template>
