<script setup lang="ts">
import Button from '@/components/ui/Button.vue'

/**
 * Previous / Page n of m / Next.
 *
 * A row rather than three adjacent inlines, which is what they render as with
 * no rule at all: `PreviousPage 2 of 18Next`.
 *
 * Paging is CLAMPED, never refused: a page number arrives in a URL, so it can
 * be zero, negative, past the end, or a bookmark to a page that emptied. Every
 * one of those lands on a real page, and the server does the clamping -- this
 * only has to render what it was told.
 */
defineProps<{ page: number; pages: number }>()
defineEmits<{ (e: 'go', page: number): void }>()
</script>

<template>
  <div v-if="pages > 1" class="pager flex flex-wrap items-center gap-4 mt-4">
    <Button variant="outline" :disabled="page <= 1" @click="$emit('go', page - 1)">Previous</Button>
    <span class="font-ui text-2xs tracking-label uppercase text-text-quiet"
      >Page {{ page }} of {{ pages }}</span
    >
    <Button variant="outline" :disabled="page >= pages" @click="$emit('go', page + 1)">Next</Button>
  </div>
</template>
