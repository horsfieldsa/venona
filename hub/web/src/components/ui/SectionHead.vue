<script setup lang="ts">
/**
 * The quiet uppercase label that leads a panel, a card section or a settings
 * group, with whatever control belongs to its right.
 *
 * It owns no outer margin: separation belongs to the parent, where it varies.
 */
withDefaults(defineProps<{ label?: string; as?: string; rule?: boolean }>(), {
  label: '',
  as: 'h2',
  rule: false,
})
</script>

<template>
  <div
    class="section-head flex items-center justify-between gap-3"
    :class="rule ? 'pb-2 border-b border-b-border-subtle' : ''"
  >
    <component
      :is="as"
      class="section-head__label text-2xs font-medium tracking-label uppercase text-text-tertiary"
      >{{ label }}</component
    >
    <slot />
  </div>
</template>
