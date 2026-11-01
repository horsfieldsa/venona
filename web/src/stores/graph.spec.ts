/**
 * The scope loaders are rivals, not independent requests.
 *
 * Overview, brief, focus and path each decide what the canvas MEANS, and each
 * calls `resetCanvas` and sets `mode` after an await. Click a brief while the
 * overview is still in flight and the overview lands second: the brief's
 * subgraph becomes the whole graph a moment after it arrives, with the rail
 * still saying brief. Nothing errors, nothing is logged, and the reader's only
 * evidence is that the picture changed under them.
 *
 * `environment: 'node'`: this is store state, and the canvas is ui_smoke's job.
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useGraphStore } from './graph'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))

vi.mock('@/api/client', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/api/client')>()
  return { ...real, apiGet: api.get, apiPost: api.post }
})

function payload(id: string) {
  return {
    nodes: [{ id, name: id, type: 'org', relevance: 0.5, mention_count: 1 }],
    edges: [],
    truncated: false,
    total: 1,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.get.mockReset()
  api.post.mockReset()
})

describe('rival scope loads', () => {
  it('discards the overview that the reader has already navigated away from', async () => {
    let releaseOverview = () => {}
    const held = new Promise<void>((resolve) => {
      releaseOverview = resolve
    })
    api.post.mockImplementation(async (path: string) => {
      if (path === '/graph/overview') {
        await held
        return { ...payload('org:everything'), isolated_hidden: 3 }
      }
      return { ...payload('org:in-the-brief'), scope: { brief_id: 'brief:1' } }
    })

    const graph = useGraphStore()
    const overview = graph.loadOverview()
    await graph.loadBrief('brief:1')

    expect(graph.mode).toBe('brief')

    releaseOverview()
    await overview

    // The slow overview describes a scope the reader has left; landing it would
    // reset the canvas out from under the brief.
    expect(graph.mode).toBe('brief')
    expect([...graph.nodes.keys()]).toEqual(['org:in-the-brief'])
    expect(graph.loading).toBe(false)
  })

  it('does not let a superseded failure claim the error line', async () => {
    let failOverview: (reason: Error) => void = () => {}
    const failing = new Promise<void>((_resolve, reject) => {
      failOverview = reject
    })
    api.post.mockImplementation(async (path: string) => {
      if (path === '/graph/overview') {
        await failing
        return payload('org:everything')
      }
      return { ...payload('org:in-the-brief'), scope: { brief_id: 'brief:1' } }
    })

    const graph = useGraphStore()
    const overview = graph.loadOverview()
    await graph.loadBrief('brief:1')

    failOverview(new Error('overview blew up'))
    await overview

    // The brief loaded fine. An error banner from the request the reader
    // abandoned describes nothing on screen.
    expect(graph.error).toBeNull()
    expect(graph.mode).toBe('brief')
  })
})

/**
 * The collections are SHALLOW, and that is a performance property nothing else
 * can see.
 *
 * A deep ref over a Map proxies every value it holds, so a thousand-node
 * payload builds a thousand reactive proxies that nothing reads reactively.
 * The types are identical either way and the app behaves identically either
 * way; the only observable difference is object identity, which is what these
 * pin.
 */
describe('the node and edge collections are shallow', () => {
  it('stores the payload objects themselves, not reactive proxies of them', async () => {
    const node = { id: 'org:a', name: 'A', type: 'org', relevance: 0.5, mention_count: 1 }
    api.post.mockResolvedValue({ nodes: [node], edges: [], truncated: false, total: 1 })

    const graph = useGraphStore()
    await graph.expand(['org:a'])

    // Strict identity: fails under deep reactivity, passes under shallow.
    expect(graph.nodes.get('org:a')).toBe(node)
  })

  it('still notifies its reactive consumers on every batch', async () => {
    api.post.mockResolvedValue({
      nodes: [payload('org:a').nodes[0], payload('org:b').nodes[0]],
      edges: [],
      truncated: false,
      total: 2,
    })

    const graph = useGraphStore()
    expect(graph.nodeCount).toBe(0)
    await graph.expand(['org:a'])

    // Driven by triggerRef, not by per-key reactivity. Miss one and a readout
    // freezes while the canvas updates.
    expect(graph.nodeCount).toBe(2)
    expect(graph.expanded.has('org:a')).toBe(true)
  })
})
