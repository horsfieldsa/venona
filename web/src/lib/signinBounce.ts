/**
 * Whether the login page may start a sign-in on its own.
 *
 * With an OIDC login configured and no token form to offer, `LoginView` holds
 * exactly one button and presses it for the visitor. That press is a request to
 * `/api/auth/login`, and that request is rate limited — so anything which mounts
 * the page again spends the budget with nobody choosing to: a reload, a second
 * tab, the back button off a round trip that did not complete. The budget then
 * runs out while the visitor is still trying to get in, which is the shape of
 * the lockout in ADR 0164.
 *
 * A COOLDOWN, NOT A ONCE-PER-TAB FLAG. The two cases have to be told apart and
 * both are legitimate: a tab whose session expires hours later should still
 * bounce on its own, while a tab that was here seconds ago is either looping or
 * being reloaded by somebody who has already been refused. It is also what makes
 * signing out stick rather than signing the visitor straight back in.
 *
 * The store is passed in rather than reached for. A browser with site data
 * blocked throws on the `sessionStorage` accessor ITSELF, so the caller owns
 * that `try` and hands back `null`; no store is no memory of a bounce, which
 * reads as a first arrival — one redirect is right, and it takes a second to
 * make a loop.
 */
export const BOUNCE_KEY = 'venona.signin.bounced-at'
export const BOUNCE_COOLDOWN_MS = 60_000

export function bouncedRecently(store: Storage | null, now: number): boolean {
  if (!store) return false
  try {
    const at = Number(store.getItem(BOUNCE_KEY))
    // A clock that moved backwards reads as "not recent" and costs one extra
    // bounce, which is the safe direction: the other one never bounces again.
    return Number.isFinite(at) && at > 0 && now >= at && now - at < BOUNCE_COOLDOWN_MS
  } catch {
    return false
  }
}

export function rememberBounce(store: Storage | null, now: number): void {
  if (!store) return
  try {
    store.setItem(BOUNCE_KEY, String(now))
  } catch {
    /* A page that cannot remember still gets its first bounce. */
  }
}
