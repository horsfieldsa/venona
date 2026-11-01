import { reactive, readonly } from 'vue'

/**
 * What the ContextBar knows about the current route.
 *
 * The bar's CONTENT arrives by `<Teleport>` -- a store cannot hold VNodes -- but
 * the bar also has to decide whether to occupy any height at all, and a teleport
 * target gives it no way to ask. So `ViewShell` declares the shape of what it is
 * about to teleport, and this is where that declaration lives.
 *
 * OWNERSHIP IS BY TOKEN, and that is the whole subtlety here. Routing mounts the
 * incoming view before the outgoing one is torn down, so a plain
 * set-on-mount/clear-on-unmount pair lets the outgoing view's cleanup run last
 * and wipe the new view's registration -- the bar then renders empty on every
 * navigation, and only on navigation, which is exactly the kind of bug that gets
 * blamed on the router. A release only clears if the caller is still the owner.
 *
 * `<KeepAlive>` covers BriefView and GraphView, so those two claim on activate
 * and release on deactivate rather than on mount and unmount. Teleported content
 * is already removed for a deactivated view; this keeps the identity line in
 * step with it.
 */
/** Where this route hangs off, rendered as a link before the identity. */
export interface ContextBarParent {
  /** The destination's NAME, not a sentence: `runs`, `run 4f2a`. The zone is
   *  uppercased with label tracking, so `back to that brief` would shout. */
  label: string
  /** A path. Not a `RouteLocationRaw`: this state is handed out through
   *  `readonly()`, which makes a location object deeply readonly and therefore
   *  unassignable to `RouterLink`'s `to`. Every parent so far is a plain path. */
  to: string
}

export interface ContextBarState {
  /** A fact about what you are looking at. Never a control. */
  identity: string
  /**
   * The parent route, or `null`.
   *
   * `ViewShell` MUST send this on every claim, `null` included. `claimContextBar`
   * merges with `Object.assign`, so a key omitted on the incoming route keeps
   * whatever the outgoing one left -- and the release that would have cleared it
   * cannot help, because the incoming view claims BEFORE the outgoing one is torn
   * down. `/admin/runs` would render with `RUNS ›` still in front of it.
   */
  parent: ContextBarParent | null
  /** Whether the view teleports anything into the controls zone. */
  controls: boolean
  /** Whether the view teleports a filter into the trailing search zone. */
  search: boolean
  /** Whether the view teleports anything into the trailing switches zone. */
  toggles: boolean
  /** Whether the view teleports anything into the actions zone. */
  actions: boolean
}

const EMPTY: ContextBarState = {
  identity: '',
  parent: null,
  controls: false,
  search: false,
  toggles: false,
  actions: false,
}

const state = reactive<ContextBarState>({ ...EMPTY })
let owner: symbol | null = null

/** Called by the mounted `ViewShell`. Last claim wins. */
export function claimContextBar(token: symbol, next: ContextBarState): void {
  owner = token
  Object.assign(state, next)
}

/** A no-op unless `token` still holds the bar -- see the note on ordering. */
export function releaseContextBar(token: symbol): void {
  if (owner !== token) return
  owner = null
  Object.assign(state, EMPTY)
}

export function useContextBar(): Readonly<ContextBarState> {
  return readonly(state)
}
