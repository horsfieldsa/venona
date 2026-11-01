import { defineStore } from 'pinia'
import { computed, ref, shallowRef, triggerRef } from 'vue'

import { apiGet, apiPost, errorText } from '@/api/client'
import { useStaleGuard } from '@/composables/useStaleGuard'
import { applyEntityTokens } from '@/lib/entityTokens'
import type { GraphMode } from '@/router/graphRoute'
import type {
  BriefScope,
  BriefSubgraphPayload,
  EdgeProvenance,
  GraphCoverage,
  GraphEdge,
  GraphFilters,
  GraphNode,
  GraphPayload,
  GraphSearchHit,
  GraphSearchResponse,
  OntologyMeta,
  PathResult,
} from '@/types/graph'

/**
 * Graph state. Note what is NOT here: the Cytoscape instance. It lives in a
 * module closure in `useCytoscape` — putting it in Pinia would make Vue proxy
 * its whole element graph and grind the canvas to a halt.
 *
 * This store holds only the plain data: which nodes/edges we have fetched,
 * what is filtered, what is selected.
 */
export const useGraphStore = defineStore('graph', () => {
  /**
   * SHALLOW, and the three `triggerRef` calls below are the contract.
   *
   * A deep `ref` over a Map proxies every value it holds, so a thousand-node
   * payload builds a thousand reactive proxies that nothing reads reactively --
   * every consumer either counts the collection or walks it imperatively inside
   * a loader. Shallow keeps the nodes as the plain objects the payload
   * delivered and notifies once per batch instead of once per key.
   *
   * The cost of that choice: a future `watch(() => graph.nodes, {deep: true})`
   * will silently never fire, and missing a `triggerRef` shows up as a readout
   * frozen while the canvas updates. Mutate the raw collection, then notify.
   */
  const nodes = shallowRef(new Map<string, GraphNode>())
  const edges = shallowRef(new Map<string, GraphEdge>())
  const expanded = shallowRef(new Set<string>())
  const seeds = ref<string[]>([])

  const loading = ref(false)
  const error = ref<string | null>(null)
  const truncated = ref(false)

  /**
   * The resident corpus is MISSING the entities that have no surviving edge,
   * because the last fetch asked the server to leave them out.
   *
   * `hideIsolated` is a SERVER-side filter, not one applied on arrival: most of
   * a payload was being serialised and immediately set to `display: none`
   * (measured 2026-08-16 at 61%). That makes "show unconnected" a question the
   * resident corpus cannot answer. This is what tells the view it has to
   * refetch rather than repaint, and it is a fact about what was FETCHED, never
   * about what is currently filtered.
   */
  const withheldIsolated = ref(false)

  /** HOW MANY it withheld, counted by the SERVER, because the server is what
   *  hides them. The readout reports the number so the reader knows a filter is
   *  on: one that stops reporting itself is a view quietly showing a fraction
   *  of the corpus as if it were all of it. */
  const isolatedWithheldCount = ref(0)

  const ontology = ref<OntologyMeta | null>(null)
  const filters = ref<GraphFilters>({
    types: [],
    predicates: [],
    minSourceCount: 1,
    hideIsolated: true,
    includeUnconfirmed: false,
  })

  /** What the canvas is NOT showing, counted against the statuses it was drawn
   *  with. A relationship resting on a name one source gave us is hidden until
   *  the reader asks for it — 371 of 468 on the live corpus when it was always
   *  hidden. Without this the graph reads as weak extraction rather than as a
   *  corroboration gate doing its job. */
  const coverage = ref<GraphCoverage | null>(null)

  /**
   * What the canvas is showing. Was a boolean (`overviewMode`), which could not
   * carry a third state once the brief got its own scope.
   *
   * `focusSubject`/`focusDepth` are the mode's parameters and live here rather
   * than in the view so they survive `<KeepAlive>` deactivation.
   */
  const mode = ref<GraphMode>('all')
  const focusSubject = ref<string | null>(null)
  const focusDepth = ref(1)
  const briefScope = ref<BriefScope | null>(null)

  const selectedNode = ref<GraphNode | null>(null)
  const selectedEdge = ref<GraphEdge | null>(null)
  const provenance = ref<EdgeProvenance | null>(null)
  const pathResult = ref<PathResult | null>(null)

  const nodeCount = computed(() => nodes.value.size)
  const edgeCount = computed(() => edges.value.size)

  async function loadOntology() {
    if (ontology.value) return
    try {
      ontology.value = await apiGet<OntologyMeta>('/meta/ontology')
      // The ontology's slot assignments become live CSS here, so entity
      // colour works for a generated domain's prefixes with no build --
      // and for every view, not just the graph (App.vue calls this at boot).
      applyEntityTokens(ontology.value)
    } catch {
      ontology.value = null
    }
  }

  function ingest(payload: GraphPayload): string[] {
    const fresh: string[] = []
    for (const node of payload.nodes) {
      if (!nodes.value.has(node.id)) fresh.push(node.id)
      nodes.value.set(node.id, node)
    }
    for (const edge of payload.edges) edges.value.set(edge.id, edge)
    truncated.value = payload.truncated
    // One notification for the whole payload, not one per key.
    triggerRef(nodes)
    triggerRef(edges)
    return fresh
  }

  function filterPayload() {
    return {
      types: filters.value.types.length ? filters.value.types : null,
      predicates: filters.value.predicates.length ? filters.value.predicates : null,
      min_source_count: filters.value.minSourceCount,
      // Only `/graph/overview` honours this; see `GraphFilters` in
      // `api/schemas/graph.py` for why the other two cannot have an isolated
      // node to drop. Sent from here anyway rather than from a second payload
      // builder: one function has to be right about what the filters are.
      hide_isolated: filters.value.hideIsolated,
      include_unconfirmed: filters.value.includeUnconfirmed,
    }
  }

  /** Fetch one hop out. Server-side filtering means we never fetch what we
   *  would immediately hide. */
  async function expand(ids: string[]): Promise<string[]> {
    const isCurrent = scopeGuard.claim()
    loading.value = true
    error.value = null
    try {
      const payload = await apiPost<GraphPayload>('/graph/query', {
        seed_entity_ids: ids,
        filters: filterPayload(),
      })
      if (!isCurrent()) return []
      ids.forEach((id) => expanded.value.add(id))
      triggerRef(expanded)
      return ingest(payload)
    } catch (err) {
      if (isCurrent()) error.value = errorText(err, 'something went wrong')
      return []
    } finally {
      if (isCurrent()) loading.value = false
    }
  }

  /**
   * ONE guard across every scope loader, not one each.
   *
   * The loaders are rivals, not independent: overview, brief, focus and path
   * each replace what the canvas means. Clicking a brief while the overview is
   * still in flight lets the overview land second, reset the canvas and set
   * `mode` back to `all` -- the reader watches their brief's subgraph become
   * the whole graph a moment after it arrives, with the rail still saying
   * brief. A per-loader guard cannot see that, because the rival is a
   * different function.
   */
  const scopeGuard = useStaleGuard()

  /** Everything the store holds is being replaced. Shared by every mode's
   *  loader so none of them can forget one of these and leave the canvas
   *  disagreeing with the readout. */
  function resetCanvas() {
    nodes.value.clear()
    edges.value.clear()
    expanded.value.clear()
    triggerRef(nodes)
    triggerRef(edges)
    triggerRef(expanded)
    pathResult.value = null
    coverage.value = null
    briefScope.value = null
    // Cleared with the corpus it describes, and set again by the one loader that
    // can withhold anything.
    withheldIsolated.value = false
    isolatedWithheldCount.value = 0
  }

  /** The whole graph. `expand` structurally cannot answer this — it requires a
   *  seed and walks exactly one hop, so clusters, bridges and isolated nodes
   *  were unreachable no matter how many times you expanded. */
  async function loadOverview(): Promise<string[]> {
    const isCurrent = scopeGuard.claim()
    loading.value = true
    error.value = null
    // Read BEFORE the await: `applyIntent` can adopt a new filter set while this
    // request is in flight, and what matters is what this payload was asked for.
    const withheld = filters.value.hideIsolated
    try {
      const payload = await apiPost<GraphPayload>('/graph/overview', {
        filters: filterPayload(),
      })
      if (!isCurrent()) return []
      resetCanvas()
      withheldIsolated.value = withheld
      isolatedWithheldCount.value = payload.isolated_hidden ?? 0
      seeds.value = []
      focusSubject.value = null
      mode.value = 'all'
      coverage.value = payload.coverage ?? null
      // Everything is present, so nothing is left to expand.
      const fresh = ingest(payload)
      payload.nodes.forEach((n) => expanded.value.add(n.id))
      triggerRef(expanded)
      return fresh
    } catch (err) {
      if (isCurrent()) error.value = errorText(err, 'something went wrong')
      return []
    } finally {
      if (isCurrent()) loading.value = false
    }
  }

  /**
   * The subgraph a brief induces: the entities it named, and the relationships
   * among them.
   *
   * The BRIEF ID goes on the wire, not the id list the reader already has in
   * `stores/briefs`. A brief written before a merge names the loser, which the
   * projector collapsed onto the survivor — so that id is not a node in Neo4j
   * and client-sent ids would silently drop exactly the entities a merge
   * touched. Only the backend can see `entity_mappings`, and it should stay that
   * way. It also means the graph never has to load a brief document, so it
   * cannot put the wrong brief into `briefs.current`.
   */
  async function loadBrief(briefId: string): Promise<string[]> {
    const isCurrent = scopeGuard.claim()
    loading.value = true
    error.value = null
    try {
      const payload = await apiPost<BriefSubgraphPayload>('/graph/brief', {
        brief_id: briefId,
        filters: filterPayload(),
      })
      if (!isCurrent()) return []
      resetCanvas()
      seeds.value = []
      focusSubject.value = null
      mode.value = 'brief'
      briefScope.value = payload.scope
      const fresh = ingest(payload)
      // Inside a brief frame there is nothing left to expand without leaving it.
      payload.nodes.forEach((n) => expanded.value.add(n.id))
      triggerRef(expanded)
      return fresh
    } catch (err) {
      if (isCurrent()) error.value = errorText(err, 'something went wrong')
      return []
    } finally {
      if (isCurrent()) loading.value = false
    }
  }

  async function search(term: string, limit = 8): Promise<GraphSearchHit[]> {
    if (term.trim().length < 2) return []
    try {
      const data = await apiGet<GraphSearchResponse>(
        `/graph/search?q=${encodeURIComponent(term)}&limit=${limit}`,
      )
      return data.entities
    } catch {
      // A failing search box must never break the canvas.
      return []
    }
  }

  /**
   * One hop out from a cold start.
   *
   * Focus mode does NOT go through here — it walks the resident corpus. This is
   * the fallback for a deep link that arrives before anything is loaded, and the
   * path the mini-graph's neighbours take when they are not on the canvas yet.
   */
  async function seed(ids: string[]): Promise<string[]> {
    resetCanvas()
    mode.value = 'focus'
    seeds.value = ids
    focusSubject.value = ids[0] ?? null
    return expand(ids)
  }

  /**
   * THE RAIL'S FILTERS GO WITH IT. The canvas draws what the filters admit, so a
   * route walked over edges they exclude is an answer the reader cannot see: the
   * trace reports a connection and the canvas shows a gap where its middle was.
   * Types are NOT sent -- they decide what is drawn, not what is reachable, and
   * the traced route is pinned past them.
   */
  async function findPath(from: string, to: string, maxDepth = 4) {
    const isCurrent = scopeGuard.claim()
    loading.value = true
    error.value = null
    const query = new URLSearchParams({
      from,
      to,
      max_depth: String(maxDepth),
      min_source_count: String(filters.value.minSourceCount),
      include_unconfirmed: String(filters.value.includeUnconfirmed),
    })
    for (const predicate of filters.value.predicates) query.append('predicates', predicate)
    try {
      const result = await apiGet<PathResult>(`/graph/path?${query.toString()}`)
      if (!isCurrent()) return null
      pathResult.value = result
      // `truncated` is CARRIED, not cleared. A path result adds a handful of
      // nodes to whatever is already on the canvas, and passing `false` here
      // made the readout stop saying `capped` about the graph underneath --
      // the canvas quietly stopped admitting it was showing part of the truth.
      ingest({
        nodes: result.nodes,
        edges: result.edges,
        truncated: truncated.value,
        total: 0,
      })
      return result
    } catch (err) {
      if (!isCurrent()) return null
      error.value = errorText(err, 'something went wrong')
      pathResult.value = null
      return null
    } finally {
      if (isCurrent()) loading.value = false
    }
  }

  /**
   * Guarded, and this is the one place in the store where it is load-bearing.
   *
   * `selectEdge` fires this, and a fast click from edge A to edge B leaves two
   * requests in flight. If A's is slower it lands last and paints A's sentences
   * under B's heading -- the panel then attributes the wrong evidence to the
   * wrong relationship, which on this surface is the worst kind of wrong: the
   * provenance panel exists precisely to say WHERE a claim came from.
   *
   * Clearing `provenance` synchronously in `selectEdge`/`selectNode` does not
   * help; it only widens the window in which the stale answer can arrive.
   */
  const provenanceGuard = useStaleGuard()

  async function loadProvenance(edgeId: string) {
    const isCurrent = provenanceGuard.claim()
    provenance.value = null
    try {
      const found = await apiGet<EdgeProvenance>(
        `/edges/${encodeURIComponent(edgeId)}/provenance`,
      )
      if (!isCurrent()) return
      provenance.value = found
    } catch {
      if (!isCurrent()) return
      provenance.value = null
    }
  }

  function selectNode(node: GraphNode | null) {
    selectedNode.value = node
    selectedEdge.value = null
    provenance.value = null
  }

  function selectEdge(edge: GraphEdge | null) {
    selectedEdge.value = edge
    selectedNode.value = null
    if (edge) void loadProvenance(edge.id)
  }

  function clearPath() {
    pathResult.value = null
  }

  return {
    nodes, edges, expanded, seeds, loading, error, truncated,
    withheldIsolated, isolatedWithheldCount,
    ontology, filters, selectedNode, selectedEdge, provenance, pathResult,
    nodeCount, edgeCount, coverage,
    mode, focusSubject, focusDepth, briefScope,
    loadOntology, seed, expand, loadOverview, loadBrief, search,
    findPath, selectNode, selectEdge, clearPath,
  }
})
