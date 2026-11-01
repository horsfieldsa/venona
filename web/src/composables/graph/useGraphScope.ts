import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  dropStash,
  fitVisible,
  getCy,
  hasStash,
  layoutFocus,
  popStash,
  stashPositions,
  type GraphSpacing,
} from '@/composables/useCytoscape'
import {
  graphShapeKey,
  hidesIsolatedByDefault,
  toGraphQuery,
  type GraphIntent,
  type GraphMode,
} from '@/router/graphRoute'
import { useBriefsStore } from '@/stores/briefs'
import { useGraphStore } from '@/stores/graph'
import { useToastsStore } from '@/stores/toasts'
import { useUiStore } from '@/stores/ui'

/**
 * WHAT IS ON THE CANVAS AND WHY: the route codec, the scope loaders, the focus
 * walk, the filters and the way back. One unit, deliberately: `applyIntent`
 * calls `enterFocus`, while `setMode` and `setDepth` call `enterFocus` AND
 * `syncUrl`, which reads `currentIntent`. Splitting it into a "route" half and a
 * "focus" half would inject callbacks in both directions, which is a cycle
 * wearing a costume. One job per unit, not uniform file size.
 *
 * THE INVARIANT THAT MAKES THIS SAFE, and the reason to read this before
 * editing:
 *
 *   A composable invoked from `setup()` has exactly the lifetime of the
 *   component's own local state. `<KeepAlive>` DEACTIVATES `GraphView` without
 *   unmounting it, so per-setup closure state survives deactivation and is
 *   discarded on a real unmount. `applied`, `focusTrail`, `cameFrom`,
 *   `hideIsolatedBeforeFocus` and `focusing` want exactly those semantics, so
 *   every one stays inside this factory.
 *
 * MOVING `applied` TO PINIA WOULD BE THE TRAP. A store is a module singleton, so
 * after a real unmount -- when `destroyCytoscape()` has run -- it would still
 * hold a shape key, and the first `applyIntent` on remount would take the
 * `shape === applied.value` early return and skip the fetch. An empty canvas
 * that reproduces only on the second visit after a hard navigation.
 *
 * The WATCHERS that drive this stay in the view, beside the lifecycle hooks they
 * interleave with. This exposes plain functions; the view decides when.
 */
export interface GraphScopeOptions {
  /** Repaint visibility. Runs after every scope change. */
  refreshVisibility: () => void
  /** Draw the store's elements and arrange them. */
  render: (
    fresh: string[],
    origin?: { x: number; y: number },
    mode?: 'initial' | 'expand',
  ) => Promise<void>
  /** Lay out from scratch and persist the result. */
  relayout: () => void
  /** Selection, so a focus can carry one. */
  selectNode: (id: string | null) => void
}

export function useGraphScope({
  refreshVisibility,
  render,
  relayout,
  selectNode,
}: GraphScopeOptions) {
  const router = useRouter()
  const graph = useGraphStore()
  const ui = useUiStore()
  const briefs = useBriefsStore()
  const toasts = useToastsStore()

  /** Where the reader came from, if they arrived from a mention in the brief.
   *  The BRIEF as well as the paragraph: `?from=<itemId>` alone could only ever
   *  return them to the latest brief, so arriving from an archived one landed on
   *  today's document holding an item id that is not in it. */
  const cameFrom = ref<{ briefId: string | null; itemId: string | null }>({
    briefId: null,
    itemId: null,
  })

  /** `graphShapeKey` of what is currently on the canvas. Set BEFORE the fetch it
   *  describes, so a second `applyIntent` racing the first (mount and activate
   *  both fire on a fresh KeepAlive mount) does not fetch the same thing twice. */
  const applied = ref<string | null>(null)

  /** The orphan setting focus mode was entered from, so leaving it restores what
   *  the reader had rather than a default. Cleared wherever focus is left. */
  let hideIsolatedBeforeFocus: boolean | null = null

  /** The scope focus was entered from, so going up out of it returns to a brief
   *  the reader was in rather than to the whole graph. */
  let modeBeforeFocus: GraphMode | null = null

  /**
   * The subjects focused through to get here, oldest first.
   *
   * Clicking a neighbour moves the focus, so exploring is a walk -- and a walk
   * needs a way back that is not "start again". This is that way back: one step
   * per click, in memory only.
   *
   * Deliberately NOT in the URL. It is navigation history rather than a
   * description of what is on screen, the URL already names where you ARE, and a
   * deep link with no trail behaves correctly -- going up from it lands on the
   * whole graph, which is the honest answer to "where did this come from" when
   * the answer is "a link".
   */
  const focusTrail = ref<string[]>([])

  /** The trail as entities, for the breadcrumb. Ids the store has dropped are
   *  omitted rather than rendered as raw ids. */
  const focusTrailNodes = computed(() =>
    focusTrail.value
      .map((id) => graph.nodes.get(id))
      .filter((n): n is NonNullable<typeof n> => Boolean(n)),
  )

  const focusSubjectNode = computed(() =>
    graph.focusSubject ? (graph.nodes.get(graph.focusSubject) ?? null) : null,
  )

  // ── the route: read one way, written the other ─────────────────────────────

  function currentIntent(): GraphIntent {
    return {
      mode: graph.mode,
      focusId: graph.mode === 'all' ? null : (graph.focusSubject ?? graph.seeds[0] ?? null),
      depth: graph.focusDepth,
      briefId: cameFrom.value.briefId,
      itemId: cameFrom.value.itemId,
      types: graph.filters.types,
      predicates: graph.filters.predicates,
      minSourceCount: graph.filters.minSourceCount,
      hideIsolated: graph.filters.hideIsolated,
      includeUnconfirmed: graph.filters.includeUnconfirmed,
    }
  }

  /**
   * Write the view's state to the URL.
   *
   * `applied` is set to the shape we are ABOUT to serialise, so that even if the
   * watcher did fire on our own write, it would find nothing to fetch. The real
   * guard is upstream and structural: the inverse rule in `router/graphRoute.ts`,
   * which is why this needs no re-entrancy flag to forget to clear.
   */
  function syncUrl() {
    const intent = currentIntent()
    applied.value = graphShapeKey(intent)
    void router.replace({ name: 'graph', query: toGraphQuery(intent) })
  }

  /** The brief a bare /graph opens on, or null when there is none yet -- a new
   *  instance, or an index that failed to load -- and the whole graph is all
   *  there is to show. */
  async function landingBriefId(): Promise<string | null> {
    // Forced while no id is known: another view's index load can still be in
    // flight, and `loadIndex` returns at once whenever one has started.
    await briefs.loadIndex(briefs.latestId === null)
    return briefs.latestId
  }

  async function applyIntent(requested: GraphIntent) {
    let intent = requested
    if (intent.mode === 'brief' && !intent.briefId) {
      const latest = await landingBriefId()
      intent = latest
        ? { ...intent, briefId: latest }
        : { ...intent, mode: 'all', hideIsolated: hidesIsolatedByDefault('all') }
    }
    // Adopted every time, even when nothing needs fetching: these repaint the
    // canvas and retarget the way back, and under `<KeepAlive>` this is the only
    // moment they are ever read.
    graph.filters.types = intent.types
    graph.filters.predicates = intent.predicates
    graph.filters.minSourceCount = intent.minSourceCount
    graph.filters.hideIsolated = intent.hideIsolated
    graph.filters.includeUnconfirmed = intent.includeUnconfirmed
    cameFrom.value = { briefId: intent.briefId, itemId: intent.itemId }

    const shape = graphShapeKey(intent)
    if (shape === applied.value) {
      graph.mode = intent.mode
      graph.focusSubject = intent.focusId
      graph.focusDepth = intent.depth
      refreshVisibility()
      return
    }
    applied.value = shape
    focusTrail.value = []

    if (intent.mode === 'brief' && intent.briefId) {
      await loadBriefScope(intent.briefId, intent.focusId)
    } else if (intent.mode === 'focus' && intent.focusId) {
      await enterFocus(intent.focusId, intent.depth)
    } else {
      await loadEverything()
    }
    refreshVisibility()
  }

  // ── loading a scope ────────────────────────────────────────────────────────

  /** The whole graph. Why a seed and `expand` structurally cannot reach it:
   *  `loadOverview` in `stores/graph.ts`. */
  async function loadEverything() {
    dropStash()
    const fresh = await graph.loadOverview()
    await render(fresh)
  }

  /** The entities one brief named, and the relationships among them. */
  async function loadBriefScope(briefId: string, highlight: string | null) {
    dropStash()
    const fresh = await graph.loadBrief(briefId)
    graph.focusSubject = highlight
    graph.seeds = highlight && graph.nodes.has(highlight) ? [highlight] : []
    await render(fresh)
    if (highlight && graph.nodes.has(highlight)) selectNode(highlight)
  }

  /**
   * One entity and everything within `depth` hops of it.
   *
   * The walk is client-side over the resident corpus, NOT a repeated
   * `/graph/query`: that endpoint caps each request at `NEIGHBOR_LIMIT` edges
   * ordered by mention count (`graph/queries.py`), so a two-hop frontier walk
   * returns hub-weighted edges indistinguishable from the real two-hop
   * neighbourhood. A view that cannot tell you whether it is complete should not
   * claim a depth.
   */
  let focusing = false

  async function enterFocus(subject: string, depth: number) {
    // Clicking a node is the way in, so this fires as fast as a pointer can
    // move, and two overlapping fcose runs fight over the same positions.
    if (focusing) return
    focusing = true
    try {
      await enterFocusInner(subject, depth)
    } finally {
      focusing = false
    }
  }

  /** NOT exported: it is `enterFocus` minus the re-entrancy guard above. */
  async function enterFocusInner(subject: string, depth: number) {
    if (graph.nodes.size === 0) await loadEverything()
    if (!graph.nodes.has(subject)) {
      // Not in what we loaded — a deep link to something the overview truncated,
      // or an entity reached from a mini-graph. Fall back to the server's one hop
      // so the reader sees something true rather than an empty canvas.
      const fresh = await graph.seed([subject])
      await render(fresh)
    } else if (graph.truncated && graph.nodes.get(subject) && !graph.expanded.has(subject)) {
      // The resident corpus is not the whole corpus. The walk can only see edges
      // we hold, so on a truncated overview it would report an empty
      // neighbourhood for a node that has one -- which is the same lie the
      // frontier-walking design was rejected for. Ask the server for this one
      // node's hop before claiming a depth.
      const fresh = await graph.expand([subject])
      await render(fresh, undefined, 'expand')
    }
    if (graph.mode !== 'focus') {
      // Entering focus is a deliberate act, and the depth control is the thing you
      // want next -- but the filter rail starts collapsed. Open it once, on the
      // transition IN, so a reader who then closes it is not fought on every depth
      // change.
      ui.graphRailLeft = true
      // Remembered so that BACKING OUT of focus puts it back, rather than leaving
      // the reader somewhere they never asked to be. Paired with the position
      // stash, which does the same job for the layout.
      hideIsolatedBeforeFocus = graph.filters.hideIsolated
      modeBeforeFocus = graph.mode
      // Same reasoning as `setMode`: adopt the mode's orphan default rather than
      // carrying the last mode's, which would otherwise ride the URL as an
      // explicit override nobody asked for.
      graph.filters.hideIsolated = hidesIsolatedByDefault('focus')
    }

    stashPositions()
    graph.mode = 'focus'
    graph.focusSubject = subject
    graph.focusDepth = depth
    graph.seeds = [subject]
    refreshVisibility()
    layoutFocus(subject, ui.graphSpacing)
    selectNode(subject)
  }

  /**
   * Switch scope.
   *
   * Leaving focus mode runs NO layout: focus hides rather than removes, and the
   * whole-graph arrangement is stashed, so it comes back exactly as it was. That
   * reversibility is the reason focus is expressed as visibility in the first
   * place.
   */
  async function setMode(next: GraphMode, { explicit = false } = {}) {
    if (next === graph.mode) return

    const wasHiding = graph.filters.hideIsolated

    // CHOOSING "whole graph" means the whole graph -- all of it, unconnected
    // entities included. Hiding them is what makes ARRIVING at /graph legible,
    // and the tri-state `orphans` key is what keeps the two apart: absent means
    // "this mode's default", and picking the scope writes `show` explicitly. A
    // control labelled "whole graph" over a canvas missing every unconnected
    // entity is simply lying.
    //
    // `explicit` is what separates picking the scope from BACKING OUT of one.
    // Leaving focus with `.` or with "show the whole graph" is "put it back how
    // it was", and silently un-hiding what the reader had hidden is not that --
    // so those restore what was there before instead.
    if (explicit && next === 'all') graph.filters.hideIsolated = false
    else if (next === 'all') graph.filters.hideIsolated = hideIsolatedBeforeFocus ?? wasHiding
    else graph.filters.hideIsolated = hidesIsolatedByDefault(next)

    // The stash can only put back what is on the canvas, and choosing "whole
    // graph" un-hides entities the server was told not to send. Without this the
    // control would restore the arrangement it left and claim to be showing every
    // entity over a canvas the server pruned -- which is exactly the lie
    // `explicit` exists to prevent, arriving by a different road.
    const mustRefetch = graph.withheldIsolated && !graph.filters.hideIsolated

    if (next === 'all') {
      if (graph.mode === 'focus' && hasStash() && !mustRefetch) {
        graph.mode = 'all'
        graph.focusSubject = null
        graph.seeds = []
        hideIsolatedBeforeFocus = null
        focusTrail.value = []
        refreshVisibility()
        popStash()
        fitVisible(60, 1.4, { animate: true })
      } else {
        hideIsolatedBeforeFocus = null
        focusTrail.value = []
        await loadEverything()
      }
    } else if (next === 'focus') {
      const subject = graph.selectedNode?.id ?? graph.focusSubject
      if (!subject) return
      await enterFocus(subject, graph.focusDepth)
    } else {
      if (!cameFrom.value.briefId) return
      await loadBriefScope(cameFrom.value.briefId, graph.selectedNode?.id ?? null)
    }

    // A loader whose request failed catches, sets `graph.error`, returns no ids
    // and leaves `graph.mode` alone -- so the canvas still holds the previous
    // scope's elements. Writing the URL here would claim a scope the canvas is
    // not showing, and the chip would snap back with nothing else on screen
    // changing at all. That is indistinguishable from a dead control, and it is
    // the one path that produces exactly that symptom.
    if (graph.mode !== next) {
      graph.filters.hideIsolated = wasHiding
      refreshVisibility()
      toasts.push({
        kind: 'error',
        text: graph.error
          ? `could not load the graph: ${graph.error}`
          : 'could not change what the graph shows',
      })
      return
    }

    syncUrl()
  }

  async function setDepth(depth: number) {
    if (depth === graph.focusDepth || !graph.focusSubject) return
    await enterFocus(graph.focusSubject, depth)
    syncUrl()
  }

  /** A search hit focuses, exactly as clicking the node would. Centring on it
   *  instead would leave the reader to find a highlighted dot among three
   *  hundred, which is the problem they opened the search box to avoid. */
  async function focusEntity(id: string) {
    await focusOn(id)
  }

  // ── filters and visibility ─────────────────────────────────────────────────

  function toggleFilter(list: string[], value: string) {
    const at = list.indexOf(value)
    if (at === -1) list.push(value)
    else list.splice(at, 1)
    refreshVisibility()
    syncUrl()
  }

  /** The slider is a magnitude rather than a set, so it stays a range input --
   *  but it repaints and re-serialises through the same pair as every chip. */
  function onMinSourceChange() {
    refreshVisibility()
    syncUrl()
  }

  /** Clearing the filters REFETCHES, where changing one does not.
   *
   * The asymmetry is real, not an oversight. A chip narrows the resident corpus
   * and `refreshVisibility` is the whole job. But the corpus itself was fetched
   * THROUGH the filters -- `loadOverview` sends `filterPayload()` -- so widening
   * cannot un-hide what the server never sent. Land on `?types=Platform` from a
   * bookmark and every non-platform entity is absent rather than hidden; clearing
   * the chips client-side leaves the canvas exactly as empty, with the filter rail
   * now insisting nothing is filtered.
   *
   * Only `all` mode is reloaded here. Brief scope has the same shape and a
   * different loader; nothing produces a filtered arrival into it today, and
   * guessing at one would be inventing a case to serve.
   */
  async function resetFilters() {
    graph.filters.types = []
    graph.filters.predicates = []
    graph.filters.minSourceCount = 1
    syncUrl()
    if (graph.mode === 'all') await loadEverything()
    else refreshVisibility()
  }

  /** Focus on one entity. Every route to it -- a canvas tap, a search hit, a
   *  palette pick, a neighbour in the mini-graph -- goes through here, so they
   *  cannot drift into meaning different things.
   *
   *  Distinct from `setMode('focus')`, which is a no-op when focus mode is
   *  already on: this is how the subject MOVES from one entity to another, which
   *  is what made the mode chip feel like it only worked once. */
  async function focusOn(id: string) {
    if (graph.mode === 'focus' && graph.focusSubject === id) return
    // The trail is pushed HERE rather than in `enterFocus`, so that a deep link,
    // a KeepAlive re-entry or a mode switch -- none of which are steps the reader
    // took -- cannot invent history that was never walked.
    if (graph.mode === 'focus' && graph.focusSubject) focusTrail.value.push(graph.focusSubject)
    await enterFocus(id, graph.focusDepth)
    syncUrl()
  }

  /**
   * Up one level: back to the entity focused before this one, or out to the whole
   * graph when there is nothing behind.
   *
   * Bound to the background click as well as to a key. Clicking empty canvas
   * meaning "out" is the gesture every map has, and it costs nothing here: in
   * focus mode the selection IS the subject, so there is no separate deselection
   * to lose.
   */
  async function focusUp() {
    if (graph.mode !== 'focus') return
    const previous = focusTrail.value.pop()
    if (previous) {
      await enterFocus(previous, graph.focusDepth)
      syncUrl()
      return
    }
    await setMode(modeBeforeFocus === 'brief' && cameFrom.value.briefId ? 'brief' : 'all')
  }

  /** Jump several levels at once, from the breadcrumb. */
  async function focusUpTo(index: number) {
    const target = focusTrail.value[index]
    if (!target) return
    focusTrail.value = focusTrail.value.slice(0, index)
    await enterFocus(target, graph.focusDepth)
    syncUrl()
  }

  async function focusOnSelection() {
    const id = graph.selectedNode?.id
    if (id) await focusOn(id)
  }

  /**
   * Show or hide the entities with no surviving relationship.
   *
   * Its own control and its own undo, deliberately NOT folded into `reset`: that
   * clears the type and predicate chips too, and one link that undoes two
   * unrelated decisions is a bug with a delivery date.
   *
   * Re-lays out, because hidden nodes hold stale positions and are excluded from
   * the force simulation -- without a fresh layout the visible component never
   * spreads into the room that was just freed.
   */
  async function setHideIsolated(value: boolean) {
    graph.filters.hideIsolated = value
    // SHOWING them is a fetch, not a repaint. `/graph/overview` applies this
    // filter server-side, so the entities being asked for are not in the
    // resident corpus to un-hide -- `withheldIsolated` is the store saying so.
    // Hiding stays a repaint: the canvas already holds them.
    if (!value && graph.withheldIsolated && graph.mode === 'all') {
      await loadEverything()
      syncUrl()
      return
    }
    refreshVisibility()
    if (graph.mode !== 'focus') relayout()
    syncUrl()
  }

  /**
   * Show or withhold the names only one source has given us (ADR 0247).
   *
   * ALWAYS a fetch, in BOTH directions, which is where this differs from
   * `setHideIsolated` above. That one can hide on the client because the canvas
   * already holds the nodes; this one cannot repaint in either direction --
   * turning it on asks for entities the server never sent, and turning it off
   * has to drop the relationships that rested on them, which the resident
   * corpus cannot do without re-deriving the server's own status filter.
   *
   * NO RELAYOUT HERE, and that is the fix rather than an omission. The setting
   * is part of `graphShapeKey` now, so each answer has its own entry in the
   * position cache and `render` already decides between restoring one and
   * laying out cold. Laying out again on top of that threw away the snapshot in
   * the one case it was certain to have -- going back to a picture the reader
   * had already arranged -- and ran a second fcose over a canvas the first was
   * still animating.
   *
   * FOCUS SURVIVES IT. `loadOverview` is the only thing that can supply the new
   * corpus and it lands in `all` mode, so a reader who asked to see the thin
   * material while focused on one entity was dropped into the whole graph by a
   * control that says nothing about scope. The mode and the orphan setting are
   * put back BEFORE re-entering, so `enterFocus` takes its already-in-focus
   * path: the block it guards is the transition IN, and running it again would
   * open the rail and overwrite what focus was entered from.
   */
  async function setIncludeUnconfirmed(value: boolean) {
    graph.filters.includeUnconfirmed = value
    const subject = graph.mode === 'focus' ? graph.focusSubject : null
    const hidden = graph.filters.hideIsolated
    await loadEverything()
    if (subject && graph.nodes.has(subject)) {
      graph.mode = 'focus'
      graph.filters.hideIsolated = hidden
      await enterFocus(subject, graph.focusDepth)
    }
    syncUrl()
  }

  /**
   * Back to how the page opens.
   *
   * Clicking a node focuses it, so a reader wanders quickly and needs ONE way
   * back that does not require remembering what they changed. This is that: the
   * latest brief's names (the whole graph on an instance with no brief yet), no
   * focus, no selection, no path, no filters.
   *
   * Deliberately NOT the same as the `whole graph` chip, which shows every
   * entity including the unconnected ones because that is what its label
   * promises. Reset restores the LANDING state; the chip states a scope.
   */
  const isDefaultView = computed(
    () =>
      (briefs.latestId
        ? graph.mode === 'brief' && cameFrom.value.briefId === briefs.latestId
        : graph.mode === 'all' && graph.filters.hideIsolated === hidesIsolatedByDefault('all')) &&
      !graph.focusSubject &&
      !graph.selectedNode &&
      !graph.pathResult &&
      graph.filters.types.length === 0 &&
      graph.filters.predicates.length === 0 &&
      graph.filters.minSourceCount === 1,
  )

  async function resetView() {
    if (isDefaultView.value) return
    focusTrail.value = []
    graph.filters.types = []
    graph.filters.predicates = []
    graph.filters.minSourceCount = 1
    graph.clearPath()
    selectNode(null)
    getCy()?.elements().removeClass('selected')

    const latest = await landingBriefId()
    if (latest) {
      hideIsolatedBeforeFocus = null
      graph.filters.hideIsolated = hidesIsolatedByDefault('brief')
      cameFrom.value = { briefId: latest, itemId: null }
      await loadBriefScope(latest, null)
      syncUrl()
      return
    }

    graph.filters.hideIsolated = hidesIsolatedByDefault('all')
    if (graph.mode === 'focus' && hasStash()) {
      graph.mode = 'all'
      graph.focusSubject = null
      graph.seeds = []
      hideIsolatedBeforeFocus = null
      refreshVisibility()
      popStash()
      fitVisible(60, 1.4, { animate: true })
    } else if (graph.mode !== 'all') {
      hideIsolatedBeforeFocus = null
      await loadEverything()
    } else {
      refreshVisibility()
      relayout()
    }
    syncUrl()
  }

  function setSpacing(value: GraphSpacing) {
    ui.graphSpacing = value
    // `ui.graphSpacing` is set FIRST, so `relayout` persists under the scope key
    // this arrangement actually belongs to -- `useGraphLayout`'s `scopeKey`
    // reads the spacing, and the same nodes at `airy` are a different picture.
    if (graph.mode === 'focus' && graph.focusSubject) layoutFocus(graph.focusSubject, value)
    else relayout()
  }

  // ── the way back ───────────────────────────────────────────────────────────

  const backLabel = computed(() => {
    const id = cameFrom.value.briefId
    if (!id || id === briefs.latestId) return 'back to the brief'
    return 'back to that brief'
  })

  function backToBrief() {
    const { briefId, itemId } = cameFrom.value
    briefs.returnItemId = itemId
    // `/` is the latest brief's canonical route. Pushing it for an ARCHIVED brief
    // lands the reader on today's document holding an item id that is not in it,
    // and `restoreScroll` then falls through to the remembered offset -- which is
    // indistinguishable from the bug this exists to fix.
    if (briefId && briefId !== briefs.latestId) {
      void router.push(`/briefs/${encodeURIComponent(briefId)}`)
    } else {
      void router.push('/')
    }
  }

  /** Leave the brief's frame on purpose, rather than by tapping a node and
   *  quietly acquiring entities it never named. */
  function exploreBeyondBrief(id: string) {
    void enterFocus(id, graph.focusDepth).then(syncUrl)
  }

  return {
    currentIntent,
    syncUrl,
    applyIntent,
    loadEverything,
    loadBriefScope,
    enterFocus,
    setMode,
    setDepth,
    toggleFilter,
    onMinSourceChange,
    setIncludeUnconfirmed,
    resetFilters,
    focusOn,
    focusEntity,
    focusUp,
    focusUpTo,
    focusOnSelection,
    setHideIsolated,
    isDefaultView,
    resetView,
    setSpacing,
    backLabel,
    backToBrief,
    exploreBeyondBrief,
    cameFrom,
    focusTrail,
    focusTrailNodes,
    focusSubjectNode,
  }
}
