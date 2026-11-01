import { defineStore } from 'pinia'
import { ref } from 'vue'

import { apiGet } from '@/api/client'
import type { components } from '@/api/schema'
import * as dates from '@/lib/dates'

type SessionInfo = components['schemas']['SessionInfo']

/**
 * Who is signed in, and what the shell should offer them.
 *
 * There was no session store at all before roles existed: `AccountView` fetched
 * `/auth/session` ad hoc and nothing else in the app held identity, because
 * there was nothing to hold — one principal, every surface.
 *
 * **THIS IS UX, NOT ENFORCEMENT.** `api/authz.py` refuses the request
 * server-side, before routing, and defaults an unclassified route to `admin`.
 * Everything here does is stop a reader being shown a link to a 403. Anything
 * that treats `isAdmin` as the gate has put the check on the wrong side of the
 * wire — and `tests/test_authz.py` is what proves the real one holds.
 *
 * Populated by the single pre-mount probe in `main.ts`, which already existed
 * for a different reason (a cold load of `/graph` flashing an empty canvas before
 * bouncing to `/login`). One round trip, reused rather than added.
 */
export const useSessionStore = defineStore('session', () => {
  const email = ref<string | null>(null)
  const role = ref<string | null>(null)
  const userId = ref<string | null>(null)
  const via = ref<string | null>(null)
  /** The avatar, when a login carried the OIDC `picture` claim. */
  const picture = ref<string | null>(null)
  /** Where sign-out lands: the hub's end-session URL on a hub-flipped
   *  instance, so ending the session here ends it there too. */
  const logoutUrl = ref<string | null>(null)
  /** The whole payload, for surfaces that render identity in full
   *  (`AccountView`) rather than re-fetching the session they are part of. */
  const info = ref<SessionInfo | null>(null)
  /** The first-boot gate's signal. On the session payload rather than read
   *  from `/health`, so the router's guard never has a reason to wait on a
   *  Neo4j or claude-CLI probe before rendering the first view. */
  const setupRequired = ref(false)
  /** Whether this reader has tuned their personal relevance. Drives the
   *  one-time welcome invitation; rides the same pre-mount probe. */
  const relevanceTailored = ref(false)
  /** Whether the probe has answered. Distinct from "is an admin". */
  const loaded = ref(false)

  /**
   * ADMIN UNTIL THE PROBE SAYS OTHERWISE, which is the opposite of the
   * server-side default and is right for the same reason the server's is right.
   *
   * Server: unclassified route → admin required → a reader is refused. Fails
   * closed.
   *
   * Client: unknown role → show everything → an admin sees their app on a cold
   * load, and a reader who somehow gets here sees a link that 403s. Failing
   * "closed" here would blank the operator's own nav for the length of a round
   * trip on every reload, to protect against a disclosure that is a link.
   */
  function isAdmin(): boolean {
    return !loaded.value || role.value === null || role.value === 'admin'
  }

  /**
   * `isAdmin()`, but only once we actually know. FOR FETCHING, never for nav.
   *
   * The two questions look alike and want opposite defaults, which is the whole
   * reason this exists as a second function rather than a second argument.
   *
   * RENDERING wants the optimistic answer: an admin's own nav must not blank
   * for a round trip on every reload, and the cost of guessing wrong is a link
   * that 403s.
   *
   * FETCHING wants the patient one. An admin-only request fired before the
   * probe lands is a refusal the reader never asked for, and `main.ts` had to
   * key the global toast because THREE of them arrived in the same tick at
   * mount. Deferring a background request one round trip costs nothing; asking
   * for it early costs a 403 and a toast on a page somebody just opened.
   *
   * Still `isAdmin()` and NOT `role === 'admin'`: a null role means an admin
   * with no `users` row -- the root bearer, the env break-glass admin, and
   * every local `make dev` with auth off. Testing the string directly takes the
   * admin surfaces away from all three. `useRunStream::mayWatch` reached this
   * conclusion first and its docblock carries the long version.
   */
  function isKnownAdmin(): boolean {
    return loaded.value && isAdmin()
  }

  function apply(payload: SessionInfo | null): void {
    info.value = payload
    email.value = payload?.email ?? null
    role.value = payload?.role ?? null
    userId.value = payload?.user_id ?? null
    via.value = payload?.via ?? null
    picture.value = payload?.picture_url ?? null
    logoutUrl.value = payload?.logout_url ?? null
    setupRequired.value = payload?.setup_required ?? false
    relevanceTailored.value = payload?.relevance_tailored ?? false
    // The one thing this store writes OUTSIDE itself, and the reason the zone
    // rides the session at all: `lib/dates.ts` is called from templates all
    // over the app, so it reads a module ref rather than taking a zone
    // argument at several hundred call sites. Null restores the browser's own
    // zone, which is what a signed-out or unset reader should see.
    dates.zone.value = payload?.timezone ?? null
    loaded.value = true
  }

  /**
   * Never throws. A 401 is already handled by the hook in `main.ts`, which
   * redirects; anything else is the server being unreachable, and the app has
   * its own error states for that. Rethrowing here would break the pre-mount
   * probe's contract of always reaching `createApp`.
   */
  async function load(): Promise<void> {
    try {
      apply(await apiGet<SessionInfo>('/auth/session'))
    } catch {
      loaded.value = true
    }
  }

  function clear(): void {
    apply(null)
    loaded.value = false
  }

  return {
    email, role, userId, via, picture, logoutUrl, info, setupRequired,
    relevanceTailored, loaded, isAdmin, isKnownAdmin, apply, load, clear,
  }
})
