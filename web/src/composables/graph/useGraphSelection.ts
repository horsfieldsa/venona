import { computed } from 'vue'

import { getCy } from '@/composables/useCytoscape'
import { useEntitiesStore } from '@/stores/entities'
import { useGraphStore } from '@/stores/graph'

/**
 * Which node is selected, by every route that can select one.
 *
 * Canvas tap, search hit, palette pick, mini-graph neighbour and keyboard all
 * come through here, so they cannot drift into meaning different things. That
 * was already true inside `GraphView`; extracting it makes it enforceable,
 * because the alternative routes now have to be imported rather than written.
 *
 * `focusOn` and `exploreBeyondBrief` are INJECTED rather than imported: they
 * belong to the scope state machine, which sits above this and would otherwise
 * make the dependency circular. See `useGraphVisibility` for why this folder is
 * not `composables/`.
 */
export interface GraphSelectionOptions {
  /** Focus the graph on an entity — the scope machine's job. */
  focusOn: (id: string) => Promise<void>
  /** Leave the brief's frame on purpose, carrying the entity with you. */
  exploreBeyondBrief: (id: string) => void
}

export function useGraphSelection({ focusOn, exploreBeyondBrief }: GraphSelectionOptions) {
  const graph = useGraphStore()
  const entities = useEntitiesStore()

  /**
   * Selecting a node fetches its full card.
   *
   * `EntityCardBody` renders aliases, status, the relevance breakdown and a
   * mini-graph, so the detail rail does not hand-roll a subset of the Neo4j node
   * data. The card is NOT already resident, though: `stores/entities` is filled by the
   * brief's batch prefetch and cleared after every run, so the graph has to ask.
   * Render the node data immediately and upgrade in place when the card lands,
   * rather than showing a spinner over information we already have.
   */
  function selectNode(id: string | null) {
    graph.selectNode(id ? (graph.nodes.get(id) ?? null) : null)
    if (id && !id.startsWith('gloss:')) void entities.fetchOne(id)
  }

  /**
   * A neighbour clicked in the detail rail's mini-graph.
   *
   * NOT a navigation: we are already on the graph. It does exactly what a tap on
   * the canvas does -- focus on it -- so the two routes to the same node cannot
   * mean different things. The node may not be drawn at all (the mini-graph comes
   * from the server's neighbour list, not from what is rendered), which
   * `enterFocus` handles.
   *
   * Brief mode is the exception, twice over. A neighbour the brief DID name is a
   * plain selection -- focusing would leave the frame. A neighbour it did NOT name
   * is not on this canvas at all, so selecting it would silently do nothing; that
   * is a deliberate step outside the brief, and it takes the same route as the
   * "explore beyond the brief" button so the URL says so.
   */
  function selectFromMiniGraph(id: string) {
    if (graph.mode === 'brief') {
      if (graph.nodes.has(id)) selectNode(id)
      else exploreBeyondBrief(id)
      return
    }
    void focusOn(id)
  }

  const selectedCard = computed(() => {
    const id = graph.selectedNode?.id
    return id ? (entities.cards.get(id) ?? null) : null
  })

  /**
   * Step through the drawn nodes by relevance.
   *
   * Sorted rather than stateful: a traversal that remembers where it was is a
   * cycle only until something changes underneath it, and one that does not is
   * bookkeeping nobody can predict from the outside.
   */
  function stepSelection(step: 1 | -1) {
    const cy = getCy()
    if (!cy) return
    const pool = cy
      .nodes()
      .not('.filtered-out')
      .sort((a, b) => (b.data('relevance') || 0) - (a.data('relevance') || 0))
    if (!pool.length) return

    const current = graph.selectedNode?.id
    let at = -1
    for (let i = 0; i < pool.length; i++) {
      if (pool[i].id() === current) {
        at = i
        break
      }
    }
    const next = pool[(at + step + pool.length) % pool.length]
    selectNode(next.id())
    cy.nodes().removeClass('selected')
    next.addClass('selected')
    cy.animate({ center: { eles: next } }, { duration: 180 })
  }

  return { selectNode, selectFromMiniGraph, selectedCard, stepSelection }
}
