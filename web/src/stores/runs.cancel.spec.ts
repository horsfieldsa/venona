/**
 * What the Cancel button knows between the press and the next probe.
 *
 * The server reports `cancelling` on every run in flight, but the probe that
 * carries it ticks every thirty seconds while a research run is the only thing
 * live. Without the optimistic half, the button reads `Cancel` for half a
 * minute after being pressed, which is exactly what a press that did nothing
 * looks like — and that is what sent people to it a second time.
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

import { useHealthStore } from '@/stores/health'
import { useRunsStore } from '@/stores/runs'

const posted: string[] = []

vi.mock('@/api/sse', () => ({ streamRun: () => () => {} }))
vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return {
    ...actual,
    apiGet: vi.fn(async () => ({ items: [], total: 0, hidden: 0 })),
    apiPost: vi.fn(async (path: string) => {
      posted.push(path)
      return {}
    }),
  }
})

function inFlight(runs: { run_id: string; cancelling?: boolean }[]) {
  const health = useHealthStore()
  health.data = {
    active_run: runs[0]?.run_id ?? null,
    runs: {
      active: runs.map((r) => ({
        run_id: r.run_id,
        kind: 'on_demand',
        lane: 'research',
        label: 'Reading',
        cancelling: r.cancelling ?? false,
      })),
      queued: [],
    },
  } as unknown as typeof health.data
  health.refresh = async () => {}
}

beforeEach(() => {
  posted.length = 0
  setActivePinia(createPinia())
})

it('reads as cancelling from the press, before the probe agrees', async () => {
  inFlight([{ run_id: 'run:x' }])
  const runs = useRunsStore()
  expect(runs.isCancelling()).toBe(false)

  await runs.cancel()

  expect(posted).toEqual(['/runs/run%3Ax/cancel'])
  expect(runs.isCancelling()).toBe(true)
  expect(runs.isCancelling('run:x')).toBe(true)
})

it('reads the server when the server is the one that knows', () => {
  // Another tab pressed it, or this one reloaded. Nothing local to go on.
  inFlight([{ run_id: 'run:x', cancelling: true }])
  const runs = useRunsStore()
  expect(runs.isCancelling('run:x')).toBe(true)
})

it('stops claiming a run is cancelling once it is no longer in flight', async () => {
  inFlight([{ run_id: 'run:x' }])
  const runs = useRunsStore()
  await runs.cancel()
  expect(runs.isCancelling('run:x')).toBe(true)

  // The run ended. `cancellingIds` reads through the in-flight list precisely
  // so that a finished run cannot be left wearing a transient state forever.
  inFlight([])
  expect(runs.isCancelling('run:x')).toBe(false)
  expect(runs.cancellingIds.size).toBe(0)
})

it('cancels the run it is given, not the one in the header', async () => {
  // A research run and a lookup are routinely live together (contract C11), and
  // the In progress row's button is the only thing that can reach the lookup.
  inFlight([{ run_id: 'run:research' }, { run_id: 'run:lookup' }])
  const runs = useRunsStore()

  await runs.cancel('run:lookup')

  expect(posted).toEqual(['/runs/run%3Alookup/cancel'])
  expect(runs.isCancelling('run:lookup')).toBe(true)
  expect(runs.isCancelling('run:research')).toBe(false)
})

it('knows which runs this instance is actually running', () => {
  // `venona run` from a shell is another process: its row reads running while
  // the cancel endpoint has no handle for it, so the button must not appear.
  inFlight([{ run_id: 'run:mine' }])
  const runs = useRunsStore()

  expect(runs.inFlight('run:mine')).toBe(true)
  expect(runs.inFlight('run:from-a-shell')).toBe(false)
  expect(runs.inFlight(null)).toBe(false)
})

it('does not claim a cancel that the server refused', async () => {
  inFlight([{ run_id: 'run:x' }])
  const client = await import('@/api/client')
  vi.mocked(client.apiPost).mockRejectedValueOnce(new Error('nope'))
  const runs = useRunsStore()

  await runs.cancel()

  expect(runs.isCancelling('run:x')).toBe(false)
  expect(runs.error).toBeTruthy()
})
