<script setup lang="ts">
/**
 * "Look up all" — the whole undefined acronym queue, in one run.
 *
 * `L` grounds one surface and takes about half a minute. The queue it works
 * through is a backlog by construction: tier 3 inside the pipeline is capped at
 * `acronym_lookup_cap_per_run` surfaces a night, so the undefined list grows
 * faster than one keypress at a time clears it. Three hundred presses, each
 * waiting out its own run, is not a way anyone works through a backlog.
 *
 * The counting, the price and the confirm are `RunLaunchDialog`'s; what is left
 * here is the two things that belong to this sweep alone — whether to retry the
 * surfaces already attempted, and whether to take every eligible surface rather
 * than one batch of `acronym_lookup_all_cap`. Both are query parameters on BOTH
 * paths, so moving either re-counts as well as re-runs: a preview quoting the
 * 303-row queue while the run attempted the 180 never tried would quote nearly
 * double and be believed (2026-08-09).
 */
import { computed, ref, useTemplateRef } from 'vue'

import RunLaunchDialog from '@/components/runs/RunLaunchDialog.vue'
import Toggle from '@/components/ui/Toggle.vue'
import type { RunPreview } from '@/types/run'

const retry = ref(false)
const whole = ref(false)

const query = computed(() => `retry=${retry.value}&uncapped=${whole.value}`)
const previewPath = computed(() => `/glossary/lookup-all/preview?${query.value}`)
const startPath = computed(() => `/glossary/lookup-all?${query.value}`)

function skipped(preview: RunPreview | null) {
  return Number(preview?.extra?.skipped_attempted ?? 0)
}

/** Whether the batch is biting: more waiting than this press would take. Once
 *  the toggle is on it never bites, so the control has to keep rendering on its
 *  own state or it would vanish under the hand that just moved it. */
function batched(preview: RunPreview | null) {
  return !!preview && preview.eligible > preview.will_attempt
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
    kind="lookup_all"
    icon="search"
    verb="Look up"
    title="Look up every undefined acronym"
    :preview-path="previewPath"
    :start-path="startPath"
  >
    <template #empty>
      Nothing left to look up. Every undefined acronym has already been looked up on the web. Turn
      on <em>try the ones already attempted</em> to run them again against whatever has been
      published since.
    </template>

    <!-- Off by default: a lookup that grounded nothing rejected every candidate
         it found, and running it again over the same web costs the same and
         fails the same way. It is worth doing after new sources land, which is
         a judgement only the reader can make. -->
    <template #extra="{ preview }">
      <div v-if="skipped(preview) || retry" class="mt-3 flex items-center gap-2 flex-wrap">
        <Toggle v-model="retry">try the ones already attempted</Toggle>
        <span v-if="!retry" class="text-xs text-text-tertiary">
          {{ skipped(preview) }} skipped: tried before, found nothing with a source
        </span>
      </div>

      <!-- Off by default for the reason the batch exists: a queue of several
           hundred is hours of lookups holding a light slot, and nobody should
           buy that by pressing the same button they press for a hundred. The
           dialog has already said how many are waiting, what the whole thing
           costs and how long it takes, so the press that follows this toggle is
           the only one in the app made with all three on screen. -->
      <div v-if="batched(preview) || whole" class="mt-3 flex items-center gap-2 flex-wrap">
        <Toggle v-model="whole">look up every one waiting</Toggle>
      </div>
    </template>
  </RunLaunchDialog>
</template>
