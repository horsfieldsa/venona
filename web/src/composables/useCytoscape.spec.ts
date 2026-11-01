/**
 * The two pure pieces of the graph's render cost.
 *
 * Both exist because the canvas cannot show you what they fix. A colour
 * resolved once per node instead of once per prefix renders identically to one
 * resolved once per prefix; a hover that re-classes every element renders
 * identically to one that touches six. The only difference is the frame, and a
 * frame is not something a test can assert on — so what is checked here is the
 * decision each one makes, extracted to where it can be read.
 *
 * `environment: 'node'`: neither piece touches the DOM. The colour probe and
 * Cytoscape itself stay ui_smoke's job.
 */
import { describe, expect, it, vi } from 'vitest'

import { emphasisDelta, expandRingRadius, framesNothing, memoResolver } from './useCytoscape'

describe('memoResolver', () => {
  it('resolves once per distinct key', () => {
    const resolve = vi.fn((key: string) => `value:${key}`)
    const memo = memoResolver(resolve)

    expect(memo.get('--entity-org')).toBe('value:--entity-org')
    expect(memo.get('--entity-org')).toBe('value:--entity-org')
    expect(memo.get('--entity-prog')).toBe('value:--entity-prog')

    expect(resolve).toHaveBeenCalledTimes(2)
  })

  it('resolves again after a clear', () => {
    const resolve = vi.fn((key: string) => `value:${key}`)
    const memo = memoResolver(resolve)

    memo.get('--entity-org')
    memo.clear()
    memo.get('--entity-org')

    // The theme switch is the clear: a cached colour belongs to the palette in
    // force when it resolved, so reusing it under another theme paints the
    // canvas in the wrong palette.
    expect(resolve).toHaveBeenCalledTimes(2)
    expect(memo.size).toBe(1)
  })
})

describe('emphasisDelta', () => {
  it('mutes what the pointer left and lights what it reached', () => {
    const { mute, unmute } = emphasisDelta(['a', 'b'], ['c', 'd'])

    expect(mute).toEqual(['a', 'b'])
    expect(unmute).toEqual(['c', 'd'])
  })

  it('leaves the shared part of two overlapping neighbourhoods alone', () => {
    const { mute, unmute } = emphasisDelta(['a', 'b', 'c'], ['b', 'c', 'd'])

    // `b` and `c` are lit before and after. Touching them is the churn the
    // whole diff exists to avoid.
    expect(mute).toEqual(['a'])
    expect(unmute).toEqual(['d'])
  })

  it('handles a hood that contains the other hood entirely', () => {
    expect(emphasisDelta(['a', 'b', 'c'], ['b'])).toEqual({ mute: ['a', 'c'], unmute: [] })
    expect(emphasisDelta(['b'], ['a', 'b', 'c'])).toEqual({ mute: [], unmute: ['a', 'c'] })
  })

  it('treats an empty side as the whole of the other', () => {
    expect(emphasisDelta([], ['a', 'b'])).toEqual({ mute: [], unmute: ['a', 'b'] })
    expect(emphasisDelta(['a', 'b'], [])).toEqual({ mute: ['a', 'b'], unmute: [] })
  })
})

describe('expandRingRadius', () => {
  it('never draws a neighbour closer than the simulation used to settle it', () => {
    // One neighbour has all the room in the world, so the ring is the distance
    // `idealEdgeLength` produced -- an expand of a single node has to look as
    // it did before the simulation was removed.
    expect(expandRingRadius(1)).toBe(90)
    expect(expandRingRadius(3)).toBe(90)
  })

  it('⭐ grows with the count, so a hub does not draw as one smudge', () => {
    // THE FAILURE THIS PREVENTS. Every neighbour of the clicked node shares
    // exactly one placed neighbour -- that node -- so centroid placement alone
    // lands them all on the same point. Sixteen at a fixed radius overlap;
    // sixteen on a ring sized by arc do not.
    const sixteen = expandRingRadius(16)
    expect(sixteen).toBeGreaterThan(expandRingRadius(4))

    const arc = (2 * Math.PI * sixteen) / 16
    expect(arc).toBeGreaterThanOrEqual(74)
  })

  it('scales with the spacing control', () => {
    expect(expandRingRadius(16, 2)).toBeCloseTo(expandRingRadius(16) * 2, 5)
    expect(expandRingRadius(1, 0.5)).toBe(45)
  })

  it('treats an empty expand as one node rather than dividing by zero', () => {
    expect(Number.isFinite(expandRingRadius(0))).toBe(true)
    expect(expandRingRadius(0)).toBe(90)
  })
})

describe('layoutExpand', () => {
  it('⭐ runs no simulation, ever', async () => {
    // A STANDING GUARD, not a behaviour test. `docs/GRAPH-PERF.md` (2026-08-26) measured the
    // pinned-fcose design at ~1s whether 57 nodes are new or 189, and at no
    // completion past a few hundred -- and it is the obvious design, so it is
    // what the next person to touch this will reach for. `placeUnpositioned`'s
    // docstring says the same thing from the other side.
    const source: string = await import('./useCytoscape?raw').then((m) => m.default)
    const body = source.slice(
      source.indexOf('export function layoutExpand'),
      source.indexOf('export function placeUnpositioned'),
    )

    // The CALL, not the word: the body names `fcose` on purpose, in the comment
    // warning the next reader off it.
    expect(body).not.toContain('cy.layout(')
    expect(body).not.toContain('fixedNodeConstraint:')
    expect(body).toContain('placeUnpositioned')
  })
})

/**
 * The guard that catches a viewport left somewhere the graph is not.
 *
 * Its whole job is to be silent: it may only fire when the reader is looking at
 * empty space, because firing while anything is on screen would move a graph
 * somebody is reading. So the cases that matter are the ones where it must
 * stay quiet.
 */
describe('framesNothing', () => {
  const view = { x1: 0, y1: 0, x2: 100, y2: 100 }

  it('is false while any part of the drawing is in view', () => {
    expect(framesNothing(view, { x1: 50, y1: 50, x2: 500, y2: 500 })).toBe(false)
    expect(framesNothing(view, { x1: -500, y1: -500, x2: 500, y2: 500 })).toBe(false)
    expect(framesNothing(view, { x1: 10, y1: 10, x2: 20, y2: 20 })).toBe(false)
  })

  it('is false for a graph resting exactly against the edge', () => {
    // One pixel of contact is still something the reader can see, and a fit
    // fired here would be the canvas twitching at rest.
    expect(framesNothing(view, { x1: 100, y1: 0, x2: 300, y2: 100 })).toBe(false)
  })

  it('is true only when the viewport and the drawing are disjoint', () => {
    expect(framesNothing(view, { x1: 101, y1: 0, x2: 300, y2: 100 })).toBe(true)
    expect(framesNothing(view, { x1: 0, y1: -400, x2: 100, y2: -1 })).toBe(true)
    // The state that was reported: the view panned far off the graph, which is
    // where a box change leaves it when nothing re-frames.
    expect(framesNothing(view, { x1: -8000, y1: -6000, x2: -7000, y2: -5000 })).toBe(true)
  })
})
