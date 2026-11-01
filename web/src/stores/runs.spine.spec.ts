import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

import { useHealthStore } from '@/stores/health'
import { useRunTypesStore } from '@/stores/runTypes'
import { useRunsStore } from '@/stores/runs'

/**
 * The live spine belongs to the run being watched, not to whatever this tab
 * last launched.
 *
 * Both callers of `attach` pass a kind they may not have yet: the stream
 * attaches the moment the health probe names a run, and the run page attaches
 * while still loading it. Keeping the previous kind in that window drew a
 * second source run under the research pipeline's six stages, which then grew a
 * seventh when its own `research` stage reported -- so the header counted the
 * run 7 of 7 while the run page counted the same run 1 of 2.
 *
 * `useRunStream` will not re-attach to a run it is already on, so a guess taken
 * in that window is the spine for the whole run unless something re-seeds it.
 */
vi.mock('@/api/sse', () => ({ streamRun: () => () => {} }))

function healthWith(runs: { run_id: string; kind: string }[]) {
  const health = useHealthStore()
  health.data = {
    active_run: runs[0]?.run_id ?? null,
    runs: {
      active: runs.map((r) => ({ ...r, lane: 'research', label: r.kind })),
      queued: [],
    },
  } as unknown as typeof health.data
}

beforeEach(() => {
  setActivePinia(createPinia())
  // Only the field the spine reads. The real table arrives over the wire and
  // carries a dozen more, none of which decide what is drawn here.
  const types = useRunTypesStore()
  types.types = {
    on_demand: { kind: 'on_demand', stages: ['fetch', 'analyze', 'acronyms', 'brief'] },
    research: { kind: 'research', stages: ['research', 'analyze'] },
  } as unknown as (typeof types)['types']
})

describe('the stage spine follows the run', () => {
  it('resolves the kind from the probe when the caller has none', () => {
    healthWith([{ run_id: 'run:a', kind: 'research' }])
    const runs = useRunsStore()

    runs.attach('run:a')

    expect(runs.stages.map((s) => s.name)).toEqual(['research', 'analyze'])
  })

  it('re-seeds when the kind arrives after the attach', async () => {
    healthWith([])
    const runs = useRunsStore()

    // The window: nobody can say what this run is yet, so it draws the default.
    runs.attach('run:a')
    expect(runs.stages.length).toBeGreaterThan(2)

    healthWith([{ run_id: 'run:a', kind: 'research' }])
    await nextTick()

    expect(runs.stages.map((s) => s.name)).toEqual(['research', 'analyze'])
  })

  it('keeps what a stage has already reported across the re-seed', async () => {
    healthWith([])
    const runs = useRunsStore()
    runs.attach('run:a')

    // What the stream would have written by the time the kind lands.
    const analyzing = runs.stages.find((s) => s.name === 'analyze')
    expect(analyzing).toBeDefined()
    analyzing!.status = 'running'

    healthWith([{ run_id: 'run:a', kind: 'research' }])
    await nextTick()

    expect(runs.stages.find((s) => s.name === 'analyze')?.status).toBe('running')
  })

  it('keeps a stage the new list does not declare, rather than losing it', async () => {
    healthWith([])
    const runs = useRunsStore()
    runs.attach('run:a')
    const fetching = runs.stages.find((s) => s.name === 'fetch')
    fetching!.status = 'ok'

    healthWith([{ run_id: 'run:a', kind: 'research' }])
    await nextTick()

    // `research` declares no fetch stage, but one that RAN is a fact about this
    // run and dropping it would hide work from the reader watching it.
    expect(runs.stages.map((s) => s.name)).toContain('fetch')
  })
})
