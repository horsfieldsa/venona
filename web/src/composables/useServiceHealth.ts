import { computed } from 'vue'

import { useHealthStore } from '@/stores/health'

/**
 * Which of the three services is actually down, as one shared derivation.
 *
 * Read off the health store rather than `navigator.onLine`, which answers a
 * different question: it reports whether the MACHINE has a network, and the
 * failure that actually happens here is this API being down while the network
 * is fine. `useAppPolling` refreshes health every 30s (3s during a light run),
 * so these settle on their own without any view polling anything itself.
 *
 * `modelDown` ORs in `circuit.open` because the probe's `ok` alone lies: under
 * `CLAUDE_CODE_OAUTH_TOKEN`, `claude auth status` reports a logged-in session
 * even for an expired token, so `ok` means "a token is present" and the
 * breaker is what knows whether calls are actually landing.
 *
 * `data.offline` exempts the model entirely: that is the backend's
 * `VENONA_OFFLINE` replay mode, a HEALTHY server whose LLM backend reports
 * `ok: false, detail: "replay backend (offline)"` -- a description, not an
 * outage. It is also why every read below optional-chains: the health route
 * serializes with `exclude_unset`, so in replay mode `circuit` and `error`
 * are absent rather than null.
 */
export function useServiceHealth() {
  const health = useHealthStore()

  const apiDown = computed(() => health.error !== null)

  const neo4jDown = computed(() => health.data?.neo4j.ok === false)

  // `model` is preferred and `claude` is the fallback for one release, and
  // BOTH are optional-chained. The block itself was read unchained, so a
  // response that stopped carrying it threw inside this computed during render
  // -- in every tab left open across a deploy, on its next thirty-second poll,
  // until somebody reloaded.
  const block = computed(() => health.data?.model ?? health.data?.claude)

  const modelDown = computed(() => {
    const data = health.data
    if (!data || data.offline) return false
    const model = block.value
    return model?.ok === false || model?.circuit?.open === true
  })

  // NOT AN OUTAGE. `not_connected` is a state an administrator put the
  // instance in, and every surface that renders it as "Venona keeps checking"
  // is promising a recovery that will not arrive until a person acts.
  const modelUnconfigured = computed(
    () => modelDown.value && block.value?.reason === 'not_connected',
  )

  // The circuit is open on a PROVIDER error (5xx, 529, a timeout), which the
  // research lane waits out rather than failing: the run is paused, not
  // lost, and the notice should say so instead of sending someone to re-auth
  // a CLI that is fine. A dead token reads as `transient: false`.
  const modelWaiting = computed(
    () => modelDown.value && block.value?.circuit?.transient === true,
  )

  // First line only: the neo4j driver's ServiceUnavailable is a multi-line
  // dump of every address it tried, and a notice is not a stack trace.
  const neo4jDetail = computed(
    () => health.data?.neo4j.error?.split('\n')[0] ?? 'unavailable',
  )

  const modelDetail = computed(() => {
    const model = block.value
    return model?.circuit?.last_error ?? model?.error ?? model?.detail ?? 'unavailable'
  })

  // How the service is spelled for a reader, when one has to be named at all.
  // Reader-facing notices deliberately do NOT use this: someone who did not
  // choose the service should be told the model is not answering, not which
  // company is having a bad morning.
  const modelLabel = computed(() => block.value?.service_label ?? 'the model')

  return {
    apiDown,
    neo4jDown,
    modelDown,
    modelWaiting,
    modelUnconfigured,
    modelLabel,
    neo4jDetail,
    modelDetail,
  }
}
