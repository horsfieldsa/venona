import { onMounted, watch } from 'vue'

import { useHealthStore } from '@/stores/health'
import { useRunsStore } from '@/stores/runs'
import { useSessionStore } from '@/stores/session'

/**
 * Keeps the live run streaming for as long as the app is open, not for as long
 * as the Runs view happens to be mounted.
 *
 * Attaching on `RunsView`'s mount and detaching on its unmount would mean:
 * start a run, navigate to the brief to read while it works, and the stream
 * dies. Come back to a full replay from seq 0 — or, if the run finished while
 * you were away, to nothing at all, because the "new brief ready" affordance
 * only appears from a terminal event this tab was listening for.
 *
 * Attachment follows `/health`'s `active_run`, which the app frame polls
 * regardless of route. If the user is inspecting a historical run, this leaves
 * them alone until that run's stream closes.
 */
export function useRunStream() {
  const health = useHealthStore()
  const runs = useRunsStore()
  const session = useSessionStore()

  /**
   * The runs list and the run stream are admin-only server-side (`authz.py`),
   * and this attaches from the app frame on every route -- so for a reader it
   * was a 403 on mount and another on every health tick, for the whole time
   * they had the app open.
   *
   * WAITS FOR THE PROBE, which is what `session.isKnownAdmin()` means: a
   * background request nobody is waiting on can be deferred a round trip for
   * free, while asking for it early costs a refusal. Nav is the opposite trade
   * and uses `isAdmin()`, which answers true before the probe lands.
   *
   * That helper carries the rest of the argument, including why it is not
   * `role === 'admin'`: a null role means an admin with no `users` row -- the
   * root bearer, the env break-glass admin, and every local `make dev` with
   * auth off -- and testing the string directly takes the live-run panel away
   * from all three.
   */
  function mayWatch(): boolean {
    return session.isKnownAdmin()
  }

  function sync(live: string | null) {
    if (!live || !mayWatch()) return
    // Do not yank the stream away from someone reading an older run; the next
    // health tick will pick the live one up once they detach.
    if (runs.attachedRunId && runs.attachedRunId !== live && runs.finished === null) return
    // The KIND too, which is why `/health` reports it (contract C11). Without
    // it the live panel falls back to `on_demand` and labels a nightly run
    // "Research" and a brief rewrite as the whole pipeline — with a
    // six-segment spine for a run that has one stage.
    const kind = health.data?.runs?.active?.find((r) => r.run_id === live)?.kind
    // Two reasons to attach: a different run is live, or the one we are already
    // watching lost its stream. EventSource gives up permanently on a hard
    // close, so without the second the panel sits on a run that stopped
    // updating and never recovers short of a reload.
    const lostStream = runs.attachedRunId === live && !runs.streaming && runs.finished === null
    if (runs.attachedRunId !== live || lostStream) runs.attach(live, 0, kind)
  }

  onMounted(() => {
    if (mayWatch()) void runs.refresh()
    sync(health.data?.active_run ?? null)
  })

  // The session probe usually resolves AFTER mount, so an admin's first
  // attempt is refused by the gate above and needs picking up when the role
  // arrives. Without this an operator's first page load would show no live run
  // until the next health tick.
  watch(
    () => session.loaded,
    (ready) => {
      if (!ready || !mayWatch()) return
      void runs.refresh()
      sync(health.data?.active_run ?? null)
    },
  )

  // Every poll, not only when `active_run` changes: a stream that dies under a
  // run that is still live never changes the id, so a value watch never fires.
  // The health cadence IS the reconnect backoff -- 30s idle, 3s during a light
  // run -- which is why there is no timer of our own here.
  watch(
    () => health.data,
    () => sync(health.data?.active_run ?? null),
  )
}
