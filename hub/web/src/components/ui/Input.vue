<script setup lang="ts">
/**
 * The boxed field. One height, `--control-h`, rising to `--control-h-touch`
 * under the phone tier; nothing ships a 28px target to a thumb.
 *
 * The field fills its box and the CALLER sizes the box. Width belongs on a
 * wrapper, never here, or two fields in one row cannot be different widths.
 */
withDefaults(defineProps<{ modelValue: string; type?: string }>(), { type: 'text' })
defineEmits<{ (e: 'update:modelValue', value: string): void }>()
</script>

<template>
  <input
    :type="type"
    :value="modelValue"
    class="boxed w-full h-control max-xs:h-(--control-h-touch) px-2 bg-surface-inset border border-border-default rounded-sm font-ui text-sm font-normal text-text-primary placeholder:text-text-tertiary"
    @input="$emit('update:modelValue', ($event.target as HTMLInputElement).value)"
  />
</template>
