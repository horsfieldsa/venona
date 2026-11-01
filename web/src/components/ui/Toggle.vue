<script setup lang="ts">
/**
 * A two-state control that stays on screen showing its state -- the graph's
 * full screen, "hide unconnected".
 *
 * Distinct from `Button` (which does a thing and returns to rest) and from
 * `Chip interactive` (which is a filter in a set).
 *
 * IT IS A SWITCH, and it looks like one. Shaped like an interactive `Chip` --
 * same height, same padding, same selected treatment, a leading state dot the
 * only difference -- it would put three visually identical controls in one bar
 * doing three different kinds of work, with a 5px dot carrying the
 * distinction. A switch says "binary, and this is which way it is set" before
 * the label is read.
 *
 * IT STILL DOES NOT FILL WITH THE ACCENT. A solid accent control means
 * "primary action", and those live in the ContextBar's `actions` zone; a toggle
 * lives in `controls`, where the whole vocabulary is outlined. Filled, "full
 * screen" rendered identically to "Rebuild graph" while doing the opposite kind
 * of work -- one changes what you are looking at, the other changes the data.
 * `ui_smoke` enforces this: any accent-filled element over 12px in the controls
 * zone fails. So the TRACK is outlined and the THUMB is the filled mark, which
 * is exactly the job the state dot had, now carrying position as well as fill.
 *
 * `role="switch"` with `aria-checked`, not `aria-pressed`. Pressed is for a
 * button that stays down; checked is for a setting that is on. Screen readers
 * announce them differently and the difference is the truth here.
 */
defineProps<{ modelValue: boolean }>()

defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()
</script>

<template>
  <button
    type="button"
    role="switch"
    class="toggle inline-flex items-center gap-2 h-control px-2 bg-transparent border-0 font-ui text-xs uppercase tracking-label leading-tight cursor-pointer whitespace-nowrap transition-colors duration-fast ease-standard"
    :class="modelValue ? 'on text-text-primary' : 'text-text-tertiary hover:text-text-secondary'"
    :aria-checked="modelValue"
    @click="$emit('update:modelValue', !modelValue)"
  >
    <!-- The track carries no fill in either state, so the control never reads
         as a primary action. Off is a border; on is the accent border, the same
         "selected" treatment every outlined control in this vocabulary uses. -->
    <span
      class="toggle__track flex items-center flex-none w-(--switch-w) h-(--switch-h) p-(--space-half) rounded-pill border transition-colors duration-fast ease-standard"
      :class="modelValue ? 'border-accent-primary' : 'border-border-strong'"
      aria-hidden="true"
    >
      <!-- The thumb is `--dot-md`, the same mark the leading state dot was, and
           it is filled in BOTH states -- position is what reads at a glance, and
           a hollow thumb at 7px reads as a rendering artefact rather than as
           off. Translated by its own travel rather than positioned absolutely,
           so the two ends cannot drift apart. -->
      <span
        class="toggle__thumb block w-(--dot-md) h-(--dot-md) rounded-pill transition-transform duration-fast ease-standard"
        :class="
          modelValue
            ? 'bg-accent-primary translate-x-(--switch-travel)'
            : 'bg-border-strong translate-x-0'
        "
      />
    </span>
    <slot />
  </button>
</template>
