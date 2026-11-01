import type { GraphEdge } from '@/types/graph'

/**
 * The ids within `depth` hops of a subject.
 *
 * Pure: no Cytoscape, no store, no request. That is the point.
 *
 * Focus mode does NOT ask the server. `/graph/query` caps at 50 EDGES per
 * request, ordered globally by mention count, so walking a frontier with it
 * returns fifty hub-weighted edges that are indistinguishable from "the whole
 * two-hop neighbourhood" -- a view that would lie about its own completeness.
 * The whole corpus is already resident after `/graph/overview` (a few hundred
 * entities against a 3000-edge ceiling), so a breadth-first walk over what we
 * have is both exact and instant.
 *
 * Adjacency is built from FILTER-PASSING edges only. Traversing an edge the
 * reader has filtered out would put a node in scope that no visible path
 * reaches, producing a subgraph that contradicts itself: nodes floating with no
 * drawn connection to the subject they are supposedly near.
 *
 * WHICH MEANS ALL THREE RULES, SPELLED AS `applyVisibility` SPELLS THEM. Two of
 * them were not: a declared edge is exempt from the source floor everywhere else
 * (it names no article, so its count is an artefact of the floor rather than a
 * publisher tally), and a type chip admits an edge through either endpoint. Miss
 * either and the walk is narrower than the canvas, which is the contradiction
 * above with the sign flipped -- an edge drawn to a node the scope dropped.
 *
 * `typeOf` rather than a node collection: this file stays pure, and the walk
 * needs one fact about a node it never otherwise holds.
 */
export function neighbourhoodIds(
  edges: Iterable<GraphEdge>,
  subject: string,
  depth: number,
  opts: {
    predicates: string[]
    minSourceCount: number
    types?: string[]
    typeOf?: (id: string) => string | null | undefined
  },
): Set<string> {
  const scope = new Set<string>([subject])
  if (depth < 1) return scope

  const adjacency = new Map<string, string[]>()
  const link = (from: string, to: string) => {
    const existing = adjacency.get(from)
    if (existing) existing.push(to)
    else adjacency.set(from, [to])
  }

  const types = opts.types ?? []
  const typeOf = opts.typeOf ?? (() => null)
  const selected = (id: string) => types.length === 0 || types.includes(typeOf(id) as string)

  for (const edge of edges) {
    if (opts.predicates.length > 0 && !opts.predicates.includes(edge.predicate)) continue
    if (!edge.declared && (edge.source_count || 1) < opts.minSourceCount) continue
    if (!selected(edge.source) && !selected(edge.target)) continue
    // Undirected: "one hop from here" means either direction. Direction is a
    // property of the relationship, not of what counts as nearby.
    link(edge.source, edge.target)
    link(edge.target, edge.source)
  }

  let frontier = [subject]
  for (let hop = 0; hop < depth && frontier.length; hop++) {
    const next: string[] = []
    for (const id of frontier) {
      for (const neighbour of adjacency.get(id) ?? []) {
        if (scope.has(neighbour)) continue
        scope.add(neighbour)
        next.push(neighbour)
      }
    }
    frontier = next
  }

  return scope
}
