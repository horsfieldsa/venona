import cytoscape, { type Core, type ElementDefinition, type NodeSingular } from 'cytoscape'
import fcose from 'cytoscape-fcose'

import type { GraphEdge, GraphNode } from '@/types/graph'

cytoscape.use(fcose)

/**
 * THE instance is held in a module-scoped closure and never enters Vue's
 * reactivity system. Wrapping `cy` in `ref`/`reactive`/Pinia makes Vue proxy
 * every element in its internal graph, which is pathologically slow at even a
 * few hundred nodes and very painful to retrofit. A standing prohibition in
 * CLAUDE.md.
 */

let cy: Core | null = null

/** Read from CSS so the canvas themes with everything else. NOT FOR COLOURS --
 *  see `colour()`. A custom property's computed value is its declared token
 *  stream rather than a resolved one: `--font-ui` comes back as the family
 *  list, but `--surface-base` comes back as the literal text
 *  `light-dark(var(--grey-075), var(--grey-950))`. The `gray` fallback is a CSS
 *  keyword reachable only with no document to read (tests, SSR), so it encodes
 *  no design decision. */
function token(name: string, fallback = 'gray'): string {
  if (typeof document === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

/**
 * A colour token, RESOLVED -- the one thing reading the root's custom property
 * cannot do.
 *
 * Cytoscape draws to a CANVAS. It cannot take `var(--x)` or `light-dark(a, b)`,
 * and it does not error on either: an unparseable colour silently becomes
 * Cytoscape's own default, so the failure renders as a graph in the wrong
 * palette and reads as a design decision.
 *
 * So make the BROWSER resolve it: paint the token onto a probe element and read
 * back the computed `color`, which is always an `rgb()`. `scripts/ui_smoke.py`
 * uses the same technique.
 */
let colourProbe: HTMLElement | null = null

/* A colour nothing in the palette is or could become, carried by the probe's
   HOST. A `var()` naming a token that does not exist is not an error -- the
   declaration is invalid at computed-value time and `color` falls back to
   INHERITED -- so a typo'd token lands somewhere recognisable instead of
   returning whatever the ancestor happened to be set to. */
const PROBE_SENTINEL = 'rgb(1, 2, 3)' /* token-lint: ok -- a tripwire, not a value */

function ensureProbe(): HTMLElement {
  if (colourProbe?.isConnected) return colourProbe
  const host = document.createElement('div')
  host.setAttribute('aria-hidden', 'true')
  host.style.display = 'none'
  host.style.color = PROBE_SENTINEL
  const probe = document.createElement('span')
  host.appendChild(probe)
  // On `documentElement`, not `body`: the tokens are declared on `:root` and the
  // theme is switched by an attribute on that same element, so a probe inside it
  // sees the current palette with no further coupling, and survives a route change.
  document.documentElement.appendChild(host)
  colourProbe = probe
  return probe
}

function colour(name: string, fallback = 'gray'): string {
  if (typeof document === 'undefined') return fallback
  const probe = ensureProbe()
  probe.style.color = `var(${name})`
  const resolved = getComputedStyle(probe).color
  if (resolved === PROBE_SENTINEL) {
    throw new Error(
      `[Venona] the graph could not resolve ${name}: the probe fell through to its ` +
        `sentinel, so the token is not declared on :root. Check styles/tokens.css. ` +
        `Unthrown, this renders a miscoloured graph rather than an error.`,
    )
  }
  return resolved
}

/** A resolver with a memo in front of it, separated from the colour it caches so
 *  the cache POLICY can be checked without a DOM -- `useCytoscape.spec.ts`. */
export function memoResolver<T>(resolve: (key: string) => T) {
  const cache = new Map<string, T>()
  return {
    get(key: string): T {
      const hit = cache.get(key)
      if (hit !== undefined) return hit
      const value = resolve(key)
      cache.set(key, value)
      return value
    },
    clear(): void {
      cache.clear()
    },
    get size(): number {
      return cache.size
    },
  }
}

/**
 * Entity colours, resolved once per prefix rather than once per node.
 * `background-color` is a per-node function Cytoscape re-runs for every node on
 * every style recompute, so an unmemoised lookup is two forced style
 * recalculations per node — at a few hundred nodes that is the frame.
 *
 * An unknown prefix degrades to neutral instead of throwing: `applyEntityTokens`
 * (`lib/entityTokens.ts`) injects `--entity-<prefix>` from the ontology at
 * runtime, so a prefix with no token is a legitimate state, and a style mapper
 * that throws is swallowed by Cytoscape into a miscoloured node. A missing CORE
 * token still throws -- those are declared in `tokens.css`.
 */
const entityColours = memoResolver((name: string) => {
  try {
    return colour(name)
  } catch {
    return colour('--text-tertiary')
  }
})

/** A token that has to reach Cytoscape as a NUMBER: it draws to a canvas, not
 *  to CSS, so `10px` is not a value it can use for a font size. */
function tokenPx(name: string, fallback: number): number {
  const raw = token(name, '')
  const value = Number.parseFloat(raw)
  return Number.isFinite(value) ? value : fallback
}

function prefixOf(id: string): string {
  return id.split(':')[0]
}

/**
 * The zoom below which a node stops naming itself: Cytoscape's own
 * `min-zoomed-font-size`, per-selector and evaluated per draw. ADR 0087 has why
 * it is that rather than a `cy.on('zoom')` handler toggling classes.
 *
 * BEWARE THE QUANTISATION. `eleTextBiggerThanMin` compares the label against
 * `2^ceil(log2(zoom * pixelRatio))`, so at `pixelRatio: 1` only three cutoffs
 * are reachable at all and tuning 12 to 14 does nothing whatever. These two sit
 * one step above the zoom the whole graph fits at: at rest only landmarks are
 * named, everything names itself past 1:1.
 */
const LABEL_FLOOR = 12
const LABEL_FLOOR_LANDMARK = 6

/**
 * What makes a node worth naming when everything else has gone quiet. DEGREE,
 * not relevance: a `relevance >= 0.55` first cut selected zero nodes, because
 * the score is 0-1 and the corpus tops out near 0.3 (ADR 0087, 2026-08-07). It
 * is the better signal anyway -- orientation in a field of dots is "which of
 * these is the middle of something".
 */
const LANDMARK_DEGREE = 5

/**
 * At most about this many landmarks, the best connected. A degree floor alone
 * grows with the corpus: 309 names cleared it on the live graph (2026-10-25),
 * and their labels printed over each other into an unreadable smear.
 */
const LANDMARK_LIMIT = 40

/**
 * Node diameter: connectedness, on `log2` so a 40-edge hub is bigger than a
 * 4-edge node without being ten times bigger. Degree is the ONLY input, because
 * nothing computes a per-entity relevance -- the score is a CLUSTER's.
 *
 * Degree is read from a DATA field, not from `n.degree()`: a style function is
 * re-evaluated when data changes, not when the graph's structure does, so a node
 * whose edges arrive on a later expansion would keep the size it was born with.
 * `stampDegrees()` keeps the field true.
 */
function nodeDiameter(n: NodeSingular): number {
  const degree = (n.data('degree') as number) || 0
  return 8 + Math.min(Math.log2(degree + 1), 5) * 1.2
}

/**
 * RAW degree, deliberately -- not filter-aware. Nodes that resized as you
 * toggled a predicate chip would make the filter look like it was editing the
 * corpus. Called after every structural change.
 */
export function stampDegrees(): void {
  if (!cy) return
  const degrees = cy.nodes().map((n: NodeSingular) => n.degree(false))
  const ranked = [...degrees].sort((a, b) => b - a)
  const floor = Math.max(LANDMARK_DEGREE, ranked[LANDMARK_LIMIT - 1] ?? 0)
  cy.batch(() => {
    cy!.nodes().forEach((n: NodeSingular, i: number) => {
      const degree = degrees[i]
      if (n.data('degree') !== degree) n.data('degree', degree)
      const landmark = degree >= floor
      if (n.data('landmark') !== landmark) n.data('landmark', landmark)
    })
  })
}

function stylesheet(): cytoscape.StylesheetJson {
  return [
    {
      selector: 'node',
      style: {
        'background-color': (n: NodeSingular) =>
          entityColours.get(`--entity-${prefixOf(n.id())}`),
        label: 'data(name)',
        color: colour('--text-secondary'),
        // Read from the token layer, not copied: a literal here is a stale
        // duplicate and the canvas and the DOM then disagree about the app's
        // typeface. The fallback is `monospace`, not `sans-serif` -- both arms
        // have to name the same VOICE, and a proportional fallback would
        // silently reletter the canvas in the face reserved for prose.
        'font-family': token('--font-ui', 'monospace'),
        'font-size': tokenPx('--label-micro', 11),
        'min-zoomed-font-size': LABEL_FLOOR,
        'text-valign': 'bottom',
        'text-margin-y': 4,
        'text-max-width': '110px',
        'text-wrap': 'ellipsis',
        width: nodeDiameter,
        height: nodeDiameter,
        'border-width': 0,
        'transition-property': 'opacity',
        // A Cytoscape style transition is JavaScript interpolating a value frame
        // by frame -- `base.css`'s clamp to 0.01ms is a CSS rule and does not
        // reach it, exactly as it does not reach an fcose layout.
        'transition-duration': prefersReducedMotion() ? 0 : 120,
      },
    },
    {
      // A NAME ONLY ONE SOURCE HAS GIVEN US, said in the FILL (ADR 0247).
      //
      // The fill is the one channel left: dashing already means a relationship
      // resting on one publisher, and 2266 of the 2267 edges this setting
      // reveals are exactly that -- so they arrive dashed into a canvas where
      // 1092 shown edges are dashed too, and a second dashed vocabulary would
      // leave neither separating anything. Border-style is taken by
      // `.expandable`, border-colour by `.seed` and `.selected`, opacity by
      // hover and path mode.
      //
      // The hue moves to the border, so the TYPE survives; a fill is present or
      // absent, so the weakness survives greyscale (docs/DESIGN.md, never
      // colour alone). And `.selected` repaints the border and leaves the fill
      // alone, so the signal survives selection too. BEFORE `.seed` and
      // `.selected` for that reason: they are allowed to take the border.
      selector: 'node[status = "provisional"]',
      style: {
        'background-color': colour('--surface-base'),
        'border-width': 2,
        'border-color': (n: NodeSingular) => entityColours.get(`--entity-${prefixOf(n.id())}`),
      },
    },
    {
      // Landmarks keep their names a full zoom step longer, so a zoomed-out view
      // still says where you are. On the field `stampDegrees` writes, so it
      // re-evaluates when the structure changes -- `degree()` would not.
      selector: 'node[?landmark]',
      style: { 'min-zoomed-font-size': LABEL_FLOOR_LANDMARK },
    },
    {
      selector: 'node.seed',
      style: {
        'border-width': 2,
        'border-color': colour('--accent-primary'),
        // A rung up from its neighbours, and the rung is the type scale's rather
        // than a number invented at the call site.
        'font-size': tokenPx('--text-xs', 12),
        color: colour('--text-primary'),
        // The subject of the view is never anonymous, at any zoom.
        'min-zoomed-font-size': 0,
      },
    },
    {
      // "This one", in the focus ring's visual language: a halo OUTSIDE the node
      // rather than a heavier edge on it, so selecting never changes a node's
      // apparent size and nothing shifts under the cursor. `--ring-focus` is 3px
      // at 28% of the accent; this is its canvas equivalent. The border stays
      // `--text-primary` on purpose -- `node.seed` above is the accent-bordered
      // state, and painting this one accent too would leave the view's SUBJECT
      // and the node you last clicked differing only by a halo.
      selector: 'node.selected',
      style: {
        'border-width': 2,
        'border-color': colour('--text-primary'),
        'underlay-color': colour('--accent-primary'),
        'underlay-opacity': 0.18,
        'underlay-padding': 6,
        'min-zoomed-font-size': 0,
      },
    },
    {
      selector: 'node.expandable',
      style: { 'border-width': 1, 'border-style': 'dotted', 'border-color': colour('--border-strong') },
    },
    {
      selector: 'edge',
      style: {
        // How many sources asserted it: epistemic strength, without clicking.
        width: (e: cytoscape.EdgeSingular) => 0.8 + Math.log2((e.data('source_count') || 1) + 1),
        'line-color': colour('--edge-confirmed'),
        'curve-style': 'bezier',
        'target-arrow-shape': 'triangle',
        'target-arrow-color': colour('--edge-confirmed'),
        'arrow-scale': 0.6,
        opacity: 0.55,
      },
    },
    {
      // UNCONFIRMED, said structurally rather than by colour alone. A DECLARED
      // edge is excluded: it names no article, so its `source_count` is a floor
      // artefact rather than a publisher count, and dashing the trunk would say
      // the org chart is thinly sourced.
      selector: 'edge[source_count <= 1][!declared]',
      style: { 'line-style': 'dashed', opacity: 0.4 },
    },
    {
      selector: 'edge.selected',
      style: {
        'line-color': colour('--accent-primary'),
        'target-arrow-color': colour('--accent-primary'),
        opacity: 1,
      },
    },

    {
      // A node no type chip selected, on the canvas because a surviving edge
      // reaches it: the far end of an answer rather than part of the set the
      // reader asked for. It has to come back on hover, so this sits BEFORE the
      // emphasis block, which raises it again.
      selector: 'node.context',
      style: { opacity: 0.45 },
    },

    // ── emphasis: one node and what it touches ───────────────────────────────
    // AFTER `edge[source_count <= 1][!declared]` so `.adjacent` recolours an edge
    // without touching `line-style`: epistemic weakness has to survive
    // greyscale. BEFORE `.on-path`/`.dimmed` so path mode outranks hover.
    { selector: 'node.muted', style: { opacity: 0.22 } },
    { selector: 'edge.muted', style: { opacity: 0.06 } },
    {
      selector: 'node.adjacent',
      style: { 'min-zoomed-font-size': 0, 'border-width': 1, 'border-color': colour('--accent-muted') },
    },
    {
      selector: 'edge.adjacent',
      style: {
        'line-color': colour('--accent-primary'),
        'target-arrow-color': colour('--accent-primary'),
        opacity: 0.95,
      },
    },
    {
      selector: 'node.emphasis',
      style: {
        'border-width': 2,
        'border-color': colour('--accent-primary'),
        'min-zoomed-font-size': 0,
        'font-size': tokenPx('--text-xs', 12),
        color: colour('--text-primary'),
        'z-index': 10,
        // A plate under the name: the emphasised label is the one that has to be
        // readable over whatever it happens to be sitting on.
        'text-background-color': colour('--surface-base'),
        'text-background-opacity': 0.72,
        'text-background-padding': 2,
      },
    },

    {
      selector: '.on-path',
      style: {
        'line-color': colour('--accent-primary'),
        'target-arrow-color': colour('--accent-primary'),
        opacity: 1,
        width: 3,
      },
    },
    { selector: 'node.on-path', style: { 'border-width': 2, 'border-color': colour('--accent-primary') } },
    { selector: '.dimmed', style: { opacity: 0.1 } },
    { selector: '.filtered-out', style: { display: 'none' } },
  ] as unknown as cytoscape.StylesheetJson
}

/**
 * Rebuild the stylesheet when the theme changes. Every colour in it is read
 * eagerly at `initCytoscape`, so without this, switching theme repaints the
 * whole app and leaves the canvas on the palette it was built with.
 *
 * Two sources, neither subsuming the other: `data-theme` on `<html>` is the
 * reader choosing, `prefers-color-scheme` is the OS moving under a reader who
 * chose System, where no attribute changes at all. Watched from HERE rather
 * than from a component -- the canvas follows the DOCUMENT, and a `watch` in
 * `GraphView` would miss the OS half.
 */
let stopThemeWatch: (() => void) | null = null

function watchTheme(): void {
  if (typeof window === 'undefined') return
  stopThemeWatch?.()
  const restyle = (): void => {
    // The palette the memo cached belongs to the outgoing theme.
    entityColours.clear()
    cy?.style(stylesheet())
  }
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  media.addEventListener('change', restyle)
  const attribute = new MutationObserver(restyle)
  attribute.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme'],
  })
  stopThemeWatch = () => {
    media.removeEventListener('change', restyle)
    attribute.disconnect()
  }
}

/**
 * How the canvas is drawn, switchable per tab from the URL: `?renderer=plain`
 * turns off the viewport textures, `?renderer=webgl` uses Cytoscape's WebGL
 * renderer, `?renderer=default` goes back. A diagnostic for a browser that draws
 * the default badly (iPad Safari, 2026-10-25), not a reader preference: kept in
 * `sessionStorage` because the view rewrites its own query string.
 */
export type RendererMode = 'default' | 'plain' | 'webgl'

function fromQueryOrSession(key: string): string | null {
  try {
    const asked = new URLSearchParams(window.location.search).get(key)
    if (asked !== null) sessionStorage.setItem(`venona.graph.${key}`, asked)
    return sessionStorage.getItem(`venona.graph.${key}`)
  } catch {
    return null
  }
}

export function rendererMode(): RendererMode {
  if (typeof window === 'undefined') return 'default'
  const mode = fromQueryOrSession('renderer')
  return mode === 'plain' || mode === 'webgl' ? mode : 'default'
}

export function graphDebugOn(): boolean {
  return typeof window !== 'undefined' && fromQueryOrSession('graphdebug') === '1'
}

/** The WebGL renderer paints its own background and takes it as RGB bytes. A
 *  pixel is the one conversion that accepts every colour syntax a token uses. */
function rgbBytes(css: string): [number, number, number] {
  const ctx = document.createElement('canvas').getContext('2d')
  if (!ctx) return [0, 0, 0]
  ctx.fillStyle = css
  ctx.fillRect(0, 0, 1, 1)
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data
  return [r, g, b]
}

const graphErrors: string[] = []
let errorsWatched = false

/** What the canvas looks like from inside this browser, for `?graphdebug=1`. */
export function graphDiagnostics(): Record<string, string | number> {
  if (!errorsWatched && typeof window !== 'undefined') {
    errorsWatched = true
    window.addEventListener('error', (e) => graphErrors.push(String(e.message).slice(0, 140)))
  }
  const canvases = cy ? [...cy.container()!.querySelectorAll('canvas')] : []
  return {
    renderer: rendererMode(),
    zoom: cy ? Number(cy.zoom().toFixed(3)) : 0,
    box: cy ? `${cy.width()}x${cy.height()}` : '',
    visible: visibleNodeCount(),
    canvases: canvases.map((c) => `${c.width}x${c.height}`).join(' '),
    dpr: window.devicePixelRatio,
    errors: graphErrors.length ? graphErrors.slice(-3).join(' | ') : 'none',
    agent: navigator.userAgent.slice(0, 90),
  }
}

export function initCytoscape(container: HTMLElement): Core {
  destroyCytoscape()
  const mode = rendererMode()
  cy = cytoscape({
    container,
    style: stylesheet(),
    // Perf settings that matter at a few hundred elements.
    textureOnViewport: mode === 'default',
    hideEdgesOnViewport: mode === 'default',
    ...(mode === 'webgl' ? { webgl: true, webglBgColor: rgbBytes(colour('--surface-base')) } : {}),
    pixelRatio: 1,
    motionBlur: false,
    wheelSensitivity: 0.2,
    // Low enough for the whole-graph view, where the edgeless entities spread
    // the bounding box until `fit` asks for a zoom of about 0.05 (ADR 0087). A
    // higher floor clamps and parks the viewport in empty space, with
    // everything loaded and nothing on screen.
    minZoom: 0.04,
    maxZoom: 3,
  })
  exposeForTesting(cy)
  watchTheme()
  warnIfCollapsed(container)
  return cy
}

/** A zero-sized container is terminal, and otherwise perfectly silent.
 *
 * Cytoscape renders into it without complaint -- correct elements, correct
 * layout, zero pixels -- and the ResizeObserver in `graph/useGraphLayout.ts`
 * SKIPS zero-sized boxes on purpose, because resizing the renderer to 0 leaves
 * it unable to recover. So nothing downstream rescues this: the graph is gone
 * and the readout still names its full node count.
 *
 * Cytoscape injects `.__________cytoscape_container{position:relative}`
 * unlayered, so it ranks above every Tailwind utility and can override an
 * `absolute` that a height depends on. Naming the measurement is what makes
 * that a five-minute fix.
 */
function warnIfCollapsed(container: HTMLElement): void {
  if (!import.meta.env.DEV) return
  const box = container.getBoundingClientRect()
  if (box.width > 0 && box.height > 0) return
  console.warn(
    `[Venona] the graph canvas mounted at ${box.width}x${box.height}. Cytoscape will ` +
      `render nothing and the ResizeObserver cannot recover a zero-sized box. Check that ` +
      `nothing overrode the container's sizing -- computed position is ` +
      `"${getComputedStyle(container).position}".`,
  )
}

export function getCy(): Core | null {
  return cy
}

/** Dev-only handle for measuring layout behaviour from outside the app
 *  (`docs/GRAPH-PERF.md` drives it). Not a supported API: `cy` stays
 *  module-scoped and out of Vue's reactivity so it cannot be grabbed casually. */
function exposeForTesting(instance: Core | null): void {
  if (import.meta.env.DEV && typeof window !== 'undefined') {
    ;(window as unknown as { __cy: Core | null }).__cy = instance
  }
}

export function destroyCytoscape(): void {
  stopThemeWatch?.()
  stopThemeWatch = null
  clearEmphasis()
  entityColours.clear()
  // A layout still running holds the canvas it is moving, and its settle
  // handler outlives `cy` unless something takes it off.
  stopRunningLayout()
  positionStash = null
  cy?.destroy()
  cy = null
  exposeForTesting(null)
}

export function toElements(nodes: GraphNode[], edges: GraphEdge[]): ElementDefinition[] {
  return [
    ...nodes.map((n) => ({ group: 'nodes' as const, data: { ...n } })),
    ...edges.map((e) => ({ group: 'edges' as const, data: { ...e } })),
  ]
}

/** Add only what is not already on the canvas. Never removes -- this is the
 *  expand path, where keeping what the user has built up is the whole point. */
export function mergeElements(elements: ElementDefinition[]): cytoscape.CollectionReturnValue | null {
  if (!cy) return null
  const fresh = elements.filter((el) => cy!.getElementById(String(el.data.id)).empty())
  const added = fresh.length ? cy.add(fresh) : null
  if (added) stampDegrees()
  return added
}

/**
 * Make the canvas match the store exactly, for the seed/overview path only --
 * expansion must keep using `mergeElements`, which is safe only while the canvas
 * can grow. It cannot: the graph opens on the whole corpus (ADR 0077), so
 * seeding on one entity afterwards is a SHRINK, and merge-only leaves the
 * readout and the canvas disagreeing about what you are looking at.
 */
export function syncElements(elements: ElementDefinition[]): void {
  if (!cy) return
  const wanted = new Set(elements.map((el) => String(el.data.id)))
  const stale = cy.elements().filter((el) => !wanted.has(el.id()))
  if (stale.length) {
    cy.remove(stale)
    // The emphasis diff is against ids, and a removed id cannot be un-muted.
    // Starting clean makes the next hover a full sweep, the correct cost exactly
    // once after the canvas changes shape.
    clearEmphasis()
  }
  mergeElements(elements)
  // Removal changes degree too, and `mergeElements` stamps only when it added
  // something -- a pure shrink would leave every survivor sized for edges it
  // has lost.
  stampDegrees()
}

// ── visibility: one class, one pass, no idea what a "mode" is ───────────────

export interface VisibilitySpec {
  /** Empty means "every type". */
  types: string[]
  /** Empty means "every predicate". */
  predicates: string[]
  minSourceCount: number
  /** `null` means no scope restriction. Focus and brief mode each produce one
   *  and this function never learns which, so a third mode is a third scope
   *  PRODUCER rather than a third code path through here. */
  scopeIds: Set<string> | null
  hideIsolated: boolean
  /** Never hidden by any rule -- the focus subject, the selection, the seeds, a
   *  traced route. ELEMENT ids, so an edge belongs here as readily as a node: a
   *  route whose nodes were pinned and whose edges were not draws as loose dots,
   *  which is the answer with the answer taken out. */
  pinned: string[]
}

export interface VisibilityCounts {
  nodesHiddenByFilter: number
  /** Drawn as context rather than as subject: on the canvas only because a
   *  surviving edge reaches it. Counted so the readout can say how much of what
   *  is on screen the reader did not ask for by name. */
  nodesAsContext: number
  /** Hidden by a RULE -- a type chip, a predicate chip or the minimum-sources
   *  slider. The only thing "reset" can undo. */
  edgesHiddenByFilter: number
  /** Hidden because an endpoint is. Not a filter, but still not on screen, so
   *  the readout has to subtract it or it claims the whole edge count over a
   *  canvas drawing three. */
  edgesHiddenByEndpoint: number
  nodesOutOfScope: number
  isolatedHidden: number
}

/**
 * Decide what is on screen. Hides by CLASS, never by removing elements: removal
 * would lose expansion state, the reader's mental map, and -- since focus mode
 * is expressed here -- the ability to leave focus mode without re-running a
 * layout. `savePositions`, `snapshotCoverage` and `layoutFocus`'s
 * `fixedNodeConstraint` all assume nodes persist.
 *
 * The four passes are ORDERED, and the order is the correctness requirement. A
 * node is isolated only once you know which of its edges survived, and -- since
 * a type chip spotlights rather than cuts -- that is known before any node's
 * fate is. Running the node pass first is what made a single-type selection
 * blank the canvas: every cross-type edge died on an endpoint, every node then
 * had no edge, and the isolated pass swept the lot.
 */
export function applyVisibility(spec: VisibilitySpec): VisibilityCounts {
  const counts: VisibilityCounts = {
    nodesHiddenByFilter: 0,
    nodesAsContext: 0,
    edgesHiddenByFilter: 0,
    edgesHiddenByEndpoint: 0,
    nodesOutOfScope: 0,
    isolatedHidden: 0,
  }
  if (!cy) return counts

  const pinned = new Set(spec.pinned)
  /** No chip set means every type is selected, which is not the same statement
   *  as "no type is": the difference between a full canvas and none. */
  const selectedType = (type: unknown) =>
    spec.types.length === 0 || spec.types.includes(type as string)

  cy.batch(() => {
    // 1. Which edges the rules admit, and therefore which nodes are on screen.
    //    A type chip reaches an edge through EITHER endpoint: asked for
    //    technologies, an edge from one to a company is the answer, not a
    //    violation of it.
    const context = new Set<string>()
    const ruledOut = new Set<string>()
    cy!.edges().forEach((e) => {
      if (pinned.has(e.id())) {
        context.add(e.source().id())
        context.add(e.target().id())
        return
      }
      const byPredicate =
        spec.predicates.length > 0 && !spec.predicates.includes(e.data('predicate'))
      const belowFloor = !e.data('declared') && (e.data('source_count') || 1) < spec.minSourceCount
      const byType = !selectedType(e.source().data('type')) && !selectedType(e.target().data('type'))
      if (byPredicate || belowFloor || byType) {
        ruledOut.add(e.id())
        return
      }
      context.add(e.source().id())
      context.add(e.target().id())
    })

    // 2. Nodes. A node is drawn when a chip names its type, when a surviving
    //    edge reaches it, or when it is the subject of the view.
    cy!.nodes().forEach((n) => {
      if (pinned.has(n.id())) {
        n.removeClass('filtered-out context')
        return
      }
      const chosen = selectedType(n.data('type'))
      const reached = context.has(n.id())
      const wrongType = !chosen && !reached
      const outOfScope = spec.scopeIds !== null && !spec.scopeIds.has(n.id())
      n.toggleClass('filtered-out', wrongType || outOfScope)
      // Drawn quietly, and only while a chip is actually set -- with none set
      // every node is chosen and nothing on the canvas is context.
      n.toggleClass('context', !chosen && reached && !outOfScope)
      if (wrongType) counts.nodesHiddenByFilter++
      else if (outOfScope) counts.nodesOutOfScope++
      else if (!chosen) counts.nodesAsContext++
    })

    // 3. Edges, once their endpoints have a fate. Counted separately: only a
    //    RULE is something "reset" can undo, so only a rule may appear in the
    //    filtered clause -- but both are off screen, so the readout subtracts
    //    both.
    cy!.edges().forEach((e) => {
      const byRule = ruledOut.has(e.id())
      const byEndpoint = e.source().hasClass('filtered-out') || e.target().hasClass('filtered-out')
      e.toggleClass('filtered-out', byRule || byEndpoint)
      if (byRule) counts.edgesHiddenByFilter++
      else if (byEndpoint) counts.edgesHiddenByEndpoint++
    })

    // 4. Unconnected last, over what actually survived.
    if (!spec.hideIsolated) return
    cy!.nodes().forEach((n) => {
      if (n.hasClass('filtered-out') || pinned.has(n.id())) return
      // NOT `n.degree() === 0`: that counts edges the filters just hid, and
      // counts a self-loop twice.
      if (n.connectedEdges().not('.filtered-out').length > 0) return
      n.addClass('filtered-out')
      counts.isolatedHidden++
      if (n.hasClass('context')) counts.nodesAsContext--
    })
  })

  return counts
}

// ── emphasis: one node and what it touches ─────────────────────────────────

/** Which node is emphasised. Module state, beside `cy` itself -- never a Vue
 *  ref: this changes at mouse-move rate and a ref write would re-render the
 *  readout on every frame of a pointer crossing the canvas. */
let emphasisId: string | null = null

/**
 * The emphasised node's closed neighbourhood, as IDS -- never a
 * `CollectionReturnValue`. A held collection pins removed elements in memory
 * once `syncElements` shrinks the canvas, and it is one careless step from
 * being lifted into a ref, which is the Cytoscape prohibition in CLAUDE.md.
 */
let emphasisHoodIds: string[] = []

/** What changes between two hovered neighbourhoods. Pure and exported so the
 *  interesting cases -- disjoint, overlapping, one containing the other's
 *  centre -- can be checked without a canvas (`useCytoscape.spec.ts`). */
export function emphasisDelta(
  prev: string[],
  next: string[],
): { mute: string[]; unmute: string[] } {
  const prevSet = new Set(prev)
  const nextSet = new Set(next)
  return {
    mute: prev.filter((id) => !nextSet.has(id)),
    unmute: next.filter((id) => !prevSet.has(id)),
  }
}

/** A collection from ids. Built by union rather than by filtering the graph:
 *  the whole point here is to touch the delta, not every element. */
function byIds(ids: string[]): cytoscape.CollectionReturnValue {
  return ids.reduce((acc, id) => acc.union(cy!.getElementById(id)), cy!.collection())
}

/**
 * Emphasise one node and its closed neighbourhood; mute everything else.
 *
 * Edge-triggered, because hover fires continuously over a node and every repeat
 * call would be a full-collection class churn with `textureOnViewport` on, which
 * invalidates the texture cache for every element it touches. Moving between two
 * nodes therefore touches only the difference between their neighbourhoods; the
 * full sweep is kept for arriving from nothing and leaving to nothing, which
 * genuinely change the class of every element on the canvas.
 */
export function applyEmphasis(id: string | null): void {
  if (!cy || id === emphasisId) return
  const previousId = emphasisId

  // Leaving the canvas and an id that is not on it are the same state. Going
  // through `clearEmphasis` keeps `emphasisId` honest -- a phantom id left in it
  // makes the next real hover take the diff path with nothing to diff against.
  const node = id ? cy.getElementById(id) : null
  if (!node || node.empty()) {
    clearEmphasis()
    return
  }
  emphasisId = id

  const hood = node.closedNeighborhood()
  const nextIds = hood.map((el) => el.id())

  cy.batch(() => {
    if (previousId === null) {
      cy!.elements().difference(hood).addClass('muted')
      hood.removeClass('muted').addClass('adjacent')
    } else {
      const { mute, unmute } = emphasisDelta(emphasisHoodIds, nextIds)
      byIds(mute).removeClass('emphasis adjacent').addClass('muted')
      byIds(unmute).removeClass('muted').addClass('adjacent')
      // The node just left stays lit when the two neighbourhoods overlap, and
      // carries `emphasis` rather than `adjacent` until told otherwise.
      if (nextIds.includes(previousId)) {
        cy!.getElementById(previousId).removeClass('emphasis').addClass('adjacent')
      }
    }
    node.removeClass('adjacent muted').addClass('emphasis')
  })

  emphasisHoodIds = nextIds
}

export function clearEmphasis(): void {
  emphasisId = null
  emphasisHoodIds = []
  if (!cy) return
  cy.batch(() => cy!.elements().removeClass('emphasis adjacent muted'))
}

/**
 * `base.css` clamps every CSS animation and transition to 0.01ms under
 * `prefers-reduced-motion`. An fcose layout is neither -- JavaScript moving
 * nodes frame by frame -- so the global rule does not reach it, and a 400ms
 * swarm plays for a reader who asked for none.
 */
function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** Above this, `quality: 'proof'` stops being worth its cost and the graph
 *  needs packing more than it needs precision. */
const LARGE_GRAPH = 120

/**
 * Above this, `default` stops being affordable and the layout drops to `draft`.
 *
 * A LOSS taken deliberately, and the threshold is NOT where `draft` gets
 * cheaper: it is cheaper at every size and worse at every size, collapsing the
 * sparse radial graph into a blob whose smaller bounding box then fits at a zoom
 * where every label turns on. The line is where `default` stops being a pause
 * and starts being a hang -- the main thread is blocked throughout, and 2,500
 * visible nodes is the last measured point under about a second and a half.
 * `docs/GRAPH-PERF.md` has the curve both tiers were measured on (2026-08-16).
 */
const DRAFT_GRAPH = 2500

function layoutQuality(nodes: number): 'draft' | 'default' | 'proof' {
  if (nodes > DRAFT_GRAPH) return 'draft'
  if (nodes > LARGE_GRAPH) return 'default'
  return 'proof'
}

/**
 * The smallest zoom at which this graph still says anything. Nodes are under
 * 20px across at zoom 1, and fitting the whole corpus asks for about 0.05 (ADR
 * 0087), at which every one of them is well under a pixel. That canvas is not
 * blank: it is drawing everything at a scale where nothing is visible, which
 * looks the same and is more confusing. So fit, then refuse to go below
 * legibility.
 */
const MIN_LEGIBLE_ZOOM = 0.32

function clampToLegibleZoom(): void {
  if (!cy || cy.zoom() >= MIN_LEGIBLE_ZOOM) return
  cy.zoom({ level: MIN_LEGIBLE_ZOOM, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } })
  cy.center()
}

/** Long enough to read as travel, short enough not to be waited on. */
const FIT_MS = 260

/**
 * The mirror of `clampToLegibleZoom`: fit what is showing, then refuse to go
 * ABOVE a comfortable zoom. Focus mode routinely draws two or three nodes, and
 * `cy.fit()` on those would take `maxZoom: 3` and fill the viewport with a pair
 * of enormous circles. `boundingBox` already excludes `display: none` elements,
 * so `:visible` is about which elements are considered, not about correcting for
 * hidden ones.
 */
export function fitVisible(padding = 60, maxZoom = 1.4, { animate = false } = {}): void {
  if (!cy) return
  const visible = cy.elements(':visible')
  if (visible.empty()) return

  const settle = () => {
    if (!cy) return
    if (cy.zoom() > maxZoom) {
      cy.zoom({ level: maxZoom, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } })
      cy.center(visible)
    }
    clampToLegibleZoom()
  }

  // An instant cut from three hundred nodes to nine is a scene change: the
  // reader re-finds themselves rather than watching themselves arrive. Animating
  // the VIEWPORT rather than the elements is what makes it read as zooming in on
  // the graph instead of the graph being swapped.
  if (!animate || prefersReducedMotion()) {
    cy.fit(visible, padding)
    settle()
    return
  }
  cy.stop()
  cy.animate({ fit: { eles: visible, padding } }, { duration: FIT_MS, easing: 'ease-out', complete: settle })
}

/**
 * How much room the layout gives each node. Honest caveat, so nobody re-derives
 * it (ADR 0087): under `fit` this is very nearly scale-INVARIANT. Multiplying
 * the separations by 1.7 makes the layout 1.7x bigger, `fit` picks a zoom 1.7x
 * smaller, and the only net change on the whole-graph view is that nodes and
 * labels end up relatively smaller. It bites where the fit zoom is near 1 --
 * focus and brief mode.
 */
export type GraphSpacing = 'compact' | 'normal' | 'airy'

const SPACING_SCALE: Record<GraphSpacing, number> = { compact: 0.72, normal: 1, airy: 1.7 }

function spacingScale(spacing: GraphSpacing | undefined): number {
  return SPACING_SCALE[spacing ?? 'normal'] ?? 1
}

/**
 * Lay out only what is on screen. `cytoscape-fcose` skips position TRANSFER for
 * hidden elements but still runs them through the force simulation, so without
 * this the hidden edgeless entities keep repelling the visible component and the
 * space freed by hiding them never gets used.
 */
function visibleEles(): cytoscape.CollectionReturnValue {
  return cy!.elements(':visible')
}

export function visibleNodeCount(): number {
  return cy ? cy.nodes(':visible').length : 0
}

/**
 * The layout currently moving nodes, so the next one can stop it first. fcose
 * animates over hundreds of milliseconds; start a second layout while the first
 * is running and both write positions in turn, so the graph shudders and settles
 * wherever the loser left it. Expanding twice quickly, or changing scope
 * mid-animation, is enough to see it.
 *
 * The LAYOUT has to be stopped, not the canvas: `cy.stop()` ends viewport
 * animations while a layout animates the ELEMENTS, and holding the handle is the
 * only way to reach it.
 */
let runningLayout: cytoscape.Layouts | null = null

/**
 * Drops the running layout's pending settle -- its listeners and its timer.
 *
 * A handler left bound holds the loose set and the spacing ITS layout was built
 * for, so the next layout's finish runs it as well: an orbit placed around the
 * bounding box of a different arrangement, then a fit to it. Two layouts in
 * quick succession is the ordinary case -- a filter landing on a fetch -- not a
 * corner.
 */
let layoutSettle: (() => void) | null = null

function stopRunningLayout(): void {
  // Cancelled BEFORE the stop: `stop()` can emit `layoutstop` itself, and the
  // superseded layout's settle must not run over a canvas the next one is about
  // to place.
  layoutSettle?.()
  layoutSettle = null
  runningLayout?.stop()
  runningLayout = null
  cy?.elements().stop()
}

/**
 * How long after `layoutready` to settle when `layoutstop` never comes.
 *
 * IT DOES NOT ALWAYS COME, and everything the reader sees hangs off it: the
 * orbit that places the unconnected, and the fit that frames the result.
 * Measured 2026-08-16 on the first paint of a scope -- `layoutstart` fires,
 * `layoutready` fires, `layoutstop` does not -- and a user-triggered relayout on
 * the same code path does emit it, which is why the symptom is "it drew wrong
 * once, then came back right". `useGraphLayout`'s `relayout` carries the same
 * fallback for the position snapshot; this one is for the picture. 700ms clears
 * the 400ms `animationDuration` with margin, and the real event cancels it.
 */
const LAYOUT_SETTLE_MS = 700


/** Visible nodes with no visible edge, computed against `:visible` on BOTH
 *  sides: a node whose only relationship is filtered out is unconnected on
 *  screen whatever the database says, and the canvas is what the reader is
 *  reasoning about. */
function looseNodes(): cytoscape.CollectionReturnValue {
  return cy!.nodes(':visible').filter((n: NodeSingular) => n.connectedEdges(':visible').length === 0)
}

/**
 * Put every unconnected node on the band, whatever put the others where they
 * are. The public entry, for the paths that produce a canvas WITHOUT running a
 * layout: a full hit on the position cache restores a snapshot and stops, and
 * showing withheld entities refetches into that same path. Where the unconnected
 * sit is decided by the renderer rather than by the snapshot, so it is
 * recomputed around wherever the connected graph landed.
 */
export function orbitUnconnected(spacing?: GraphSpacing): void {
  if (!cy) return
  orbitLoose(looseNodes(), spacingScale(spacing))
}

/**
 * Unconnected entities as an ORBIT around the connected graph rather than a grid
 * beside it: a grid says "these are a table", a ring says "these are the rest of
 * it", and the edgeless are the largest block on this canvas. ADR 0182 has the
 * argument and the geometry `ui_smoke.py` asserts.
 *
 * CONCENTRIC, and it has to be -- one ring of every isolate wants a
 * circumference `fit` answers with a zoom that smears the whole graph, so each
 * ring is packed to its own and the band grows as the square root of the
 * population. Ordered by id, because it is rebuilt on every layout and an
 * unstable order would rotate the whole band for no reason a reader could see.
 */
function orbitLoose(loose: cytoscape.CollectionReturnValue, scale: number): void {
  if (!cy || loose.length === 0) return

  const core = cy.nodes(':visible').difference(loose)
  const box = core.length > 0 ? core.boundingBox() : null
  const originX = box ? (box.x1 + box.x2) / 2 : 0
  const originY = box ? (box.y1 + box.y2) / 2 : 0
  // The core's half-diagonal, so the first ring clears its corners rather than
  // cutting through the nodes that sit out at them.
  const coreRadius = box ? Math.hypot(box.w, box.h) / 2 : 0

  // Packed for DOTS, not for labels. A degree-0 node draws no name until zoom
  // passes 1:1 (`LABEL_FLOOR`), and at that zoom the reader sees a slice of the
  // band rather than all of it -- so spending the circumference on room for
  // names nobody reads at rest buys a band thicker than the graph it surrounds.
  const along = 60 + 30 * scale
  const between = 20 + 20 * scale

  // `toArray` widens to the element union; this collection is nodes by
  // construction, and `position` is what the cast buys.
  const ordered = (loose.toArray() as NodeSingular[]).sort((a, b) => (a.id() < b.id() ? -1 : 1))

  let placed = 0
  // One gap clear of the core's corners. Further out and the band stops
  // reading as attached to the thing it surrounds.
  let radius = coreRadius + between
  cy.batch(() => {
    while (placed < ordered.length) {
      const capacity = Math.max(1, Math.floor((2 * Math.PI * radius) / along))
      const count = Math.min(capacity, ordered.length - placed)
      for (let k = 0; k < count; k++) {
        // From twelve o'clock, so a short ring reads as deliberate rather than
        // as having fallen to one side.
        const angle = (2 * Math.PI * k) / count - Math.PI / 2
        ordered[placed + k].position({
          x: originX + Math.cos(angle) * radius,
          y: originY + Math.sin(angle) * radius,
        })
      }
      placed += count
      radius += between
    }
  })
}

/**
 * FRAME WHAT IS DRAWN: the box, then the band, then the fit.
 *
 * Every path that changes what the canvas holds ends here, and they end here
 * TOGETHER because the three steps are one statement and the order is the
 * correctness requirement -- the band is placed around the connected graph, and
 * the fit has to frame both, against a box that is the box the reader actually
 * has.
 *
 * The alternative is what this replaces: each path deciding for itself whether
 * to orbit, whether to fit and which box to fit against, which is three chances
 * to be wrong per path and no single place to check.
 */
export function settleCanvas(spacing?: GraphSpacing, maxZoom = 1.4): void {
  if (!cy) return
  resizeToContainer()
  orbitLoose(looseNodes(), spacingScale(spacing))
  fitVisible(40, maxZoom)
}

/**
 * The one thing the canvas is never allowed to be: empty of everything it
 * holds.
 *
 * A viewport transform computed against a box that has since changed -- a rail
 * opening, a window narrowing, full screen's transient zero, a canvas that
 * mounted before it had a size -- leaves the reader looking at blank space with
 * a readout underneath claiming several hundred nodes. Nothing else notices,
 * because every element is present, visible and correctly placed; it is the
 * VIEW that is somewhere else. That was reported as "it doesn't render, and a
 * hard refresh fixes it", which is exactly what a refresh does: it recomputes
 * the transform against the real box.
 *
 * So this is the invariant, checked wherever the box can change and nowhere
 * else: if the canvas holds visible nodes and not ONE of them is inside the
 * viewport, fit. It cannot fire while anything is on screen, so it can never
 * move a graph the reader is reading; and a canvas with no size is left alone,
 * because nothing can be framed against nothing.
 */
export function ensureOnScreen(): boolean {
  if (!cy) return false
  const visible = cy.elements(':visible')
  if (visible.empty()) return false
  const ext = cy.extent()
  // A canvas with no size frames nothing by definition, and resizing a renderer
  // to zero is what it cannot come back from -- so a dead box is left for the
  // observer to recover, not corrected here.
  if (!(ext.w > 0) || !(ext.h > 0)) return false
  if (!framesNothing(ext, visible.boundingBox())) return false
  fitVisible(40, 1.4)
  return true
}

/** Two rectangles that do not overlap at all -- the decision `ensureOnScreen`
 *  acts on, extracted so it can be read and tested without a canvas, the way
 *  `emphasisDelta` is. Touching edges count as overlapping: a graph exactly
 *  against the viewport edge is one the reader can still see. */
export function framesNothing(
  extent: { x1: number; y1: number; x2: number; y2: number },
  content: { x1: number; y1: number; x2: number; y2: number },
): boolean {
  return (
    extent.x2 < content.x1 ||
    extent.x1 > content.x2 ||
    extent.y2 < content.y1 ||
    extent.y1 > content.y2
  )
}

export function layoutInitial(spacing?: GraphSpacing): void {
  if (!cy) return
  stopRunningLayout()
  const animate = !prefersReducedMotion()
  const eles = visibleEles()
  const large = eles.nodes().length > LARGE_GRAPH
  const scale = spacingScale(spacing)

  /*
   * The unconnected are kept OUT of the force simulation rather than tiled by
   * it. Handing fcose the whole set and repositioning afterwards looks simpler
   * and costs a visible jump: `fit: true` frames the grid fcose just drew, and
   * the ring then lands somewhere else (ADR 0182).
   *
   * `core` can be empty -- every name in the corpus can be unconnected, a state
   * the empty-canvas copy already names -- so fall back to the whole set rather
   * than handing fcose nothing.
   */
  const loose = looseNodes()
  const core = eles.difference(loose)

  let taken = false
  let fallback: ReturnType<typeof setTimeout> | undefined

  function unbind(): void {
    clearTimeout(fallback)
    cy?.off('layoutstop', settle)
    cy?.off('layoutready', onReady)
  }

  function settle(): void {
    if (taken) return
    taken = true
    unbind()
    layoutSettle = null
    // `settleCanvas` rather than fcose's own `fit: true`, which frames what
    // fcose was GIVEN -- the core, without the band -- against whatever box the
    // renderer last measured. A scope change has to frame what the reader got,
    // against the box they have.
    settleCanvas(spacing)
  }

  function onReady(): void {
    fallback = setTimeout(settle, LAYOUT_SETTLE_MS)
  }

  layoutSettle = () => {
    taken = true
    unbind()
  }
  cy.on('layoutstop', settle)
  cy.on('layoutready', onReady)

  runningLayout = cy.layout({
    name: 'fcose',
    eles: core.length > 0 ? core : eles,
    // 'proof' is a full run to convergence: slow, and counterproductive on the
    // whole-graph view because it spreads disconnected components as far as it
    // likes. `LARGE_GRAPH` and `DRAFT_GRAPH` carry what each tier costs.
    quality: layoutQuality(eles.nodes().length),
    randomize: true,
    animate: animate && !large,
    animationDuration: 400,
    // Pull the whole thing together at scale so `fit` lands at a zoom where
    // nodes are still legible.
    nodeSeparation: (large ? 45 : 120) * scale,
    idealEdgeLength: (large ? 55 : 90) * scale,
    // fcose packs disconnected components rather than letting them drift, which
    // is what keeps the bounding box sane on a sparse corpus.
    packComponents: true,
    // Nothing left to tile: the zero-degree nodes are out of `eles` and take
    // their places on the orbit once this stops. The paddings stay because a
    // wholly-new component can still arrive edgeless inside `core` between
    // layouts, and they are the numbers `orbitLoose` spaces the ring by.
    tilingPaddingHorizontal: 40 * scale,
    tilingPaddingVertical: 20 * scale,
    fit: true,
    padding: 40,
  } as cytoscape.LayoutOptions)
  runningLayout.run()
}

/**
 * Focus layout: pin the subject, unfold its neighbourhood around it. The subject
 * keeps the position it already had, so entering focus mode reads as everything
 * else falling away rather than as the canvas teleporting. Only visible elements
 * take part -- out-of-scope nodes are `display: none` and must not push the
 * neighbourhood around from off screen.
 */
export function layoutFocus(subjectId: string, spacing?: GraphSpacing): void {
  if (!cy) return
  stopRunningLayout()
  const subject = cy.getElementById(subjectId)
  const scale = spacingScale(spacing)

  const layout = cy.layout({
    name: 'fcose',
    eles: visibleEles(),
    // `fixedNodeConstraint` is honoured only when the layout is not randomised,
    // and it is the pin that keeps the subject still. The `quality: 'proof'`
    // below is the price of that, not a preference (ADR 0115).
    randomize: false,
    quality: 'proof',
    animate: !prefersReducedMotion(),
    animationDuration: 350,
    fit: false,
    // Roomier than `layoutInitial`'s 120/90. A hub's one-hop neighbourhood is
    // forty nodes on one ring, all of them labelled at focus mode's 1.4 zoom,
    // and the separation is what stops the names overprinting each other.
    nodeSeparation: 150 * scale,
    idealEdgeLength: 130 * scale,
    fixedNodeConstraint: subject.empty()
      ? []
      : [{ nodeId: subjectId, position: { x: subject.position('x'), y: subject.position('y') } }],
  } as cytoscape.LayoutOptions)

  // `fit: false` above, so this fit is the ONLY one -- and `layoutstop` is not
  // a signal to hang it on alone. Same two-event settle as `layoutInitial`: a
  // focus that never framed itself leaves the neighbourhood unfolded around a
  // subject sitting wherever the whole-graph arrangement had put it, which on a
  // canvas the reader has panned is off screen entirely.
  let taken = false
  let fallback: ReturnType<typeof setTimeout> | undefined
  const unbind = () => {
    clearTimeout(fallback)
    layout.off('layoutstop', settle)
    layout.off('layoutready', onReady)
  }
  function settle(): void {
    if (taken) return
    taken = true
    unbind()
    layoutSettle = null
    fitVisible(80, 1.4, { animate: true })
  }
  function onReady(): void {
    fallback = setTimeout(settle, LAYOUT_SETTLE_MS)
  }
  layoutSettle = () => {
    taken = true
    unbind()
  }
  layout.on('layoutstop', settle)
  layout.on('layoutready', onReady)

  runningLayout = layout
  layout.run()
}

/**
 * Resize the renderer to its container without moving the graph under the
 * reader. `cy.resize()` alone recomputes the viewport size but leaves the
 * model-to-screen transform anchored at its previous origin, so collapsing a
 * rail slides the whole graph sideways. Capturing the extent's centre first and
 * panning by the delta afterwards keeps what the reader was looking at in the
 * middle.
 *
 * `cy.fit()` would be the lazy fix and is exactly wrong: it rescales and
 * repositions everything, destroying the spatial memory that the whole expand
 * and focus path exists to protect.
 */
export function resizeToContainer(): void {
  if (!cy) return
  const before = cy.extent()
  const cx = (before.x1 + before.x2) / 2
  const cy0 = (before.y1 + before.y2) / 2

  cy.resize()

  const after = cy.extent()
  const dx = cx - (after.x1 + after.x2) / 2
  const dy = cy0 - (after.y1 + after.y2) / 2
  const zoom = cy.zoom()
  if (dx || dy) cy.panBy({ x: -dx * zoom, y: -dy * zoom })
  // Preserving the centre is the right answer for a box that changed a little
  // and no answer at all for one that changed enough to leave the graph
  // outside it. Here rather than at the call sites because this function is
  // what every box change already goes through, and the guard is silent unless
  // the canvas is showing nothing.
  ensureOnScreen()
}

/**
 * Expand-on-click places ONLY the new nodes and runs no simulation. Nothing
 * already on the canvas moves, which is the whole value of a graph view: a
 * reader who clicks a node to see its neighbours must not have to re-find
 * everything else. `CENTROID_PASSES` below carries why the obvious pinned-fcose
 * design does not buy that, and `useCytoscape.spec.ts` guards against it coming
 * back.
 *
 * The trade is that new nodes appear rather than animating into place: 350ms of
 * settling, against an expand that stays responsive on a graph large enough to
 * want one. Placing directly needs the one thing a simulation gave for free --
 * SPREADING the clicked node's neighbours around it, which is this ring.
 */
// `EXPAND_RING_RADIUS` is the `idealEdgeLength` a simulation settles one
// neighbour at, so a single-neighbour expand lands exactly where a solver would.
// `EXPAND_MIN_ARC` is the arc a node needs before its label meets the next
// one's, and is what grows the ring when a hub is expanded.
const EXPAND_RING_RADIUS = 90
const EXPAND_MIN_ARC = 74

/**
 * How far from the clicked node its new neighbours sit. Separate from
 * `layoutExpand` because it is the only judgement in it and the canvas cannot
 * show you it is wrong: a ring too tight draws sixteen neighbours as one smudge,
 * which reads as a rendering fault rather than as a spacing constant.
 */
export function expandRingRadius(count: number, scale = 1): number {
  const wanted = (Math.max(count, 1) * EXPAND_MIN_ARC * scale) / (2 * Math.PI)
  return Math.max(EXPAND_RING_RADIUS * scale, wanted)
}

/**
 * Bring newly placed nodes into view, and only if they are not already there. A
 * full `fitVisible` would reframe the whole graph on every expand, which is the
 * scene change this path exists to avoid. This pans the shortest distance that
 * puts the new nodes on screen, and does nothing in the common case where they
 * already are.
 */
function revealNewNodes(added: cytoscape.CollectionReturnValue): void {
  if (!cy || added.empty()) return
  const view = cy.extent()
  const box = added.boundingBox()
  const dx = box.x2 > view.x2 ? box.x2 - view.x2 : box.x1 < view.x1 ? box.x1 - view.x1 : 0
  const dy = box.y2 > view.y2 ? box.y2 - view.y2 : box.y1 < view.y1 ? box.y1 - view.y1 : 0
  if (!dx && !dy) return

  const zoom = cy.zoom()
  const pan = { x: -dx * zoom, y: -dy * zoom }
  if (prefersReducedMotion()) {
    cy.panBy(pan)
    return
  }
  cy.animate({ panBy: pan }, { duration: 220 })
}

export function layoutExpand(
  newNodeIds: string[],
  origin?: { x: number; y: number },
  spacing?: GraphSpacing,
): void {
  if (!cy) return
  stopRunningLayout()

  // A Set: this runs per node over the ids being added, an `includes` would be a
  // scan per node, and expand is the path where both sides grow.
  const wanted = new Set(newNodeIds)
  const added = cy.nodes().filter((n: NodeSingular) => wanted.has(n.id()))
  if (added.empty()) return
  const scale = spacingScale(spacing)
  const known = new Set<string>()
  cy.nodes().forEach((n: NodeSingular) => {
    if (!wanted.has(n.id())) known.add(n.id())
  })

  // A RING, not a centroid. `placeUnpositioned` puts a new node at the MEAN of
  // its placed neighbours, which is right when it has several and degenerate
  // when it has one: every neighbour of the node just expanded shares exactly
  // one placed neighbour -- that node -- so they all land on the same point,
  // separated only by the jitter, and sixteen draw as one smudge. The radius
  // grows with the count so the arc between two never falls below a label.
  if (origin) {
    const spread = added.filter((n: NodeSingular) =>
      n
        .neighborhood('node')
        .some((m: cytoscape.CollectionArgument) => known.has((m as NodeSingular).id())),
    )
    const count = Math.max(spread.length, 1)
    const radius = expandRingRadius(count, scale)
    cy.batch(() => {
      spread.forEach((n: NodeSingular, i: number) => {
        const angle = (i / count) * 2 * Math.PI
        n.position({
          x: origin.x + Math.cos(angle) * radius,
          y: origin.y + Math.sin(angle) * radius,
        })
        known.add(n.id())
      })
    })
  }

  // Anything left is a new node not adjacent to the origin -- several seeds at
  // once, or a neighbour-of-a-neighbour. Centroid placement is right for those,
  // and tiles what it cannot reach.
  placeUnpositioned(known, spacing)

  // NO SIMULATION, ever. Pinning the existing nodes and solving for the rest is
  // the obvious design and it is measured not to work: `CENTROID_PASSES` below,
  // and docs/GRAPH-PERF.md.
  revealNewNodes(added)
}

/**
 * A one-slot position stash for entering and leaving focus mode, deliberately
 * NOT the persistent cache: `lib/positionCache.ts` holds the cross-navigation
 * snapshot for a SCOPE, and writing a focus layout into the whole graph's scope
 * would restore a focus layout when the reader comes back from Runs. This lives
 * and dies with the Cytoscape instance, and is why leaving focus mode needs no
 * relayout -- the whole-graph arrangement comes back byte for byte.
 */
let positionStash: Record<string, { x: number; y: number }> | null = null

export function stashPositions(): void {
  if (!cy || positionStash) return
  positionStash = savePositions()
}

/** Restore and clear the stash. Returns false when there was nothing stashed,
 *  so the caller can fall back to a layout. */
export function popStash(): boolean {
  if (!cy || !positionStash) return false
  restorePositions(positionStash)
  positionStash = null
  return true
}

export function hasStash(): boolean {
  return positionStash !== null
}

/** Throw the stash away. Anything that REPLACES the element set has to call
 *  this: the stashed positions describe nodes about to stop existing, and
 *  restoring them later scatters the new set across the departed set's
 *  coordinates. */
export function dropStash(): void {
  positionStash = null
}

/** Snapshot positions so a round trip to the reader and back does not
 *  re-scramble the layout. */
export function savePositions(): Record<string, { x: number; y: number }> {
  if (!cy) return {}
  const out: Record<string, { x: number; y: number }> = {}
  cy.nodes().forEach((n: NodeSingular) => {
    out[n.id()] = { x: n.position('x'), y: n.position('y') }
  })
  return out
}

export function restorePositions(positions: Record<string, { x: number; y: number }>): void {
  if (!cy) return
  cy.batch(() => {
    Object.entries(positions).forEach(([id, pos]) => {
      const node = cy!.getElementById(id)
      if (!node.empty()) node.position(pos)
    })
  })
}

/**
 * How much of this canvas the snapshot describes, 0 to 1. This is what makes
 * `restorePositions` work at all: `layoutInitial()` starts a 400ms ANIMATED
 * fcose and does not await it, so a snapshot applied immediately afterwards is
 * overwritten frame by frame. With a covering snapshot the right move is to skip
 * the layout rather than race it.
 *
 * A FRACTION, not exact set equality -- equality made the cache worthless, one
 * new entity from a run invalidating everything. The scope KEY in
 * `lib/positionCache.ts` is what stops a snapshot of one view being tested
 * against another, which frees this to answer how much of this we already know
 * (ADR 0115). Counted over every node on the canvas, not only the visible ones:
 * a hidden node still needs somewhere to be for the moment a filter reveals it.
 */
export function snapshotCoverage(positions: Record<string, unknown>): number {
  if (!cy || cy.nodes().length === 0) return 0
  let covered = 0
  cy.nodes().forEach((n: NodeSingular) => {
    if (n.id() in positions) covered += 1
  })
  return covered / cy.nodes().length
}

/**
 * Restored names that have relationships now but sit far from every one of them.
 *
 * A snapshot outlives the graph it describes. A name that was unconnected when it
 * was saved sat on the outer orbit, and once a merge or a second source gives it
 * a relationship, restoring it there draws a line across the whole canvas into
 * the core. Dozens of those (iPad, 2026-10-25) stretched the bounding box past
 * `MIN_LEGIBLE_ZOOM` and left the graph as a corner of spokes. No layout leaves
 * an edge this long, so a name in this state is treated as one the snapshot did
 * not know.
 */
const DETACHED_DISTANCE = 600

export function detachedNodes(known: ReadonlySet<string>, spacing?: GraphSpacing): string[] {
  if (!cy) return []
  const limit = DETACHED_DISTANCE * spacingScale(spacing)
  const out: string[] = []
  cy.nodes().forEach((n: NodeSingular) => {
    if (!known.has(n.id())) return
    const p = n.position()
    let linked = false
    let near = false
    n.neighborhood('node').forEach((m: NodeSingular) => {
      if (!known.has(m.id())) return
      linked = true
      const q = m.position()
      if (Math.hypot(p.x - q.x, p.y - q.y) <= limit) near = true
    })
    if (linked && !near) out.push(n.id())
  })
  return out
}

/**
 * Place the nodes the snapshot did not know about, at the mean position of their
 * already-placed neighbours.
 *
 * READ THIS BEFORE REPLACING IT WITH A LAYOUT. The obvious design is to restore
 * what you know, pin it with `fixedNodeConstraint` and let fcose solve the rest,
 * and it is a trap: measured 2026-08-16 at ~1s whether 57 nodes are new or 189,
 * and no completion at all at 749, because pinning constrains the simulation
 * without shrinking it. Centroid placement runs no simulation and is flat
 * instead -- 76ms at 57 new, 95ms at 1,939. ADR 0115 and `docs/GRAPH-PERF.md`
 * have both curves and the 0.7 coverage floor.
 *
 * PASSES, because a new node adjacent only to other new nodes has no placed
 * neighbour on the first one. Each pass commits together so the result does not
 * depend on iteration order, and the loop stops as soon as a pass places
 * nothing. The cap is 8 rather than 3 because three cut the loop short while it
 * was still making progress: 123 of 265 new nodes at 70% coverage were tiled
 * into the unconnected grid WITH THEIR EDGES, drawing as long lines across empty
 * canvas and reading as a fault.
 */
const CENTROID_PASSES = 8

export function placeUnpositioned(
  known: ReadonlySet<string>,
  spacing?: GraphSpacing,
): { placed: number; stranded: number } {
  if (!cy) return { placed: 0, stranded: 0 }
  const scale = spacingScale(spacing)
  const settled = new Set(known)
  let pending = cy.nodes().filter((n: NodeSingular) => !settled.has(n.id()))
  let placed = 0

  cy.batch(() => {
    for (let pass = 0; pass < CENTROID_PASSES && pending.length > 0; pass++) {
      const landing = new Map<string, { x: number; y: number }>()
      pending.forEach((n: NodeSingular) => {
        let x = 0
        let y = 0
        let count = 0
        n.neighborhood('node').forEach((m: NodeSingular) => {
          if (!settled.has(m.id())) return
          x += m.position('x')
          y += m.position('y')
          count += 1
        })
        if (count === 0) return
        const [jx, jy] = jitterFor(n.id(), NEW_NODE_JITTER * scale)
        landing.set(n.id(), { x: x / count + jx, y: y / count + jy })
      })
      if (landing.size === 0) break
      landing.forEach((position, id) => {
        cy!.getElementById(id).position(position)
        settled.add(id)
      })
      placed += landing.size
      pending = pending.filter((n: NodeSingular) => !settled.has(n.id()))
    }

    tileStranded(pending, settled, scale)
  })

  // The band is rebuilt, not appended to: a newly arrived isolate has to land ON
  // the orbit rather than beside it, and the ring's capacity depends on how many
  // there are. One position write per unconnected node buys one rule for where
  // they sit, whether the graph was laid out or expanded into.
  orbitLoose(looseNodes(), scale)

  return { placed, stranded: pending.length }
}

/**
 * Two nodes averaging to the same centroid would draw on top of each other and
 * read as one. Derived from the id rather than from `Math.random`, so a scope
 * laid out twice from the same cache lands in the same place both times -- a
 * node that hops on every reload is a worse artefact than one that overlaps.
 */
const NEW_NODE_JITTER = 30

function jitterFor(id: string, radius: number): [number, number] {
  let hash = 2166136261
  for (let i = 0; i < id.length; i++) {
    hash ^= id.charCodeAt(i)
    hash = Math.imul(hash, 16777619)
  }
  const bits = hash >>> 0
  const angle = ((bits % 3600) / 3600) * 2 * Math.PI
  // An annulus, never the full radius and never zero: the offset always
  // separates, and never throws the node clear of its neighbourhood.
  const distance = radius * (0.35 + 0.65 * (((bits >>> 12) % 100) / 100))
  return [Math.cos(angle) * distance, Math.sin(angle) * distance]
}

/**
 * Nodes with no placed neighbour after every pass. By definition they are
 * isolated or sit in a wholly-new COMPONENT, and the two are laid out
 * differently: a component has shape, so it is tiled with room for the labels
 * its nodes draw, while a true isolate is not tiled at all -- `placeUnpositioned`
 * sends the whole unconnected population through `orbitLoose` afterwards, onto
 * the band rather than into a rectangle beside the graph (ADR 0182).
 */
function tileStranded(
  nodes: cytoscape.CollectionReturnValue,
  settled: ReadonlySet<string>,
  scale: number,
): void {
  if (!cy || nodes.length === 0) return

  // Label width plus the layout's horizontal tiling padding; a node's diameter
  // plus a label line plus the vertical one. Both from the numbers
  // `layoutInitial` passes fcose, so this grid does not drift from fcose's own.
  const stepX = 110 + 40 * scale
  const stepY = 24 + 20 * scale

  const anchored = cy.nodes().filter((n: NodeSingular) => settled.has(n.id()))
  const box = anchored.length > 0 ? anchored.boundingBox() : null
  const x0 = box && Number.isFinite(box.x2) ? box.x2 + stepX : 0
  const y0 = box && Number.isFinite(box.y1) ? box.y1 : 0

  // COMPONENT ORDER, not collection order. These are not all singletons: a
  // wholly-new pair or chain is stranded together, and arrival order put
  // partners at opposite ends of the grid with the edge between them drawing as
  // a long line across it. The traversal is over a set small by definition.
  const ordered = nodes
    .union(nodes.edgesWith(nodes))
    .components()
    .flatMap((component) => component.nodes().toArray())

  const columns = Math.max(1, Math.ceil(Math.sqrt(ordered.length)))
  ordered.forEach((n: NodeSingular, i: number) => {
    n.position({ x: x0 + (i % columns) * stepX, y: y0 + Math.floor(i / columns) * stepY })
  })
}
