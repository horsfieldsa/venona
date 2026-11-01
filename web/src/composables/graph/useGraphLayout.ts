import { ref } from 'vue'

import {
  detachedNodes,
  getCy,
  layoutExpand,
  layoutInitial,
  mergeElements,
  placeUnpositioned,
  resizeToContainer,
  restorePositions,
  savePositions,
  settleCanvas,
  snapshotCoverage,
  syncElements,
  toElements,
  visibleNodeCount,
} from '@/composables/useCytoscape'
import { readPositions, writePositions } from '@/lib/positionCache'
import { graphShapeKey, type GraphIntent } from '@/router/graphRoute'
import { useGraphStore } from '@/stores/graph'
import { useUiStore } from '@/stores/ui'

/**
 * The drawing pipeline: elements onto the canvas, then the cheapest arrangement
 * that is still the picture the reader left.
 *
 * The riskiest piece of `GraphView`'s decomposition, and the best covered:
 * `scripts/ui_smoke.py` asserts the three render tiers off the `__graphRender`
 * probe at the foot of this file, and asserts that a partial hit leaves no node
 * outside the viewport or sitting at the origin. `reportRender`'s shape and its
 * `import.meta.env.DEV` guard are part of that contract -- changing either
 * breaks a check nothing else can make.
 *
 * TWO THINGS ARE INJECTED, and they are the reason this is not a leaf.
 * `currentIntent` because `scopeKey` has to name the same scope the route does,
 * and `refreshVisibility` because visibility runs BEFORE every layout. Both
 * belong to units this one sits under; taking them as options keeps the
 * direction of dependency in one direction.
 *
 * NO LIFECYCLE HOOKS HERE. `observe`/`disconnect` are plain functions the view
 * calls from its own `onMounted`/`onUnmounted`, because `initCytoscape` has to
 * run before anything here touches `getCy()` and hooks registered inside a
 * composable fire in call order -- which would make that ordering implicit.
 */
export interface GraphLayoutOptions {
  /** The route intent this canvas is showing. `scopeKey` reads it. */
  currentIntent: () => GraphIntent
  /** Repaint visibility. Runs before every layout; see `render`. */
  refreshVisibility: () => void
}

/**
 * How much of the canvas a snapshot has to describe before restoring it beats
 * laying the graph out again.
 *
 * A real decision rather than a tuning constant, and it was made by looking at
 * the result: readability was verified 2026-08-16 by rendering the 95%, 80% and 70% cases.
 * At 70% the new nodes cluster slightly toward the denser region of the graph
 * and everything is still legible and still where you left it, which is worth
 * far more than a prettier arrangement of a graph you have to wait five seconds
 * for. Below that the restored positions stop being the majority of what you are
 * looking at, so it lays out cold.
 *
 * If this ever reads badly, RAISE it. Do not add a simulation back -- see
 * `placeUnpositioned`, which records why the obvious pinned-fcose design does
 * not work.
 */
const COVERAGE_FLOOR = 0.7

/**
 * From this many visible names a cold layout is covered until it has settled.
 *
 * Past `LARGE_GRAPH` the layout does not animate: it runs in one block on the
 * main thread and the canvas shows whatever it held before, then a finished
 * picture that may still be unfitted. A phone spends seconds there with nothing
 * to say the page is working. Below this the block is short enough that a cover
 * would only flash.
 */
const COVER_LAYOUT_NODES = 400

export function useGraphLayout({ currentIntent, refreshVisibility }: GraphLayoutOptions) {
  const graph = useGraphStore()
  const ui = useUiStore()

  /**
   * Which render owns the canvas.
   *
   * `render` reads the position cache, which is asynchronous, so two renders can
   * be in flight -- a filter change landing while an overview is still resolving.
   * Whoever started last wins; the earlier one must not apply a snapshot of a
   * scope that has left the screen.
   */
  let renderGeneration = 0

  /** How many names a covered layout is arranging, 0 when none is. */
  const arranging = ref(0)
  let arrangeFrame = 0

  /** Resolves once whatever was just set has been painted: the first frame
   *  commits it, the second starts after the paint. */
  function afterPaint(): Promise<void> {
    return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
  }

  function uncover() {
    if (arrangeFrame) cancelAnimationFrame(arrangeFrame)
    arrangeFrame = 0
    arranging.value = 0
  }

  let observer: ResizeObserver | null = null
  let pendingFrame = 0

  /** Whether the canvas has been seen at zero size since it was last framed.
   *  See `observe`. */
  let collapsed = false

  /**
   * What the cached positions describe.
   *
   * `graphShapeKey` is the natural basis -- it is already the answer to "does this
   * need a different payload" -- but it is not enough on its own: a type or
   * predicate chip produces a different VISIBLE set from the same payload, and the
   * spacing control multiplies every separation the layout uses, so a snapshot
   * taken at `compact` describes a graph nobody at `airy` is looking at.
   */
  function scopeKey(): string {
    return [
      graphShapeKey(currentIntent()),
      graph.filters.types.join(','),
      graph.filters.predicates.join(','),
      graph.filters.minSourceCount,
      ui.graphSpacing,
    ].join('|')
  }

  /**
   * Remember where this scope's nodes ended up.
   *
   * Focus mode is skipped deliberately. A focus layout is a transformation OF the
   * whole-graph arrangement -- focus hides rather than removes, and `stashPositions`
   * already restores it for free on the way out -- so persisting it would either
   * poison the scope it was derived from or occupy a key nothing ever reads.
   */
  async function persistPositions() {
    if (graph.mode === 'focus' || !getCy()) return
    await writePositions(scopeKey(), savePositions())
  }

  /**
   * The three tiers, cheapest first: a full hit skips the layout entirely, a
   * partial hit skips the SIMULATION, and anything below the floor is cold.
   */
  async function resolveFromCache(): Promise<'full' | 'partial' | null> {
    const cy = getCy()
    if (!cy) return null
    const positions = await readPositions(scopeKey())
    if (!positions || !getCy()) return null

    if (snapshotCoverage(positions) < COVERAGE_FLOOR) return null

    restorePositions(positions)
    const known = new Set(Object.keys(positions))
    for (const id of detachedNodes(known, ui.graphSpacing)) known.delete(id)
    const coverage = cy.nodes().filter((n) => known.has(n.id())).length / cy.nodes().length
    if (coverage < COVERAGE_FLOOR) return null
    if (coverage < 1) placeUnpositioned(known, ui.graphSpacing)
    // The same settle the layout path ends with, for the path that runs no
    // layout at all. The band is the renderer's, not the snapshot's: a canvas
    // drawn before would otherwise keep whatever arrangement was current when
    // it was saved, and the unconnected are the population that arrangement is
    // about.
    settleCanvas(ui.graphSpacing, 3)
    return coverage < 1 ? 'partial' : 'full'
  }

  /** Lay the visible graph out from scratch, and remember where it landed. The
   *  layout is animated and asynchronous, so the snapshot has to wait for it --
   *  taking it immediately would record the positions the nodes were passing
   *  through. */
  /**
   * The listeners the last relayout bound, so this one can take them off.
   *
   * `cy.one` removes a handler when it FIRES, and the comment below records
   * that `layoutstop` sometimes never does. An un-fired handler stays bound for
   * the life of the canvas, so a relayout per scope change leaves one behind
   * each time -- and the next `layoutstop` that does arrive runs all of them,
   * every stale closure persisting the current canvas under the scope it was
   * created for.
   */
  let unbindLayoutWatch: (() => void) | null = null

  function relayout() {
    const cy = getCy()
    let taken = false
    let settle: ReturnType<typeof setTimeout> | undefined

    unbindLayoutWatch?.()

    const snapshot = () => {
      clearTimeout(settle)
      unbindLayoutWatch?.()
      uncover()
      if (taken) return
      taken = true
      void persistPositions()
    }

    // `layoutstop` is the RIGHT signal and is not a sufficient one.
    //
    // The snapshot has to wait for the layout: fcose animates, and taking it
    // immediately records the positions the nodes were passing through. So this
    // bound `layoutstop` and nothing else -- and on the FIRST paint of a scope
    // that event never arrives. Measured 2026-08-16, on a 115-node graph well inside the
    // animated path: `layoutstart` fires, `layoutready` fires, `layoutstop`
    // does not. A user-triggered relayout on the same code path does emit it,
    // so something about the mounting frame swallows it; the root cause is in
    // fcose or in the resize that lands during the first animation, and this
    // does not pretend to know which.
    //
    // What it cost is the whole point of the cache. `positionCache`'s docstring
    // says the arrival that most wants a snapshot is "opening the app in the
    // morning" -- and that is exactly the arrival that never wrote one. Every
    // first visit to a scope paid a full cold fcose layout, silently, because
    // `positionCache` swallows failures by design.
    //
    // So: take it on `layoutstop` when it comes, and otherwise a beat after
    // `layoutready`, which fires once positions are computed. 700ms clears the
    // 400ms `animationDuration` with margin, and the fallback is cancelled the
    // moment the real event arrives.
    const onReady = () => {
      settle = setTimeout(snapshot, 700)
    }
    cy?.on('layoutstop', snapshot)
    cy?.on('layoutready', onReady)
    unbindLayoutWatch = () => {
      clearTimeout(settle)
      cy?.off('layoutstop', snapshot)
      cy?.off('layoutready', onReady)
      unbindLayoutWatch = null
    }

    uncover()
    const size = visibleNodeCount()
    if (size < COVER_LAYOUT_NODES) {
      layoutInitial(ui.graphSpacing)
      return
    }
    // Two frames: the first commits the cover, and the second starts after it
    // has painted, so the block that follows has something on screen to hold.
    arranging.value = size
    arrangeFrame = requestAnimationFrame(() => {
      arrangeFrame = requestAnimationFrame(() => {
        arrangeFrame = 0
        // Focus can be entered in between, and it brings its own layout.
        if (graph.mode === 'focus') {
          uncover()
          return
        }
        layoutInitial(ui.graphSpacing)
      })
    })
  }

  /** Dev-only, and the same kind of handle as `__cy`: which tier answered the
   *  last render. A cache hit and a cold layout are indistinguishable from the
   *  outside once the graph is on screen, which is exactly the sort of thing
   *  `scripts/ui_smoke.py` exists to see. */
  function reportRender(tier: 'full' | 'partial' | 'cold') {
    if (!import.meta.env.DEV || typeof window === 'undefined') return
    ;(window as unknown as { __graphRender: unknown }).__graphRender = {
      tier,
      scope: scopeKey(),
      nodes: getCy()?.nodes().length ?? 0,
      at: Date.now(),
    }
  }

  async function render(
    fresh: string[],
    origin?: { x: number; y: number },
    mode: 'initial' | 'expand' = 'initial',
  ) {
    const cy = getCy()
    if (!cy) return

    let generation = 0
    if (mode !== 'expand') {
      generation = ++renderGeneration
      uncover()
      // Building thousands of elements blocks as surely as laying them out
      // does, so the cover goes up before either.
      if (graph.nodes.size >= COVER_LAYOUT_NODES) {
        arranging.value = graph.nodes.size
        await afterPaint()
        if (generation !== renderGeneration) return
      }
    }

    // Expansion adds; seeding and the overview REPLACE. Merging on the replace
    // path left everything from the previous view behind.
    const elements = toElements([...graph.nodes.values()], [...graph.edges.values()])
    if (mode === 'expand') mergeElements(elements)
    else syncElements(elements)

    cy.batch(() => {
      cy.nodes().removeClass('seed expandable')
      graph.seeds.forEach((id) => cy.getElementById(id).addClass('seed'))
      cy.nodes().forEach((n) => {
        if (!graph.expanded.has(n.id())) n.addClass('expandable')
      })
    })

    // Visibility BEFORE the layout, always. Both layouts run over
    // `:visible` and both end in a fit, so hiding afterwards would fit the
    // viewport to a set that includes what is about to disappear -- which is how
    // the brief's nineteen drawn nodes ended up as a sparse scatter in a corner
    // sized for thirty-five.
    refreshVisibility()

    // An expansion that added nothing must NOT fall through to `layoutInitial`.
    // That repositions every node on the canvas, so clicking into a dense cluster
    // whose neighbours are already loaded -- or an expand whose request failed --
    // scrambled the layout the curator had been building up hop by hop.
    if (mode === 'expand' && cy.nodes().length > fresh.length) {
      if (fresh.length) layoutExpand(fresh, origin, ui.graphSpacing)
      return
    }

    if (!generation) generation = ++renderGeneration
    const tier = await resolveFromCache()
    // A newer render started while the cache was being read, and it owns the
    // canvas now. Laying out here would fight it.
    if (generation !== renderGeneration) return
    if (tier) uncover()
    if (tier === 'full') {
      reportRender('full')
      return
    }
    if (tier === 'partial') {
      // Final already -- no simulation ran, nothing is animating -- so the new
      // arrangement is worth keeping straight away.
      reportRender('partial')
      void persistPositions()
      return
    }
    relayout()
    reportRender('cold')
  }

  /**
   * Keep the renderer's idea of its box in step with the element's.
   *
   * Coalesced into a single trailing frame because a 120ms grid transition fires
   * this on every frame, and each call forces a full re-render with
   * `textureOnViewport` on. Zero-sized boxes are skipped -- entering and leaving
   * full screen transiently produces them, and resizing to 0 leaves the renderer
   * unable to recover.
   */
  function observe(el: HTMLElement) {
    observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect
      // A zero box is NOT resized into -- the renderer cannot recover from
      // being told it is zero pixels -- but it is remembered, because
      // everything computed while it held is worthless: a fit against no width
      // leaves a transform that frames nothing, and it survives until something
      // recomputes it. Entering and leaving full screen produces one
      // transiently, a rail can starve the track on a narrow window, and the
      // canvas can mount before it has been given a size at all. A measurement
      // that did not arrive is treated the same way, for the same reason.
      if (!box || box.width === 0 || box.height === 0) {
        collapsed = true
        return
      }
      if (pendingFrame) cancelAnimationFrame(pendingFrame)
      pendingFrame = requestAnimationFrame(() => {
        pendingFrame = 0
        // Coming back from a dead box is the one case that re-frames
        // unconditionally rather than through `resizeToContainer`'s guard:
        // there is no centre worth preserving from a viewport that had no
        // dimensions.
        if (collapsed) {
          collapsed = false
          settleCanvas(ui.graphSpacing)
          return
        }
        resizeToContainer()
      })
    })
    observer.observe(el)
  }

  function disconnect() {
    uncover()
    observer?.disconnect()
    observer = null
    if (pendingFrame) cancelAnimationFrame(pendingFrame)
    pendingFrame = 0
  }

  return { render, relayout, persistPositions, scopeKey, observe, disconnect, arranging }
}
