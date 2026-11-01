import { BASE } from '@/api/client'
import type { RunEvent } from '@/types/run'

/**
 * EventSource wrapper.
 *
 * The one non-obvious rule: **close explicitly on the terminal `done` event.**
 * EventSource treats a server-closed stream as a dropped connection and
 * reconnects forever, so a finished run would otherwise re-open the stream on a
 * loop until the tab is closed.
 */
export interface RunStreamHandlers {
  onEvent: (event: RunEvent) => void
  onDone: (event: RunEvent) => void
  onError?: (message: string) => void
}

export function streamRun(
  runId: string,
  handlers: RunStreamHandlers,
  afterSeq = 0,
): () => void {
  const url = `${BASE}/runs/${encodeURIComponent(runId)}/stream?after_seq=${afterSeq}`
  const source = new EventSource(url)
  let closed = false

  // Declared up here because `close` clears it. See `armWatchdog` below for
  // what it is for: a stream stuck in CONNECTING forever, which `onerror`
  // reports and nothing else acts on.
  const WATCHDOG_MS = 120_000
  let watchdog: ReturnType<typeof setTimeout> | undefined

  function close() {
    if (!closed) {
      closed = true
      clearTimeout(watchdog)
      source.close()
    }
  }

  const parse = (raw: MessageEvent): RunEvent | null => {
    try {
      return JSON.parse(raw.data) as RunEvent
    } catch {
      return null
    }
  }

  // Named events, so a listener per type rather than one switch.
  for (const type of [
    'stage_started',
    'stage_progress',
    'stage_finished',
    'counter',
    'log',
    'warning',
    'error',
    'model_message',
  ]) {
    source.addEventListener(type, (raw) => {
      const event = parse(raw)
      if (event) handlers.onEvent(event)
    })
  }

  source.addEventListener('done', (raw) => {
    const event = parse(raw)
    if (event) {
      handlers.onEvent(event)
      handlers.onDone(event)
    }
    close()
  })

  source.onerror = (raw) => {
    // `error` is a run event name on the wire AND EventSource's own name for a
    // connection failure, and assigning `onerror` registers the 'error'-type
    // listener -- so a run reporting an error arrives here too. Only a
    // connection failure comes without a payload. Without this, a run that logs
    // an error arms the watchdog against a stream that is working perfectly.
    //
    // The wire name stays: every stored `run_events` row already carries it, so
    // renaming would need both branches on the client forever anyway.
    if ('data' in raw) return

    // A transient drop is normal — the browser reconnects with Last-Event-ID
    // and the server replays from the log. Only surface a hard failure.
    if (source.readyState === EventSource.CLOSED) {
      handlers.onError?.('stream closed')
      close()
      return
    }
    // CONNECTING means it is retrying. That is usually right, and occasionally
    // forever: a server that accepts the TCP connection and never responds
    // leaves the stream here permanently, with `onerror` firing and this branch
    // doing nothing. The Runs page just quietly stops updating, and nothing
    // ever says why. The watchdog below is the only thing that notices.
    armWatchdog()
  }

  /**
   * Give up if a stream spends too long CONNECTING without arriving.
   *
   * Armed only from `onerror`, and the guard inside it is `readyState !== OPEN`
   * -- so a stream that reconnects successfully disarms it by definition rather
   * than by anything clearing the timer.
   *
   * It is NOT "cleared on every event": no event handler touches `watchdog`.
   *
   * WHAT THIS STILL DOES NOT COVER, stated so it is a known gap rather than an
   * assumed one: a stream that is OPEN and silent. `run_service.py` emits a
   * heartbeat every 15s, so a genuinely wedged server holding the socket open
   * would be detectable -- but nothing here is watching the gap between events,
   * only the gap before connecting. Worth building the day a stall is observed;
   * not worth guessing at now.
   */
  function armWatchdog() {
    clearTimeout(watchdog)
    watchdog = setTimeout(() => {
      if (source.readyState !== EventSource.OPEN) {
        handlers.onError?.('the run stream stopped responding')
        close()
      }
    }, WATCHDOG_MS)
  }

  return close
}
