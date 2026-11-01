// @vitest-environment happy-dom
/**
 * Reconnecting after the stream dies under a run that is still going.
 *
 * The failure this covers is invisible to every other check: the types are
 * identical whether the stream is alive or dead, the store still holds the run
 * it thinks it is watching, and the panel keeps rendering the last events it
 * received. It simply stops moving, for the rest of the tab's life, and the
 * only tell is that the counters never advance again.
 *
 * happy-dom because the composable is `onMounted`-driven, so it needs a real
 * component instance to be exercised the way the app frame exercises it.
 */
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'

import { useRunStream } from './useRunStream'

import { useHealthStore } from '@/stores/health'
import { useRunsStore } from '@/stores/runs'
import { useSessionStore } from '@/stores/session'

const stream: { onError?: (message: string) => void; opened: string[] } = { opened: [] }

vi.mock('@/api/sse', () => ({
  streamRun: (runId: string, handlers: Record<string, unknown>) => {
    stream.opened.push(runId)
    stream.onError = handlers.onError as (message: string) => void
    return () => {}
  },
}))

let app: ReturnType<typeof createApp> | null = null

/** The app frame's use of it: mounted once, for as long as the tab is open.
 *
 * Seeded with an ADMIN session, because the runs list and the run stream are
 * admin-only server-side and the composable will not ask for either until it
 * knows the viewer may have them. A reader mounting the same frame is the
 * `stays silent for a reader` case below.
 */
function mountFrame(role: string | null = 'admin', opts: { loaded?: boolean } = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const session = useSessionStore()
  session.role = role
  session.loaded = opts.loaded ?? true
  app = createApp(
    defineComponent({
      setup() {
        useRunStream()
        return () => h('div')
      },
    }),
  )
  app.use(pinia)
  app.mount(document.createElement('div'))
}

/** A poll answering with the same live run, as a fresh object each time. */
function tick(activeRun: string | null) {
  useHealthStore().data = { active_run: activeRun } as never
  return nextTick()
}

beforeEach(() => {
  stream.opened = []
  setActivePinia(createPinia())
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Response.json({ items: [], total: 0, hidden: 0 })),
  )
})

afterEach(() => {
  app?.unmount()
  app = null
  vi.unstubAllGlobals()
})

describe('useRunStream', () => {
  it('re-attaches on the next poll when the stream closed hard', async () => {
    mountFrame()
    await tick('run:x')
    expect(stream.opened).toEqual(['run:x'])

    stream.onError!('stream closed')

    // The id has not changed, so a watch on `active_run` alone would never
    // fire. The poll itself is the reconnect signal.
    await tick('run:x')
    expect(stream.opened).toEqual(['run:x', 'run:x'])
  })

  it('leaves a healthy stream alone on every poll', async () => {
    mountFrame()
    await tick('run:x')
    await tick('run:x')
    await tick('run:x')

    expect(stream.opened).toEqual(['run:x'])
  })

  it('does not re-attach to a run that has finished', async () => {
    mountFrame()
    await tick('run:x')
    const runs = useRunsStore()
    runs.finished = {
      status: 'succeeded',
      outcome: 'delivered',
      outcomeText: 'brief written',
      briefId: null,
    }
    stream.onError!('stream closed')

    await tick('run:x')
    expect(stream.opened).toEqual(['run:x'])
  })

  it('asks for nothing a reader is not allowed to have', async () => {
    // ⭐ The runs list and the run stream are admin-only server-side, and this
    // composable runs in the APP FRAME on every route -- so for a reader it
    // was a 403 on mount and another on every health tick, for as long as they
    // had the app open.
    mountFrame('reader')
    await tick('run:x')

    expect(stream.opened).toEqual([])
    expect(fetch).not.toHaveBeenCalled()
  })

  it('picks the live run up when the probe lands after mount', async () => {
    // The session probe usually resolves after mount, so the gate refuses the
    // first attempt and something has to notice when the answer comes back.
    // Mounted UNPROBED -- `loaded` false -- because that is the real sequence.
    mountFrame('admin', { loaded: false })
    await tick('run:x')
    expect(stream.opened).toEqual([])

    useSessionStore().loaded = true
    await nextTick()

    expect(stream.opened).toEqual(['run:x'])
  })

  it('watches for an admin who has no users row', async () => {
    // A null role is an admin without a row: the root bearer, the env
    // break-glass admin, and every local `make dev` with auth off. Gating on
    // `role === "admin"` would take the live-run panel away from all three.
    mountFrame(null)
    await tick('run:x')
    expect(stream.opened).toEqual(['run:x'])
  })
})
