<script setup lang="ts">
import StatusNote from '@/components/ui/StatusNote.vue'

/**
 * The loading and error swap, owned by the PANE rather than the shell -- so the
 * rail beside it never blanks while a page loads.
 */
defineProps<{ loading: boolean; error: string }>()
</script>

<template>
  <div v-if="loading" class="flex items-center gap-2 self-start text-text-secondary text-sm">
    loading
  </div>
  <StatusNote v-else-if="error" kind="error">{{ error }}</StatusNote>
  <slot v-else />
</template>
