<script setup lang="ts">
/**
 * One run, after the fact.
 *
 * `GET /runs/{id}` has always returned per-stage status, counts, timings and
 * errors plus `params`, `profile_hash` and `warnings`; the UI used it only as a
 * poll for an acronym lookup's terminal status. Clicking a row in the history
 * attached the SSE replay instead, which answered "what did it print" but never
 * "which stage degraded, and by how much".
 *
 * IT LEADS WITH THE ANSWER. `RunResultHeader` is the first thing on the page,
 * because the commonest non-delivering outcome in the system is a lookup that
 * found no definition, and that reader is here to learn one fact. Identity,
 * timing and the diagnostics all come after it.
 *
 * `succeeded_partial` means the run DEGRADED -- `ctx.warn`, which sets
 * `ctx.degraded`, as opposed to `ctx.note`, which records a remark and leaves
 * the status alone. It is not "warnings is not empty", and it is not how a run
 * says it found nothing: that is `run.outcome`. Those two facts are why the
 * Why block below appears only on runs that genuinely had trouble, and why
 * notes get their own quieter heading. ADR 0183.
 *
 * COST AND LATENCY ARE NOT HERE. They live on `/admin/runs/:id/metrics`
 * (`RunMetricsView.vue`), which is a separate route so that it can be gated
 * behind a role without auditing which panels of a shared page a given viewer
 * may see. This page is "what happened and did it degrade"; that one is "what
 * did it cost".
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import LiveRunPanel from '@/components/runs/LiveRunPanel.vue'
import ModelTranscript from '@/components/runs/ModelTranscript.vue'
import RunLog from '@/components/runs/RunLog.vue'
import RunCandidates from '@/components/runs/RunCandidates.vue'
import RunFacts from '@/components/runs/RunFacts.vue'
import RunResultHeader from '@/components/runs/RunResultHeader.vue'
import StatusChip from '@/components/shared/StatusChip.vue'
import ApiUnreachable from '@/components/shared/ApiUnreachable.vue'
import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import Disclosure from '@/components/ui/Disclosure.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import FactList from '@/components/ui/FactList.vue'
import Panel from '@/components/ui/Panel.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import Table, { type Column } from '@/components/ui/Table.vue'
import CancelRunDialog from '@/components/runs/CancelRunDialog.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { useServiceHealth } from '@/composables/useServiceHealth'
import { apiGet, CancelledError, errorText } from '@/api/client'
import { useRequestScope } from '@/composables/useRequestScope'
import { usePoll } from '@/composables/usePoll'
import { agoFrom, humanBetween } from '@/lib/duration'
import { counterLabel } from '@/lib/runCopy'
import { hasTrouble } from '@/lib/runOutcome'
import { destinationFor, notesOf, todosOf, warningsOf } from '@/lib/runMessages'
import { useRunsStore } from '@/stores/runs'
import { useRunTypesStore } from '@/stores/runTypes'
import type { Run } from '@/types/run'

const route = useRoute()
const runs = useRunsStore()
const runTypes = useRunTypesStore()

const run = ref<Run | null>(null)
/** Ticked by the live poll so "started 4 minutes ago" stays true. */
const nowMs = ref(Date.now())
const loading = ref(false)
const error = ref<string | null>(null)

const runId = computed(() => String(route.params.id ?? ''))
const { signal } = useRequestScope()

/** The id IS this route's identity -- it is the one string a reader copies, so
 *  it is stated in full rather than truncated into a chip. No word in front of
 *  it: ids in this app are type-prefixed, so the id already begins `run:`. */
const identity = computed(() =>
  run.value?.started_at ? `${runId.value} · ${run.value.started_at.slice(0, 10)}` : runId.value,
)

async function load() {
  loading.value = true
  error.value = null
  try {
    nowMs.value = Date.now()
    run.value = await apiGet<Run>(`/runs/${encodeURIComponent(runId.value)}`, { signal })
  } catch (err) {
    // Navigating away aborts the fetch; that is the reader's choice, not a
    // failure to render.
    if (err instanceof CancelledError) return
    error.value = errorText(err, 'something went wrong')
    run.value = null
  } finally {
    loading.value = false
  }
}

/**
 * Is this run happening RIGHT NOW, and is the store's single stream on it?
 *
 * The store holds one SSE connection. `useRunStream` points it at whatever
 * `/health` calls the active research run, which is right for the app frame and
 * wrong for someone who deliberately opened a different in-flight run -- with
 * two lanes there can be a lookup and a pipeline going at once (C11).
 * Navigating here IS the choice, so it wins.
 */
const isLive = computed(() => run.value?.status === 'running' || run.value?.status === 'queued')
/**
 * Offered only for a run this instance is actually running.
 *
 * `isLive` reads the ROW, and a `venona run` from a shell is a different
 * process: its row says running here while the cancel endpoint has no handle to
 * answer with. `runs.inFlight` asks the probe instead, so the button appears
 * exactly where it can work.
 */
const cancellable = computed(() => isLive.value && runs.inFlight(runId.value))
const confirmingCancel = ref(false)

async function confirmCancel() {
  confirmingCancel.value = false
  await runs.cancel(runId.value)
  await load()
}
const attached = computed(() => runs.attachedRunId === runId.value)

watch([isLive, runId], () => {
  if (isLive.value && !attached.value) runs.attach(runId.value, 0, run.value?.kind)
})

/**
 * The session behind a FINISHED run, on demand.
 *
 * `attach` replays the whole log before it sends the terminal `done`, so a
 * finished run's transcript needs no second endpoint -- the page simply never
 * asked, because it attached only while `isLive`. Every model call in the system
 * goes through `runner.call_and_record` or `analyze_document`, both of which
 * wire `transcript_emitter`, so `run_events` has held this for every kind since
 * it shipped and nothing read it back.
 *
 * ON FIRST OPEN, not on mount: a night's pipeline run is hundreds of messages,
 * and fetching them to render a heading nobody clicked is the reason this is
 * behind a disclosure at all.
 *
 * REFUSED WHILE A RESEARCH RUN IS LIVE. The store holds one SSE connection and
 * the app frame points it at the active research run; stealing it to replay
 * something that finished last week would stop the header following the run
 * that is actually spending money.
 */
const busyElsewhere = computed(() => runs.researchRunning && !attached.value)
const replayed = ref(false)

function replay() {
  if (isLive.value || busyElsewhere.value || attached.value) return
  runs.attach(runId.value, 0, run.value?.kind)
  replayed.value = true
}

onMounted(load)
watch(runId, load)

/**
 * Re-read the run itself while it is live, so the header chip and the stage
 * table follow it. The SSE stream drives the live panel; this drives everything
 * on the page that comes from `GET /runs/{id}`.
 */
const poll = usePoll(
  () => {
    // Kept in step with the poll so the elapsed-time readout is elapsed time
    // and not a frozen number. `load()` sets it too, for the first paint.
    nowMs.value = Date.now()
    return (
      apiGet<Run>(`/runs/${encodeURIComponent(runId.value)}`, { signal })
        .then((r) => (run.value = r))
        // A blip must not replace a rendered run with an error.
        .catch(() => {})
    )
  },
  { interval: 5000 },
)
watch(isLive, (live) => (live ? poll.start() : poll.stop()), { immediate: true })

const STAGE_COLUMNS: Column[] = [
  { key: 'stage', label: 'step' },
  { key: 'status', label: 'status' },
  { key: 'elapsed', label: 'elapsed', numeric: true },
  { key: 'detail', label: 'counts' },
]

/** One formatter for the app; see `lib/duration.ts` for why there is exactly
 *  one. Aliased rather than renamed at the call sites, which read better. */
const seconds = humanBetween

const stageRows = computed(() =>
  (run.value?.stages ?? []).map((s) => ({
    ...s,
    stage: s.stage.replace(/_/g, ' '),
    elapsed: seconds(s.started_at, s.finished_at ?? null),
    // Humanised, like the Result block below. The two rendered the same kind of
    // counter two different ways -- `grounded_in_article 205` beside
    // `documents fetched` -- on the same page.
    detail:
      s.error ??
      Object.entries(s.counts ?? {})
        // The narrowing has to be in the predicate's TYPE, not just its logic:
        // `Object.entries` yields `unknown` values, and a `typeof` check inside
        // `.filter` does not carry into `.map`.
        .filter((entry): entry is [string, number] => typeof entry[1] === 'number' && entry[1] !== 0)
        .map(([k, v]) => `${counterLabel(k)} ${v}`)
        .join(' · '),
  })),
)

/**
 * "Research · started 4 minutes ago · took 8m 23s".
 *
 * Replaces four rows of `started <ISO>`, `kind`, `profile` and `params` at the
 * foot of the page. Those answered questions a reader does not have: an ISO
 * stamp has to be converted in the head to the thing they wanted (how long
 * ago), and the profile hash and the params blob are operator details, which is
 * why they moved to the metrics page rather than being deleted.
 */
const summaryLine = computed(() => {
  if (!run.value) return ''
  const parts = [runLabel.value]
  // What started it. `scheduled` is the one run with no person below — the
  // scheduler is its author.
  parts.push(run.value.trigger === 'scheduled' ? 'scheduled' : 'on demand')
  const ago = agoFrom(run.value.started_at, nowMs.value)
  if (ago) parts.push(`started ${ago}`)
  const took = humanBetween(run.value.started_at, run.value.finished_at)
  if (took) parts.push(`took ${took}`)
  // WHO asked for it. `runs.started_by` has been written on every launch since
  // identity existed and read by nothing, so a run that spent money had no
  // author on any screen. Absent for the scheduler and for `venona run`, and
  // omitted rather than rendered as "unknown": a nightly run has no person
  // behind it, and saying so every night would be noise on the common case.
  if (run.value.started_by_email) parts.push(`by ${run.value.started_by_email}`)
  // WHICH SERVICE THOUGHT. Omitted rather than guessed on a row from before
  // the column existed, the same rule `started_by` follows above -- and the
  // same rule the outcome column follows, because a guessed value on a
  // historical row is worse than an absent one. It earns its place here rather
  // than only on /admin/model: once an instance has switched, "why does this
  // brief not read like last week's" is answered by this line and by nothing
  // else on the page.
  if (run.value.harness_label) parts.push(`thought by ${run.value.harness_label}`)
  return parts.join(' · ')
})

/** Contract C11. `run.label`/`run.detail` come from the server, but fall back
 *  through the store for a payload written before they existed. */
const runType = computed(() => runTypes.typeFor(run.value?.kind ?? ''))
const runLabel = computed(() => run.value?.label ?? runType.value.label)

/** A one-stage run renders its RESULT, not a stage table whose single row says
 *  everything twice. The result is `RunFacts` now rather than two hardcoded
 *  panels, so the row would restate the facts panel a block above it. */
const isSingle = computed(() => (run.value?.detail ?? runType.value.detail) === 'single')

/** Whether anything actually went wrong. A run that found nothing is not
 *  trouble, and neither is a row whose status claims a degradation it has no
 *  warning to back. `lib/runOutcome.ts` owns that judgement for every
 *  surface. */
const inTrouble = computed(() => !!run.value && hasTrouble(run.value))

// Split once, here, so the three panels cannot disagree about one message.
const degradations = computed(() => warningsOf(run.value?.warnings))
const todos = computed(() => todosOf(run.value?.warnings))
const notes = computed(() => notesOf(run.value?.warnings))

/**
 * Every non-zero counter, with the kind's declared `summary` first.
 *
 * The panel dumped `run.counters` in wire order while `LiveRunPanel` showed the
 * declared subset, so one run read two ways either side of its own finish. The
 * order is the fix rather than the filter: dropping to `summary` alone would
 * take `documents_fetched`, `entities_seen`, `entities_promoted` and
 * `curation_open` off a research run's page for good.
 */
const counterRows = computed(() => {
  const counters = (run.value?.counters ?? {}) as Record<string, number | undefined>
  const declared = runType.value.summary ?? []
  const shown = Object.entries(counters).filter(([, v]) => v !== undefined && v !== 0)
  const rank = (key: string) => {
    const at = declared.indexOf(key)
    return at === -1 ? declared.length : at
  }
  return shown.sort((a, b) => rank(a[0]) - rank(b[0]))
})

/** The rejection trace leads the page when the answer was no, so it is pulled
 *  out of the facts panel for that case alone. On a run that landed something
 *  it is a footnote and `RunFacts` keeps it. */
const rejectedFact = computed(() =>
  (run.value?.facts ?? []).find((f) => f.shape === 'candidates' && Array.isArray(f.value)),
)
const leadWithCandidates = computed(
  () => run.value?.outcome === 'nothing_found' && !!rejectedFact.value,
)

/** What `RunFacts` shows. The trace moves out when it is leading the page, so
 *  the same list is never rendered twice. */
const factsBelow = computed(() =>
  (run.value?.facts ?? []).filter((f) => !(leadWithCandidates.value && f.shape === 'candidates')),
)
const { apiDown } = useServiceHealth()
</script>

<template>
  <ViewShell
    width="wide"
    scroll="page"
    :loading="loading"
    :error="error"
    :identity="identity"
    :parent="{ label: 'runs', to: '/admin/runs' }"
  >
    <!-- Nothing in the centre track. How the run went is the question this page
         is opened to answer, and `RunResultHeader` answers it at the top of the
         body, in words and at heading size. A chip up here saying the same
         thing is the page telling a reader something it is about to tell them
         again, four lines lower.

         The trailing edge instead, in the zone beside the verbs. `Metrics` goes
         to another view rather than changing anything, so it is not a verb and
         `#actions` keeps its verbs-only rule (ADR 0181); it is not a tab either,
         being a different audience and, later, a different permission. `hidden`
         is a STATE, and it sits last so that the Restore button acting on it is
         the next thing along. -->
    <template #toggles>
      <Button
        variant="outline"
        icon="right"
        label="Metrics"
        @click="$router.push(`/admin/runs/${runId}/metrics`)"
      >
        Metrics
      </Button>
      <Chip v-if="run?.hidden_at" tone="neutral">hidden</Chip>
    </template>

    <template #actions>
      <!-- Beside Hide rather than instead of it: the two never apply at once,
           since a live run cannot be hidden and a finished one cannot be
           stopped. -->
      <Button
        v-if="cancellable"
        variant="outline"
        icon="close"
        tone="critical"
        :label="runs.isCancelling(runId) ? 'Cancelling' : 'Cancel'"
        :disabled="runs.isCancelling(runId)"
        @click="confirmingCancel = true"
      >
        {{ runs.isCancelling(runId) ? 'Cancelling…' : 'Cancel' }}
      </Button>
      <Button
        v-if="run?.hidden_at"
        variant="outline"
        icon="undo"
        label="Restore"
        @click="runs.unhide(runId).then(load)"
      >
        Restore
      </Button>
      <!-- Not while it is live. `hide_run` refuses a run whose owner process
           is still working (`RunIsLive` -> 409), so offering the control was
           offering an action the server was always going to reject. A run
           whose process actually died is reaped to a terminal status first,
           and becomes hideable then. -->
      <Button
        v-else-if="run && !isLive"
        variant="outline"
        icon="eye-off"
        label="Hide"
        @click="runs.hide(runId).then(load)"
      >
        Hide
      </Button>
    </template>

    <template #error>
      <!-- Offline first: with the API unreachable, "no such run" is a claim
           about a database nothing has been able to ask. -->
      <ApiUnreachable v-if="apiDown" />
      <EmptyState v-else title="No such run">
        Venona has no run with that id. Nothing in Venona deletes runs.
        <template #action>
          <Button icon="left" label="Back to runs" @click="$router.push('/admin/runs')">Back to runs</Button>
        </template>
      </EmptyState>
    </template>

    <div v-if="run" class="flex flex-col gap-4">
      <!-- The answer, first. Everything below it is how the run got there. -->
      <RunResultHeader v-if="!isLive" :run="run" />

      <!-- Why every candidate was refused, DIRECTLY under the answer when the
           answer was no.
           It lived at the foot of the lookup panel, four blocks down, which is
           precisely backwards: on a run that landed a definition the trace is
           a footnote, and on one that landed nothing it is the entire content
           of the page. Without it a lookup that failed its checks is
           indistinguishable from one that never ran, since both produce
           nothing, which is what the trace was added to fix. -->
      <Panel
        v-if="leadWithCandidates"
        variant="bare"
        title="What was read, and why none of it counted"
      >
        <RunCandidates :rejected="(rejectedFact!.value as unknown[])" />
      </Panel>

      <!-- What this run is and when, in one line at the top, replacing four
           rows of raw ISO/JSON at the foot. `title` keeps the exact stamp one
           hover away for anyone matching a run against a log. -->
      <!-- `tabular-nums` on the whole line rather than on a span: this is one
           interpolated string ("… · started 4m ago · took 1m12s"), so there is
           no element to hang it on, and the line ticks every second while a run
           is live -- which is precisely the reflow tabular figures prevent. -->
      <p class="text-sm tabular-nums text-text-secondary" :title="run.started_at ?? ''">
        {{ summaryLine }}
      </p>

      <!-- A run in flight IS this page. On the runs list the spine, counters, model
           session and log would share one panel serving whichever run the store
           happened to be attached to. -->
      <!-- Titled `Progress`, not the run's label: the label is the first
           thing in the summary line directly above, and on that list this panel
           carried it only because there was no other context there. -->
      <LiveRunPanel
        v-if="isLive && attached"
        title="Progress"
        :detail="isSingle ? 'single' : 'pipeline'"
        :summary="runType.summary ?? []"
        @brief="$router.push({ name: 'brief' })"
      />
      <StatusNote v-else-if="isLive">
        This run is live, but live output is following another run. Reload to follow this one.
      </StatusNote>

      <!-- THREE blocks, each fed by the messages' own levels rather than by
           the run's status. One heading chosen from the status put the
           curation backlog under an amber "Why" on any run that also
           degraded, and a real warning under a grey "Notes" on one that did
           not. Only degradations belong here. -->
      <Panel v-if="inTrouble" variant="bare" title="Why">
        <p v-if="run.error" class="err mono text-signal-critical text-sm mb-3">{{ run.error }}</p>
        <ul
          v-if="degradations.length"
          class="warnings list-disc pl-5 text-signal-high text-sm leading-normal"
        >
          <li v-for="(message, i) in degradations" :key="i">{{ message.text }}</li>
        </ul>
      </Panel>

      <!-- Work waiting on a PERSON. Not a degradation, and not an idle
           remark: it re-fires every run until somebody rules on it, so it
           gets the link to the surface where that is done rather than the
           name of a command. -->
      <Panel v-if="todos.length" variant="bare" title="Needs you">
        <ul class="todos list-disc pl-5 text-text-primary text-sm leading-normal">
          <li v-for="(message, i) in todos" :key="i">
            {{ message.text }}
            <RouterLink
              v-if="destinationFor(message)"
              :to="destinationFor(message)!.to"
              class="text-accent-primary underline"
            >
              {{ destinationFor(message)!.label }}
            </RouterLink>
          </li>
        </ul>
      </Panel>

      <!-- Observations with nothing to do about them. Still shown, because
           `ctx.note` exists to surface something without claiming a
           degradation, and hiding them would undo half of that. -->
      <Panel v-if="notes.length" variant="bare" title="Notes">
        <ul class="warnings list-disc pl-5 text-text-tertiary text-sm leading-normal">
          <li v-for="(message, i) in notes" :key="i">{{ message.text }}</li>
        </ul>
      </Panel>

      <!-- WHAT IT PRODUCED, from the kind's own declaration.
           Two hardcoded panels stood here: one for `ingest`, and one shaped for
           a lookup that every other single-stage kind fell into. A reader's
           brief therefore rendered a blank `subject` and `sourced: no` on a run
           that had just written a document. Nothing here knows what kind of run
           it is looking at; `run_types.RunType.facts` does. Contract C11. -->
      <Panel v-if="factsBelow.length" variant="bare" :title="runLabel">
        <RunFacts :facts="factsBelow" />
      </Panel>

      <!-- Steps stays multi-stage only. A one-stage run's single row would
           carry `considered 4 · grounded 1` in wire spellings, a block under
           the same two numbers in the panel above, and its status and elapsed
           are already in the summary line. What single-stage kinds are actually
           missing is the per-item trace, which is a run of its own to add. -->
      <Panel v-if="!isSingle" variant="bare" title="Steps">
        <Table :columns="STAGE_COLUMNS" :rows="stageRows" row-key="stage">
          <template #cell:status="{ value }">
            <StatusChip :status="String(value)" />
          </template>
          <template #cell:detail="{ row }">
            <span :class="row.error ? 'err text-signal-critical text-xs' : 'text-text-tertiary text-xs'">{{
              row.detail
            }}</span>
          </template>
          <template #empty>
            <EmptyState title="No step records">
              This run predates step timing, or ended before its first step finished.
            </EmptyState>
          </template>
        </Table>
      </Panel>

      <!-- HOW IT RAN. Not "Result": the result is the header at the top of
           the page and the panel above it, and three panels called Result on
           one screen is two of them lying.
           The kind's declared `summary` orders these rather than filtering
           them, so the page agrees with the live panel about what matters
           without dropping the six counters the live panel never shows. -->
      <!-- Only when there is something to say. A kind that writes no counter
           at all -- an export spends nothing and reads nothing new -- rendered
           this as a heading with a rule and empty space under it, which reads
           as a panel that failed to load rather than one with nothing to
           report. -->
      <Panel v-if="counterRows.length" variant="bare" title="How it ran">
        <FactList
          emphasis="figure"
          :items="counterRows.map(([key, value]) => ({
            key,
            label: counterLabel(key),
            shape: 'count',
            text: key === 'cost_usd' ? `$${Number(value).toFixed(3)}` : String(value),
          }))"
        />
      </Panel>

      <!-- THE HEAVY DATA, COLLAPSED, and in the same place for every kind.
           A run's transcript is the record of what the model actually did, and
           it was reachable only while the run was still going: `ModelTranscript`
           reads the store, and the store was attached only when `isLive`. The
           events have been in `run_events` permanently the whole time. -->
      <template v-if="!isLive">
        <Disclosure
          v-if="runType.uses_model"
          label="Model session"
          :hint="replayed && runs.transcriptTotal ? String(runs.transcriptTotal) : ''"
          @open="replay"
        >
          <StatusNote v-if="busyElsewhere" kind="info">
            A run is in progress, and live output is following it. This session can be read once
            that run finishes.
          </StatusNote>
          <ModelTranscript v-else-if="attached" bare />
          <p v-else class="text-text-tertiary text-sm">Nothing was recorded for this run.</p>
        </Disclosure>

        <Disclosure label="Run log" @open="replay">
          <StatusNote v-if="busyElsewhere" kind="info">
            A run is in progress, and live output is following it. This log can be read once that
            run finishes.
          </StatusNote>
          <RunLog v-else-if="attached && runs.log.length" :events="runs.log" />
          <p v-else class="text-text-tertiary text-sm">This run recorded no notes.</p>
        </Disclosure>
      </template>
    </div>
    <CancelRunDialog
      v-model:open="confirmingCancel"
      :label="run?.label"
      :subject="run?.subject_text"
      @confirm="confirmCancel"
    />
  </ViewShell>
</template>
