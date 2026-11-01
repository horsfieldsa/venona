<script setup lang="ts">
/**
 * One of N, as radios.
 *
 * WHY RADIOS AND NOT A `<select>` OR A `Toggle`. The server-rendered page this
 * replaces used a two-option `<select>`, argued at the time against a checkbox:
 * a checkbox says what it is only if its label is written as a question, and
 * these are answers -- "they pick a domain and get it" against "they ask, and I
 * decide". Both halves of that argument survive here, and the control now looks
 * like the app's, which a `<select>` never did. The app's own two-state control
 * is `Toggle`, a button that flips client state; these post a value, so radios
 * are the form-shaped member of the same family.
 *
 * Square, never a pill, and the selection is `--surface-selected` rather than
 * an accent fill: an accent-filled control means "primary action".
 */
withDefaults(
  defineProps<{
    modelValue: string
    name: string
    options: readonly { value: string; label: string }[]
    /**
     * Stack the options under the phone tier instead of keeping one row.
     *
     * For options whose labels are SENTENCES rather than words. "They pick a
     * domain and get it" beside "They ask, and I decide" is 146px wider than a
     * 390px screen, and because every segment is `whitespace-nowrap` the
     * overflow lands on the document rather than inside the control -- so the
     * whole page scrolls sideways because of one switch. Stacking is the
     * answer the design system asks for here: simpler on a phone, not the same.
     */
    stack?: boolean
  }>(),
  { stack: false },
)
defineEmits<{ (e: 'update:modelValue', value: string): void }>()
</script>

<template>
  <div
    class="segmented inline-flex items-center max-w-full min-w-0 h-control p-(--space-half) gap-(--space-half) rounded-sm border border-border-default"
    :class="stack ? 'max-xs:flex-col max-xs:items-stretch max-xs:h-auto max-xs:w-full' : 'max-xs:h-auto'"
  >
    <label
      v-for="option in options"
      :key="option.value"
      class="segmented__seg inline-flex items-center px-3 h-full max-xs:h-(--control-h-touch) rounded-sm font-ui text-xs uppercase tracking-label leading-none cursor-pointer transition-colors duration-fast ease-standard"
      :class="[
        modelValue === option.value
          ? 'is-selected bg-surface-selected text-text-primary'
          : 'bg-transparent text-text-tertiary hover:text-text-secondary',
        stack ? 'max-xs:justify-center whitespace-normal text-center' : 'whitespace-nowrap',
      ]"
    >
      <!-- The radio itself is never drawn: the label IS the control, and a
           13px box beside a word would be a second affordance for one choice.
           `sr-only` rather than `hidden`, so it keeps its tab stop and its
           accessible name. -->
      <input
        class="sr-only"
        type="radio"
        :name="name"
        :value="option.value"
        :checked="modelValue === option.value"
        @change="$emit('update:modelValue', option.value)"
      />
      {{ option.label }}
    </label>
  </div>
</template>
