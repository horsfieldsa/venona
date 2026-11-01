<script setup lang="ts">
/**
 * A section a reader opens, closed by default.
 *
 * `<details>`/`<summary>`, not a button and a `v-if`. It is the element for
 * this: keyboard operation, `aria-expanded` and the open/closed state come from
 * the platform, and it keeps the markup out of `lint_tokens.py`'s raw-`<button>`
 * rule without an allowlist entry for chrome that is not a control.
 *
 * `@open` fires ONCE, on the first expand, so a caller can fetch what goes
 * inside rather than paying for it on every page load. A run's model session is
 * hundreds of messages; the run detail page must not fetch them to render a
 * heading nobody clicked.
 *
 * A link inside the `#label` slot needs `@click.stop`: a click anywhere in a
 * `<summary>` toggles the `<details>`, so without it the link and the expander
 * both fire and the reader navigates away from the thing they just opened.
 */
import { ref } from 'vue'

import SectionHead from '@/components/ui/SectionHead.vue'

withDefaults(defineProps<{ label?: string; hint?: string; open?: boolean }>(), {
  label: '',
  open: false,
})
const emit = defineEmits<{ (e: 'open'): void }>()

const opened = ref(false)

function onToggle(event: Event) {
  if (!(event.target as HTMLDetailsElement).open || opened.value) return
  opened.value = true
  emit('open')
}
</script>

<template>
  <details class="disc group" :open="open" @toggle="onToggle">
    <!-- `list-none` plus the webkit marker rule: the default disclosure
         triangle is drawn by the UA in its own colour and at its own size, and
         it is the one mark on the page that no token reaches. The caret is
         ours. -->
    <summary
      class="flex cursor-pointer list-none items-center gap-2 py-2 [&::-webkit-details-marker]:hidden"
    >
      <span
        class="caret mono text-2xs text-text-quiet group-open:rotate-90 inline-block transition-transform duration-fast ease-standard"
        aria-hidden="true"
        >›</span
      >
      <!-- `#label` opts OUT of the uppercase micro-label, for a row that is
           data rather than a region heading. The connections list needs it: a
           name is not a section title, and twenty-five of them rendered as
           `SectionHead` is twenty-five 10px grey capitals where the reader is
           looking for names. The default is unchanged, so every existing caller
           keeps the heading it had. -->
      <slot name="label"><SectionHead :label="label" /></slot>
      <!-- The count, beside the heading rather than inside it: it is what tells
           a reader whether opening this is worth it. -->
      <span v-if="hint" class="text-2xs text-text-tertiary">{{ hint }}</span>
    </summary>
    <div class="body pt-2">
      <slot />
    </div>
  </details>
</template>
