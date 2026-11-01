import { watch } from 'vue'

import { useHealthStore } from '@/stores/health'

/**
 * "Something else changed this — reload yourself."
 *
 * THE PROBLEM. Exactly one thing in this app was ever live: the per-run SSE
 * stream. Every store loaded once and then mutated its own copy optimistically,
 * so nothing could see a change it had not itself made — a nightly run, a CLI
 * command, `lookup_all` finishing, a second tab. The report was "we aren't
 * getting real time updates and I often need to refresh", and the duplicate
 * curation asks were one visible instance of the general case.
 *
 * THE MECHANISM. `/api/health` returns a short stamp per domain (see
 * `services/freshness.py`). A subscriber registers a domain and a callback; when
 * that domain's stamp changes from the value last seen, the callback runs. No
 * new transport, and it catches changes that never went through this browser.
 *
 * IT IS A HINT, and the asymmetry is the whole design:
 *
 *   a stamp that MOVES makes a store refetch.
 *   a stamp that DOES NOT move must never make a store SKIP anything.
 *
 * So every store keeps its load-on-mount and its own TTL. A stamp is allowed to
 * be coarse — the cost is a refetch nobody needed. It is never allowed to be
 * the reason something did not load.
 *
 * WHAT THIS DOES NOT DO. Responsiveness is bounded by the health interval: 30s
 * normally, 3s while a run is live (`useAppPolling`). That ordering is right by
 * accident and worth keeping deliberately — the poll is already fast exactly
 * when the database is being written to. `/health` probes Neo4j and shells out
 * to the `claude` CLI, so it cannot simply be polled harder to make this
 * quicker; a faster freshness signal would need its own endpoint, and 30s for
 * "another tab merged something" has not been worth one.
 */
type Listener = () => void

const listeners = new Map<string, Set<Listener>>()

/**
 * The last stamp each domain was seen at, MODULE-level rather than per-caller.
 *
 * There is one health poll for the whole app, so there is one answer to "has
 * this domain moved since we looked". Holding it per-subscriber would make a
 * store that mounts late believe every domain had just changed, and refetch
 * everything on arrival — which is the thundering-herd version of the bug this
 * fixes.
 */
const seen = new Map<string, string>()

/** Register interest in a domain. Returns the unsubscribe. */
export function onFreshness(domain: string, run: Listener): () => void {
  const set = listeners.get(domain) ?? new Set<Listener>()
  set.add(run)
  listeners.set(domain, set)
  return () => set.delete(run)
}

/**
 * Drive the dispatch from the health store. Called ONCE, by the app frame that
 * owns the poll loop — not by each store, which would install N watchers on one
 * source and fan every change out N times.
 */
export function useFreshnessDispatch() {
  const health = useHealthStore()

  watch(
    () => health.data?.stamps,
    (stamps) => {
      if (!stamps) return
      for (const [domain, stamp] of Object.entries(stamps)) {
        const previous = seen.get(domain)
        seen.set(domain, stamp)
        // FIRST SIGHTING IS NOT A CHANGE. Without this, the first successful
        // poll after load looks like every domain moved at once and every
        // store refetches what it has just finished fetching.
        if (previous === undefined || previous === stamp) continue
        for (const run of listeners.get(domain) ?? []) run()
      }
    },
    { deep: true },
  )
}

/** Test seam: forget what has been seen, so a suite can assert first-sighting
 *  behaviour more than once. Not used by the app. */
export function resetFreshness() {
  seen.clear()
  listeners.clear()
}
