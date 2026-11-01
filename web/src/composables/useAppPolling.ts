import { onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'

import { useFreshnessDispatch } from '@/composables/useFreshness'
import { usePoll } from '@/composables/usePoll'
import { useCurationStore } from '@/stores/curation'
import { useHealthStore } from '@/stores/health'
import { useNotificationsStore } from '@/stores/notifications'
import { useRunsStore } from '@/stores/runs'
import { useSessionStore } from '@/stores/session'
import { useRunTypesStore } from '@/stores/runTypes'

/**
 * The one poll loop, owned by the app frame.
 *
 * Neither belongs in a view's `onMounted`, which makes them lie whenever that
 * view is not the one on screen:
 *
 *   - a health poll owned by any one component stops whenever that component
 *     is hidden -- the graph's full screen, for instance -- so Neo4j could
 *     drop and the only symptom was an error on the next query. Polling at
 *     app level is what lets every consumer of the health store stay a pure
 *     read with no timer of its own.
 *   - `curation.refreshStatus()` ran only in `CurationView`, so the header's
 *     open-conflict badge would read zero everywhere else.
 *
 * A badge or a status line that is only correct on the page it describes is
 * worse than not having one.
 */
const INTERVAL_MS = 30_000

/**
 * While a light run is in flight -- or a cancel is waiting to land -- poll
 * faster.
 *
 * A research run takes tens of minutes, so a 30-second health tick describes it
 * perfectly well. A lookup takes 25 to 60 seconds -- at 30 seconds it can start
 * and finish between two ticks, so the Runs page would show a row that was
 * never seen running and a spinner that never appeared. Contract C11 made these
 * common enough to matter: several can now be in flight at once.
 *
 * 3 seconds because that is fast enough to look live against a ~30s job. Keep
 * what it costs in view: the tick also fetches `/curation/status` for admins,
 * which counts open pairs in Postgres and asks Neo4j what a rebuild would still
 * apply. Both are bounded reads, and they have to stay that way for this
 * cadence to be affordable.
 */
const LIVE_INTERVAL_MS = 3_000

export function useAppPolling() {
  const health = useHealthStore()
  const curation = useCurationStore()
  const notifications = useNotificationsStore()
  const runs = useRunsStore()
  const session = useSessionStore()
  const runTypes = useRunTypesStore()
  const route = useRoute()

  // ONE dispatcher for the whole app, installed here because this is where the
  // poll it reads from is owned. Every store that cares subscribes with
  // `onFreshness(domain, …)`; see `useFreshness.ts` for why it stays a hint.
  useFreshnessDispatch()

  let fast = false

  /**
   * PUBLIC ROUTES. On `/login` neither of these calls can succeed, so the loop
   * would fire two 401s every 30 seconds forever — into the same rate limiter
   * that protects the login endpoint. The operator would be locked out of their
   * own app by the page they were sitting on waiting to sign in.
   *
   * The general re-entrancy guard and the visibility pause are `usePoll`'s.
   * This one stays because it is the only guard that is about THIS loop rather
   * than about polling in general.
   */
  /**
   * `/health` for everyone; `/curation/status` for admins ONLY.
   *
   * Curation is admin-owned, so a reader's poll of it is a 403 -- and this loop
   * runs every three seconds while a light run is live. Without the guard a
   * reader collects a "you cannot change this" toast several times a minute, for
   * a request they never made, about a page they cannot see. The count it feeds
   * is the Curation row's badge on `AdminRail`, which a reader never reaches.
   *
   * `Promise.allSettled` already tolerated a failing member, which is exactly why
   * this would have been easy to miss: nothing breaks, it just shouts.
   */
  const poll = usePoll(
    () =>
      Promise.allSettled([
        health.refresh(),
        // Both admin-only, and gated for the same reason: `/api/curation/status`
        // and `/api/notifications/status` are admin INCLUDING the read, so an
        // ungated call hands every reader a refusal several times a minute for
        // a request they never made. The notification one is what puts the mark
        // on the Admin rail when Venona has stopped being able to tell anybody
        // anything -- the one failure nothing else in the app would report.
        // `isKnownAdmin`, not `isAdmin`: the optimistic answer is for NAV, and
        // asking before the probe lands hands a reader two 403s and the global
        // toast on a page they just opened. Coming back from Stripe Checkout is
        // exactly that page.
        ...(session.isKnownAdmin()
          ? [curation.refreshStatus(), notifications.refreshStatus()]
          : []),
      ]),
    { interval: INTERVAL_MS, skip: () => Boolean(route.meta.public) },
  )

  onMounted(() => {
    // Once, at mount. The table is compiled into the backend and can only
    // change across a restart, which reloads the page anyway.
    //
    // Admin only: `/api/runs/types` describes the run table, which a reader has
    // no surface for. One request rather than a loop, so the cost of getting this
    // wrong is a single toast rather than a stream -- but it is the same mistake.
    if (session.isKnownAdmin()) void runTypes.load()
    poll.start()
  })

  // Speed up only while light runs exist, and slow back down when they are
  // gone -- a permanently fast loop would poll all night for nothing.
  // A pending cancel counts as live for the same reason a light run does: the
  // run ends within seconds of the press, and at the 30-second tick the row
  // that stopped keeps reading `cancelling` long after it is over.
  watch(
    () => runs.lightRuns.length > 0 || runs.cancellingIds.size > 0,
    (live) => {
      if (live === fast) return
      fast = live
      poll.setInterval(live ? LIVE_INTERVAL_MS : INTERVAL_MS)
      // Both directions. The history table is what shows a light run resolving
      // and it is not part of the health payload -- so without the refresh on
      // the way DOWN, the row that just finished keeps reading `running` until
      // something else happens to reload the list.
      // Admin only, same reason: `/api/runs` is admin, and a reader has no Runs
      // page for this list to feed.
      if (session.isKnownAdmin()) void runs.revalidate()
    },
  )

  // Teardown is `usePoll`'s, registered against this component instance.
}
