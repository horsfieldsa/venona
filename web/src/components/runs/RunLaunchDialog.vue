<script setup lang="ts">
/**
 * The one dialog in front of every run somebody starts by hand.
 *
 * Five launches grew their own and all five reached the same discipline by
 * different words — count what the RUN will attempt rather than what is
 * queued, price it off this machine's own ledger, say when the figure is a
 * fallback rather than an average — which is four chances to leave one out,
 * and one of them did: `SecondSourceDialog` quoted money with no duration, so
 * an afternoon read like a coffee. The rules live here now and a caller cannot
 * decline them.
 *
 * TWO SHAPES, ONE COMPONENT. A run either counts a queue and quotes a price
 * (`previewPath`) or collects the ceilings it may raise (the `fields` slot),
 * and two of them do neither and simply confirm. The quote block renders only
 * when there is a preview to render, so a parameter dialog is not a spend
 * dialog with the numbers blanked out — it never asks for them.
 *
 * WHAT A CALLER MAY NOT OVERRIDE: the amber cost block, the "measured from"
 * caveat, and the confirm staying disabled until the count it is labelled with
 * exists. A spend this size may never be authorised against a blank, and that
 * was the one rule every copy did keep — which is the argument for it living
 * in one place rather than in five.
 *
 * `unit` comes off the wire (`RunPreview`), not from a prop: the endpoint knows
 * whether it is counting acronyms, names or articles, and a dialog that had to
 * be told would eventually be told wrongly.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import Dialog from '@/components/ui/Dialog.vue'
import Spinner from '@/components/ui/Spinner.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import { apiGet, apiPost, errorText } from '@/api/client'
import { useServiceHealth } from '@/composables/useServiceHealth'
import { useStaleGuard } from '@/composables/useStaleGuard'
import { useRunTypesStore } from '@/stores/runTypes'
import { useRunsStore } from '@/stores/runs'
import { useToastsStore } from '@/stores/toasts'
import type { RunPreview } from '@/types/run'

const props = withDefaults(
  defineProps<{
    /** The run-type key. Supplies the label and the help text, so the control
     *  that STARTS a run and the row that reports it having run cannot call it
     *  two things. */
    kind: string
    /** Where the confirm posts. */
    startPath: string
    /** Counted and priced when given; a plain confirm when not. */
    previewPath?: string
    /** Body for the start request. */
    body?: Record<string, unknown>
    /** Dialog heading. Falls back to the run type's own label. */
    title?: string
    /** Menu-row glyph. */
    icon?: string
    /** Disables the row and says why, for a caller with its own reason. */
    disabledReason?: string
    /** Confirm verb. "Look up", "Read", "Generate". */
    verb?: string
    /** Whether PRESSING THIS ROW spends, said on the row itself.
     *
     *  On the row and not on the run type, because the two are different
     *  questions: `redefine` can spend when the CLI asks it to read articles
     *  with the model, and the menu entry for it cannot, since it passes
     *  `use_model=false`. `RunType.uses_model` answers a third question again
     *  (whether to show a transcript) and is true for every kind, so it cannot
     *  carry this.
     *
     *  Two free-form words would drift; two states do not. The dialog supplies
     *  the number, this supplies the answer to "will this cost me anything",
     *  which is what a reader needs BEFORE opening anything. Two adjacent
     *  items reading "Definitions" and "Re-read" with nothing to separate them
     *  sent a reader to the paid one for work the free one does. */
    cost?: 'free' | 'spends' 
  }>(),
  { body: () => ({}), icon: 'play', verb: 'Start', cost: 'spends' },
)

const emit = defineEmits<{
  started: [runId: string]
  /** Fired every time the dialog opens, BEFORE the preview is read.
   *
   *  A parameter dialog that remembers last week's answer starts a much bigger
   *  run than the one somebody thinks they are starting, because the field
   *  they set is off the bottom of the panel. Each of these owned its own
   *  `watch(() => props.open)` for that; the shared dialog owns `open` now, so
   *  it has to hand the moment back. */
  opened: []
}>()

const runs = useRunsStore()
const runTypes = useRunTypesStore()
const toasts = useToastsStore()
const { modelDown } = useServiceHealth()

const jobName = computed(() => runTypes.typeFor(props.kind).label)
const jobHelp = computed(() => runTypes.typeFor(props.kind).description)

const open = ref(false)
/** The FIRST count of this opening, with nothing on screen yet. */
const loading = ref(false)
/** Whether a count has settled since the dialog opened, either way. A REFUSED
 *  count still counts: after it the reader is looking at a box they have to
 *  correct, and blanking that box on their next keystroke is the same defect
 *  as blanking it on their first. */
const counted = ref(false)
/** A later count, with figures already on screen that this one supersedes. */
const recounting = ref(false)
const starting = ref(false)
const preview = ref<RunPreview | null>(null)
const error = ref<string | null>(null)

/** Long enough to type `2155` through, at a hunting pace, as one number. The
 *  200ms this app uses for a filter box is too short for that: nothing partial
 *  here is worth seeing, since `21` and `215` are numbers nobody asked about,
 *  while a filter's narrowing list is useful on the way. */
const PREVIEW_DEBOUNCE_MS = 400

let recount: ReturnType<typeof setTimeout> | undefined
const { claim } = useStaleGuard()
/** The path the count in flight, or the count on screen, belongs to. Set when
 *  the request goes OUT: the watcher below fires a tick after `show()` has
 *  already asked, and comparing against it is what keeps one opening to one
 *  request. */
let countedPath = ''

/** Re-read whenever a caller's own field changes what the run will attempt.
 *  The numbers in front of a control have to be the numbers of the run that
 *  control will start, and a toggle that moves one without the other is the
 *  preview bug this component exists to make unrepeatable.
 *
 *  DEBOUNCED, and the confirm goes dead on the keystroke rather than when the
 *  request goes out: between the two, the figure on screen belongs to the
 *  number that WAS in the box. Typing `2155` was four counts, of which three
 *  quoted a number nobody asked for. */
watch(
  () => props.previewPath,
  (path) => {
    if (!open.value || path === countedPath) return
    recounting.value = true
    clearTimeout(recount)
    recount = setTimeout(() => void loadPreview(), PREVIEW_DEBOUNCE_MS)
  },
)

watch(open, (isOpen) => {
  if (!isOpen) clearTimeout(recount)
})
onBeforeUnmount(() => clearTimeout(recount))

async function loadPreview() {
  if (!props.previewPath) return
  clearTimeout(recount)
  countedPath = props.previewPath
  const fresh = claim()
  // Only the first count blanks the dialog. A later one leaves the caller's
  // fields mounted, because blanking them takes the focus out of the box being
  // typed into: every digit after the first is dropped, and the box cannot hold
  // a number longer than one keystroke.
  loading.value = !counted.value
  recounting.value = true
  error.value = null
  try {
    const counted = await apiGet<RunPreview>(props.previewPath)
    if (!fresh()) return
    preview.value = counted
  } catch (err) {
    // The confirm stays disabled without a preview. Guessing at the count is
    // exactly what this dialog exists to prevent.
    if (!fresh()) return
    preview.value = null
    error.value = errorText(err, 'could not count the queue')
  } finally {
    if (fresh()) {
      counted.value = true
      loading.value = false
      recounting.value = false
    }
  }
}

function show() {
  open.value = true
  error.value = null
  // Last opening's figures are not this opening's. Cleared rather than left to
  // be overwritten, because a stale count with a live confirm under it is a
  // spend authorised against the wrong number.
  preview.value = null
  counted.value = false
  // Before the preview, so a caller resetting a field that the preview path
  // depends on is reflected in the numbers rather than one open behind.
  emit('opened')
  void loadPreview()
}

async function start() {
  starting.value = true
  error.value = null
  try {
    const { run_id, status } = await apiPost<{ run_id: string; status: string }>(
      props.startPath,
      props.body,
    )
    open.value = false
    await runs.launched()
    toasts.push({
      kind: 'success',
      // `queued` is a real answer and not a failure: the lane was full
      // (contract C11) and this starts when a slot frees.
      text: `${jobName.value}${status === 'queued' ? ' queued' : ' started'}`,
      to: run_id ? `/admin/runs/${run_id}` : '/admin/runs',
      toLabel: 'watch',
    })
    emit('started', run_id)
    // So the Runs page and the header count it immediately rather than at the
    // next 30-second tick.
    await runs.revalidate()
  } catch (err) {
    error.value = errorText(err, 'could not start the run')
  } finally {
    starting.value = false
  }
}

const count = computed(() => preview.value?.will_attempt ?? 0)
const quoted = computed(() => !!props.previewPath)
const nothingToDo = computed(() => quoted.value && !loading.value && !!preview.value && !count.value)

/** True when the cap is actually biting — there is more queue than this run
 *  will take. Not merely "a cap is configured": a batch of 100 against 40
 *  eligible is not a batch, and saying "0 will be left" is noise. */
const batched = computed(() => !!preview.value && preview.value.eligible > preview.value.will_attempt)

const noun = computed(() => {
  const p = preview.value
  if (!p) return ''
  return count.value === 1 ? p.unit : p.unit_plural
})

/** Hours or minutes, not 7143 seconds. The point of showing it at all is that
 *  "this is an afternoon" and "this is a coffee" are different decisions. */
const duration = computed(() => {
  const seconds = preview.value?.estimate_seconds ?? 0
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} seconds`
  if (seconds < 5400) return `${Math.round(seconds / 60)} minutes`
  const hours = seconds / 3600
  return `${hours < 10 ? hours.toFixed(1) : Math.round(hours)} hours`
})

/** Two decimals always. `$0.1` beside a count of 300 reads as a typo. */
function usd(value: number) {
  return `$${value.toFixed(2)}`
}

/** A run of this kind already in flight. `launch` coalesces an identical light
 *  request into the one that exists, so a second press cannot start a second —
 *  but it would still open a dialog quoting a price for work already under way. */
const inFlight = computed(() => runs.lightRuns.find((r) => r.kind === props.kind) ?? null)

const blocked = computed(() => props.disabledReason || (inFlight.value ? 'already running; watch it on the Runs page' : modelDown.value ? 'the model is not answering; see Runs' : ''))

/** Opened BY THE MENU, not by a trigger of our own.
 *
 *  This component renders a dialog and nothing else, and `RunsView` mounts it
 *  at the view root rather than inside the run menu. `Dialog` teleports to
 *  `body`, so a dialog rendered inside the teleported menu panel is not
 *  CONTAINED by that panel: the first mousedown in it lands outside
 *  `runMenuPanel`, `onMenuBlur` closes the menu, this component unmounts and
 *  the click never reaches the confirm. The symptom is a button that does
 *  nothing, which is the failure `RunsView.onMenuBlur`'s own comment records
 *  for the anchor and which teleporting the dialog reintroduces one level up.
 *
 *  `blocked` is still computed here and read by the menu, so the row and the
 *  dialog cannot disagree about whether this run may start. */
defineExpose({ show, blocked, jobName, jobHelp })
</script>

<template>
  <Dialog v-model:open="open" :title="title || jobName" :busy="starting">
    <div v-if="loading" class="flex items-center gap-2 text-text-tertiary">
      <Spinner :size="14" /> counting…
    </div>

    <template v-else>
      <!-- The caller's own sentence, and its fields when it has any. A
           parameter run renders only this.

           ALWAYS MOUNTED once there is anything to show, error included: these
           are the boxes the reader is typing in, and unmounting them takes the
           focus with them. A refused count is a reason to correct the number,
           which takes a box to do it in. -->
      <slot name="body" :preview="preview" />

      <StatusNote v-if="error" kind="error" class="mt-3">{{ error }}</StatusNote>

      <template v-else-if="quoted && preview">
        <p v-if="nothingToDo" class="m-0">
          <slot name="empty">Nothing left to do here.</slot>
        </p>

        <template v-else>
          <p class="m-0">
            This will work through
            <strong class="text-text-primary">{{ count }}</strong> {{ noun }}.<template
              v-if="batched"
            >
              The first {{ count }} of {{ preview.eligible }} waiting.</template
            >
          </p>

          <!-- The spend and the wall clock, set apart in the caution signal
               rather than run on in body copy. Every other confirmation in the
               app guards something reversible; these are the only presses that
               cost real money, and prose the same colour as the sentence above
               does not say so. Amber, not the critical rust: nothing here is
               destroyed, and overloading `critical` would make the tone that
               means "this cannot be undone" mean "this is expensive" as well. -->
          <p class="mt-3 mb-0 border border-signal-high rounded-sm px-3 py-2 text-signal-high">
            Roughly <strong>{{ usd(preview.estimate_usd) }}</strong> of model usage and
            <strong>{{ duration }}</strong
            >, one at a time.
            <span class="text-text-tertiary">
              <template v-if="preview.cost_sample">
                Measured from your last {{ preview.cost_sample }} run{{
                  preview.cost_sample === 1 ? '' : 's'
                }}, at {{ usd(preview.per_item_usd) }} each.
              </template>
              <template v-else>
                This machine has not run one yet, so {{ usd(preview.per_item_usd) }} each is an
                estimate rather than a measurement.
              </template>
            </span>
          </p>

          <p class="mt-2 mb-0 text-text-tertiary">
            It runs in the background and can be cancelled from the Runs page; whatever has landed
            by then is kept.
            <!-- A batch is only honest if it says what it is NOT doing.
                 Without this the dialog reads as "all of them" and quietly
                 does a fifth, which is the same lie as a preview counting the
                 wrong rows. -->
            <template v-if="batched">
              {{ preview.eligible - count }} will be left after this one.
            </template>
          </p>

          <div v-if="preview.sample?.length" class="mt-3 flex items-center gap-1 flex-wrap">
            <span class="text-xs text-text-tertiary">starting with</span>
            <Chip v-for="item in preview.sample" :key="item" size="sm">{{ item }}</Chip>
            <span v-if="preview.eligible > preview.sample.length" class="text-xs text-text-tertiary">
              + {{ preview.eligible - preview.sample.length }} more
            </span>
          </div>
        </template>

        <!-- Numbers belonging to one kind of run only. They arrive in
             `extra` and the caller renders them; this component has no opinion
             about retries or corroboration. -->
        <slot name="extra" :preview="preview" />
      </template>
    </template>

    <template #actions>
      <Button variant="outline" icon="close" :disabled="starting" @click="open = false"
        >Cancel</Button
      >
      <!-- Disabled until the count it is labelled with exists. A spend may
           never be authorised against a blank — the one rule all five copies
           kept, and the clearest argument for them being one. -->
      <Button
        :icon="starting ? undefined : icon"
        :disabled="starting || loading || recounting || (quoted && !count)"
        @click="start()"
      >
        <Spinner v-if="starting || recounting" :size="12" />
        {{ quoted && count ? `${verb} ${count}` : verb }}
      </Button>
    </template>
  </Dialog>
</template>
