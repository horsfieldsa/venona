import { getCurrentScope, onScopeDispose } from 'vue'

/**
 * An `AbortSignal` tied to the component's lifetime.
 *
 * `apiGet` composes a caller signal with its own timeout (`client.ts`), but
 * almost nothing passed one — so navigating away from a view left its fetches
 * running to completion for a response nobody would read. Pass this signal
 * and the request dies with the component instead.
 *
 * THE CALLER OWNS THE CATCH: an aborted request rejects with
 * `CancelledError`, and a load path that writes that into its `error` ref
 * will flash a failure state during teardown (or, under `<KeepAlive>`, show
 * one on return). Swallow it — cancellation is the caller giving up, not the
 * server failing.
 */
export function useRequestScope(): { signal: AbortSignal } {
  const controller = new AbortController()
  if (getCurrentScope()) onScopeDispose(() => controller.abort())
  return { signal: controller.signal }
}
