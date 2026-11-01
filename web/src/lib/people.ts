/**
 * The words and the colours for people, roles and access requests.
 *
 * Here rather than in the two views for the reason `lib/curation.ts` exists: a
 * status means the same thing on `/admin` as it does on `/admin/people`, and
 * two copies of a tone map is how one screen ends up calling a suspended account
 * amber and the other red.
 *
 * EVERY MAP IS A LITERAL LOOKUP, never `text-signal-${tone}`. Tailwind scans
 * source text, so an interpolated class name is simply never generated -- and
 * the failure renders as an uncoloured chip, which reads as a data problem
 * rather than as a missing stylesheet (CLAUDE.md).
 *
 * The four literal sets below are duplicated from the backend on purpose and
 * held to it by `tests/test_frontend_drift.py`: `ROLES` is `authz.ROLES`,
 * `REQUEST_STATUSES` is `repositories/access.py`. A set that drifts renders a
 * chip with no label instead of raising.
 */
import type { Column } from '@/components/ui/Table.vue'
import { agoFrom } from '@/lib/duration'
import type { components } from '@/api/schema'

type AccessRequest = components['schemas']['AccessRequest']
type AuthorityEvent = components['schemas']['AuthorityEvent']

type Tone = 'neutral' | 'accent' | 'high' | 'critical' | 'ok' | 'info'

/** `api/authz.py::ROLES`. Two, and the ceiling over HTTP is `reader`. */
export const ROLES = ['admin', 'reader'] as const

/** `users.status`. */
export const USER_STATUSES = ['active', 'suspended'] as const

/** `db/repositories/access.py`. */
export const REQUEST_STATUSES = ['pending', 'approved', 'declined'] as const

export const ROLE_TONE: Record<string, Tone> = {
  admin: 'accent',
  reader: 'neutral',
}

export const USER_STATUS_TONE: Record<string, Tone> = {
  active: 'ok',
  suspended: 'critical',
}

export const REQUEST_STATUS_TONE: Record<string, Tone> = {
  pending: 'high',
  approved: 'ok',
  declined: 'neutral',
}


/**
 * "never signed in" is a fact about an ACCOUNT, not a scolding.
 *
 * Venona sends no mail (Decision 4), so this is the only signal that someone was
 * approved and never told -- which is why it renders as its own column rather
 * than as a blank last-seen cell.
 */
export function seenLabel(neverSignedIn: boolean, ago: string): string {
  return neverSignedIn ? 'never signed in' : ago
}


/**
 * The rows and the columns two admin screens both build.
 *
 * `/admin` previews the queue and the log; `/admin/people` is where they are
 * acted on. Both mapped the same wire objects into the same cells, twice, and
 * the copy inside those cells is a judgement rather than a field name: `cli`
 * for a change no `users` row made, an age that falls back to the raw
 * timestamp. Two copies of a judgement is how the same event ends up worded
 * two ways on two screens the same person reads in one sitting.
 *
 * Each view still owns its own COLUMNS beyond the shared set, and the extra
 * field it carries for its own actions (`user`, `request`); those genuinely
 * differ, because only one of the two screens can act.
 */
export function requestRow(request: AccessRequest, now = Date.now()) {
  return {
    id: request.id,
    email: request.email,
    role: request.role,
    status: request.status,
    coverage: request.coverage ?? '',
    age: agoFrom(request.created_at, now) || request.created_at,
  }
}

export function eventRow(event: AuthorityEvent, now = Date.now()) {
  return {
    id: event.id,
    who: event.user_email ?? event.user_id,
    // None means `venona users` did it: shell access, which is not a row. `cli`
    // is the honest rendering of that, and a blank cell would read as a column
    // that failed to load.
    actor: event.actor_email ?? 'cli',
    action: event.action,
    detail: event.detail ?? '',
    when: agoFrom(event.created_at, now) || event.created_at,
  }
}

/** The four columns of the request queue that both screens show. `/admin/people`
 *  appends its own `status` column, which is a control rather than a cell. */
export const REQUEST_COLUMNS: Column<ReturnType<typeof requestRow>>[] = [
  { key: 'email', label: 'address' },
  { key: 'role', label: 'says they are' },
  { key: 'coverage', label: 'what they follow' },
  { key: 'age', label: 'waiting' },
]

/**
 * The subscription, in the reader's words rather than the wire's.
 *
 * RE-EXPORTED from `web/src/lib/billing.ts` rather than restated. This was a
 * second copy, and the copy had already drifted: it read "payment failed" where
 * the reader's own screen reads "Payment problem" and adds that they still have
 * full access. One vocabulary, so an operator and a reader looking at the same
 * account are told the same thing about it.
 */
export { statusLabel as subscriptionLabel, statusTone as subscriptionTone } from '@/lib/billing'

export const EVENT_COLUMNS: Column<ReturnType<typeof eventRow>>[] = [
  { key: 'who', label: 'account' },
  { key: 'action', label: 'change' },
  { key: 'actor', label: 'by' },
  { key: 'when', label: 'when' },
]
