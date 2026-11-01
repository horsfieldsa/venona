import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { ApiError, apiGet, apiPost, errorText } from '@/api/client'
import { onFreshness } from '@/composables/useFreshness'
import { useSessionStore } from '@/stores/session'
import { awaitLookup, noteLookupLaunched, pushLookupStarted } from '@/lib/lookupRun'
import { createPagedList } from '@/lib/paging'
import { type WaitedRun } from '@/lib/waitForRun'
import { verdictFor } from '@/lib/runOutcome'
import { useEntitiesStore } from '@/stores/entities'
import { useHealthStore } from '@/stores/health'
import { useToastsStore } from '@/stores/toasts'
import type { RunInFlight } from '@/types/run'
import type {
  Conflict,
  ConflictList,
  ConflictOptions,
  CurationStatus,
  DefinitionQueue,
  Merge,
  MergeList,
  QueueItem,
} from '@/types/curation'

/** The route's cap, and its default: the queue is worked card-at-a-time, so one
 *  bounded read beats offset arithmetic the server's moot-row drop makes
 *  unreliable. */
const CONFLICTS_LIMIT = 500
/** How many decisions the undo stack keeps. Undo only ever reads the newest
 *  entry, so trimming the tail costs a curator nothing and a long session
 *  cannot grow the tab without limit. */
const HISTORY_LIMIT = 50
const DEFINITIONS_PAGE = 50
/** Refill the definitions buffer when the cursor gets this close to its end. */
const DEFINITIONS_REFILL_MARGIN = 15

/**
 * The curation queue. Two rules shape this store.
 *
 * **Decisions settle in place.** The queue is worked by keyboard (147 open
 * pairs, 2026-08-06), so a refetch after every action would throw away the
 * curator's position and make the counts flicker. Rows are removed locally and
 * the counters decremented; nothing reloads until the view is left.
 *
 * **Undo is a server call, not an illusion.** `unmerge` sets the mapping
 * inactive AND reopens the conflict it closed; keep-apart reverses by clearing
 * `resolved`. Fast keyboard decisions make misclicks certain.
 */
export interface Decision {
  conflict: Conflict
  action: 'merged' | 'kept_apart' | 'related'
  /** Present for merges — what `unmerge` needs. */
  mergeId?: string
  fromId?: string
}

export const useCurationStore = defineStore('curation', () => {
  const conflicts = ref<Conflict[]>([])
  const status = ref<CurationStatus | null>(null)
  const cursor = ref(0)
  const loading = ref(false)
  const busy = ref(false)
  const error = ref<string | null>(null)
  /** Most recent first. Bounded — this is an undo affordance, not an audit log;
   *  the durable record is `entity_mappings` and `venona entity merges`. */
  const history = ref<Decision[]>([])

  /** Newest first, so the tail is what goes. Never the head: `undo` reads
   *  `[0]`. */
  function trim<T>(stack: { value: T[] }): void {
    if (stack.value.length > HISTORY_LIMIT) stack.value.length = HISTORY_LIMIT
  }

  /** Something outside this tab changed the queue, and the reload is the
   *  curator's — one key, at a moment they choose. The `conflicts` handler at
   *  the foot of this file carries why. */
  const stale = ref(false)

  const current = computed<Conflict | null>(() => conflicts.value[cursor.value] ?? null)
  const remaining = computed(() => conflicts.value.length)
  const position = computed(() => Math.min(cursor.value + 1, conflicts.value.length))
  const pendingRebuild = computed(() => status.value?.pending_rebuild ?? 0)

  /**
   * Anchors were applied and the projection has not caught up.
   *
   * The same fact `pendingRebuild` reports, from a cause it cannot see: an
   * anchors apply rewrites that run's assertions in Postgres, which is not a
   * curation decision and so is not counted. It lives here rather than on the
   * config editor because the reader who needs it is the one who walked away
   * to look at the graph.
   */
  const anchorsAwaitingRebuild = ref(false)

  async function refreshStatus() {
    try {
      status.value = await apiGet<CurationStatus>('/curation/status')
    } catch {
      // The banner is informational; a failed poll must not break the queue.
    }
  }

  /** What `load` was last called with, so freshness can repeat it rather than
   *  silently widening a filtered queue back to everything. */
  const lastKind = ref<string | null | undefined>(undefined)
  /** Whether a queue fetch has SUCCEEDED — the revisit guard's half of the
   *  freshness contract. A failed load leaves it false, so an error state
   *  retries on the next mount rather than being cached as if it were data. */
  const queueLoaded = ref(false)

  async function load(kind?: string | null, force = false) {
    // The revisit guard: the same queue, already held, nothing stale — render
    // what is in memory. The `conflicts` stamp below reloads (or flags) any
    // out-of-band change, which is what a per-mount refetch would be asking.
    // `force` is for the callers that KNOW it moved: the freshness handler and
    // post-verdict reloads.
    if (!force && kind === lastKind.value && queueLoaded.value && !stale.value) return
    lastKind.value = kind
    stale.value = false
    loading.value = true
    error.value = null
    try {
      const parts = [`limit=${CONFLICTS_LIMIT}`]
      if (kind) parts.push(`kind=${encodeURIComponent(kind)}`)
      const data = await apiGet<ConflictList>(`/curation/conflicts?${parts.join('&')}`)
      conflicts.value = data.items
      cursor.value = 0
      queueLoaded.value = true
      // NOT awaited: the pair on screen fetches its own two cards when they are
      // not cached (`MergeReview`), so holding `loading` open for a lookahead of
      // eight pairs makes every curator wait on cards none of them can see yet.
      // It also keeps a failed prefetch out of the catch below, which treats any
      // throw as "the queue did not load" and empties it.
      void prefetchAround()
    } catch (err) {
      error.value = errorText(err, 'could not load the queue')
      conflicts.value = []
      queueLoaded.value = false
    } finally {
      loading.value = false
    }
  }

  /** Warm the cards for the next few pairs so moving through the queue never
   *  waits on a round trip. `prefetch` batches 100 ids into one request. */
  async function prefetchAround(lookahead = 8) {
    const entities = useEntitiesStore()
    const ids = conflicts.value
      .slice(cursor.value, cursor.value + lookahead)
      .flatMap((c) => [c.existing_entity_id, c.proposed_entity_id])
    if (ids.length) await entities.prefetch(ids)
  }

  function drop(conflictId: string) {
    const index = conflicts.value.findIndex((c) => c.id === conflictId)
    if (index === -1) return
    conflicts.value.splice(index, 1)
    // Stay where we are: the next pair slides into the same slot, which is what
    // makes a keyboard queue feel like one.
    if (cursor.value >= conflicts.value.length) cursor.value = Math.max(0, conflicts.value.length - 1)
    if (status.value) status.value.open_conflicts = Math.max(0, status.value.open_conflicts - 1)
    // A drained buffer with open rows still on the server means the queue was
    // longer than one bounded read. Reloading now is safe precisely because
    // the buffer is empty — there is no pair under the curator's fingers to
    // move. (`open_conflicts` may overcount — moot rows — so a reload can
    // legitimately come back empty; nothing re-triggers until the next drop.)
    if (!conflicts.value.length && (status.value?.open_conflicts ?? 0) > 0) {
      void load(lastKind.value, true)
    }
  }

  async function merge(conflict: Conflict, loserId: string, survivorId: string) {
    busy.value = true
    error.value = null
    try {
      // THE VERDICT, never the kind the pair was filed under. Recording the
      // kind made `curated: collision` a cohort of twenty-five merges, which
      // reads as a rule that merges collisions — the opposite of what every
      // one of them meant: a curator overruling the classifier. The kind is
      // not lost by saying so, because the conflict row keeps it alongside the
      // resolution; a merge reason is the only place the verdict can live.
      const result = await apiPost<Merge>('/curation/merges', {
        from_id: loserId,
        to_id: survivorId,
        reason: 'curated: merge',
      })
      history.value.unshift({
        conflict,
        action: 'merged',
        mergeId: result.merge_id,
        fromId: loserId,
      })
      trim(history)
      // Drop every row this merge answered, not just the one that was clicked.
      // The resolver files a conflict per SURFACE, so one real-world pair is
      // routinely several rows and the client cannot work out which ones:
      // `settled_conflict_ids` is the server's own list, matched on merge
      // CLOSURES rather than the two ids as posted, so it also catches a row
      // filed under an id since merged away. The incident behind it is in
      // `curate.close_conflicts_for_pair`.
      const answered = result.settled_conflict_ids?.length
        ? result.settled_conflict_ids
        : [conflict.id]
      for (const id of answered) drop(id)
      useEntitiesStore().invalidate()
      void refreshStatus()
      return true
    } catch (err) {
      // 409 carries a real refusal from `MergeError` — a cycle, or a survivor
      // that is itself merged away. The curator should read it.
      error.value = errorText(err, 'merge failed')
      return false
    } finally {
      busy.value = false
      void prefetchAround()
    }
  }

  /** The third verdict: different things, and here is how they relate.
   *  `General Dynamics` / `GDIT` is the shape — merging is wrong and keeping
   *  them apart discards the one fact the conflict surfaced. */
  async function relate(conflict: Conflict, subjectId: string, objectId: string, predicate: string) {
    busy.value = true
    error.value = null
    try {
      await apiPost(`/curation/conflicts/${encodeURIComponent(conflict.id)}/relate`, {
        subject_id: subjectId,
        object_id: objectId,
        predicate,
      })
      history.value.unshift({ conflict, action: 'related' })
      trim(history)
      drop(conflict.id)
      useEntitiesStore().invalidate()
      void refreshStatus()
      return true
    } catch (err) {
      // 422 is the direction error, and its message names the fix.
      error.value = errorText(err, 'could not record the relationship')
      return false
    } finally {
      busy.value = false
      void prefetchAround()
    }
  }

  /** Say what kind the thing is; both readings fold into one entry.
   *
   *  Settles the pair like a merge does, so the queue drops it and the entity
   *  cache is invalidated: the survivor may be an id this very call minted, and
   *  a card rendered from either original would be a tombstone. */
  async function correctType(conflict: Conflict, entityType: string) {
    busy.value = true
    error.value = null
    try {
      await apiPost(
        `/curation/conflicts/${encodeURIComponent(conflict.id)}/correct-type`,
        { entity_type: entityType },
      )
      history.value.unshift({ conflict, action: 'related' })
      trim(history)
      drop(conflict.id)
      useEntitiesStore().invalidate()
      void refreshStatus()
      return true
    } catch (err) {
      error.value = errorText(err, 'could not record the kind')
      return false
    } finally {
      busy.value = false
      void prefetchAround()
    }
  }

  /** Which predicates are legal for this pair, both ways round.
   *
   *  A failure is REPORTED rather than swallowed, and NULL rather than an empty
   *  list: `{forward: [], reverse: []}` makes "the server did not answer" and
   *  "no relationship connects these two types" the same value, and the Relate
   *  control is absent either way, so a 500 reads as an ontology fact. Setting
   *  `error` is not enough on its own -- the caller still has a legal-looking
   *  empty answer to render a sentence from. */
  async function options(conflict: Conflict): Promise<ConflictOptions | null> {
    try {
      return await apiGet<ConflictOptions>(
        `/curation/conflicts/${encodeURIComponent(conflict.id)}/options`,
      )
    } catch (err) {
      error.value =
        errorText(err, 'could not load the relationship options')
      return null
    }
  }

  async function keepApart(conflict: Conflict) {
    busy.value = true
    error.value = null
    try {
      await apiPost(`/curation/conflicts/${encodeURIComponent(conflict.id)}/keep-apart`, {})
      history.value.unshift({ conflict, action: 'kept_apart' })
      trim(history)
      drop(conflict.id)
      void refreshStatus()
      return true
    } catch (err) {
      error.value = errorText(err, 'could not record that')
      return false
    } finally {
      busy.value = false
      void prefetchAround()
    }
  }

  async function undo() {
    const last = history.value[0]
    if (!last) return false
    busy.value = true
    error.value = null
    try {
      if (last.action === 'merged' && last.mergeId && last.fromId) {
        await apiPost(
          `/curation/merges/${encodeURIComponent(last.mergeId)}/unmerge?from_id=${encodeURIComponent(last.fromId)}`,
          {},
        )
      } else {
        await apiPost(`/curation/conflicts/${encodeURIComponent(last.conflict.id)}/reopen`, {})
      }
      history.value.shift()
      // Put it back where it was, so undo restores the queue rather than
      // appending the pair to the end of it.
      conflicts.value.splice(cursor.value, 0, last.conflict)
      if (status.value) status.value.open_conflicts += 1
      useEntitiesStore().invalidate()
      void refreshStatus()
      return true
    } catch (err) {
      error.value = errorText(err, 'undo failed')
      return false
    } finally {
      busy.value = false
    }
  }

  function move(delta: number) {
    const next = cursor.value + delta
    if (next < 0 || next >= conflicts.value.length) return
    cursor.value = next
    void prefetchAround()
  }

  /**
   * The durable merge record: `GET /curation/merges`, the same list
   * `venona entity merges` prints.
   *
   * `history` above is a bounded, in-memory, single-session array that survives
   * neither a reload nor leaving the view, so it cannot answer "I merged
   * something an hour ago and it was wrong".
   */
  const mergesLoaded = ref(false)
  const mergeList = createPagedList<Merge>({
    page: 50,
    key: (merge) => merge.merge_id,
    onError: (message) => {
      error.value = message
    },
    fetchPage: async (limit, offset) => {
      const data = await apiGet<MergeList>(
        `/curation/merges?active_only=false&limit=${limit}&offset=${offset}`,
      )
      return { items: data.items, total: data.total }
    },
  })
  const merges = mergeList.items
  const mergesLoading = mergeList.loading

  async function loadMerges(force = false) {
    // History, not a queue: rows only change on a merge or unmerge, and both
    // ride the `entities` stamp (`entity_mappings` is one of its terms) — so a
    // revisit renders from memory and a real change reloads by itself.
    if (mergesLoaded.value && !force) return
    mergesLoaded.value = true
    await mergeList.refresh()
  }

  async function unmerge(merge: Merge) {
    busy.value = true
    error.value = null
    try {
      await apiPost(
        `/curation/merges/${encodeURIComponent(merge.merge_id)}/unmerge?from_id=${encodeURIComponent(merge.from_id)}`,
        {},
      )
      useEntitiesStore().invalidate()
      // The window the reader has scrolled to build, re-read in place — the
      // reversed row flips to "reversed" wherever it sits, however deep.
      await mergeList.revalidate()
      void refreshStatus()
      return true
    } catch (err) {
      error.value = errorText(err, 'unmerge failed')
      return false
    } finally {
      busy.value = false
    }
  }

  // ── the definitions queue ──────────────────────────────────────────
  //
  // The machinery lives here rather than in `DefinitionQueue.vue`, so a fix to
  // how a queue settles is made once; that component's docblock says what stays
  // with the surface.
  //
  // `busy` and `error` are the ones above, deliberately shared: the two queues
  // are mutually exclusive tabs on `CurationView`, so they can never both be
  // mounted, and the header's spinner reads `curation.busy` for either.

  const definitions = ref<QueueItem[]>([])
  const definitionsPending = ref(0)
  const definitionsCursor = ref(0)
  const definitionsLoading = ref(false)
  const definitionsLoaded = ref(false)
  /** Whether the last page came back full — the only safe has-more signal for
   *  a windowed queue (`total_pending` is display; comparing the buffer
   *  against it would refetch forever once counts drift out-of-band). */
  const definitionsLastPageFull = ref(false)
  let definitionsRefilling = false

  /** One step, like the merge queue's. Enough to take back the keystroke you
   *  did not mean; the durable reversal is the `reopen` call underneath.
   *  The ITEM rides along so undo can splice it back where it was — a page-0
   *  reload would clamp a deep cursor and destroy the curator's position. */
  const definitionsHistory = ref<{ surface: string; verdict: string; item: QueueItem }[]>([])

  const currentDefinition = computed<QueueItem | null>(
    () => definitions.value[definitionsCursor.value] ?? null,
  )

  async function loadDefinitions(force = false) {
    // The same revisit guard `load` uses: the `definitions` stamp reloads an
    // out-of-band change, so a mount refetch has nothing left to ask.
    if (definitionsLoaded.value && !force) return
    definitionsLoading.value = true
    error.value = null
    try {
      const data = await apiGet<DefinitionQueue>(
        `/curation/definitions?limit=${DEFINITIONS_PAGE}`,
      )
      definitions.value = data.items ?? []
      definitionsPending.value = data.total_pending
      definitionsLastPageFull.value = (data.items ?? []).length >= DEFINITIONS_PAGE
      definitionsCursor.value = Math.min(
        definitionsCursor.value,
        Math.max(0, definitions.value.length - 1),
      )
      definitionsLoaded.value = true
    } catch (err) {
      error.value = errorText(err, 'could not load the queue')
      definitions.value = []
      definitionsLoaded.value = false
    } finally {
      definitionsLoading.value = false
    }
  }

  /**
   * Pull the next window when the cursor nears the buffer's end.
   *
   * Append-only, behind the cursor's back: nothing here may touch `cursor` or
   * splice before it — the pair under the curator's fingers must not move.
   * `offset = definitions.length` stays exact because a verdict removes the
   * surface from the server's pending set and this buffer in the same step; a
   * verdict recorded elsewhere (CLI, second tab, a sweep) can skip rows, and
   * de-dupe absorbs the overlap.
   */
  async function refillDefinitions() {
    if (definitionsRefilling || !definitionsLastPageFull.value) return
    if (definitionsCursor.value < definitions.value.length - DEFINITIONS_REFILL_MARGIN) return
    definitionsRefilling = true
    try {
      const data = await apiGet<DefinitionQueue>(
        `/curation/definitions?limit=${DEFINITIONS_PAGE}&offset=${definitions.value.length}`,
      )
      const held = new Set(definitions.value.map((i) => i.normalized))
      const fresh = (data.items ?? []).filter((i) => !held.has(i.normalized))
      definitions.value.push(...fresh)
      definitionsPending.value = data.total_pending
      definitionsLastPageFull.value = (data.items ?? []).length >= DEFINITIONS_PAGE
    } catch {
      // Opportunistic: the next move or drop retries. The queue still works
      // from what it holds.
    } finally {
      definitionsRefilling = false
    }
  }

  /**
   * The decided surface leaves and the next one is on screen.
   *
   * `dropDefinition`, not `loadDefinitions()`. Exactly what `drop` above does
   * for merges, and it is the difference between a queue and a list: a refetch
   * keeps the row wherever it still matches the filter, so settling a surface
   * would change nothing you could see.
   */
  function dropDefinition(surface: string) {
    const index = definitions.value.findIndex((i) => i.surface === surface)
    if (index === -1) return
    definitions.value.splice(index, 1)
    definitionsPending.value = Math.max(0, definitionsPending.value - 1)
    if (definitionsCursor.value >= definitions.value.length) {
      definitionsCursor.value = Math.max(0, definitions.value.length - 1)
    }
    void refillDefinitions()
  }

  /** Run a verdict against the current surface, then drop it and advance. */
  async function definitionVerdict(
    name: string,
    run: () => Promise<unknown>,
    { advance = true } = {},
  ) {
    const item = currentDefinition.value
    const surface = item?.surface
    if (!item || !surface || busy.value) return false
    busy.value = true
    error.value = null
    try {
      await run()
      if (advance) {
        definitionsHistory.value.unshift({ surface, verdict: name, item })
        trim(definitionsHistory)
        dropDefinition(surface)
      }
      void refreshStatus()
      return true
    } catch (err) {
      // A 409 here carries a real refusal — a surface another entity already
      // owns, a reopen of something attached. The curator should read it, so it
      // stays on screen instead of fading in a toast.
      error.value = errorText(err, `${name} failed`)
      return false
    } finally {
      busy.value = false
    }
  }

  /** Lookups in flight, keyed by the exact `surface` the POST used. That same
   *  string is what `/health` reports as `subject` for an acronym lookup, so a
   *  reload re-attaches under the identical key. */
  const pendingLookups = ref(new Map<string, { runId: string; status: 'queued' | 'running' }>())

  /** Grounded web lookup. NOT a verdict, and not blocking either: the run
   *  queues on the light lane and curation moves on. The surface carries a
   *  pending badge until the run lands and its readings refresh in place. */
  async function lookupDefinition(alternatives = false) {
    const surface = currentDefinition.value?.surface
    if (!surface || busy.value || pendingLookups.value.has(surface)) return
    error.value = null
    try {
      const { run_id, status } = await apiPost<{ run_id: string; status: string }>(
        `/glossary/${encodeURIComponent(surface)}/lookup?alternatives=${alternatives}`,
        {},
      )
      noteLookupLaunched()
      trackLookup(surface, run_id, status === 'queued' ? 'queued' : 'running')
      pushLookupStarted(surface, status)
      moveDefinition(1)
    } catch (err) {
      error.value = errorText(err, 'lookup failed')
    }
  }

  /** One detached driver per surface: poll the run, then refresh that surface.
   *  A coalesced 202 hands back an already-tracked run and this no-ops. */
  function trackLookup(surface: string, runId: string, initial: 'queued' | 'running') {
    if (pendingLookups.value.has(surface)) return
    pendingLookups.value.set(surface, { runId, status: initial })
    void (async () => {
      try {
        // Failures reach the curator as a toast rather than through the shared
        // `error` ref: they may be three surfaces past this one by now, and
        // `error` belongs to the verdict in hand.
        const result = await awaitLookup(runId, surface, (s) => {
          const entry = pendingLookups.value.get(surface)
          if (entry && s === 'running') entry.status = 'running'
        })
        if (result.run && result.run.status !== 'cancelled') {
          await refreshSurface(surface, result.run)
        }
      } finally {
        pendingLookups.value.delete(surface)
      }
    })()
  }

  /** Refresh one surface's queue item after its lookup lands. A full
   *  `loadDefinitions()` would reorder the queue under the curator's fingers;
   *  this splices the one item that changed and touches nothing else. */
  async function refreshSurface(surface: string, run: WaitedRun) {
    const toasts = useToastsStore()
    try {
      // Sized to the loaded buffer (within the route's cap), because the fresh
      // copy has to be FOUND in this response for the splice below to work.
      const span = Math.min(200, Math.max(DEFINITIONS_PAGE, definitions.value.length))
      const data = await apiGet<DefinitionQueue>(`/curation/definitions?limit=${span}`)
      const index = definitions.value.findIndex((i) => i.surface === surface)
      if (index !== -1) {
        const old = definitions.value[index]
        const fresh = (data.items ?? []).find((i) => i.normalized === old.normalized)
        if (fresh) {
          definitions.value.splice(index, 1, fresh)
        } else {
          // The surface fell outside the fetch window; keep its chips honest.
          old.attempted = true
          old.attempt_count = old.attempt_count + 1
        }
      }
      definitionsPending.value = data.total_pending
      // ⭐ This GUESSED, and got it wrong on the case that matters. It read
      // `runStatus === 'succeeded' ? 'lookup finished' : 'lookup found nothing
      // new'`, so a lookup that hit its call ceiling told the curator the web
      // had no definition -- a fact about our budget reported as a fact about
      // the world. `status` could not tell those apart; `outcome` can, and
      // `verdictFor` is the one place either is turned into words. ADR 0183.
      const verdict = verdictFor(run)
      toasts.push({
        kind: verdict.tone === 'ok' ? 'success' : verdict.tone === 'critical' ? 'error' : 'info',
        text: `lookup ${verdict.text}`,
        subject: surface,
      })
    } catch (err) {
      toasts.push({
        kind: 'error',
        text: errorText(err, 'could not refresh the queue'),
        subject: surface,
      })
    }
  }

  /** Re-attach to lookups still in flight after a reload. `target_kind` is
   *  what tells an acronym lookup from an entity define — the two share
   *  `kind: "lookup"`, and an entity's name can collide with a surface. */
  async function seedPendingLookups() {
    const health = useHealthStore()
    if (!health.data) await health.refresh()
    const runs = health.data?.runs
    if (!runs) return
    const sides: Array<[RunInFlight[], 'running' | 'queued']> = [
      [runs.active ?? [], 'running'],
      [runs.queued ?? [], 'queued'],
    ]
    for (const [briefs, initial] of sides) {
      for (const run of briefs) {
        if (run.kind === 'lookup' && run.target_kind === 'acronym' && run.subject) {
          trackLookup(run.subject, run.run_id, initial)
        }
      }
    }
  }

  /** Undo the last verdict. `reopen` is the durable reversal; the queue puts the
   *  surface back where it was rather than appending it to the end. */
  async function undoDefinition() {
    const last = definitionsHistory.value[0]
    if (!last || busy.value) return false
    busy.value = true
    error.value = null
    try {
      await apiPost(`/glossary/${encodeURIComponent(last.surface)}/reopen`, {})
      definitionsHistory.value.shift()
      // Splice the surface back where the cursor is, as the merge queue's undo
      // does. A full reload here would truncate the buffer to one page and clamp
      // a deep cursor, throwing away the curator's position for the sake of
      // chips the reopen barely changed.
      if (!definitions.value.some((i) => i.normalized === last.item.normalized)) {
        definitions.value.splice(definitionsCursor.value, 0, last.item)
        definitionsPending.value += 1
      } else {
        definitionsCursor.value = definitions.value.findIndex(
          (i) => i.normalized === last.item.normalized,
        )
      }
      void refreshStatus()
      return true
    } catch (err) {
      // `attach-entity` wrote an alias and promoted an entity, which reaches
      // past this queue, so `reopen` refuses it rather than half-undoing.
      error.value =
        errorText(err, 'that verdict cannot be undone from here')
      return false
    } finally {
      busy.value = false
    }
  }

  function moveDefinition(delta: number) {
    definitionsCursor.value = Math.min(
      Math.max(definitionsCursor.value + delta, 0),
      Math.max(0, definitions.value.length - 1),
    )
    void refillDefinitions()
  }

  /**
   * A 409 whose code is `rebuilding` is not a failure.
   *
   * The graph refreshes at the end of every run and those passes are serialised,
   * so a request arriving during one is answered rather than queued -- queueing
   * would leave the button spinning for the minutes a projection takes. The
   * refusal is reported as a wait, in the server's own words, not as `rebuild
   * failed` under a red banner for something nobody did wrong.
   */
  async function rebuild() {
    busy.value = true
    error.value = null
    try {
      await apiPost('/curation/rebuild', {})
      anchorsAwaitingRebuild.value = false
      await refreshStatus()
      return true
    } catch (err) {
      const code =
        err instanceof ApiError && typeof err.body === 'object' && err.body !== null
          ? (err.body as { detail?: { error?: string } }).detail?.error
          : undefined
      if (code === 'rebuilding') {
        useToastsStore().push({
          kind: 'info',
          text: errorText(err, 'the graph is refreshing right now; try again in a moment'),
        })
        return false
      }
      error.value = errorText(err, 'rebuild failed')
      return false
    } finally {
      busy.value = false
    }
  }

  /**
   * The queue changed somewhere else — a run filing new conflicts, the CLI, a
   * second tab.
   *
   * An UNTOUCHED queue reloads itself: nothing is lost, and this is the common
   * case of leaving the page open while a run works. A queue someone is in the
   * middle of raises `stale` instead, because reloading under a keyboard
   * curator moves the pair between reading it and pressing `m`. The counts are
   * refreshed either way — a badge is safe to change under anyone.
   */
  onFreshness('conflicts', () => {
    // Admins only: both fetches below are admin routes, and this store is
    // instantiated by the app frame's poll loop for every signed-in user —
    // for a reader they can only ever answer 403.
    if (!useSessionStore().isKnownAdmin()) return
    void refreshStatus()
    if (busy.value || history.value.length > 0 || cursor.value > 0) {
      stale.value = true
      return
    }
    void load(lastKind.value, true)
  })

  // The definition queue's version of the rule above: an untouched queue
  // reloads itself, one mid-curation keeps its position and its rows until the
  // cursor is back at the top and a later stamp change reloads it.
  onFreshness('definitions', () => {
    if (!definitionsLoaded.value) return
    if (busy.value || definitionsCursor.value > 0) return
    void loadDefinitions(true)
  })

  // Merges ride `entities` (`entity_mappings` is one of its terms). History
  // has no in-progress state to protect; the loaded window is re-read in
  // place so a reader scrolled deep into it keeps their position.
  onFreshness('entities', () => {
    if (mergesLoaded.value) void mergeList.revalidate()
  })

  return {
    conflicts, status, cursor, loading, busy, error, history, merges, mergesLoading,
    current, remaining, position, pendingRebuild, anchorsAwaitingRebuild, stale,
    mergesCanLoadMore: mergeList.canLoadMore, loadMoreMerges: mergeList.loadMore,
    load, refreshStatus, merge, keepApart, relate, correctType, options, undo, move, rebuild,
    prefetchAround, loadMerges, unmerge,
    definitions, definitionsPending, definitionsCursor, definitionsLoading,
    definitionsHistory, currentDefinition, pendingLookups,
    loadDefinitions, dropDefinition, definitionVerdict, lookupDefinition,
    seedPendingLookups, undoDefinition, moveDefinition,
  }
})
