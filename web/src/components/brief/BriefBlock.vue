<script setup lang="ts">
import { computed } from 'vue'

import TextRuns from '@/components/brief/TextRuns.vue'
import type { BriefBlock } from '@/types/brief'

/**
 * One block: the tag from `type`, the content from `TextRuns` -- which owns
 * every rule about how a run renders, so a title and a paragraph cannot
 * disagree about what a mention looks like.
 */
const props = defineProps<{ block: BriefBlock; activeId?: string | null }>()

const tag = computed(() => {
  switch (props.block.type) {
    case 'heading':
      return 'h3'
    case 'list_item':
      return 'li'
    case 'blockquote':
      return 'blockquote'
    case 'code':
      return 'pre'
    default:
      return 'p'
  }
})
</script>

<template>
  <component :is="tag">
    <TextRuns :runs="block.runs" :active-id="activeId" />
  </component>
</template>
