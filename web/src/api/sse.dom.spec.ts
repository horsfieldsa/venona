// @vitest-environment happy-dom
/**
 * `error` means two different things on this stream.
 *
 * It is a run event the server sends (a stage reporting a failure), and it is
 * EventSource's own name for a connection failure. Assigning `onerror`
 * registers the 'error'-type listener, so a run that merely LOGS an error also
 * lands in the connection-failure path -- and arms a watchdog against a stream
 * that is working. Two minutes later, if the connection is briefly reconnecting
 * for unrelated reasons, that watchdog kills it and reports "the run stream
 * stopped responding" to a reader whose run is fine.
 *
 * happy-dom for `EventSource.OPEN`/`CLOSED` and a stubbable global; the source
 * itself is a fake, because the real one needs a server.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { streamRun } from './sse'

type Listener = (event: Event) => void

class FakeSource {
  static last: FakeSource | null = null
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2

  listeners = new Map<string, Listener[]>()
  onerror: Listener | null = null
  readyState = FakeSource.OPEN
  closed = false

  constructor(public url: string) {
    FakeSource.last = this
  }

  addEventListener(type: string, fn: Listener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn])
  }

  close() {
    this.closed = true
    this.readyState = FakeSource.CLOSED
  }

  /** What the browser does for a run event the server named `error`. */
  sendErrorEvent(payload: object) {
    const event = { data: JSON.stringify(payload) } as unknown as Event
    for (const fn of this.listeners.get('error') ?? []) fn(event)
    this.onerror?.(event)
  }

  /** What the browser does for a genuine connection failure. */
  dropConnection(state: number) {
    this.readyState = state
    this.onerror?.(new Event('error'))
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  FakeSource.last = null
  vi.stubGlobal('EventSource', FakeSource)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function attach() {
  const onError = vi.fn()
  const onEvent = vi.fn()
  streamRun('run:x', { onEvent, onDone: vi.fn(), onError })
  return { onError, onEvent, source: FakeSource.last! }
}

describe('a run event named error', () => {
  it('reaches the event handler', () => {
    const { onEvent, source } = attach()
    source.sendErrorEvent({ seq: 1, type: 'error', data: {} })

    expect(onEvent).toHaveBeenCalledTimes(1)
  })

  it('does not arm the watchdog against a healthy stream', () => {
    const { onError, source } = attach()
    source.sendErrorEvent({ seq: 1, type: 'error', data: {} })

    // The connection drops and is reconnecting -- normal, and the browser
    // handles it. A watchdog armed by the run's own error line would fire here
    // and tear down a stream that was coming back.
    source.readyState = FakeSource.CONNECTING
    vi.advanceTimersByTime(120_000)

    expect(onError).not.toHaveBeenCalled()
    expect(source.closed).toBe(false)
  })
})

describe('a genuine connection failure', () => {
  it('is surfaced and closed when the browser gives up', () => {
    const { onError, source } = attach()
    source.dropConnection(FakeSource.CLOSED)

    expect(onError).toHaveBeenCalledWith('stream closed')
    expect(source.closed).toBe(true)
  })

  it('is surfaced when a reconnect never arrives', () => {
    const { onError, source } = attach()
    source.dropConnection(FakeSource.CONNECTING)
    expect(onError).not.toHaveBeenCalled()

    vi.advanceTimersByTime(120_000)

    expect(onError).toHaveBeenCalledWith('the run stream stopped responding')
    expect(source.closed).toBe(true)
  })
})
