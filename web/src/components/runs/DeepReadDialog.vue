<script setup lang="ts">
/**
 * What a deep read is allowed to do, before it starts.
 *
 * A deep read is the reading without the writing: it works through what Venona
 * has fetched and never got to, and writes no brief. The three fields here are
 * the ceilings a normal run keeps and this one may raise, and they are here
 * rather than in `config/settings.yaml` because they are a decision about one
 * run rather than a standing answer for every run.
 *
 * WHY "go looking" IS OFF AND STAYS OFF UNTIL ASKED. Searching for older
 * articles is by a wide margin the most expensive thing Venona does, and unlike
 * the rest of this dialog its cost does not scale with what it finds. Read as a
 * default it would be one checkbox between an idle evening and a large bill, so
 * it is a switch somebody has to reach for, with its own separate depth.
 *
 * EVERY FIELD HOLDS `number | string` AND IS COERCED ONCE, on submit.
 * `Input` emits `el.valueAsNumber` for `type="number"` (its own note explains
 * why: `v-model.number` does not survive a component boundary), so a box starts
 * out holding `''`, becomes a NUMBER the moment somebody types, and becomes
 * `NaN` if they clear it again. Treating the value as a string -- calling
 * `.trim()` on it -- throws on the first keystroke's worth of input, inside a
 * click handler, where it surfaces as "something on this page failed to render"
 * and the request never leaves the browser.
 */
import { computed, ref, useTemplateRef } from 'vue'

import RunLaunchDialog from '@/components/runs/RunLaunchDialog.vue'
import Input from '@/components/ui/Input.vue'
import Toggle from '@/components/ui/Toggle.vue'

/** What the instance is configured to allow a run, for the placeholder.
 *  A scale to judge the box against: without one, any number looks arbitrary. */
const USUAL_LIMIT = 250

const days = ref<number | string>('')
const maxCalls = ref<number | string>('')
const goLooking = ref(false)
const lookingDays = ref<number | string>(30)

/** Total by construction: a blank box, a cleared one (`NaN`) and a nonsense
 *  one all mean "not set", which is what the server reads as no override. */
function toNumber(value: number | string): number | undefined {
  const n = typeof value === 'number' ? value : Number(value.trim())
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : undefined
}

const summary = computed(() => {
  const back = toNumber(days.value)
  return back
    ? `Reads the last ${back} days of what Venona has already fetched.`
    : 'Reads everything Venona has fetched and not yet got to.'
})

/** Cleared on each opening. A dialog that remembers the last answer is a
 *  dialog that starts a much bigger run than the one somebody thinks they are
 *  starting, because the field they set last week is off the bottom of the
 *  panel. */
function reset() {
  days.value = ''
  maxCalls.value = ''
  goLooking.value = false
  lookingDays.value = 30
}

const body = computed(() => ({
  kind: 'backfill',
  lookback_days: toNumber(days.value),
  max_calls: toNumber(maxCalls.value),
  go_looking: goLooking.value,
  discovery_max_age_days: goLooking.value ? toNumber(lookingDays.value) : undefined,
}))

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
    kind="backfill"
    icon="waypoints"
    verb="Start reading"
    title="Deep read"
    start-path="/runs"
    :body="body"
    @opened="reset"
  >
    <template #body>
      <div class="flex flex-col gap-5">
      <p class="font-prose text-base leading-normal text-text-secondary">
        {{ summary }} It writes no brief, so nothing is sent to anybody. What it finds
        becomes names and relationships you can search and explore.
      </p>

      <!-- THE UNIT IS IN THE LABEL, not only in the help text underneath. A box
           reading "how much work at most" with 5000 in it does not say 5000 of
           what, and the reader is being asked to pick the number. "Model calls"
           is this page's own word for it: the stat row above says it too. -->
      <label class="flex flex-col gap-2">
        <span class="font-ui text-xs uppercase tracking-label text-text-tertiary"
          >Days back to read</span
        >
        <Input v-model="days" type="number" placeholder="everything waiting" />
        <span class="font-prose text-sm leading-normal text-text-tertiary"
          >Leave it empty to read everything that is waiting, however old.</span
        >
      </label>

      <label class="flex flex-col gap-2">
        <span class="font-ui text-xs uppercase tracking-label text-text-tertiary"
          >Model calls at most</span
        >
        <Input v-model="maxCalls" type="number" :placeholder="`${USUAL_LIMIT}, the usual limit`" />
        <span class="font-prose text-sm leading-normal text-text-tertiary">
          Venona stops when it reaches this and keeps the rest for next time, so
          starting another deep read picks up where this one left off. Reading
          everything that is waiting usually takes a few thousand.
        </span>
      </label>

      <div class="flex flex-col gap-2">
        <Toggle v-model="goLooking">Go looking for older articles</Toggle>
        <span class="font-prose text-sm leading-normal text-text-tertiary">
          Searches the web for material Venona never fetched, rather than reading what it
          already has. This is the expensive part of a run and it costs the same whether it
          finds anything or not.
        </span>
        <label v-if="goLooking" class="flex flex-col gap-2 mt-2">
          <span class="font-ui text-xs uppercase tracking-label text-text-tertiary"
            >How old an article may be</span
          >
          <Input v-model="lookingDays" type="number" />
          <span class="font-prose text-sm leading-normal text-text-tertiary">In days.</span>
        </label>
      </div>

      <!-- The one consequence somebody cannot see from here, and the one they
           would mind: Venona reads once at a time, so a long deep read is still
           going at six in the morning and the day's brief is skipped. -->
      <p class="font-prose text-sm leading-normal text-text-tertiary">
        Venona reads one thing at a time. A deep read still running in the morning means
        no brief that day, so give it a limit or stop it before then.
      </p>
      </div>
    </template>
  </RunLaunchDialog>
</template>
