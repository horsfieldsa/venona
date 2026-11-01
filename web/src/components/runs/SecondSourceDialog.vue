<script setup lang="ts">
/**
 * How far a corroboration sweep is allowed to reach.
 *
 * THE NUMBER THIS CLOSES is on the graph page: most of what Venona knows is
 * hidden, because a relationship reaches the graph only once both its names
 * have been confirmed by a second publisher. Reading more of the same press
 * fixes that by accident and rarely; this goes and asks.
 *
 * The default is a daily's default and this is not a daily. Thirty names beside
 * a morning's reading is right; against thousands of blocked names it is
 * nothing, which is why the box is here.
 *
 * The counting, the price, the duration and the confirm are
 * `RunLaunchDialog`'s. What stays is the two ceilings this run may raise and
 * the one number it is actually judged on — `would_appear`, the relationships
 * that are one confirmation away, which is the honest answer to "what will this
 * buy" and which no shared component could know to ask for.
 *
 * Fields hold `number | string` and are coerced once: `Input` emits the raw
 * string, and an empty box must mean "the usual" rather than zero.
 */
import { computed, ref, useTemplateRef } from 'vue'

import RunLaunchDialog from '@/components/runs/RunLaunchDialog.vue'
import Input from '@/components/ui/Input.vue'
import type { RunPreview } from '@/types/run'

const names = ref<number | string>('')
const maxCalls = ref<number | string>('')

/** As far as either ceiling is allowed to reach, and the API says the same
 *  (`runs.py::SecondSourceRun`). Held to HERE as well because past it the count
 *  is refused, and a reader asking what 2155 names would cost was answered with
 *  "could not count the queue" rather than with a number. */
const MOST_NAMES = 2000
const MOST_CALLS = 5000

function toNumber(value: number | string, most: number): number | undefined {
  const n = typeof value === 'number' ? value : Number(String(value).trim())
  return Number.isFinite(n) && n > 0 ? Math.min(Math.floor(n), most) : undefined
}

/** Said on screen rather than silently applied: a number quietly reduced is
 *  the same lie as a limit the sweeps truncate. */
const cappedNames = computed(() => Number(String(names.value).trim()) > MOST_NAMES)
const cappedCalls = computed(() => Number(String(maxCalls.value).trim()) > MOST_CALLS)

/** The quote follows the box. `RunLaunchDialog` re-reads whenever this path
 *  changes, which is what keeps the number in front of the control equal to
 *  the number the control will start. */
/** Cleared on each opening, for the reason `DeepReadDialog.reset` gives: a
 *  remembered number starts a bigger run than the one being asked for. */
function reset() {
  names.value = ''
  maxCalls.value = ''
}

const previewPath = computed(() => {
  const wanted = toNumber(names.value, MOST_NAMES)
  return `/runs/second-source/preview${wanted ? `?names=${wanted}` : ''}`
})

const body = computed(() => ({
  names: toNumber(names.value, MOST_NAMES),
  max_calls: toNumber(maxCalls.value, MOST_CALLS),
}))

function wouldAppear(preview: RunPreview | null) {
  return Number(preview?.extra?.would_appear ?? 0)
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
    kind="research"
    icon="search"
    verb="Start looking"
    title="Second source"
    :preview-path="previewPath"
    start-path="/runs/second-source"
    :body="body"
    @opened="reset"
  >
    <template #body="{ preview }">
      <p class="m-0 font-prose text-base leading-normal text-text-secondary">
        A relationship reaches the graph once both its names have been confirmed by another
        publisher. This goes looking for one, worst first, instead of waiting for a morning's
        reading to turn one up.
      </p>

      <label class="mt-4 flex flex-col gap-2">
        <span class="font-ui text-xs uppercase tracking-label text-text-tertiary"
          >Names to chase</span
        >
        <Input v-model="names" type="number" :max="MOST_NAMES" placeholder="30, the usual" />
        <span class="font-prose text-sm leading-normal text-text-tertiary">
          <template v-if="cappedNames"
            >One search reaches {{ MOST_NAMES }} names at most, so that is what it asks
            for.</template
          >
          <template v-else-if="preview"
            >{{ preview.eligible }} names are holding at least one relationship back. Ordered so the
            ones whose partner is already confirmed come first, because those are the only ones that
            can appear.</template
          >
          <template v-else>The names holding back the most relationships, worst first.</template>
        </span>
      </label>

      <label class="mt-4 mb-4 flex flex-col gap-2">
        <span class="font-ui text-xs uppercase tracking-label text-text-tertiary"
          >Model calls at most</span
        >
        <Input v-model="maxCalls" type="number" :max="MOST_CALLS" placeholder="the usual limit" />
        <span class="font-prose text-sm leading-normal text-text-tertiary">
          Searching is the expensive part of Venona and costs the same whether it finds anything.
          Venona stops when it reaches this and keeps the rest for next time.
          <template v-if="cappedCalls"> {{ MOST_CALLS }} is the most one run will make.</template>
        </span>
      </label>
    </template>

    <template #empty>
      Every name that is holding a relationship back has already been chased. Nothing is waiting on
      a second publisher.
    </template>

    <template #extra="{ preview }">
      <p v-if="wouldAppear(preview)" class="mt-3 mb-0 text-text-tertiary">
        Up to {{ wouldAppear(preview) }} relationship{{ wouldAppear(preview) === 1 ? '' : 's' }}
        could appear. A ceiling, not a forecast: about a third of the names chased find a second
        source. Venona reads one thing at a time, so a long search still running in the morning
        means no brief that day.
      </p>
    </template>
  </RunLaunchDialog>
</template>
