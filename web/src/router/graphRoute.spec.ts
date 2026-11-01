import type { LocationQuery } from 'vue-router'
import { describe, expect, it } from 'vitest'

import {
  emptyIntent,
  fromGraphQuery,
  graphIntentKey,
  graphShapeKey,
  toGraphQuery,
} from '@/router/graphRoute'

/**
 * The two properties the graph view is built on, and neither is visible from
 * inside a component: encode and decode are inverses, and the shape key names
 * every payload apart.
 *
 * The second one is what the position cache is keyed by. `includeUnconfirmed`
 * roughly triples the canvas and was missing from the key, so one setting's
 * coordinates described the other's graph at full coverage -- no layout ever ran
 * to correct it, and the snapshot outlived the tab. ADR 0247.
 */
function query(over: Record<string, string> = {}) {
  return { mode: 'all', ...over }
}

describe('fromGraphQuery / toGraphQuery', () => {
  it('round-trips an intent unchanged', () => {
    const intent = {
      ...emptyIntent('focus'),
      focusId: 'company:a',
      depth: 2,
      types: ['Company'],
      predicates: ['PART_OF'],
      minSourceCount: 3,
      includeUnconfirmed: true,
    }
    expect(graphIntentKey(fromGraphQuery(toGraphQuery(intent) as LocationQuery))).toBe(
      graphIntentKey(intent),
    )
  })

  it('writes the names seen once only when they are asked for', () => {
    expect(toGraphQuery(emptyIntent('all')).unconfirmed).toBeUndefined()
    expect(toGraphQuery({ ...emptyIntent('all'), includeUnconfirmed: true }).unconfirmed).toBe(
      'show',
    )
  })

  it('defaults to confirmed names, whatever else the URL says', () => {
    expect(fromGraphQuery(query()).includeUnconfirmed).toBe(false)
    expect(fromGraphQuery(query({ unconfirmed: 'nonsense' })).includeUnconfirmed).toBe(false)
    expect(fromGraphQuery(query({ unconfirmed: 'show' })).includeUnconfirmed).toBe(true)
  })
})

describe('graphShapeKey', () => {
  const on = { ...emptyIntent('all'), includeUnconfirmed: true }

  it('separates the two answers in the whole graph', () => {
    expect(graphShapeKey(on)).not.toBe(graphShapeKey(emptyIntent('all')))
  })

  it('separates them in focus mode, whose endpoint honours the flag too', () => {
    const focus = { ...emptyIntent('focus'), focusId: 'company:a' }
    expect(graphShapeKey({ ...focus, includeUnconfirmed: true })).not.toBe(graphShapeKey(focus))
  })

  it("leaves brief mode alone, because the brief's endpoint ignores it", () => {
    const brief = { ...emptyIntent('brief'), briefId: 'brief:1' }
    expect(graphShapeKey({ ...brief, includeUnconfirmed: true })).toBe(graphShapeKey(brief))
  })
})

describe('the landing scope', () => {
  it('opens on the latest brief, not the whole graph', () => {
    const intent = fromGraphQuery({})
    expect(intent.mode).toBe('brief')
    expect(intent.briefId).toBeNull()
    expect(intent.hideIsolated).toBe(false)
  })

  it('still reads a pre-mode seed bookmark as a focus', () => {
    expect(fromGraphQuery({ seed: 'company:a' }).mode).toBe('focus')
  })

  it('keeps the whole graph reachable by asking for it', () => {
    expect(fromGraphQuery({ mode: 'all' }).mode).toBe('all')
    expect(fromGraphQuery({ mode: 'all' }).hideIsolated).toBe(true)
  })
})
