import { defineStore } from 'pinia'
import { computed, reactive, ref, watch } from 'vue'

import { apiGet, apiPost, errorText } from '@/api/client'
import { streamRun } from '@/api/sse'
import { onFreshness } from '@/composables/useFreshness'
import { useEntitiesStore } from '@/stores/entities'
import { useHealthStore } from '@/stores/health'
import { useSessionStore } from '@/stores/session'
import { useRunTypesStore } from '@/stores/runTypes'
import { useToastsStore } from '@/stores/toasts'
import { createPagedList } from '@/lib/paging'
import { asText } from '@/lib/text'
import { verdictFor } from '@/lib/runOutcome'
import type { ModelMessage, Run, RunCounters, RunEvent, RunInFlight, RunList } from '@/types/run'
import type { RunHideAll, RunStarted } from '@/types/run'

// The pipeline's stages, and the default spine before a run is attached. A run's
// OWN stages come from its type (contract C11); these are the pipeline's.
// `test_the_ui_knows_every_stage` scrapes this literal out of the file to pin it
// against `orchestrator.STAGES`, so it stays a literal.
const STAGES = ['fetch', 'analyze', 'acronyms', 'brief', 'define', 'curate'] as const
const LOG_LIMIT = 300
// The transcript is chattier than the log — one analyze call emits several
// messages — so it gets its own, tighter window. Everything older is still on
// disk in `run_events`.
const TRANSCRIPT_LIMIT = 200
const PAGE = 25

/**
 * What a stage says about itself WHILE it runs.
 *
 * Named rather than a bare record so a template can do arithmetic on the
 * counts -- `Record<string, unknown>` typechecks in a template and fails in the
 * linter, which is where `[object Object]` gets caught.
 */
export interface StageProgress {
  current?: number
  total?: number
  backlog?: number
  /** True while a call is sitting out a transient failure rather than working. */
  waiting?: boolean
  /** Seconds until the next attempt, or null once the wait is over. */
  retry_in?: number | null
  /** What the service said, for the operator log rather than the strip. */
  why?: string
  /** Whatever else a stage chooses to put on its own progress. */
  [key: string]: unknown
}

export interface StageState {
  name: string
  /** `interrupted` is the server's word for a stage that was STOPPED rather than
   *  one that failed (`_settle_open_stages` writes it), and the client uses the
   *  same one so a run watched live and the same run reopened later cannot
   *  disagree about what colour its last stage was. */
  status: 'pending' | 'running' | 'ok' | 'error' | 'interrupted'
  counts: Record<string, unknown>
  /** Merged `stage_progress` payloads. Kept OUT of `counts`, which is the
   *  stage's final report and must not be half-written while it runs. */
  progress?: StageProgress
}

export const useRunsStore = defineStore('runs', () => {
  const hidden = ref(0)
  const includeHidden = ref(false)

  /**
   * The run this store's live log/transcript belongs to.
   *
   * Deliberately NOT called "active". A stream can end without a terminal event
   * -- `onError` below is that path -- and an `isRunning` defined as
   * `activeRunId !== null && finished === null` then latches TRUE forever,
   * because nothing is left to set `finished`: a nav badge driven from it never
   * goes out again. "Is a run in flight" is answered by `/health`, which the app
   * frame polls unconditionally. This id only says what we are streaming.
   */
  const attachedRunId = ref<string | null>(null)

  const counters = ref<RunCounters>({})
  const stages = ref<StageState[]>(
    STAGES.map((name) => ({ name, status: 'pending', counts: {} })),
  )
  const log = ref<RunEvent[]>([])
  const transcript = ref<(ModelMessage & { seq: number; ts: string; stage: string | null })[]>([])
  /**
   * How many model messages this run has produced, as opposed to how many are
   * still in the buffer.
   *
   * `transcript` is capped at TRANSCRIPT_LIMIT and spliced from the front, so
   * its `length` pins at the cap: read as the message count, it reported 200 for
   * a run that made 900 calls (2026-08-16). The cap is right -- a long run must
   * not grow the tab without limit -- and counting it is not. Rebuilt on
   * reconnect, because the server replays the whole event log.
   */
  const transcriptTotal = ref(0)
  /**
   * The last run this store saw end.
   *
   * `outcome` and `outcomeText` alongside `status`, because the two answer
   * different questions (ADR 0183). The `done` event carries the raw outcome
   * (C6); the PHRASE is resolved here from the run's own type, since an SSE
   * event is not decorated the way a row on the runs list is.
   */
  const finished = ref<{
    status: string
    outcome: string | null
    outcomeText: string | null
    briefId: string | null
  } | null>(null)
  const error = ref<string | null>(null)
  const starting = ref(false)

  /** The history list. `revalidate()` — not `refresh()` — is the answer to
   *  "something changed": it re-reads the window the reader has scrolled to
   *  build instead of truncating it back to one page. */
  const list = createPagedList<Run>({
    page: PAGE,
    key: (run) => String(run.id),
    onError: (message) => {
      error.value = message
    },
    fetchPage: async (limit, offset) => {
      const parts = [`limit=${limit}`, `offset=${offset}`]
      if (includeHidden.value) parts.push('include_hidden=true')
      const data = await apiGet<RunList>(`/runs?${parts.join('&')}`)
      hidden.value = data.hidden
      return { items: data.items, total: data.total }
    },
  })
  const runs = list.items
  const total = list.total
  const loading = list.loading
  const canLoadMore = list.canLoadMore
  const refresh = list.refresh
  const loadMore = list.loadMore
  const revalidate = list.revalidate

  let stopStream: (() => void) | null = null
  /**
   * Whether a stream is actually carrying events, as opposed to which run we
   * mean to be watching (`attachedRunId`, which outlives the connection so the
   * live panel keeps its subject).
   *
   * EventSource reconnects on its own only for a transient drop. On a hard
   * close it gives up for good, and without this nothing downstream can tell
   * that apart from a run that has simply gone quiet.
   */
  const streaming = ref(false)

  /** The stage list the live panel is rendering — the attached run's own, or
   *  the pipeline's before anything is attached. */
  const attachedKind = ref<string>('on_demand')

  /** Authoritative, and never stale: it comes from the polled health probe
   *  rather than from whether this tab happens to hold a stream. */
  const liveRunId = computed(() => useHealthStore().data?.active_run ?? null)

  /**
   * What a run in flight actually IS, from the probe rather than from whatever
   * this tab last launched.
   *
   * Both lanes, and queued as well as active: a run reaches `attach` through
   * the stream the moment the probe names it, and it can be named while still
   * waiting for the lane.
   */
  function kindOf(runId: string | null): string | null {
    if (!runId) return null
    const health = useHealthStore().data?.runs
    const all = [...(health?.active ?? []), ...(health?.queued ?? [])]
    return all.find((r) => r.run_id === runId)?.kind ?? null
  }

  /**
   * Is the PIPELINE running? The name is the point: called `isRunning`, this
   * gets reached for to gate things like the hover card's "Look this up" button,
   * making one acronym impossible to ground for the tens of minutes a research
   * run takes — exactly when someone is reading a brief and hits an undefined
   * term. Under contract C11 a lookup runs in its own lane, so
   * the only thing that legitimately keys off this is work that would collide
   * with the pipeline: starting a second one, or rebuilding the projection.
   */
  const researchRunning = computed(() => liveRunId.value !== null)

  /**
   * Runs this tab has asked to stop, while they are still in flight.
   *
   * The server reports the same fact on every run in flight (`cancelling`), and
   * that is the authority — this set only covers the seconds before the next
   * probe lands. Membership is meaningless once a run leaves the in-flight
   * list, so `cancellingIds` reads THROUGH that list rather than from here.
   */
  const asked = reactive(new Set<string>())

  const cancellingIds = computed(() => {
    const health = useHealthStore().data?.runs
    const inFlight = [...(health?.active ?? []), ...(health?.queued ?? [])]
    const ids = new Set<string>()
    for (const run of inFlight) {
      if (run.cancelling || asked.has(run.run_id)) ids.add(run.run_id)
    }
    return ids
  })

  /** Is this run stopping? With no id, the research run the Runs page's own
   *  Cancel button acts on. */
  function isCancelling(runId?: string | null) {
    const id = runId ?? liveRunId.value ?? attachedRunId.value
    return Boolean(id && cancellingIds.value.has(id))
  }

  /**
   * Is this run in flight IN THIS PROCESS?
   *
   * Not the same question as `status === 'running'`. A `venona run` from a
   * shell is a different pid and is not in the registry, so its row reads
   * running here and the cancel endpoint answers 404 for it. Only a run the
   * probe lists can be stopped, so only that one is offered the control.
   */
  function inFlight(runId: string | null) {
    if (!runId) return false
    const health = useHealthStore().data?.runs
    return [...(health?.active ?? []), ...(health?.queued ?? [])].some((r) => r.run_id === runId)
  }

  /** Light runs in flight, from the same health probe.
   *
   *  `active` and `queued` are optional on the generated type because they
   *  carry pydantic defaults, and a defaulted field is not `required` in JSON
   *  Schema even though FastAPI always serialises it. Coalescing is cheaper
   *  than arguing with that. */
  const lightRuns = computed<RunInFlight[]>(() => {
    const health = useHealthStore().data?.runs
    if (!health) return []
    return [...(health.active ?? []), ...(health.queued ?? [])].filter((r) => r.lane === 'light')
  })

  /** Re-seed the spine from a run type's stages, so a one-stage lookup does not
   *  draw the whole pipeline with nothing lit on it. */
  function stagesFor(kind: string): StageState[] {
    const declared = useRunTypesStore().typeFor(kind).stages
    const names = declared.length ? declared : [...STAGES]
    return names.map((name) => ({ name, status: 'pending' as const, counts: {} }))
  }

  /**
   * The spine follows the run, even when the run's kind arrives after it does.
   *
   * `attach` resolves the kind when it can, and there is a window where nobody
   * can: the probe has not listed the run yet, or the run page has not loaded
   * it. Without this the guess taken in that window is the spine for the whole
   * run, because `useRunStream` will not re-attach to a run it is already on.
   *
   * What has already been observed is carried across by NAME rather than by
   * position -- two stage lists share names at different indices -- and a stage
   * reported under a name the new list does not declare is kept, for the same
   * reason `applyEvent` appends one rather than dropping it.
   */
  watch(
    () => (attachedRunId.value ? kindOf(attachedRunId.value) : null),
    (resolved) => {
      if (!resolved || resolved === attachedKind.value) return
      attachedKind.value = resolved
      const seen = new Map(stages.value.map((s) => [s.name, s]))
      const next = stagesFor(resolved).map((s) => seen.get(s.name) ?? s)
      const declared = new Set(next.map((s) => s.name))
      for (const stage of stages.value) {
        if (!declared.has(stage.name) && stage.status !== 'pending') next.push(stage)
      }
      stages.value = next
    },
  )

  function resetLive() {
    counters.value = {}
    stages.value = stagesFor(attachedKind.value)
    log.value = []
    transcript.value = []
    transcriptTotal.value = 0
    finished.value = null
    error.value = null
  }

  function applyEvent(event: RunEvent) {
    // The model CLI session behind the current stage. Kept out of `log` so the
    // operator log stays a short list of decisions rather than a firehose.
    if (event.type === 'model_message') {
      transcriptTotal.value += 1
      transcript.value.push({
        ...(event.data as unknown as ModelMessage),
        seq: event.seq,
        ts: event.ts,
        // The transcript row wants a definite `string | null`.
        stage: event.stage ?? null,
      })
      if (transcript.value.length > TRANSCRIPT_LIMIT) {
        transcript.value.splice(0, transcript.value.length - TRANSCRIPT_LIMIT)
      }
      return
    }

    if (event.type === 'counter') {
      counters.value = { ...counters.value, ...(event.data as RunCounters) }
      return
    }

    if (event.stage) {
      // Do NOT drop events whose stage name is unknown: that is how `persist`
      // was emitted and invisible from S1 onwards. With per-type stage lists an
      // unknown name is more likely, not less, so an unrecognised stage is
      // appended rather than discarded.
      let stage = stages.value.find((s) => s.name === event.stage)
      // `stage_progress` TOO: a progress event for a name the list has never
      // heard of is the same silent hole ADR 0053 describes, and `persist` is
      // still emitted under a name this literal does not carry.
      if (
        !stage &&
        (event.type === 'stage_started' ||
          event.type === 'stage_finished' ||
          event.type === 'stage_progress')
      ) {
        stage = { name: event.stage, status: 'pending', counts: {} }
        stages.value.push(stage)
      }
      if (stage) {
        if (event.type === 'stage_started') stage.status = 'running'
        // Progress is not logged — it fires per item and would bury the
        // operator log — but it is the only live signal a long stage gives,
        // so it is kept on the stage itself.
        if (event.type === 'stage_progress') {
          // MERGED, not replaced. Two writers know different halves: the stage
          // reports how far through it is, and the retry reports that it is
          // waiting for the provider. Replacing means a wait erases the count
          // and the count erases the wait, so the strip flickers between the two
          // facts it is trying to state at once.
          stage.progress = {
            ...(stage.progress ?? {}),
            ...(event.data as unknown as StageProgress),
          }
          stage.status = 'running'
        }
        if (event.type === 'stage_finished') {
          stage.status = event.data.error ? 'error' : 'ok'
          stage.counts = event.data
        }
      }
    }

    // MESSAGES only. The spine above is the stage display and the stages table
    // carries the counts, so `stage_started`/`stage_finished` in here is a third
    // statement of the same thing -- a row per stage whose body renders as the
    // literal `{}` under a timestamp.
    if (['log', 'warning', 'error'].includes(event.type)) {
      log.value.push(event)
      // Bounded: a long run must not grow the tab's memory without limit.
      if (log.value.length > LOG_LIMIT) log.value.splice(0, log.value.length - LOG_LIMIT)
    }
  }

  /** Attach to a run's stream. Safe to call for a finished run — the server
   *  replays the whole log and then sends a terminal `done`. */
  function attach(runId: string, afterSeq = 0, kind?: string) {
    if (attachedRunId.value === runId && stopStream) return
    stopStream?.()
    attachedRunId.value = runId
    // RESOLVED, never carried over. Both callers pass a kind they may not have
    // yet -- the stream attaches off the health probe and the run page off a
    // run it is still loading -- and keeping the last one silently draws the
    // previous run's spine under this one. A second source run inherited the
    // six-stage research spine that way, then grew a seventh when its own
    // `research` stage reported: the header counted it 7 of 7 while the run
    // page, which had the run by then, counted the same run 1 of 2 (2026-08-08).
    attachedKind.value = kind ?? kindOf(runId) ?? attachedKind.value
    if (afterSeq === 0) resetLive()

    streaming.value = true
    stopStream = streamRun(
      runId,
      {
        onEvent: applyEvent,
        onDone: (event) => {
          const status = asText(event.data.status) || 'unknown'
          const briefId = (event.data.brief_id as string | null) ?? null
          const outcome = (event.data.outcome as string | null) ?? null
          const outcomeText = outcome
            ? (useRunTypesStore().typeFor(attachedKind.value ?? '').outcomes?.[outcome] ?? null)
            : null
          finished.value = { status, outcome, outcomeText, briefId }
          // NOTHING IS STILL RUNNING ONCE THE RUN IS OVER.
          //
          // A stage leaves `running` on `stage_finished`, and a run that
          // FAILS or is CANCELLED emits none: it ends on `error` or on the
          // terminal event alone. Without this the spine pulses the stage that
          // was open when it stopped, under a header already reading
          // `cancelled`, with its count frozen and the aria-label still
          // announcing work. `interrupted` is the server's own word for it
          // (`run_service._settle_open_stages` repairs its copy the same way),
          // so the live view and the reopened one agree about the last stage.
          if (status !== 'succeeded' && status !== 'succeeded_partial') {
            for (const stage of stages.value) {
              if (stage.status === 'running') stage.status = 'interrupted'
            }
          }
          // The wrapper closes itself on `done`; say so, or the run reads as
          // one whose stream was lost and gets re-attached on the next tick.
          streaming.value = false
          // A run rewrites entities and the graph, so every cached card is
          // potentially stale — and the TTL is ten minutes. Without this,
          // hovering right after a finished run shows the pre-run summary,
          // scores and signals, with no indication anything moved.
          useEntitiesStore().invalidate()
          // The operator may be anywhere — reading the brief, in the graph's
          // full screen — and the toast host is the one surface that reaches
          // them. The brief BY ID, not `/`: briefs are unique per generation, so
          // "the latest brief" and "the brief this run produced" differ the
          // moment two runs overlap, and landing on the wrong one is
          // indistinguishable from landing on the right one. No `subject`: ids
          // here are uuid7-length and wider than the toast. The verdict comes
          // from `verdictFor`, in the history table's words -- this once read
          // `run succeeded_partial`, a database value spelled out at a reader.
          const verdict = verdictFor({ status, outcome, outcome_text: outcomeText })
          useToastsStore().push({
            kind: verdict.tone === 'critical' ? 'error' : 'success',
            text: `run ${verdict.text}`,
            to: briefId ? `/briefs/${encodeURIComponent(briefId)}` : `/admin/runs/${runId}`,
            toLabel: briefId ? 'open brief' : 'open run',
          })
          // Health too, not just the history list. `In flight` is health-driven,
          // so refreshing history alone left the finished run sitting in it for
          // up to another 30s -- with its counters frozen, which reads as a run
          // that hung. Same asymmetry as `launched()`, at the other edge.
          void launched()
        },
        onError: (message) => {
          error.value = message
          // The wrapper has already closed the source. Releasing the handle is
          // what lets `attach` run again for this same id: the guard below
          // reads a non-null `stopStream` as a live connection, so a dead
          // stream would otherwise refuse every reconnect for the tab's life.
          stopStream = null
          streaming.value = false
        },
      },
      afterSeq,
    )
  }

  function detach() {
    stopStream?.()
    stopStream = null
    streaming.value = false
    attachedRunId.value = null
  }

  async function setIncludeHidden(value: boolean) {
    includeHidden.value = value
    await refresh()
  }

  /**
   * A run was just launched. Call this after EVERY launch, from anywhere.
   *
   * The `In flight` table reads `health.data.runs.active`, not this store's
   * history list. Without a re-probe a new run appears only on the next
   * scheduled tick, which is 30 SECONDS (`useAppPolling`); the 3s live cadence
   * keys off `lightRuns`, so a research-lane run -- `on_demand`, `daily`,
   * `rewrite` -- never speeds it up. Reloading the page looks like the fix
   * because `onMounted` ticks immediately: it is not a cache, it is a poll that
   * has not come round yet.
   *
   * Refreshing the HISTORY list alone is what hid this: the row shows up under
   * `History` while `In flight` stays empty, which reads as a run that already
   * finished. Both, together, because they answer different questions and the
   * page shows them side by side -- from launch sites all over the app, which
   * is why one place has to know what to do about it.
   */
  async function launched() {
    await Promise.allSettled([useHealthStore().refresh(), revalidate()])
  }

  async function trigger(params: Record<string, unknown> = {}) {
    starting.value = true
    error.value = null
    // WHATEVER WAS ASKED FOR, not a literal. `attach` decides which stage spine
    // the live panel draws and what it calls the run, so a kind named in
    // `params` has to reach all three of them. Pinned to `on_demand`, a deep
    // read draws the research kind's six stages -- one of which, `brief`, it
    // never runs -- under a title claiming a brief was coming.
    const kind = typeof params.kind === 'string' ? params.kind : 'on_demand'
    try {
      const data = await apiPost<RunStarted>('/runs', {
        kind: 'on_demand',
        ...params,
      })
      attachedKind.value = kind
      resetLive()
      attach(data.run_id, 0, kind)
      await launched()
      return data.run_id
    } catch (err) {
      error.value = errorText(err, 'something went wrong')
      return null
    } finally {
      starting.value = false
    }
  }

  /** Stop one run. Every caller names it; with no id, the live research run --
   *  the fallback `isCancelling()` reads for the Runs page's own button. */
  async function cancel(runId?: string) {
    const id = runId ?? liveRunId.value ?? attachedRunId.value
    if (!id) return
    // OPTIMISTICALLY, before the request. The server's own answer to "is this
    // one stopping" rides the health probe, which ticks every thirty seconds
    // while a research run is the only thing live — so waiting for it leaves
    // the button reading `Cancel` for half a minute after the press, which is
    // indistinguishable from a press that did nothing.
    asked.add(id)
    try {
      await apiPost(`/runs/${encodeURIComponent(id)}/cancel`)
      await useHealthStore().refresh()
    } catch (err) {
      asked.delete(id)
      error.value = errorText(err, 'something went wrong')
    }
  }

  /** Hiding, never deleting: `documents`, `sightings` and `assertions` all carry
   *  a run's id, so the history gets tidied rather than destroyed. ADR 0237. */
  async function hide(runId: string) {
    try {
      await apiPost(`/runs/${encodeURIComponent(runId)}/hide`)
      await revalidate()
    } catch (err) {
      error.value = errorText(err, 'something went wrong')
    }
  }

  async function unhide(runId: string) {
    try {
      await apiPost(`/runs/${encodeURIComponent(runId)}/unhide`)
      await revalidate()
    } catch (err) {
      error.value = errorText(err, 'something went wrong')
    }
  }

  async function hideAll() {
    try {
      const data = await apiPost<RunHideAll>('/runs/hide', {})
      await revalidate()
      useToastsStore().push({
        kind: 'success',
        text: `${data.hidden} run${data.hidden === 1 ? '' : 's'} hidden`,
      })
      return data
    } catch (err) {
      error.value = errorText(err, 'something went wrong')
      return null
    }
  }

  /**
   * A run started, finished, or emitted events — including one this browser did
   * not trigger: the scheduler, the CLI, a second tab.
   *
   * The history TABLE only. The attached run's stages and log come from the SSE
   * stream, which is already live and is the authority while it is connected;
   * refetching them here would race it.
   */
  onFreshness('runs', () => {
    // Admins only: `/api/runs` is admin, and this store is instantiated on
    // reader surfaces (the brief, hover cards) — so an ungated refetch here
    // hands every reader a 403 toast each time a run moves the stamp.
    if (!useSessionStore().isKnownAdmin()) return
    void revalidate()
  })

  return {
    runs, total, hidden, includeHidden, attachedRunId, attachedKind, streaming, counters, stages, log,
    transcript, transcriptTotal, finished, error, starting, liveRunId, researchRunning,
    lightRuns, cancellingIds, isCancelling, inFlight,
    canLoadMore, loading,
    attach, detach, refresh, revalidate, launched, loadMore, setIncludeHidden, trigger, cancel,
    hide, unhide, hideAll,
  }
})
