/**
 * How a run's event stream becomes what is on screen.
 *
 * Driven through `attach` with `streamRun` mocked, rather than by exporting the
 * reducer: the wiring between the two is part of what is being checked, and a
 * function exported only for a test stops being the one production uses.
 *
 * Every case here is a state machine over a stream, which is the shape
 * `vue-tsc --noEmit` is least able to help with — the types are the same
 * whether an unknown stage is appended or dropped, and the same whether
 * counters merge or replace. Both have been wrong here before, and both were
 * invisible until someone read a run.
 */
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useRunsStore } from './runs'

import type { RunEvent } from '@/types/run'

const emit: {
  onEvent?: (e: RunEvent) => void
  onDone?: (e: RunEvent) => void
  onError?: (message: string) => void
  opened: string[]
} = { opened: [] }

vi.mock('@/api/sse', () => ({
  streamRun: (runId: string, handlers: Record<string, unknown>) => {
    emit.opened.push(runId)
    emit.onEvent = handlers.onEvent as (e: RunEvent) => void
    emit.onDone = handlers.onDone as (e: RunEvent) => void
    emit.onError = handlers.onError as (message: string) => void
    return () => {}
  },
}))

let seq = 0
function event(type: string, extra: Partial<RunEvent> = {}): RunEvent {
  seq += 1
  return {
    seq,
    run_id: 'run:x',
    ts: '2026-08-08T00:00:00.000Z',
    type,
    stage: null,
    data: {},
    ...extra,
  } as RunEvent
}

beforeEach(() => {
  seq = 0
  emit.opened = []
  setActivePinia(createPinia())
  // `stores/toasts.ts` schedules dismissal with `window.setTimeout`, and a
  // finished run raises a toast. The environment here is `node` (see
  // `vitest.config.ts`: unit tests, no DOM), so `window` has to exist for the
  // done path to be reachable at all. Stubbed rather than changing the store:
  // it runs in a browser and is right to say so.
  vi.stubGlobal('window', { setTimeout, clearTimeout })
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } }),
    ),
  )
})
afterEach(() => vi.restoreAllMocks())

describe('the run event stream', () => {
  it('appends a stage it has never heard of rather than dropping it', () => {
    // The `persist` regression: the store discarded events whose stage name was
    // not in its hardcoded spine, so a real stage ran and finished with nothing
    // on screen from S1 onwards. Per-type stage lists make an unknown name MORE
    // likely, not less.
    const runs = useRunsStore()
    runs.attach('run:x', 0, 'daily')
    const before = runs.stages.map((s) => s.name)
    expect(before).not.toContain('persist')

    emit.onEvent!(event('stage_started', { stage: 'persist' }))

    expect(runs.stages.map((s) => s.name)).toContain('persist')
    expect(runs.stages.find((s) => s.name === 'persist')?.status).toBe('running')
  })

  it('marks a finished stage by whether it carried an error, not by arriving', () => {
    const runs = useRunsStore()
    runs.attach('run:x', 0, 'daily')

    emit.onEvent!(event('stage_started', { stage: 'fetch' }))
    emit.onEvent!(event('stage_finished', { stage: 'fetch', data: { documents_new: 4 } }))
    emit.onEvent!(event('stage_started', { stage: 'analyze' }))
    emit.onEvent!(event('stage_finished', { stage: 'analyze', data: { error: 'the model died' } }))

    const byName = Object.fromEntries(runs.stages.map((s) => [s.name, s]))
    expect(byName.fetch.status).toBe('ok')
    expect(byName.fetch.counts).toEqual({ documents_new: 4 })
    expect(byName.analyze.status).toBe('error')
  })

  it('MERGES counters rather than replacing them', () => {
    // Contract C6 counters arrive in separate events across stages. Replacing
    // would make the last event the only one, so a finished run would report
    // whatever its final stage happened to emit and zero for everything before.
    const runs = useRunsStore()
    runs.attach('run:x', 0, 'daily')

    emit.onEvent!(event('counter', { data: { documents_new: 12 } }))
    emit.onEvent!(event('counter', { data: { entities_new: 5 } }))
    emit.onEvent!(event('counter', { data: { documents_new: 14 } }))

    expect(runs.counters).toEqual({ documents_new: 14, entities_new: 5 })
  })

  it('keeps model chatter out of the operator log', () => {
    // The log is a short list of decisions. A research run emits ten thousand
    // `model_message` events; putting them here would make it unreadable and is
    // why the transcript is a separate buffer.
    const runs = useRunsStore()
    runs.attach('run:x', 0, 'daily')

    emit.onEvent!(event('model_message', { data: { task: 'analyze', kind: 'text' } }))
    emit.onEvent!(event('warning', { stage: 'fetch', data: { message: 'a source is down' } }))

    expect(runs.transcript).toHaveLength(1)
    expect(runs.log).toHaveLength(1)
    expect(runs.log[0].type).toBe('warning')
  })

  it('does not log a counter or a stage_progress event', () => {
    const runs = useRunsStore()
    runs.attach('run:x', 0, 'daily')

    emit.onEvent!(event('counter', { data: { llm_calls: 3 } }))
    emit.onEvent!(event('stage_progress', { stage: 'fetch', data: { done: 2 } }))

    expect(runs.log).toHaveLength(0)
  })

  it('bounds the transcript so a long run cannot grow the tab without limit', () => {
    const runs = useRunsStore()
    runs.attach('run:x', 0, 'daily')

    for (let i = 0; i < 260; i++) {
      emit.onEvent!(event('model_message', { data: { task: 'analyze', kind: 'text', n: i } }))
    }

    expect(runs.transcript.length).toBeLessThanOrEqual(200)
    // The OLDEST are dropped, not the newest: what someone watching wants is
    // what just happened.
    expect(runs.transcript.at(-1)?.seq).toBe(260)
  })

  it('records both terminal verdicts from the done event', () => {
    const runs = useRunsStore()
    runs.attach('run:x', 0, 'daily')

    emit.onDone!(
      event('done', {
        data: {
          status: 'succeeded_partial',
          outcome: 'delivered',
          brief_id: 'brief:2026-08-08:x',
        },
      }),
    )

    // TWO verdicts, and this run needs both: it wrote its brief AND it hit
    // trouble on the way. `succeeded_partial` is not a failure -- CLAUDE.md's
    // "never let a partially useful run report as failed" has a UI half -- and
    // it is not how a run says it found nothing either. ADR 0183.
    //
    // `outcomeText` is null here because this store has not loaded
    // `/runs/types`, which is where the per-kind phrases come from. That is
    // the honest result and `verdictFor` handles it: it opens the underscores
    // on the raw value rather than rendering nothing.
    expect(runs.finished).toEqual({
      status: 'succeeded_partial',
      outcome: 'delivered',
      outcomeText: null,
      briefId: 'brief:2026-08-08:x',
    })
  })

  it('leaves the outcome null when the run had none', () => {
    const runs = useRunsStore()
    runs.attach('run:x', 0, 'daily')

    emit.onDone!(event('done', { data: { status: 'failed', brief_id: null } }))

    // A run that failed never reached an answer, and inventing one for it is
    // the same overload `status` was relieved of, running the other way.
    expect(runs.finished).toEqual({
      status: 'failed',
      outcome: null,
      outcomeText: null,
      briefId: null,
    })
  })
})

/**
 * `In flight` is health-driven, so a launch that does not re-probe health leaves
 * the run invisible.
 *
 * The symptom is "I had to hard reset to see it": the run appears only on the
 * next scheduled tick, 30 seconds away (`useAppPolling`), and reloading ticks
 * immediately on mount. The 3s live cadence keys off `lightRuns`, so a
 * research-lane run -- `on_demand`, `daily`, `rewrite` -- never speeds it up.
 *
 * Refreshing the history list alone is the trap that hid it: the row shows up
 * under `History` while `In flight` stays empty, which reads as a run that had
 * already finished.
 */
/**
 * The history list rides `createPagedList` (`lib/paging.ts`), which owns the
 * append/de-dupe/in-flight rules and proves them in `paging.spec.ts`. What is
 * the STORE's to get wrong is the fetcher: the filter must ride every page,
 * and the envelope's `hidden` extra must land.
 */
describe('the history list', () => {
  it('appends pages, carrying the hidden filter and the hidden count on each', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: unknown) => {
        const offset = Number(new URL(String(url), 'http://x').searchParams.get('offset'))
        const items = Array.from({ length: 25 }, (_, i) => ({
          id: `run:${offset + i}`,
          status: 'succeeded',
          counters: {},
          warnings: [],
        }))
        return new Response(JSON.stringify({ items, total: 60, hidden: 3 }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }),
    )
    const runs = useRunsStore()

    await runs.setIncludeHidden(true)
    await runs.loadMore()

    expect(runs.runs).toHaveLength(50)
    expect(runs.total).toBe(60)
    expect(runs.hidden).toBe(3)
    expect(runs.canLoadMore).toBe(true)
    const asked = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.map((c) =>
      String(c[0]),
    )
    expect(asked).toHaveLength(2)
    expect(asked.every((p) => p.includes('include_hidden=true'))).toBe(true)
    expect(asked[1]).toContain('offset=25')
  })
})

describe('a run appearing and disappearing without a reload', () => {
  function paths() {
    return (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.map((c) =>
      String(c[0]),
    )
  }

  it('re-probes health as well as the history list when a run is launched', async () => {
    const runs = useRunsStore()
    await runs.launched()

    const asked = paths()
    expect(asked.some((p) => p.includes('/health'))).toBe(true)
    expect(asked.some((p) => p.includes('/runs'))).toBe(true)
  })

  it('re-probes health when a run finishes, so the row leaves In flight', () => {
    const runs = useRunsStore()
    runs.attach('run:x', 0, 'daily')
    ;(fetch as unknown as { mockClear: () => void }).mockClear()

    emit.onDone!(event('done', { data: { status: 'succeeded', brief_id: null } }))

    // Health, not only `/runs`: refreshing history alone left the finished run
    // sitting in `In flight` with frozen counters for up to another 30s, which
    // reads as a run that hung.
    expect(paths().some((p) => p.includes('/health'))).toBe(true)
  })
})

describe('a stream that closed hard', () => {
  it('can be re-attached for the same run', () => {
    const runs = useRunsStore()
    runs.attach('run:x', 0, 'daily')
    expect(runs.streaming).toBe(true)

    emit.onError!('stream closed')
    expect(runs.streaming).toBe(false)
    // The subject survives the connection: the live panel keeps rendering the
    // run it is waiting to hear from again.
    expect(runs.attachedRunId).toBe('run:x')

    runs.attach('run:x', 0, 'daily')
    expect(emit.opened).toEqual(['run:x', 'run:x'])
    expect(runs.streaming).toBe(true)
  })

  it('still refuses a duplicate attach while the stream is live', () => {
    const runs = useRunsStore()
    runs.attach('run:x', 0, 'daily')
    runs.attach('run:x', 0, 'daily')

    expect(emit.opened).toEqual(['run:x'])
  })

  it('reports no stream once the run is done', () => {
    const runs = useRunsStore()
    runs.attach('run:x', 0, 'daily')

    emit.onDone!(event('done', { data: { status: 'succeeded', brief_id: null } }))

    expect(runs.streaming).toBe(false)
  })
})
