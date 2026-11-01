import { computed, ref } from 'vue'

import { applyVisibility, type VisibilityCounts } from '@/composables/useCytoscape'
import { useGraphEmphasis } from '@/composables/useGraphEmphasis'
import { neighbourhoodIds } from '@/lib/graphFocus'
import { useGraphStore } from '@/stores/graph'

/**
 * What is on the canvas, and the readouts that describe it.
 *
 * The cleanest leaf of `GraphView`'s decomposition: it depends on the store,
 * `applyVisibility` and `neighbourhoodIds`, and on nothing else in the view.
 * Nothing is injected, nothing is called back into.
 *
 * WHY IT LIVES IN `composables/graph/` AND NOT `composables/`. `router/index.ts`
 * splits `/graph` because cytoscape and its layout bases are ~184KB gzipped of a
 * 288KB bundle, and `useCytoscape` imports the engine at module scope. Anything
 * EAGER that value-imports this file drags all of it into the entry chunk, so
 * these files sit in a folder only `GraphView` reaches. `scripts/lint_bundle.py`
 * walks the eager closure transitively and will catch it if that changes; the
 * folder is the convention that makes the mistake obvious first.
 *
 * NO MODULE-SCOPE STATE. Every ref is inside the factory, which is what makes
 * the lifetime identical to the view's own local state -- see `useGraphScope`
 * for why that is load-bearing under `<KeepAlive>`.
 */
const ZERO_COUNTS: VisibilityCounts = {
  nodesHiddenByFilter: 0,
  nodesAsContext: 0,
  edgesHiddenByFilter: 0,
  edgesHiddenByEndpoint: 0,
  nodesOutOfScope: 0,
  isolatedHidden: 0,
}

export function useGraphVisibility() {
  const graph = useGraphStore()
  const counts = ref<VisibilityCounts>({ ...ZERO_COUNTS })

  const emphasis = useGraphEmphasis({
    selectedId: () => graph.selectedNode?.id ?? null,
    suppressed: () => Boolean(graph.pathResult?.found),
  })

  /** What is actually drawn. Derived from the store's totals minus what the last
   *  visibility pass hid, so the readout cannot drift from the canvas. */
  const visibleNodes = computed(() =>
    Math.max(
      0,
      graph.nodeCount -
        counts.value.nodesHiddenByFilter -
        counts.value.nodesOutOfScope -
        counts.value.isolatedHidden,
    ),
  )
  const visibleEdges = computed(() =>
    Math.max(
      0,
      graph.edgeCount - counts.value.edgesHiddenByFilter - counts.value.edgesHiddenByEndpoint,
    ),
  )

  /**
   * Unconnected entities not on the canvas, however they came to be missing.
   *
   * TWO SOURCES, and the readout must not care which. `counts.isolatedHidden` is
   * what the client hid -- a node whose every edge a predicate chip removed --
   * while `graph.isolatedWithheldCount` is what the server never sent, because
   * `hideIsolated` is a request filter now. Reading only the first was correct
   * exactly until the filter moved, and it fails silently: the canvas drops from
   * 298 entities to 115 and the clause that says so, with the `show` link that
   * undoes it, simply stops rendering.
   */
  const unconnectedHidden = computed(
    () => counts.value.isolatedHidden + graph.isolatedWithheldCount,
  )

  /** Hidden by a chip or the slider — the only thing `reset` can undo. */
  const filteredOut = computed(
    () => counts.value.nodesHiddenByFilter + counts.value.edgesHiddenByFilter,
  )

  /** On screen, but not because the reader named it: the far end of an edge a
   *  type chip kept. Reported rather than left to the dimming alone -- somebody
   *  who deselected a type and still sees it needs the sentence that explains
   *  why, or the rail reads as broken. */
  const shownAsContext = computed(() => counts.value.nodesAsContext)

  const typeOptions = computed(() => graph.ontology?.entity_types ?? [])
  const predicateOptions = computed(() => graph.ontology?.predicates ?? [])
  const activeFilterCount = computed(
    () =>
      graph.filters.types.length +
      graph.filters.predicates.length +
      (graph.filters.minSourceCount > 1 ? 1 : 0),
  )

  /** Why the canvas is blank, when it is.
   *
   * Four causes render identically and want different answers, and the obvious
   * discriminator is wrong: an empty payload does NOT mean an empty projection,
   * because the type, predicate and source-count filters are applied SERVER-side.
   * Filter to a type nothing has and `nodeCount` is 0 with a full graph sitting
   * in Neo4j -- telling that reader to rebuild sends them at a graph that is
   * already there.
   *
   * So an unprojected graph is only ever the LAST answer: it is the one that
   * accuses the corpus, and every other cause is a thing the reader can undo.
   */
  const emptyCanvas = computed(() => {
    // WITHHELD NAMES OUTRANK A SET FILTER, because each cause is picked for the
    // undo it offers and only one of these undos is guaranteed to produce
    // something. `show` has names waiting behind it by definition; `reset
    // filters` throws away the reader's whole selection to maybe reveal some.
    if (unconnectedHidden.value > 0) return { cause: 'isolated', title: 'Nothing connected' }
    if (activeFilterCount.value > 0) return { cause: 'filtered', title: 'Nothing matches' }
    if (graph.nodeCount === 0) return { cause: 'unprojected', title: 'Nothing projected' }
    // Its OWN cause. Reaching here means nodes exist, no filter is set and none
    // are hidden for being isolated -- so blaming the filters offered a "reset
    // filters" button that resets nothing, over a rail with nothing set on it.
    return { cause: 'empty', title: 'Nothing to show' }
  })

  /**
   * Repaint what is on screen. NEVER writes the URL -- this runs on mount and on
   * every route read, and a repaint that re-serialised would put the view in a
   * conversation with itself.
   */
  function refreshVisibility() {
    const scopeIds =
      graph.mode === 'focus' && graph.focusSubject
        ? neighbourhoodIds(graph.edges.values(), graph.focusSubject, graph.focusDepth, {
            predicates: graph.filters.predicates,
            minSourceCount: graph.filters.minSourceCount,
            types: graph.filters.types,
            typeOf: (id) => graph.nodes.get(id)?.type,
          })
        : null

    counts.value = applyVisibility({
      types: graph.filters.types,
      predicates: graph.filters.predicates,
      minSourceCount: graph.filters.minSourceCount,
      scopeIds,
      hideIsolated: graph.filters.hideIsolated,
      // The subject of the view is never hidden by a rule about everything else.
      // An edgeless focus subject that hid itself would be a view of nothing --
      // and a traced route is the same statement about a set: the reader asked
      // for THIS connection, so the filters do not get to erase the answer.
      pinned: [
        graph.focusSubject,
        ...graph.seeds,
        ...(graph.pathResult?.nodes ?? []).map((node) => node.id),
        ...(graph.pathResult?.edges ?? []).map((edge) => edge.id),
      ].filter((id): id is string => Boolean(id)),
    })
    emphasis.refresh()
  }

  return {
    counts,
    emphasis,
    refreshVisibility,
    visibleNodes,
    visibleEdges,
    unconnectedHidden,
    filteredOut,
    shownAsContext,
    typeOptions,
    predicateOptions,
    activeFilterCount,
    emptyCanvas,
  }
}
