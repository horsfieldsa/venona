<script setup lang="ts">
/**
 * "Generate questions" — study questions written from recent articles.
 *
 * The counting, the price and the confirm are `RunLaunchDialog`'s. What stays
 * here is what a reader has to know about THIS run before authorising it: that
 * nothing it writes reaches a learner until a person approves it, and how many
 * earlier proposals are still waiting — a number that argues for not starting
 * another batch, which no shared component could know to say.
 *
 * This dialog is why the price rules moved into one place. It quoted `null` as
 * "no prior calls to price this from" and showed no duration at all, so the one
 * launch in the app with no figure in front of it was the one a reader was most
 * likely to press repeatedly.
 */
import { useTemplateRef } from 'vue'

import RunLaunchDialog from '@/components/runs/RunLaunchDialog.vue'
import type { RunPreview } from '@/types/run'

function open(preview: RunPreview | null) {
  return Number(preview?.extra?.proposed_open ?? 0)
}

function calls(preview: RunPreview | null) {
  return Number(preview?.extra?.calls ?? 0)
}

/** Forwarded so the run menu can open this without owning it. The dialog is
 *  mounted at the view root, because one rendered inside the teleported menu
 *  panel unmounts with the panel on its own confirm button's mousedown. */
const inner = useTemplateRef<{ show: () => void; blocked: string }>('inner')
defineExpose({
  show: () => inner.value?.show(),
  get blocked() {
    return inner.value?.blocked ?? ''
  },
})
</script>

<template>
  <RunLaunchDialog
    ref="inner"
    kind="questions"
    icon="plus"
    verb="Generate"
    title="Generate study questions"
    preview-path="/learning/generate/preview"
    start-path="/learning/generate"
  >
    <template #empty>
      Nothing new to read. Every recent article has already had questions written from it.
    </template>

    <template #extra="{ preview }">
      <p class="mt-3 mb-0 text-text-tertiary">
        Read in batches of several articles per model call<template v-if="calls(preview)">,
          {{ calls(preview) }} call{{ calls(preview) === 1 ? '' : 's' }} for this one</template
        >. Every question is checked against its source article and lands as a proposal here;
        nothing reaches a learner until you approve it.
        <template v-if="open(preview)">
          {{ open(preview) }} earlier proposal{{ open(preview) === 1 ? ' is' : 's are' }} still
          waiting.
        </template>
      </p>
    </template>
  </RunLaunchDialog>
</template>
