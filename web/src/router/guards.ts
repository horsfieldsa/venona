/**
 * Where a navigation is sent, and why. One function, deliberately.
 *
 * FOUR REDIRECTS THAT CAN ARGUE WITH EACH OTHER. Setup, the paywall, the
 * tailoring invitation and the operator check were four inline conditions in
 * `beforeEach`, and what none of them showed was the INTERACTION: the paywall
 * sent `/` to billing, the invitation sent billing to `/welcome`, and the
 * paywall sent `/welcome` back to billing. A navigation that never settles is a
 * hung tab, and it hit every new reader, because a new reader has by definition
 * dismissed nothing.
 *
 * Together in one pure function they can be read in order and tested without a
 * DOM, a router or a store. The ORDER IS THE RULE: earlier wins.
 */

export interface GuardState {
  /** The instance has no domain descriptor yet. */
  setupRequired: boolean
  /** Whether the session probe has answered. */
  isAdmin: boolean
  /** Null before the probe, and for a signed-out visitor. */
  hasSession: boolean
  /** Null when this instance takes no money. */
  billing: { enforced: boolean; entitled: boolean; status: string } | null
  relevanceTailored: boolean
  welcomeDismissed: boolean
}

export interface GuardTarget {
  name: string
  isPublic: boolean
  isOperator: boolean
}

/**
 * Screens an unsubscribed reader may still reach. THE CLIENT HALF of
 * `authz.UNENTITLED_ROUTES`, and it has to agree with it: the server keeps the
 * whole settings rail open on purpose, so bouncing those routes here refuses a
 * screen the API would have served, with nothing in the network tab to explain
 * it.
 *
 * Agents matters most. Somebody must always be able to disconnect an assistant
 * they have come to distrust, subscription or not -- there is a test on the
 * server named for exactly that, and this list is what made it unreachable.
 */
const REACHABLE_UNSUBSCRIBED = [
  'settings-billing',
  'account',
  'settings-relevance',
  'settings-notifications',
  'settings-agents',
  'welcome',
  'not-found',
]

/**
 * Routes an admin may reach WHILE the instance is still unconfigured.
 *
 * The wizard is a redirect rather than a lockout, and these are the screens it
 * depends on: `setup` itself, and the one that connects a model service, since
 * the wizard's first act is to start a run.
 */
const SETUP_PREREQUISITES = ['setup', 'admin-model']

/** The route this navigation should be sent to instead, or null to allow it. */
export function redirectFor(to: GuardTarget, state: GuardState): string | null {
  // 1. First boot. An unconfigured instance sends its ADMINS to the wizard --
  //    a redirect, not a lockout. Readers see the ordinary app, whose
  //    empty-corpus screens are the honest state.
  //
  //    `admin-model` is exempt because the wizard CANNOT RUN without it. Step
  //    one of setup starts a run, a run needs a model service, and a fresh
  //    instance has none connected -- so bouncing that page here left the
  //    only route out of first boot behind the redirect that first boot
  //    installs. The wizard links to it, and the link has to arrive.
  if (
    state.setupRequired &&
    !to.isPublic &&
    !SETUP_PREREQUISITES.includes(to.name) &&
    state.isAdmin
  ) {
    return 'setup'
  }

  // 2. The paywall. Somebody who has never subscribed has NO archive at all --
  //    their ceiling excludes every brief -- so the brief, the graph and the
  //    archive are all empty pages. Send them to the offer.
  //
  //    `entitled`, NOT the status word: an administrator who does not pay reads
  //    `none` and is entitled by role, and keying on the label bounced every
  //    admin to the subscribe page. Only `none` -- a LAPSED reader keeps the
  //    archive they paid for and is bounced nowhere (ADR 0211).
  if (
    state.billing?.enforced &&
    !state.billing.entitled &&
    state.billing.status === 'none' &&
    !to.isPublic &&
    !REACHABLE_UNSUBSCRIBED.includes(to.name)
  ) {
    return 'settings-billing'
  }

  // 3. The first-visit tailoring invitation. An INVITATION, not a gate.
  //
  //    `settings-billing` is excluded for the same reason `welcome` is excluded
  //    above: the two would otherwise trade the reader back and forth. And a
  //    wrong address is not an occasion to invite anyone to tune anything --
  //    without `not-found` here, a mistyped URL bounces to `/welcome` and the
  //    reader never learns the address was wrong.
  if (
    !state.relevanceTailored &&
    state.hasSession &&
    !state.setupRequired &&
    !to.isPublic &&
    !['welcome', 'not-found', 'settings-billing'].includes(to.name) &&
    !state.welcomeDismissed
  ) {
    return 'welcome'
  }

  // 4. Operator routes. UX, never enforcement: `api/authz.py` refuses the
  //    request server-side, and this only stops a reader being shown a link
  //    that 403s.
  if (to.isOperator && !state.isAdmin) return 'brief'

  return null
}
