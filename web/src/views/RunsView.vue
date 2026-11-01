<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import ApiUnreachable from '@/components/shared/ApiUnreachable.vue'
import AdminRail from '@/components/nav/AdminRail.vue'
import RunLaunchDialog from '@/components/runs/RunLaunchDialog.vue'
import LookupAllButton from '@/components/curation/LookupAllButton.vue'
import GenerateQuestionsButton from '@/components/learn/GenerateQuestionsButton.vue'
import CancelRunDialog from '@/components/runs/CancelRunDialog.vue'
import DeepReadDialog from '@/components/runs/DeepReadDialog.vue'
import SecondSourceDialog from '@/components/runs/SecondSourceDialog.vue'
import QuotaWindows from '@/components/runs/QuotaWindows.vue'
import RunOutcomeChip from '@/components/runs/RunOutcomeChip.vue'
import StatBlock from '@/components/shared/StatBlock.vue'
import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import Dialog from '@/components/ui/Dialog.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import IconButton from '@/components/ui/IconButton.vue'
import Panel from '@/components/ui/Panel.vue'
import RailLayout from '@/components/ui/RailLayout.vue'
import Spinner from '@/components/ui/Spinner.vue'
import StateNotice from '@/components/ui/StateNotice.vue'
import Toggle from '@/components/ui/Toggle.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import Table, { type Column } from '@/components/ui/Table.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { useInfiniteScroll } from '@/composables/useInfiniteScroll'
import { useKeyBindings } from '@/composables/useKeyboard'
import { usePopover } from '@/composables/usePopover'
import { useSchedule } from '@/composables/useSchedule'
import { useServiceHealth } from '@/composables/useServiceHealth'
import { useHealthStore } from '@/stores/health'
import { useRunsStore } from '@/stores/runs'
import { useRunTypesStore } from '@/stores/runTypes'
import type { Run } from '@/types/run'
import Icon from '@/components/ui/Icon.vue'
import { asText } from '@/lib/text'
import { counterLabel } from '@/lib/runCopy'
import { verdictFor } from '@/lib/runOutcome'
import { todosOf, warningsOf } from '@/lib/runMessages'

/**
 * The stream is NOT attached or detached here. `useRunStream` owns it at app
 * level, so starting a run and navigating away to read the brief leaves the
 * transcript alive. This view renders; it does not own the connection.
 */
const runs = useRunsStore()
const runTypes = useRunTypesStore()
const health = useHealthStore()
/** Every run spends through the `claude` CLI, so this is the page that owns
 *  the outage notice. See `useServiceHealth` for what counts as down. */
const { apiDown, modelDown, modelWaiting, modelUnconfigured, modelDetail } =
  useServiceHealth()
const schedule = useSchedule()
const router = useRouter()
const clearing = ref(false)
/* WHICH run is being cancelled, not whether a modal is open. There are two
 * places to press it on this page -- the header button, which acts on the
 * pipeline, and any row in the In progress table -- and a confirmation that
 * cannot name its run is asking about whichever one you were not looking at. */
const cancelTarget = ref<{ id: string; label: string; subject: string } | null>(null)

/* The other way to start a run. `Run now` stays the primary action and keeps
 * its own button: a deep read is the rarer, more expensive and less reversible
 * of the two, so it sits one press further away rather than beside it as an
 * equal. `usePopover` is what makes this close when another loose panel opens;
 * see its own note on why every panel deciding that for itself gets it wrong.
 *
 * TELEPORTED AND FIXED, not absolute, and that is not a preference. This view's
 * `#actions` slot is teleported into `#context-actions`, which sits inside the
 * context bar's `overflow-hidden` frame -- so a panel positioned below the bar
 * is CLIPPED, whatever its z-index, and the only symptom is a caret that opens
 * nothing. `EntityHoverCard` is the pattern: teleport to `body`, position
 * `fixed` off the anchor's rect, clamp to the window. */
const runMenu = ref(false)
const runMenuRoot = ref<HTMLElement | null>(null)
const runMenuPanel = ref<HTMLElement | null>(null)
const runMenuAt = ref({ top: 0, left: 0 })
usePopover(runMenu)

const MENU_GAP = 4
/** Only for the first paint, before there is a panel to measure. */
const MENU_W_FALLBACK = 180

function placeRunMenu() {
  const anchor = runMenuRoot.value
  if (!anchor) return
  const rect = anchor.getBoundingClientRect()
  // MEASURED, not declared. The menu sizes to its content, so a width constant
  // here would be a second opinion about it that drifts the moment an item is
  // added -- and the symptom is a menu subtly off its anchor, which nobody
  // files. `EntityHoverCard` places twice for the same reason: once to get it
  // on screen, once with the real box.
  const width = runMenuPanel.value?.offsetWidth || MENU_W_FALLBACK
  const left = Math.max(
    MENU_GAP,
    Math.min(rect.right - width, window.innerWidth - width - MENU_GAP),
  )
  runMenuAt.value = { top: rect.bottom + MENU_GAP, left }
}

/* Containment is tested against the PANEL as well as the anchor. Teleporting
 * moves the menu out of `runMenuRoot`, so a root-only test closes on the
 * mousedown of the very item being pressed -- the button unmounts and the click
 * never lands, which reads as a menu item that does nothing. */
function onMenuBlur(e: MouseEvent) {
  if (!runMenu.value) return
  const target = e.target as Node
  if (runMenuRoot.value?.contains(target) || runMenuPanel.value?.contains(target)) return
  runMenu.value = false
}

function closeRunMenu() {
  runMenu.value = false
}

async function toggleRunMenu() {
  runMenu.value = !runMenu.value
  if (runMenu.value) {
    placeRunMenu()
    await nextTick()
    placeRunMenu()
  }
}

watch(runMenu, (open) => {
  if (open) {
    document.addEventListener('mousedown', onMenuBlur)
    // A fixed panel does not travel with its anchor, so it is dismissed rather
    // than chased: a menu is a decision in progress, not a thing to read while
    // scrolling past it.
    window.addEventListener('scroll', closeRunMenu, true)
    window.addEventListener('resize', closeRunMenu)
  } else {
    document.removeEventListener('mousedown', onMenuBlur)
    window.removeEventListener('scroll', closeRunMenu, true)
    window.removeEventListener('resize', closeRunMenu)
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onMenuBlur)
  window.removeEventListener('scroll', closeRunMenu, true)
  window.removeEventListener('resize', closeRunMenu)
})

/**
 * Every run somebody can start by hand, in the one place they look for runs.
 *
 * These were buttons on `/admin/curation`, which put them where the WORK is
 * rather than where the runs are: a curator reading a queue had three controls
 * that start a run, each appearing only on the tab it belonged to, and the Runs
 * page -- the page named after the thing -- offered one. A reader who wanted to
 * ground the backlog had to know which tab hid the button.
 *
 * `path` because these are not all `POST /runs`: two are curation's own
 * endpoints and they answer 202 with a run id the same way.
 */
const LAUNCHES = [
  // Ordered cheapest-and-most-local first: what Venona already holds, then
  // what it has to go and buy.
  { kind: 'redefine', icon: 'book-open', cost: 'free' },
  { kind: 'adjudicate', icon: 'check', cost: 'spends' },
  { kind: 'anchors', icon: 'target', cost: 'spends' },
  { kind: 'backfill', icon: 'waypoints', cost: 'spends' },
  { kind: 'definitions', icon: 'refresh-cw', cost: 'spends' },
  { kind: 'lookup_all', icon: 'search', cost: 'spends' },
  { kind: 'research', icon: 'search', cost: 'spends' },
  { kind: 'questions', icon: 'plus', cost: 'spends' },
] as const

/** The four with no wrapper of their own. The other four carry copy that
 *  belongs to one run only, so they keep their component and are mounted
 *  beside these. */
const MENU_DIALOGS = [
  {
    kind: 'redefine',
    icon: 'book-open',
    verb: 'Re-read',
    cost: 'free',
    // FREE, AND THE WHOLE CORPUS. `use_model=false` stops after the pass that
    // costs nothing, and `min_salience=0` makes it every name rather than only
    // the prominent ones: the argument for a floor is about time, and there is
    // no money here to weigh it against.
    path: '/entities/definitions/reread?use_model=false&min_salience=0',
    previewPath: undefined,
    body: {},
  },
  {
    kind: 'adjudicate',
    icon: 'check',
    verb: 'Rule',
    cost: 'spends',
    path: '/runs',
    previewPath: undefined,
    body: { kind: 'adjudicate' },
  },
  {
    kind: 'anchors',
    icon: 'target',
    verb: 'Suggest',
    cost: 'spends',
    path: '/curation/placements',
    previewPath: undefined,
    body: {},
  },
  // COUNTED AND PRICED, because this one goes to the web per name and can run
  // for hours. Six hundred names are waiting on this instance and each costs
  // about a quarter, so the dialog in front of it is the feature.
  {
    kind: 'definitions',
    icon: 'refresh-cw',
    verb: 'Look up',
    cost: 'spends',
    path: '/entities/definitions/refresh',
    previewPath: '/entities/definitions/preview',
    body: {},
  },
] as const

/** The dialog for each kind, so the menu can open one it does not own. */
const launchers = ref<Record<string, { show: () => void; blocked: string } | null>>({})

function setLauncher(kind: string, el: unknown) {
  launchers.value[kind] = (el as { show: () => void; blocked: string } | null) ?? null
}

function blockedFor(kind: string) {
  return launchers.value[kind]?.blocked ?? ''
}

function openLaunch(kind: string) {
  // The menu closes first. It is a decision in progress and the decision has
  // been made; leaving it open behind a modal also leaves its blur handler
  // armed over the dialog.
  runMenu.value = false
  launchers.value[kind]?.show()
}

/**
 * The "next run" stat: when the daily will fire, or why it will not.
 *
 * Offline replay outranks everything -- a computed fire time against fixtures
 * is a countdown to nothing. Disabled reads "off" rather than hiding the
 * block: an absent stat looks like a rendering bug, and "off" is the fact a
 * reader wondering about last night's brief actually needs.
 */
const nextRunStat = computed(() => {
  if (health.data?.offline) {
    return { value: '—', title: 'replaying recorded data: scheduled runs are off' }
  }
  if (!schedule.enabled.value) {
    return {
      value: 'off',
      title: 'scheduled runs are turned off in Settings; something outside Venona may still start them',
    }
  }
  return {
    value: schedule.untilLabel.value || schedule.nextRunLabel.value || '--',
    title: schedule.nextRunAt.value ?? undefined,
  }
})

/** What a run row is ABOUT. A lookup without its target is nine identical rows
 *  reading "Definition"; a research run without its brief is a row that spent
 *  $15 on nothing nameable. */
function targetOf(run: Run): string {
  // The kind's own answer first (`run_types.RunType.subject`, contract C11).
  // The cascade below is inference from param names, and it matched none of a
  // reader's brief -- so those rows named nothing at all on a page whose whole
  // job is telling nine runs of one kind apart. It stays as the fallback for a
  // row written before the kind declared a subject.
  if (run.subject_text) {
    return run.subject_text.startsWith('brief:') ? briefRef(run.subject_text) : run.subject_text
  }
  const params = (run.params ?? {}) as Record<string, unknown>
  // `surface` / `entity_id` without `target_kind` is a row written before the
  // params were normalised. Both endpoints have always written `kind:"lookup"`,
  // so the fallback is not optional.
  const direct = asText(params.name ?? params.surface ?? params.entity_id)
  if (direct) return direct
  // A pipeline run is about the brief it wrote; a rewrite about the brief it
  // rewrote. `brief_id` is stamped at finish, so a failed run falls through.
  if (run.brief_id) return briefRef(String(run.brief_id))
  if (params.rewrite_of) return briefRef(asText(params.rewrite_of))
  // Ingest: the page, by host — the full URL does not fit a column.
  if (typeof params.url === 'string') {
    try {
      return new URL(params.url).host
    } catch {
      return params.url
    }
  }
  // Entity research: ids are database values, so the count is the readable fact.
  if (Array.isArray(params.entities)) {
    return `${params.entities.length} entit${params.entities.length === 1 ? 'y' : 'ies'}`
  }
  return ''
}

/** The stamp in two halves, so the clock can be dropped at narrow widths.
 *
 *  Built in script rather than by slicing in the template: the separator has to
 *  live INSIDE the interpolation, because Vue collapses the whitespace between
 *  a mustache and an adjacent tag and the two halves render welded together. */
function startedDate(value: unknown): string {
  return asText(value).slice(0, 10)
}

function startedTime(value: unknown): string {
  const time = asText(value).slice(11, 19)
  return time ? ` ${time}` : ''
}

/** `brief:2026-08-18:<uuid>` → `brief:2026-08-18`. The date names it; the
 *  uuid is noise at column width. */
function briefRef(id: string): string {
  return id.split(':').slice(0, 2).join(':')
}

/**
 * One line of numbers for a finished run, in that kind's own terms.
 *
 * This replaces the `docs` and `calls` columns, which were two of the thirteen
 * frozen counters picked ONCE for every kind of run -- so a lookup rendered an
 * em dash under `docs` for a figure it had never tried to produce, and a
 * glossary sweep showed neither of the two numbers it exists to move.
 *
 * `RunType.summary` already says which counters a kind reports (contract C11);
 * this reads it rather than choosing again. At most two, because this is a
 * table cell and a run's full counter set is on its own page.
 */
/**
 * A counter label agreeing with its number.
 *
 * Every plural in `COUNTER_LABELS` singularises by dropping a trailing `s` --
 * clusters, names, edges, model calls -- and the labels that do not end in one
 * (`fetched`, `defined`, `to review`) are not counted nouns and are left alone.
 * Checked by `test_frontend_drift.py`, which already pins that map.
 */
function countLabel(key: string, value: number): string {
  const label = counterLabel(key)
  return value === 1 && label.endsWith('s') ? label.slice(0, -1) : label
}

function detailOf(run: Run): string {
  const summary = runTypes.typeFor(run.kind)?.summary ?? []
  const parts: string[] = []
  for (const key of summary) {
    // Money is not in the history feed. ADR 0057: on a flat-rate subscription a
    // dollar total implies a budget nothing is drawing down.
    if (key === 'cost_usd') continue
    const value = run.counters[key as keyof typeof run.counters]
    if (typeof value !== 'number' || !value) continue
    parts.push(`${value} ${countLabel(key, value)}`)
    if (parts.length === 2) break
  }
  return parts.join(' · ')
}

/**
 * What a batch run landed, out of what it tried.
 *
 * Read from the counters rather than the stage data, because the history
 * listing does not carry stages. Only the sweep kinds report both halves; for
 * everything else the chip keeps its phrase.
 */
/**
 * An error, short enough for a table cell and cut at a word.
 *
 * A fixed character slice cut `interrupted (process died after 0 stage(`
 * mid-token, which reads as corrupted text rather than as truncation. The full
 * string is on the run's own page.
 */
function clip(text: string, limit = 44): string {
  if (text.length <= limit) return text
  const cut = text.slice(0, limit)
  const lastSpace = cut.lastIndexOf(' ')
  return `${(lastSpace > limit / 2 ? cut.slice(0, lastSpace) : cut).trimEnd()}…`
}

function landedOf(run: Run): { done?: number; total?: number } {
  const grounded = run.counters.acronyms_grounded ?? run.counters.entities_defined
  const ungrounded = run.counters.acronyms_undefined
  if (typeof grounded !== 'number' || typeof ungrounded !== 'number') return {}
  return { done: grounded, total: grounded + ungrounded }
}

const IN_FLIGHT_COLUMNS: Column[] = [
  { key: 'label', label: 'run' },
  { key: 'trigger', label: 'trigger' },
  { key: 'subject', label: 'subject' },
  { key: 'lane', label: 'kind of work' },
  { key: 'stage', label: 'step' },
  // Unlabelled and hidden below `sm`, exactly like the history's own actions
  // column: a header names a thing that is not there most of the time, and a
  // hover-revealed button could never be reached by touch anyway. The run's
  // own page carries Cancel as a real button, which is the phone's path to it.
  { key: 'actions', label: '', width: '2.5rem', hideBelow: 'sm' },
]

/** The wire value, as words. `on_demand` is a database spelling. */
function triggerText(trigger: string | undefined): string {
  return trigger === 'scheduled' ? 'scheduled' : 'on demand'
}

/** Likewise. Translated at the render seam only: the store's `lane` is a wire
 *  fact that other code compares against, and renaming it there would break
 *  those comparisons silently. */
function laneText(lane: string | undefined): string {
  if (lane === 'research') return 'full run'
  if (lane === 'light') return 'quick task'
  return lane ?? ''
}

/**
 * The last-run note's tone and words, from the one place that decides them.
 *
 * A fixed `success` kind renders a failed run in green, which is the one
 * verdict that must not look like the others. `cancelled` is not a success
 * either, and it is not an error: nothing went wrong, someone stopped it.
 */
const finishedVerdict = computed(() =>
  verdictFor({
    status: runs.finished?.status ?? '',
    outcome: runs.finished?.outcome,
    outcome_text: runs.finished?.outcomeText,
  }),
)

const finishedKind = computed<'error' | 'info' | 'success'>(() => {
  const tone = finishedVerdict.value.tone
  if (tone === 'critical') return 'error'
  if (runs.finished?.status === 'cancelled') return 'info'
  return 'success'
})

/** What the run DID, not what its status column says. "Last run not found" is
 *  a sentence; "Last run partial" was a schema value read out loud. */
const finishedText = computed(() => finishedVerdict.value.text)

/**
 * History earns its place once there is any history: a past run, or a filter
 * the reader set. Hidden only in the one case where it has nothing to say and
 * something else on the page already says it -- nothing finished, and a run
 * going.
 */
const showHistory = computed(
  () => rows.value.length > 0 || (!inFlightRows.value.length && !runs.starting),
)

function openLive(row: { runId: string }) {
  void router.push(`/admin/runs/${encodeURIComponent(String(row.runId))}`)
}

/** In flight, so not hideable. Matches `_assert_not_live` on the server. */
const LIVE_STATUSES = new Set(['running', 'queued'])

/**
 * Seven columns, and `result` is the one a reader scans for.
 *
 * Each of the others earns its place by answering a different question:
 * `started` and `kind` and `subject` say WHICH run, `result` says how it went,
 * `detail` and `notes` say by how much. Two columns per question is how a
 * history grows to ten and stops fitting on a phone.
 *
 *   * `result` is one chip carrying both verdicts, per the rule in
 *     `lib/runOutcome.ts`.
 *   * `detail` is per kind. See `detailOf`.
 *   * `notes` holds the warning count or the error: both answer "is there
 *     something to read on this row".
 *   * A scheduled run has no subject of its own, so `subject` carries its
 *     trigger instead — and below `sm` it carries the kind as well, since that
 *     column cannot fit beside the result chip at 390px.
 *
 * `detail` and `notes` carry elaboration rather than identity, so they are the
 * two that go at 390px, which `docs/DESIGN.md` requires every page to work at.
 *
 * No cost column. The figure is still ledgered and still on the run's own
 * page, but on a flat-rate subscription a dollar total in the history feed
 * implies a budget nothing is drawing down. ADR 0057.
 */
const COLUMNS: Column[] = [
  { key: 'started_at', label: 'started' },
  // Below `sm` the kind moves INTO the subject cell rather than being lost:
  // four columns plus the result chip's 159px do not fit 390px, and a stacked
  // label costs a line where a column costs the whole row.
  { key: 'kind', label: 'kind', hideBelow: 'sm' },
  { key: 'target', label: 'subject' },
  { key: 'result', label: 'result' },
  { key: 'detail', label: 'detail', hideBelow: 'sm' },
  { key: 'notes', label: 'notes', hideBelow: 'sm' },
  // Hover-revealed, so touch could never reach it: hiding it below `sm` loses
  // nothing a phone had. The run's own page carries Hide and Restore as real
  // buttons.
  { key: 'actions', label: '', hideBelow: 'sm' },
]

/**
 * History is the PAST. A run in flight is listed above in `In flight`, with its
 * live stage and position, so leaving it here too put the same run on the page
 * twice -- once with progress and once as a row whose every counter is still
 * zero, which reads as a run that did nothing.
 *
 * Filtered on the run's own status rather than on the health list, so a run
 * this tab has not heard about yet is still excluded the moment its row says
 * `running`.
 */
const rows = computed(() =>
  runs.runs
    .filter((run) => !LIVE_STATUSES.has(run.status))
    .map((run) => ({
      ...run,
      target: targetOf(run),
      detailText: detailOf(run),
      landed: landedOf(run),
    })),
)

/** A queued sweep fills whole pages of the history feed with live rows, and
 *  every one of them belongs to the in-flight surfaces instead — leaving "No
 *  runs yet" under a non-zero total. Pull further pages until a settled row
 *  lands or the list runs out; each append re-fires this via `runs.length`. */
watch(
  () => [runs.runs.length, rows.value.length, runs.loading] as const,
  () => {
    // `loading` gates this as well as the store's own in-flight guard: the
    // guard makes a double-fire harmless, the gate keeps this watcher from
    // burning a re-render on a call that would no-op anyway.
    if (!rows.value.length && runs.canLoadMore && !runs.loading) void runs.loadMore()
  },
  { immediate: true },
)

/** Scrolling near the history's end pulls the next page. The scroller is the
 *  ViewShell's (`scroll="page"` — the window never scrolls in this app), and
 *  the Load more button stays as the keyboard path. */
const shell = ref<{ scroller: HTMLElement | null } | null>(null)
const moreSentinel = ref<HTMLElement | null>(null)
useInfiniteScroll(
  () => shell.value?.scroller ?? null,
  moreSentinel,
  () => void runs.loadMore(),
  { disabled: () => runs.loading || !runs.canLoadMore },
)

/** The page's subject, as figures -- rather than a 150px panel of inert stage
 *  pills.
 *
 *  `calls` rather than a dollar total: on a flat-rate subscription money is a
 *  shadow price -- API list rates for tokens nothing is billed for -- and what
 *  bounds a run is how much WORK it attempts, the same quantity
 *  `llm.max_calls_per_run` caps. See ADR 0057. The limit itself is the strip
 *  below the counters.
 */
const totals = computed(() => {
  const list = runs.runs
  return {
    count: runs.total || list.length,
    calls: list.reduce((n, r) => n + (r.counters?.llm_calls ?? 0), 0),
    // Degradations only. Counting every message put the curation backlog and
    // the ontology's routine rejections into an amber stat headed "warnings",
    // which is the same false alarm the status split was made to remove.
    warnings: list.reduce((n, r) => n + warningsOf(r.warnings).length, 0),
    todos: list.reduce((n, r) => n + todosOf(r.warnings).length, 0),
    last: list[0]?.started_at?.slice(11, 16) ?? '--',
  }
})

/**
 * Everything in flight, both lanes, from `/health.runs.active` -- which is
 * contract C11's whole point: a light run queued behind a full lane is
 * distinguishable from a running one, and there can be several at once.
 *
 * The stage is attached only to the run the store is streaming. There is one
 * SSE connection, so `runs.stages` describes THAT run; showing it beside every
 * row would report one run's progress against another's name.
 */
/** The queue behind the lanes, one count per run type. Every ACTIVE run has a
 *  row in the in-flight table below, so names belong there; what the backlog
 *  needs to say is only how deep it is. Fifty queued lookups rendered as fifty
 *  wrapped spinners — unreadable at exactly the moment the size of the queue
 *  was the fact a reader wanted. */
const queuedGroups = computed(() => {
  const groups = new Map<string, { label: string; queued: number }>()
  for (const r of health.data?.runs?.queued ?? []) {
    const g = groups.get(r.kind) ?? {
      label: r.label || runTypes.typeFor(r.kind).label,
      queued: 0,
    }
    g.queued += 1
    groups.set(r.kind, g)
  }
  return [...groups.entries()].map(([kind, g]) => ({ kind, ...g }))
})

const inFlightRows = computed(() => {
  const active = health.data?.runs?.active ?? []
  return active.map((r) => {
    const streamed = runs.attachedRunId === r.run_id
    const stage = streamed ? runs.stages.find((s) => s.status === 'running') : undefined
    const progress = stage?.progress
    return {
      runId: r.run_id,
      cancelling: runs.isCancelling(r.run_id),
      label: r.label || runTypes.typeFor(r.kind).label,
      trigger: runTypes.typeFor(r.kind).trigger,
      subject: r.subject ?? '',
      lane: laneText(r.lane),
      stage: (stage?.name ?? '').replace(/_/g, ' '),
      at: progress?.total ? `${progress.current ?? 0}/${progress.total}` : '',
    }
  })
})

function open(row: { id: string }) {
  void router.push(`/admin/runs/${encodeURIComponent(row.id)}`)
}

async function confirmClear() {
  await runs.hideAll()
  clearing.value = false
}

/** The pipeline, which is what the header button has always meant. */
function askCancelLive() {
  const id = runs.liveRunId
  if (!id) return
  const row = inFlightRows.value.find((r) => r.runId === id)
  cancelTarget.value = {
    id,
    label: row?.label ?? runTypes.typeFor(runs.attachedKind).label,
    subject: row?.subject ?? '',
  }
}

function askCancel(row: { runId: string; label: string; subject: string }) {
  cancelTarget.value = { id: row.runId, label: row.label, subject: row.subject }
}

async function confirmCancel() {
  const target = cancelTarget.value
  cancelTarget.value = null
  if (target) await runs.cancel(target.id)
}

// Deliberately no shortcut for "Run now". A run draws down a real usage window
// -- and a bare letter that spends a share of the week on a stray keystroke is
// not a convenience.
useKeyBindings([
  {
    keys: ['l'],
    label: 'Load more history',
    group: 'runs',
    run: () => {
      if (runs.canLoadMore) void runs.loadMore()
    },
  },
])

/** Segment fill per stage status. The spine is one object: done reads as a
 *  filled track, current as the accent, pending as almost nothing. */
/**
 * `admin · runs · 128 total · 1 in flight`.
 *
 * The in-flight count is the half a reader on this page actually came for, and
 * the identity line said only the total -- so the one fact that changes while
 * you watch was the one it did not carry. Appended rather than replacing the
 * total, and omitted entirely when nothing is running: a permanent "0 in flight"
 * is the same non-information the footer's "no active run" was.
 */
const identity = computed(() => {
  const inFlight = runs.lightRuns.length + (runs.researchRunning ? 1 : 0)
  const parts = [`admin · runs · ${totals.value.count} total`]
  if (inFlight) parts.push(`${inFlight} in progress`)
  return parts.join(' · ')
})

</script>

<template>
  <ViewShell ref="shell" width="wide" scroll="page" :identity="identity">
    <!-- What you SEE: the run state, and whether hidden runs are listed. -->
    <template #controls>
      <!-- Only the research chip. Light runs are already counted in the
           identity line and broken out by type in the strip below — a third
           statement of the same number was the duplication, not information. -->
      <Chip v-if="runs.researchRunning" tone="accent" dot>running</Chip>
    </template>

    <!-- A binary that stays on screen showing its state, which is a `Toggle`
         rather than a `Chip interactive` carrying its own state in its label:
         "58 hidden · show" had to spell out the action because nothing about a
         chip says which way it is set. The switch says it, so the label is
         free to state the fact. -->
    <template #toggles>
      <Toggle
        v-if="runs.hidden"
        :model-value="runs.includeHidden"
        @update:model-value="runs.setIncludeHidden($event)"
      >
        {{ runs.hidden }} hidden
      </Toggle>
    </template>

    <!-- No Spinner beside "Run now": progress belongs to the thing that is
         progressing, and that is the spine below. A disabled button already
         says the request is in flight. -->
    <template #actions>
      <div ref="runMenuRoot" class="relative inline-flex items-center">
        <Button
          icon="play"
          label="Run now"
          :disabled="runs.researchRunning || runs.starting || modelDown"
          :title="modelDown ? 'the model is not answering; see the notice below' : undefined"
          @click="runs.trigger()"
          >Run now</Button
        >
        <!-- `IconButton` takes a SLOT, not an `icon` prop -- an undeclared
             attribute falls through to the real button, so `icon="down"` drew
             a correctly sized, entirely empty square. -->
        <IconButton
          label="Other ways to read"
          :disabled="runs.researchRunning || runs.starting || modelDown"
          :aria-expanded="runMenu"
          aria-haspopup="menu"
          @click="toggleRunMenu"
        >
          <Icon name="down" />
        </IconButton>
      </div>
      <!-- OUT to `body`, because `#context-actions` sits inside the context
           bar's `overflow-hidden` frame and clips anything hanging below it.
           Panel and item styling copy `UserMenu`, this app's one menu, rather
           than holding a second opinion about a menu's radius and padding; the
           ITEM is a `Button` because one entry is a control, not a full-bleed
           row, so none of `lint_tokens`'s BUTTON_ALLOWLIST arguments apply. -->
      <Teleport to="body">
        <div
          v-if="runMenu"
          ref="runMenuPanel"
          role="menu"
          class="menu fixed z-popover flex flex-col whitespace-nowrap rounded-lg border border-border-default bg-surface-overlay p-1"
          :style="{ top: `${runMenuAt.top}px`, left: `${runMenuAt.left}px` }"
        >
          <!-- ROWS ONLY. Every dialog is mounted at the view root below,
               because `Dialog` teleports to `body` and so a dialog rendered
               in here is NOT contained by `runMenuPanel`: the first mousedown
               inside it closes the menu, unmounts the dialog and swallows the
               click. That is this file's own `onMenuBlur` comment, one level
               up, and it renders as a button that does nothing. -->
          <Button
            v-for="item in LAUNCHES"
            :key="item.kind"
            variant="text"
            case="literal"
            align="start"
            :icon="item.icon"
            role="menuitem"
            class="w-full px-2"
            :disabled="!!blockedFor(item.kind)"
            :title="blockedFor(item.kind) || runTypes.typeFor(item.kind).description"
            @click="openLaunch(item.kind)"
          >
            {{ runTypes.typeFor(item.kind).label }}…
            <span class="ml-auto pl-3 text-xs text-text-tertiary">{{ item.cost }}</span>
          </Button>
        </div>
      </Teleport>
      <!-- Disabled once asked, and saying so. A press that changes nothing and
           says nothing reads as a press that did not land, which is what sent
           people to the button a second time. -->
      <Button
        v-if="runs.researchRunning"
        variant="outline"
        icon="close"
        :label="runs.isCancelling() ? 'Cancelling' : 'Cancel'"
        tone="critical"
        :disabled="runs.isCancelling()"
        @click="askCancelLive"
      >
        {{ runs.isCancelling() ? 'Cancelling…' : 'Cancel' }}
      </Button>
      <!-- Nothing to clear is not a thing to offer. -->
      <Button
        v-else
        variant="outline"
        icon="trash-2"
        label="Clear history"
        :disabled="!totals.count"
        @click="clearing = true"
        >Clear history</Button
      >
    </template>

    <RailLayout>
      <template #rail>
        <AdminRail current="runs" />
      </template>

      <!-- ONE ELEMENT in this slot, and the `Dialog` below is deliberately
           outside it: `RailLayout` wraps neither slot, so a second child here
           would land in the rail's own grid as a third column. -->
      <div class="flex flex-col gap-6">
      <!-- No <Masthead>: the ContextBar states this route's identity, and two
           headings for one page is worse than none. The stat row stays -- it is
           the page's data, not its title. -->
      <header class="masthead pb-4 border-b border-b-border-subtle">
        <div class="stats flex items-center gap-6 flex-wrap">
          <StatBlock :value="totals.count" label="runs" />
          <StatBlock :value="totals.calls" label="model calls" />
          <StatBlock
            :value="totals.warnings"
            label="warnings"
            :tone="totals.warnings ? 'high' : 'neutral'"
          />
          <StatBlock v-if="totals.todos" :value="totals.todos" label="need you" tone="neutral" />
          <StatBlock :value="totals.last" label="last run" />
          <StatBlock :value="nextRunStat.value" label="next run" :title="nextRunStat.title" />
        </div>

        <!-- Beside the counts because it answers the question they raise. "Can
             I start another run" is a question about the usage window, and this
             is the only figure on the page that can answer it. -->
        <QuotaWindows
          class="mt-4 max-w-measure"
          :windows="health.data?.quota?.windows ?? []"
          :available="health.data?.quota?.available ?? false"
          :billing-mode="health.data?.model?.billing_mode ?? 'subscription'"
          :reported="health.data?.quota?.reported ?? true"
          :service-label="health.data?.model?.service_label ?? 'This service'"
        />
      </header>

      <!-- The two outages that pause this page. API first: with the whole API
           unreachable, blaming the model would be a guess about a server that
           is not answering. -->
      <ApiUnreachable v-if="apiDown">
        Reading works from what is already loaded; new runs are paused until it comes
        back. Venona keeps trying on its own.
      </ApiUnreachable>

      <!-- BEFORE the outages, because it is not one. Nothing here recovers on
           its own: an administrator disconnected the service or switched to one
           with nothing to sign in with, and the only sentence worth showing is
           the one that says where to fix it. -->
      <StateNotice
        v-else-if="modelUnconfigured"
        tone="high"
        status="no service · runs paused"
        heading="No model service is connected"
      >
        Past runs still read fine. Nothing new can run until a service is connected.
        <template #action>
          <Button variant="text" href="/admin/model">Open Model</Button>
        </template>
      </StateNotice>

      <StateNotice
        v-else-if="modelWaiting"
        tone="high"
        status="provider outage · waiting"
        heading="The model provider is returning errors"
      >
        Past runs still read fine. A research run in progress is waiting for
        the provider to recover rather than giving up, and the scheduled run
        starts itself again if it finished short. Lookups fail immediately
        until it clears.
        <span class="mono">{{ modelDetail }}</span>
        <template #action>
          <span class="flex items-center gap-2">
            <Spinner /><span class="font-ui text-2xs text-text-quiet">waiting</span>
          </span>
        </template>
      </StateNotice>

      <StateNotice
        v-else-if="modelDown"
        tone="high"
        status="model down · runs paused"
        heading="The model stopped answering"
      >
        Past runs still read fine. New runs and acronym lookups are paused
        until the model answers again; Venona keeps checking.
        <span class="mono">{{ modelDetail }}</span>
        <template #action>
          <Button variant="text" href="/admin/model">Check the connection</Button>
        </template>
      </StateNotice>

      <StatusNote v-if="runs.error" kind="error">{{ runs.error }}</StatusNote>

      <!-- Light runs in flight (contract C11). They finish in well under a
           minute, so without this the only trace of one is a history row that
           appears already finished. `queued` is shown as itself: the light lane
           holds three at once and the fourth waits, which is a different thing
           from being slow. -->
      <div
        v-if="queuedGroups.length"
        class="inflight flex items-center gap-3 flex-wrap py-2 px-3 border border-border-subtle rounded-md bg-surface-raised"
      >
        <span
          v-for="group in queuedGroups"
          :key="group.kind"
          class="flex items-center gap-2 text-xs text-text-secondary"
        >
          <Spinner :size="10" />
          {{ group.label }}
          <span class="text-text-tertiary">· {{ group.queued }} queued</span>
        </span>
      </div>

      <!-- Titled from the run TYPE, not the literal string "Research run".
           That title was on screen whatever was running, so a one-call lookup
           announced itself as the pipeline. -->
      <!-- IN FLIGHT, as a list. This was one expanded panel -- spine, counters,
           model session and log -- for whichever run the store was attached to,
           which made this route two pages at once and could only ever show one
           run. Both lanes run independently (C11), so a lookup and a pipeline
           are routinely live together and the panel showed one of them.

           The readout lives on `/admin/runs/:id`, where it belongs to a run rather
           than to whatever the stream picked. What is left here is the fact
           that matters on a list page: what is going, and a way in. -->
      <Panel v-if="inFlightRows.length" variant="bare" title="In progress">
        <!-- A Table, and clicking a row opens it -- which is how the History
             below has always navigated. A list with a `Detail →` button on each
             row would be a second way to do the same thing on the same page. -->
        <Table
          :columns="IN_FLIGHT_COLUMNS"
          :rows="inFlightRows"
          row-key="runId"
          selectable
          :active-key="runs.attachedRunId"
          @select="openLive"
        >
          <template #cell:label="{ row }">
            <Chip tone="accent" dot>{{ row.label }}</Chip>
          </template>
          <template #cell:trigger="{ row }">
            <span class="text-xs text-text-tertiary whitespace-nowrap">{{ triggerText(row.trigger) }}</span>
          </template>
          <template #cell:stage="{ row }">
            <!-- What it is doing, unless it has been told to stop -- which is
                 the more useful fact about it and the only confirmation the
                 person who pressed Cancel gets while it winds down. -->
            <span v-if="row.cancelling" class="text-xs text-text-secondary">cancelling…</span>
            <!-- Stage and position only for the run the stream is following:
                 there is one SSE connection, so claiming a stage for the others
                 would report this run's progress under that one's name. -->
            <span v-else-if="row.stage" class="mono text-xs text-text-secondary">
              {{ row.stage }}<template v-if="row.at"> · {{ row.at }}</template>
            </span>
            <span v-else class="text-xs text-text-quiet">no live output</span>
          </template>
          <!-- Per row, because the header button acts on the pipeline and
               nothing else could reach a light run at all: a whole-queue
               acronym sweep runs for hours and spends, and stopping it meant
               restarting the process. -->
          <template #cell:actions="{ row }">
            <IconButton
              v-if="!row.cancelling"
              size="sm"
              :label="`Cancel ${row.label}`"
              @click.stop="askCancel(row)"
            >
              <Icon name="close" />
            </IconButton>
          </template>
        </Table>
      </Panel>

      <!-- The finished run's outcome, on the list rather than in the live
           panel: it is how someone learns a brief landed without having the
           run's page open. -->
      <StatusNote v-else-if="runs.finished" :kind="finishedKind">
        <span class="flex items-center gap-2">
          Last run {{ finishedText }}.
          <Button
            v-if="runs.finished.briefId"
            variant="text"
            icon="external"
            @click="router.push({ name: 'brief' })"
          >
            New brief ready
          </Button>
        </span>
      </StatusNote>

      <!-- No History panel at all while the only run there has ever been is
           the one in flight above. `rows` now excludes live runs, so a first run
           left this panel rendering its "No runs yet" empty state -- inviting
           someone to start a run, with a Run now button the lane would refuse,
           directly beneath the run they had just started. -->
      <Panel v-if="showHistory" variant="bare" title="History">
        <Table
          :columns="COLUMNS"
          :rows="rows"
          row-key="id"
          selectable
          :active-key="runs.attachedRunId"
          @select="open"
        >
          <!-- The date always; the clock only where there is room for it. A
               19-character stamp is 161px of mono, which at 390px is 40% of
               the screen spent on seconds nobody is reading. -->
          <template #cell:started_at="{ value }">
            <span class="mono text-xs whitespace-nowrap">
              {{ startedDate(value) }}<span class="hidden sm:inline">{{ startedTime(value) }}</span>
            </span>
          </template>
          <!-- The type's LABEL, not the raw `kind`. "Definition" is what a
               reader is looking for; `lookup` is a database value. -->
          <template #cell:kind="{ row }">
            <span class="text-xs">{{ row.label }}</span>
          </template>
          <!-- Nowrap: `brief:2026-08-18` breaks at its hyphens otherwise, and
               a date split across two lines reads as two values.

               A scheduled run has no subject of its own worth a column, so it
               says so here — which is what let the `trigger` column go. -->
          <template #cell:target="{ row }">
            <!-- Wraps below `sm` only. Nowrap is right on a wide table -- a
                 `brief:2026-08-18` split at its hyphens reads as two values --
                 and wrong on a phone, where the alternative is not two lines
                 but a row running off the screen. -->
            <span class="block text-2xs text-text-tertiary sm:hidden">{{ row.label }}</span>
            <span
              v-if="row.target"
              class="mono text-xs text-text-tertiary whitespace-normal sm:whitespace-nowrap"
            >
              {{ row.target }}
            </span>
            <span v-else class="text-xs text-text-tertiary whitespace-nowrap">
              {{ triggerText(row.trigger) }}
            </span>
          </template>
          <!-- The one verdict column. `lib/runOutcome.ts` holds the rule: an
               execution problem wins, otherwise show what the run FOUND, in
               that kind of run's own words. A definition run reads `defined`
               or `not found`; a research run reads `brief written` or
               `nothing new`; a degraded run reads `degraded` whatever it
               found. -->
          <template #cell:result="{ row }">
            <RunOutcomeChip :run="row" :done="row.landed.done" :total="row.landed.total" />
          </template>
          <template #cell:detail="{ row }">
            <span class="text-xs text-text-tertiary whitespace-nowrap">{{ row.detailText }}</span>
          </template>
          <!-- Warnings and the error in ONE column: both answer "is there
               something to read on this row", and they were a column apart.
               The count, not the text — `13 warnings` looks different from
               `1` without opening the run. -->
          <template #cell:notes="{ row }">
            <span v-if="row.error" class="err text-signal-critical text-xs">
              {{ clip(row.error) }}
            </span>
            <span v-else-if="warningsOf(row.warnings).length" class="warn text-signal-high text-xs">
              {{ warningsOf(row.warnings).length }} warning{{
                warningsOf(row.warnings).length === 1 ? '' : 's'
              }}
            </span>
            <span v-else-if="todosOf(row.warnings).length" class="todo text-text-tertiary text-xs">
              {{ todosOf(row.warnings).length }} need{{
                todosOf(row.warnings).length === 1 ? 's' : ''
              }}
              you
            </span>
          </template>
          <template #cell:actions="{ row }">
            <IconButton
              v-if="row.hidden_at"
              size="sm"
              label="Restore this run to the history"
              @click.stop="runs.unhide(String(row.id))"
            >
              <Icon name="undo" />
            </IconButton>
            <!-- Nothing for a run still in flight: `hide_run` 409s on one
                 whose owner is alive, so the button could only ever fail. -->
            <IconButton
              v-else-if="!LIVE_STATUSES.has(String(row.status))"
              size="sm"
              label="Hide this run (reversible; nothing is deleted)"
              @click.stop="runs.hide(String(row.id))"
            >
              <Icon name="close" />
            </IconButton>
          </template>
          <template #empty>
            <EmptyState title="No runs yet">
              A run fetches the trade press, scores what it finds against your
              interests, and writes a brief.
              <template #action>
                <Button icon="play" :disabled="runs.starting || modelDown" @click="runs.trigger()">Run now</Button>
              </template>
            </EmptyState>
          </template>
        </Table>

        <div
          v-if="runs.canLoadMore"
          ref="moreSentinel"
          class="more pt-4 justify-center flex items-center"
        >
          <Button variant="text" icon="down" :disabled="runs.loading" @click="runs.loadMore()">
            Load more
          </Button>
        </div>
      </Panel>
      </div>
    </RailLayout>

    <Dialog
      v-model:open="clearing"
      title="Clear run history?"
      confirm-label="Hide them"
      confirm-icon="eye-off"
      @confirm="confirmClear"
    >
      This hides every run currently listed. <strong>Nothing is deleted</strong>;
      everything a run wrote stays, every document and relationship keeps
      pointing at the run that produced it, and the “hidden” chip above brings
      any of them back. A run still in progress is skipped.
    </Dialog>

    <CancelRunDialog
      :open="cancelTarget !== null"
      :label="cancelTarget?.label"
      :subject="cancelTarget?.subject"
      @update:open="cancelTarget = null"
      @confirm="confirmCancel"
    />


  </ViewShell>

    <!-- MOUNTED AT THE ROOT, never inside the run menu. See the note on the
         menu rows. Each renders no trigger of its own; the menu opens it. -->
    <DeepReadDialog :ref="(el) => setLauncher('backfill', el)" />
    <SecondSourceDialog :ref="(el) => setLauncher('research', el)" />
    <LookupAllButton :ref="(el) => setLauncher('lookup_all', el)" />
    <GenerateQuestionsButton :ref="(el) => setLauncher('questions', el)" />
    <RunLaunchDialog
      v-for="item in MENU_DIALOGS"
      :key="item.kind"
      :ref="(el) => setLauncher(item.kind, el)"
      :kind="item.kind"
      :icon="item.icon"
      :verb="item.verb"
      :cost="item.cost"
      :start-path="item.path"
      :preview-path="item.previewPath"
      :body="item.body"
    />
</template>
