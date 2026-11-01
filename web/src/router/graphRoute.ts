import type { LocationQuery, LocationQueryRaw } from 'vue-router'

/**
 * The /graph route's query string, encoded and decoded in one place.
 *
 * This module exists because the route grew keys nobody owned. `showEverything`
 * wrote `?view=all`, which nothing ever read and which destroyed the seed, the
 * filters and the way back to the brief on its way past. `?from=` named a
 * PARAGRAPH but not the brief containing it, so "back to the brief" could only
 * ever go to the latest one. And because `GraphView` is inside `<KeepAlive>`,
 * a hand-rolled `onMounted` read meant every key but `seed` was ignored on every
 * visit after the first.
 *
 * Two rules keep it fixed:
 *
 *  1. `fromGraphQuery` and `toGraphQuery` are INVERSES. That is what makes the
 *     view's own `router.replace` unable to feed itself: the watcher watches
 *     `graphIntentKey(fromGraphQuery(route.query))`, and writing a query that
 *     decodes to the same intent leaves the watch source unchanged, so the
 *     watcher never fires. The echo loop cannot start -- there is no flag to
 *     forget to clear, and browser back/forward still works, which a flag breaks.
 *
 *  2. Nothing else builds this URL by hand -- a hand-built string is how a
 *     contract drifts.
 *
 * Every value degrades to a default rather than blanking the view, which is also
 * how a `mode` this build does not implement stays harmless.
 *
 * The key table lives in `docs/DESIGN.md`, "The graph route's query string".
 */

/**
 * What the canvas is showing.
 *
 *  - `all`   the whole projection. The escape hatch from the other two.
 *  - `focus` one entity and everything within `depth` hops of it.
 *  - `brief` the entities one brief named, and the relationships among them.
 *            The default, on the latest brief when no `briefId` is given:
 *            the whole projection is thousands of names, which no reader can
 *            look at and a phone takes seconds to arrange.
 */
export type GraphMode = 'all' | 'focus' | 'brief'

export const GRAPH_MODES: { value: GraphMode; label: string; hint: string }[] = [
  { value: 'all', label: 'whole graph', hint: "Every name and relationship in Venona's reading" },
  { value: 'focus', label: 'focus', hint: 'One entity and its neighbourhood, to a chosen depth' },
  { value: 'brief', label: 'this brief', hint: 'Only the entities this brief named' },
]

/** Beyond three hops from a hub the walk covers most of the corpus, at which
 *  point focus mode is the whole graph with extra steps. */
export const MAX_FOCUS_DEPTH = 3

export interface GraphIntent {
  mode: GraphMode
  /** In `focus`, the subject. In `brief`, the node to highlight within the
   *  brief's set. Ignored in `all`. */
  focusId: string | null
  /** Hops from the subject. Meaningful in `focus` only. */
  depth: number
  /** The brief the reader came from -- meaningful in ANY mode, because the way
   *  back has to survive a trip through the whole graph. In `brief` mode it is
   *  also the set the subgraph is induced over. */
  briefId: string | null
  /** The paragraph to return to. Implies `briefId`. */
  itemId: string | null
  types: string[]
  predicates: string[]
  minSourceCount: number
  /**
   * False shows entities with no surviving relationship. They are a real
   * finding, so hiding them is a default rather than a policy.
   *
   * The DEFAULT is per-mode, which is why `orphans` is tri-state on the wire
   * (`show` / `hide` / absent). Hiding a third of a 324-node field is what makes
   * the whole graph legible; hiding a third of the thirty-five entities one
   * brief named removes things the reader has already met in prose, in a view
   * small enough to have no legibility problem to solve.
   */
  hideIsolated: boolean
  /**
   * True draws the names only one source has given us, hollow (ADR 0247).
   *
   * IN THE INTENT, not a stray store flag, because it decides what the SERVER
   * sends. Everything that shapes a payload has to be addressable, or the
   * position cache keys two different pictures the same way -- and it does the
   * keying through `graphShapeKey`, which is derived from here.
   *
   * Two-state rather than tri-state: the corroborated graph is the one worth
   * landing on in every mode, so there is no per-mode default for `absent` to
   * stand for.
   */
  includeUnconfirmed: boolean
}

/** Hidden by default only where the count is what makes the view unreadable. */
export function hidesIsolatedByDefault(mode: GraphMode): boolean {
  return mode === 'all'
}

export function emptyIntent(mode: GraphMode = 'all'): GraphIntent {
  return {
    mode,
    focusId: null,
    depth: 1,
    briefId: null,
    itemId: null,
    types: [],
    predicates: [],
    minSourceCount: 1,
    hideIsolated: hidesIsolatedByDefault(mode),
    includeUnconfirmed: false,
  }
}

function one(value: LocationQuery[string]): string | null {
  if (Array.isArray(value)) return value[0] ?? null
  return value ?? null
}

function list(value: LocationQuery[string]): string[] {
  const raw = one(value)
  return raw ? raw.split(',').filter(Boolean) : []
}

function clampInt(raw: string | null, fallback: number, min: number, max: number): number {
  const n = Number(raw)
  if (!Number.isFinite(n)) return fallback
  return Math.min(max, Math.max(min, Math.trunc(n)))
}

export function fromGraphQuery(query: LocationQuery): GraphIntent {
  // `seed` is the pre-mode spelling of `focus` and is still produced by any
  // bookmark, so it is read for ever and written never. It could carry a comma
  // list; nothing ever produced more than one, and focus mode has one subject.
  //
  // `subject` is the browser extension's spelling, which was read nowhere: its
  // "Open in Venona →" fell through `applyIntent` and loaded the WHOLE graph
  // instead of the entity that was clicked. The extension now writes `focus`,
  // but an unpacked build lives in a browser long after the repo moves on, so
  // this is read for ever too.
  const focusId = one(query.focus) ?? list(query.seed)[0] ?? one(query.subject) ?? null
  // `from` is the pre-mode spelling of `item`, same deal.
  const itemId = one(query.item) ?? one(query.from)

  const declared = one(query.mode)
  const mode: GraphMode =
    declared === 'all' || declared === 'focus' || declared === 'brief'
      ? declared
      : // An unrecognised or absent mode is not an error. A `?seed=` bookmark
        // from before modes existed means "focus on this"; anything else means
        // the landing scope, the latest brief.
        focusId
        ? 'focus'
        : 'brief'

  return {
    mode,
    focusId,
    depth: clampInt(one(query.depth), 1, 1, MAX_FOCUS_DEPTH),
    briefId: one(query.brief),
    itemId,
    types: list(query.types),
    predicates: list(query.preds),
    minSourceCount: clampInt(one(query.minSrc), 1, 1, 5),
    // Tri-state: absent means "whatever this mode does by default".
    hideIsolated:
      one(query.orphans) === 'show'
        ? false
        : one(query.orphans) === 'hide'
          ? true
          : hidesIsolatedByDefault(mode),
    includeUnconfirmed: one(query.unconfirmed) === 'show',
  }
}

export function toGraphQuery(intent: GraphIntent): LocationQueryRaw {
  return {
    mode: intent.mode,
    focus: intent.focusId || undefined,
    depth: intent.mode === 'focus' && intent.depth > 1 ? String(intent.depth) : undefined,
    brief: intent.briefId || undefined,
    item: intent.itemId || undefined,
    types: intent.types.length ? intent.types.join(',') : undefined,
    preds: intent.predicates.length ? intent.predicates.join(',') : undefined,
    minSrc: intent.minSourceCount > 1 ? String(intent.minSourceCount) : undefined,
    // Written only when it DIFFERS from the mode's default, so that decoding it
    // again yields the same intent -- the inverse property the watcher relies on.
    orphans:
      intent.hideIsolated === hidesIsolatedByDefault(intent.mode)
        ? undefined
        : intent.hideIsolated
          ? 'hide'
          : 'show',
    unconfirmed: intent.includeUnconfirmed ? 'show' : undefined,
  }
}

/** Everything the intent says, so a watcher can tell a real navigation from the
 *  view re-serialising its own state. */
export function graphIntentKey(intent: GraphIntent): string {
  return [
    intent.mode,
    intent.focusId ?? '',
    intent.mode === 'focus' ? intent.depth : 1,
    intent.briefId ?? '',
    intent.itemId ?? '',
    intent.types.join(','),
    intent.predicates.join(','),
    intent.minSourceCount,
    intent.hideIsolated ? 'h' : 's',
    intent.includeUnconfirmed ? 'u' : 'c',
  ].join('|')
}

/**
 * The part of the intent that decides what has to be FETCHED.
 *
 * Deliberately excludes the brief and item in non-brief modes -- they only
 * retarget the back button -- and excludes the type, predicate and source-count
 * filters, which are applied to elements already on the canvas.
 *
 * `hideIsolated` is here, and ONLY in `all`, because it is the one filter the
 * server now applies: `/graph/overview` never sends the edgeless entities when
 * it is set, so un-hiding them is a fetch rather than a repaint. It stays out of
 * the other two modes because neither endpoint honours it -- `/graph/query`
 * returns seeds and edge endpoints, `/graph/brief` returns a named id set -- so
 * including it there would refetch an identical payload.
 *
 * `includeUnconfirmed` is here by the same rule, in the two modes whose
 * endpoints honour it: `/graph/overview` and `/graph/query` both take the flag,
 * `/graph/brief` does not (ADR 0247). AND IT IS WHAT KEYS THE POSITION CACHE --
 * `useGraphLayout`'s `scopeKey` is built on this string, and the setting roughly
 * triples the canvas, so a key that could not tell the two apart let a snapshot
 * of one describe the other: 339 confirmed names restored onto coordinates laid
 * out for 982, at full coverage, so no layout ever ran to correct it. It
 * persisted, because the cache is IndexedDB.
 */
export function graphShapeKey(intent: GraphIntent): string {
  const names = intent.includeUnconfirmed ? 'seen-once' : 'confirmed'
  if (intent.mode === 'brief') return `brief|${intent.briefId ?? ''}`
  if (intent.mode === 'all') return `all|${intent.hideIsolated ? 'hide' : 'show'}|${names}`
  return `focus|${intent.focusId ?? ''}|${intent.depth}|${names}`
}

/** For the one caller whose `to` has to be a string. */
export function graphPath(intent: GraphIntent): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(toGraphQuery(intent))) {
    if (value !== undefined && value !== null) params.set(key, String(value))
  }
  const query = params.toString()
  return query ? `/graph?${query}` : '/graph'
}
