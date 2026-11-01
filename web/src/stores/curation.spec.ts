/**
 * How a review queue SETTLES.
 *
 * These are the behaviours `vue-tsc --noEmit` is structurally blind to. The
 * types are identical whether `drop` keeps the cursor where it is or resets it
 * to zero, and identical whether `undo` puts a pair back where it was or
 * appends it to the end — but a curator working 147 conflicts by keyboard
 * notices immediately, and the store's own header calls both of them
 * load-bearing rather than polish.
 *
 * The definitions queue is here for a second reason. It held all of this as
 * local component state until it was folded into this store, and the reason the
 * roadmap gave for folding it was "the next bug that has to be fixed twice".
 * Testing both queues against the same expectations is what makes that concrete:
 * if one drifts from the other, one of these fails.
 */
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useCurationStore } from './curation'
import { useToastsStore } from './toasts'

import type { Conflict } from '@/types/curation'

/** `fetch` may be called with a `Request` rather than a URL string -- which is
 *  exactly how `openapi-fetch` calls it. `String(aRequest)` is
 *  `"[object Request]"`, so normalise before parsing. */
function urlOf(input: RequestInfo | URL): string {
  return input instanceof Request ? input.url : String(input)
}

/** Answer by path, so a store that fans out to `/curation/status` and
 *  `/entities/cards:batch` behind the call under test does not need each one
 *  wired individually. */
function routes(table: Record<string, unknown>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = urlOf(input)
    const key = Object.keys(table).find((k) => url.includes(k))
    return new Response(JSON.stringify(key ? table[key] : {}), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  })
}

/** Every field the wire carries, so the fixture can be handed to the store's
 *  typed API and not only stuffed into a fetch response. */
function conflict(id: string): Conflict {
  return {
    id,
    kind: 'duplicate',
    surface: id.toUpperCase(),
    entity_type: 'Organization',
    existing_entity_id: `org:${id}-a`,
    existing_name: `${id} A`,
    proposed_entity_id: `org:${id}-b`,
    proposed_name: `${id} B`,
    created_at: '2026-08-08T00:00:00.000Z',
    resolved: false,
    article_id: null,
    resolution: null,
    resolved_at: null,
    resolved_by: null,
    suggestion: null,
    suggestion_child: null,
    suggestion_note: null,
    suggestion_predicate: null,
  }
}

function surface(name: string) {
  return {
    surface: name,
    normalized: name.toLowerCase(),
    sightings: 3,
    articles: 2,
    attempted: false,
    attempt_count: 0,
    decided: false,
    readings: [],
    contradiction: null,
    suggestion: null,
  }
}

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => vi.restoreAllMocks())

describe('the merge queue settles in place', () => {
  it('keeps the cursor where it is, so the next pair slides into the slot', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        '/curation/conflicts': { items: ['a', 'b', 'c'].map(conflict), total_open: 3 },
        '/curation/status': { open_conflicts: 3 },
        '/curation/merges': { merge_id: 'm1', from_id: 'org:b-a', to_id: 'org:b-b', active: true },
        '/entities': { cards: {}, missing: [] },
      }),
    )
    const curation = useCurationStore()
    await curation.load()
    curation.move(1)
    expect(curation.current?.id).toBe('b')

    await curation.merge(curation.current!, 'org:b-a', 'org:b-b')

    // Not zero, and not two. The pair that WAS at index 1 has gone and `c` has
    // taken its place, which is what makes a keyboard queue feel like one.
    expect(curation.cursor).toBe(1)
    expect(curation.current?.id).toBe('c')
    expect(curation.remaining).toBe(2)
  })

  it('reloads when the buffer drains with open rows still on the server', async () => {
    // The queue is one bounded read (limit=500) rather than offset-paged; a
    // longer queue is reached by reloading once the buffer is worked dry —
    // which is safe exactly then, because there is no pair under the fingers.
    const fetchMock = routes({
      '/curation/conflicts': { items: [conflict('only')], counts: {}, total_open: 6 },
      '/keep-apart': {},
      '/curation/status': { open_conflicts: 6 },
    })
    vi.stubGlobal('fetch', fetchMock)
    const curation = useCurationStore()
    await curation.load()
    await curation.refreshStatus()

    await curation.keepApart(curation.current!)

    await vi.waitFor(() => {
      const asked = fetchMock.mock.calls.filter((c) =>
        urlOf(c[0] as RequestInfo).includes('/curation/conflicts?'),
      )
      expect(asked.length).toBeGreaterThanOrEqual(2)
    })
  })

  it('decrements the counter locally rather than refetching it', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        '/curation/conflicts': { items: ['a', 'b'].map(conflict), total_open: 2 },
        '/curation/status': { open_conflicts: 2 },
        '/entities': { cards: {}, missing: [] },
      }),
    )
    const curation = useCurationStore()
    await curation.load()
    await curation.refreshStatus()
    expect(curation.status?.open_conflicts).toBe(2)

    await curation.keepApart(curation.current!)

    // A refetch would make the counter flicker and would throw away the
    // curator's position; the store decrements what is on screen.
    expect(curation.remaining).toBe(1)
  })

  it('steps the cursor back when the last pair in the queue is decided', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        '/curation/conflicts': { items: ['a', 'b'].map(conflict), total_open: 2 },
        '/curation/status': { open_conflicts: 2 },
        '/entities': { cards: {}, missing: [] },
      }),
    )
    const curation = useCurationStore()
    await curation.load()
    curation.move(1)

    await curation.keepApart(curation.current!)

    // Without the clamp the cursor points one past the end and `current` is
    // null with a non-empty queue on screen.
    expect(curation.cursor).toBe(0)
    expect(curation.current?.id).toBe('a')
  })

  it('puts an undone pair back where it was, not on the end', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        '/curation/conflicts': { items: ['a', 'b', 'c'].map(conflict), total_open: 3 },
        '/curation/status': { open_conflicts: 3 },
        '/entities': { cards: {}, missing: [] },
      }),
    )
    const curation = useCurationStore()
    await curation.load()
    curation.move(1)
    await curation.keepApart(curation.current!)
    expect(curation.conflicts.map((c) => c.id)).toEqual(['a', 'c'])

    await curation.undo()

    // Appending would silently reorder the queue under someone working it.
    expect(curation.conflicts.map((c) => c.id)).toEqual(['a', 'b', 'c'])
    expect(curation.current?.id).toBe('b')
  })

  it('reports a refusal instead of dropping the pair', async () => {
    let call = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = urlOf(input)
        if (url.includes('/curation/conflicts')) {
          return new Response(JSON.stringify({ items: [conflict('a')], total_open: 1 }), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          })
        }
        if (url.includes('/curation/merges')) {
          call += 1
          // The shape `MergeError` produces: a real refusal, not a fault.
          return new Response(JSON.stringify({ detail: 'org:a-a is already merged away' }), {
            status: 409,
            headers: { 'content-type': 'application/json' },
          })
        }
        return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
      }),
    )
    const curation = useCurationStore()
    await curation.load()

    const ok = await curation.merge(curation.current!, 'org:a-a', 'org:a-b')

    expect(ok).toBe(false)
    expect(call).toBe(1)
    // The pair MUST still be there: a 409 means the merge did not happen, and
    // dropping it would lose a conflict nobody has decided.
    expect(curation.remaining).toBe(1)
    expect(curation.error).toContain('already merged away')
    expect(curation.busy).toBe(false)
  })
})

describe('the definitions queue settles the same way', () => {
  it('keeps the cursor, so the next surface slides into the slot', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        '/curation/definitions': {
          items: ['AAA', 'BBB', 'CCC'].map(surface),
          total_pending: 40,
          decided: 0,
        },
        '/curation/status': { open_conflicts: 0 },
      }),
    )
    const curation = useCurationStore()
    await curation.loadDefinitions()
    curation.moveDefinition(1)
    expect(curation.currentDefinition?.surface).toBe('BBB')

    await curation.definitionVerdict('reject', async () => {})

    expect(curation.definitionsCursor).toBe(1)
    expect(curation.currentDefinition?.surface).toBe('CCC')
    // `total_pending` is the WHOLE backlog, not the loaded page, so it counts
    // down from 40 rather than from 3.
    expect(curation.definitionsPending).toBe(39)
  })

  it('refills from the cursor forward and never moves the pair in hand', async () => {
    // The buffer is a window on a longer queue now. Refill must append behind
    // the cursor's back: the surface under the curator's fingers stays put.
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(urlOf(input), 'https://t')
      if (url.pathname.includes('/curation/definitions')) {
        const offset = Number(url.searchParams.get('offset') ?? 0)
        const limit = Number(url.searchParams.get('limit') ?? 50)
        const names = Array.from({ length: limit }, (_, i) => `S${offset + i}`)
        return new Response(
          JSON.stringify({ items: names.map(surface), total_pending: 120, decided: 0 }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        )
      }
      return new Response('{}', {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)
    const curation = useCurationStore()
    await curation.loadDefinitions()
    expect(curation.definitions).toHaveLength(50)

    // Walk to within the refill margin of the end.
    for (let i = 0; i < 40; i++) curation.moveDefinition(1)
    await vi.waitFor(() => expect(curation.definitions.length).toBe(100))

    expect(curation.definitionsCursor).toBe(40)
    expect(curation.currentDefinition?.surface).toBe('S40')
    // The second page was asked for at the buffer's length, not page 0 again.
    const asked = fetchMock.mock.calls.map((c) => urlOf(c[0] as RequestInfo))
    expect(asked.some((u) => u.includes('offset=50'))).toBe(true)
  })

  it('puts an undone surface back at the cursor instead of reloading page 0', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        '/curation/definitions': {
          items: ['AAA', 'BBB', 'CCC'].map(surface),
          total_pending: 3,
          decided: 0,
        },
        '/reopen': {},
        '/curation/status': { open_conflicts: 0 },
      }),
    )
    const curation = useCurationStore()
    await curation.loadDefinitions()
    curation.moveDefinition(1)
    await curation.definitionVerdict('reject', async () => {})
    expect(curation.currentDefinition?.surface).toBe('CCC')

    await curation.undoDefinition()

    // Back in the slot it left, cursor on it — not appended, not a reload.
    expect(curation.definitions.map((d) => d.surface)).toEqual(['AAA', 'BBB', 'CCC'])
    expect(curation.currentDefinition?.surface).toBe('BBB')
    expect(curation.definitionsPending).toBe(3)
  })

  it('does not advance for a verdict that is not one', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        '/curation/definitions': {
          items: ['AAA', 'BBB'].map(surface),
          total_pending: 2,
          decided: 0,
        },
        '/curation/status': { open_conflicts: 0 },
      }),
    )
    const curation = useCurationStore()
    await curation.loadDefinitions()

    await curation.definitionVerdict('lookup', async () => {}, { advance: false })

    // A grounded lookup fetches EVIDENCE. The surface has to stay under the
    // cursor so the new readings appear where the curator is looking.
    expect(curation.currentDefinition?.surface).toBe('AAA')
    expect(curation.definitionsPending).toBe(2)
    expect(curation.definitionsHistory).toHaveLength(0)
  })

  it('keeps the surface and reports the refusal on a 409', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        '/curation/definitions': { items: [surface('AAA')], total_pending: 1, decided: 0 },
        '/curation/status': { open_conflicts: 0 },
      }),
    )
    const curation = useCurationStore()
    await curation.loadDefinitions()

    const ok = await curation.definitionVerdict('accept', async () => {
      throw new Error('boom')
    })

    expect(ok).toBe(false)
    expect(curation.currentDefinition?.surface).toBe('AAA')
    expect(curation.definitionsPending).toBe(1)
    expect(curation.busy).toBe(false)
  })

  it('refuses to act twice while one verdict is in flight', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        '/curation/definitions': {
          items: ['AAA', 'BBB'].map(surface),
          total_pending: 2,
          decided: 0,
        },
        '/curation/status': { open_conflicts: 0 },
      }),
    )
    const curation = useCurationStore()
    await curation.loadDefinitions()

    let release: () => void = () => {}
    const slow = curation.definitionVerdict(
      'accept',
      () => new Promise<void>((resolve) => (release = resolve)),
    )
    // A held key repeats. Without the `busy` guard the second verdict drops a
    // DIFFERENT surface from the one the curator was looking at.
    const second = await curation.definitionVerdict('reject', async () => {})
    release()
    await slow

    expect(second).toBe(false)
    expect(curation.definitionsPending).toBe(1)
    expect(curation.definitions.map((i) => i.surface)).toEqual(['BBB'])
  })
})

describe('a lookup runs in the background', () => {
  afterEach(() => vi.useRealTimers())

  /** A mutable route table plus fake timers: `waitForRun` polls on a 1.5s
   *  `setTimeout`, so each `advanceTimersByTimeAsync(1500)` is one poll, and
   *  mutating the table between advances is the run changing state. */
  function lookupWorld() {
    vi.useFakeTimers()
    // `stores/toasts.ts` schedules dismissal with `window.setTimeout`, and the
    // environment here is `node` (see `vitest.config.ts`) — same stub as
    // `runs.spec.ts` uses.
    vi.stubGlobal('window', { setTimeout, clearTimeout })
    const table: Record<string, unknown> = {
      '/curation/definitions': {
        items: ['AAA', 'BBB', 'CCC'].map(surface),
        total_pending: 3,
        decided: 0,
      },
      '/curation/status': { open_conflicts: 0 },
      '/lookup': { run_id: 'r1', status: 'running', lane: 'light' },
      '/runs/r1': { status: 'running' },
    }
    const fetchMock = routes(table)
    vi.stubGlobal('fetch', fetchMock)
    return { table, fetchMock }
  }

  it('does not block: busy stays false, the cursor advances, other surfaces still settle', async () => {
    lookupWorld()
    const curation = useCurationStore()
    await curation.loadDefinitions()

    await curation.lookupDefinition()

    // The whole point: the POST came back 202 and nothing is waiting on it.
    expect(curation.busy).toBe(false)
    expect(curation.pendingLookups.get('AAA')?.status).toBe('running')
    expect(curation.currentDefinition?.surface).toBe('BBB')

    // The 202 is acknowledged out loud, the way every other run launch is.
    const toasts = useToastsStore()
    expect(toasts.items.some((t) => t.kind === 'running' && t.subject === 'AAA')).toBe(true)

    // A verdict on ANOTHER surface goes through while the lookup is in flight.
    await curation.definitionVerdict('reject', async () => {})
    expect(curation.definitions.map((i) => i.surface)).toEqual(['AAA', 'CCC'])
  })

  it('replaces only the looked-up surface when the run lands', async () => {
    const { table } = lookupWorld()
    const curation = useCurationStore()
    await curation.loadDefinitions()
    await curation.lookupDefinition()
    const untouched = curation.definitions[2]

    table['/runs/r1'] = { status: 'succeeded' }
    table['/curation/definitions'] = {
      items: [
        { ...surface('AAA'), attempted: true, attempt_count: 1 },
        surface('BBB'),
        surface('CCC'),
      ],
      total_pending: 3,
      decided: 0,
    }
    await vi.advanceTimersByTimeAsync(1500)

    expect(curation.pendingLookups.size).toBe(0)
    expect(curation.definitions[0].attempted).toBe(true)
    // Splice, not reload: every other item keeps its identity and the cursor
    // has not moved under the curator.
    expect(curation.definitions[2]).toBe(untouched)
    expect(curation.currentDefinition?.surface).toBe('BBB')
  })

  it('reports a failed run as a toast, never through the shared error', async () => {
    const { table } = lookupWorld()
    const curation = useCurationStore()
    await curation.loadDefinitions()
    await curation.lookupDefinition()

    table['/runs/r1'] = { status: 'failed', error: 'grounding refused' }
    await vi.advanceTimersByTimeAsync(1500)

    // The curator may be three surfaces past AAA; the shared `error` belongs
    // to the verdict in hand.
    expect(curation.pendingLookups.size).toBe(0)
    expect(curation.error).toBeNull()
    const toasts = useToastsStore()
    expect(toasts.items.some((t) => t.kind === 'error' && t.subject === 'AAA')).toBe(true)
  })

  it('refuses a second lookup for a surface already in flight', async () => {
    const { fetchMock } = lookupWorld()
    const curation = useCurationStore()
    await curation.loadDefinitions()

    await curation.lookupDefinition()
    curation.moveDefinition(-1)
    await curation.lookupDefinition()

    const posts = fetchMock.mock.calls.filter(([input]) => urlOf(input).includes('/lookup'))
    expect(posts).toHaveLength(1)
    expect(curation.pendingLookups.size).toBe(1)
  })

  it('re-attaches to in-flight acronym lookups after a reload, and only those', async () => {
    const { table } = lookupWorld()
    table['/health'] = {
      runs: {
        active: [
          {
            run_id: 'r1',
            kind: 'lookup',
            lane: 'light',
            label: 'Definition',
            subject: 'AAA',
            target_kind: 'acronym',
          },
        ],
        queued: [
          // An entity define also has kind "lookup", and its subject is a NAME
          // that can collide with a surface. `target_kind` is the filter.
          {
            run_id: 'r2',
            kind: 'lookup',
            lane: 'light',
            label: 'Definition',
            subject: 'BBB',
            target_kind: 'entity',
          },
          {
            run_id: 'r3',
            kind: 'lookup',
            lane: 'light',
            label: 'Definition',
            subject: 'CCC',
            target_kind: 'acronym',
          },
        ],
      },
    }
    const curation = useCurationStore()
    await curation.loadDefinitions()

    await curation.seedPendingLookups()

    expect([...curation.pendingLookups.keys()].sort()).toEqual(['AAA', 'CCC'])
    expect(curation.pendingLookups.get('AAA')?.status).toBe('running')
    expect(curation.pendingLookups.get('CCC')?.status).toBe('queued')
  })
})

/**
 * The undo stack called itself bounded and was not.
 *
 * A queue of 147 is worked by keyboard at speed, so the stack grows one entry
 * per keystroke for as long as the session lasts. Nothing surfaces it: undo
 * keeps working, the counts stay right, and the tab simply holds every decision
 * ever made until it is reloaded.
 */
describe('the undo stack is bounded', () => {
  it('caps the history and keeps the newest decision undoable', async () => {
    const conflicts = Array.from({ length: 60 }, (_, i) => conflict(`c${i}`))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.includes('/curation/conflicts')) {
          return Response.json({ items: conflicts, total: conflicts.length })
        }
        if (url.includes('/curation/merges')) return Response.json({ merge_id: 'merge:1' })
        if (url.includes('/curation/status')) {
          return Response.json({ open_conflicts: conflicts.length, pending_rebuild: 0 })
        }
        return Response.json({ cards: {}, missing: [] })
      }),
    )

    const curation = useCurationStore()
    await curation.load()
    for (const c of conflicts) {
      await curation.merge(c, c.proposed_entity_id, c.existing_entity_id)
    }

    expect(curation.history.length).toBe(50)
    // Newest first, so the head is the LAST decision and undo still reaches it.
    expect(curation.history[0].conflict.id).toBe('c59')
    // The tail is what went, never the head.
    expect(curation.history.at(-1)?.conflict.id).toBe('c10')
  })
})
