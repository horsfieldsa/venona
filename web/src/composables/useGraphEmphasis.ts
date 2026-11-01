import type { Core } from 'cytoscape'

import { applyEmphasis, clearEmphasis } from '@/composables/useCytoscape'

/**
 * Hover and selection emphasis on the canvas.
 *
 * Three things about this are deliberate:
 *
 *  - **`mouseout` does not clear.** It falls back to whatever is SELECTED, so
 *    hover is a temporary override of a sticky emphasis rather than a second,
 *    parallel highlight system. That is also what makes the keyboard path
 *    produce byte-identical output to the mouse path: selecting a node with `n`
 *    emphasises exactly as pointing at it does, and hover is left as an
 *    enhancement rather than the only route to the information.
 *
 *  - **It never writes a Vue ref.** Pointer motion across a dense canvas fires
 *    these handlers continuously; a ref write per event would re-render the
 *    readout at mouse-move rate. Only SELECTION touches the store, and selection
 *    is a click.
 *
 *  - **One rAF, coalesced.** The handlers record an intent and schedule a single
 *    frame; the frame does the work. Crossing five nodes in one flick costs one
 *    class pass, not five.
 *
 * The Cytoscape instance is held in a local closure, never in a `ref` -- Vue's
 * proxy over its element graph is unusable at a few hundred nodes.
 */
export interface GraphEmphasisOptions {
  /** The node the store considers selected, or null. */
  selectedId: () => string | null
  /** Emphasis stands down entirely while this is true. Path mode owns the
   *  canvas's dimming when it is showing a result, and two dimming systems
   *  fighting over the same elements is worse than either alone. */
  suppressed: () => boolean
}

export interface GraphEmphasis {
  attach(instance: Core): void
  /** Re-evaluate from the current selection. Call when selection changes by any
   *  route -- canvas tap, search, palette, mini-graph, keyboard. */
  refresh(): void
  dispose(): void
}

export function useGraphEmphasis(opts: GraphEmphasisOptions): GraphEmphasis {
  let cy: Core | null = null
  let frame = 0
  /** The hovered node, or `undefined` for "nothing hovered, use the selection". */
  let hovered: string | undefined

  function settle() {
    frame = 0
    if (!cy) return
    if (opts.suppressed()) {
      clearEmphasis()
      return
    }
    applyEmphasis(hovered ?? opts.selectedId() ?? null)
  }

  function schedule() {
    if (frame) return
    frame = requestAnimationFrame(settle)
  }

  function cursor(value: string) {
    const container = cy?.container()
    if (container) container.style.cursor = value
  }

  function attach(instance: Core) {
    cy = instance

    instance.on('mouseover', 'node', (event) => {
      hovered = event.target.id()
      cursor('pointer')
      schedule()
    })

    instance.on('mouseout', 'node', () => {
      hovered = undefined
      cursor('')
      schedule()
    })

    // Edges get the cursor but not the emphasis: an edge already says what it
    // connects by being drawn between two things, and muting the graph around it
    // would mean two different highlight vocabularies on one canvas.
    instance.on('mouseover', 'edge', () => cursor('pointer'))
    instance.on('mouseout', 'edge', () => cursor(''))
  }

  function refresh() {
    schedule()
  }

  function dispose() {
    if (frame) cancelAnimationFrame(frame)
    frame = 0
    hovered = undefined
    // Handlers themselves die with the instance; the pending frame does not.
    cy = null
  }

  return { attach, refresh, dispose }
}
