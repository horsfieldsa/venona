import { getCurrentInstance, onUnmounted, ref } from 'vue'

/**
 * One poll loop, with the three guards that every hand-rolled copy needed and
 * only one of them had.
 *
 * There were three loops before this: `useAppPolling`'s health tick, and one
 * each in `RunDetailView` and `RunMetricsView`. `useAppPolling` had worked out
 * two of the guards the hard way; the other two had neither, and none of the
 * three paused while nobody was looking.
 *
 * THE GUARDS, and what each one is for:
 *
 *   RE-ENTRANCY. `setInterval` does not wait for the previous tick. `/api/health`
 *   probes Neo4j and shells out to the `claude` CLI, and at the 3s live interval
 *   a slow probe means ticks STACK -- each holding a connection, none finishing.
 *   A single flag turns that back into "skip this one".
 *
 *   VISIBILITY. Nothing in `web/src` looked at `document.hidden`, so a
 *   backgrounded tab kept hitting `/api/health` every three seconds for the
 *   length of a run. Browsers already throttle background `setInterval` to
 *   roughly once a minute, so the win here is smaller than it looks -- say so
 *   rather than overclaiming it, or someone will reasonably optimise it back
 *   out. What it buys is the difference between "once a minute forever" and
 *   "not at all", on an endpoint that spawns a subprocess.
 *
 *   THE TICK ON RETURN. Not optional, and the reason pausing is safe at all. A
 *   tab returned to would otherwise show up to 30s of stale data. `useFreshness`
 *   is built on "a stamp that MOVES makes a store refetch; a stamp that does not
 *   move must never make a store SKIP anything" -- a paused poll causes a delay,
 *   never a skip, but only if resuming fires immediately instead of waiting out
 *   the interval.
 *
 * SSE IS UNAFFECTED, and that is what makes the visibility pause safe on the
 * Runs pages: `EventSource` keeps streaming while hidden, so a live run still
 * fills in. This only pauses the polls that ask "has anything changed".
 */
export interface PollOptions {
  /** Milliseconds between ticks. May change; call `setInterval` to switch. */
  interval: number
  /** Skip a tick entirely — `useAppPolling` uses this for public routes, where
   *  every request would be a 401 into the limiter protecting the login page. */
  skip?: () => boolean
}

export function usePoll(tick: () => void | Promise<unknown>, options: PollOptions) {
  let timer: ReturnType<typeof setInterval> | undefined
  let interval = options.interval
  let inFlight = false
  const running = ref(false)

  /** Ticked on every run, so an elapsed-time readout is elapsed time and not a
   *  frozen number. Both run views kept their own copy of this. */
  const nowMs = ref(0)

  async function fire() {
    if (options.skip?.() || inFlight || document.hidden) return
    inFlight = true
    nowMs.value = Date.now()
    try {
      await tick()
    } finally {
      inFlight = false
    }
  }

  function schedule() {
    clearInterval(timer)
    timer = setInterval(() => void fire(), interval)
  }

  function start() {
    if (running.value) return
    running.value = true
    void fire()
    schedule()
  }

  function stop() {
    running.value = false
    clearInterval(timer)
    timer = undefined
  }

  /** Change cadence without losing the loop. `useAppPolling` speeds up to 3s
   *  while a light run is in flight and slows back down after — a permanently
   *  fast loop would poll all night for nothing. */
  function setInterval_(next: number) {
    if (next === interval) return
    interval = next
    if (running.value) schedule()
  }

  function onVisibility() {
    if (!running.value) return
    if (document.hidden) {
      clearInterval(timer)
      timer = undefined
      return
    }
    // Immediately, THEN reschedule. See the docstring.
    void fire()
    schedule()
  }

  document.addEventListener('visibilitychange', onVisibility)

  /**
   * Guarded, because a Pinia setup store is a setup function with no component
   * lifecycle. Registering unconditionally logs Vue's "called when there is no
   * active component instance" warning on every store that uses this.
   */
  if (getCurrentInstance()) {
    onUnmounted(() => {
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
    })
  }

  return { start, stop, running, nowMs, setInterval: setInterval_, tickNow: fire }
}
