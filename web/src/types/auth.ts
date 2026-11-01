/**
 * The authentication boundary's read side, on `src/api/schema.d.ts` — generated
 * by `make types` and held to the tree by `make lint-generated`.
 *
 * Both of these were hand-declared TWICE, identically: `AccountView` and
 * `LoginView` each carried their own `Mode`, and `AccountView` a `Session`. Two
 * copies of one wire shape is the state before drift, not a state that avoids
 * it -- and the `Session` copy was already missing `expires_at`, which the
 * server has been sending since sessions had an expiry.
 *
 * Contract C12 is the boundary these describe. Note what is deliberately NOT
 * here: there is no token type, because the client never holds one. See
 * `api/client.ts:107-110` on why `credentials` is left unset.
 *
 * See contract C9 on generated artifacts.
 */

import type { components } from '@/api/schema'

type Schemas = components['schemas']

/**
 * `GET /auth/session`.
 *
 * `email` and `via` are optional on the model -- an unauthenticated probe answers
 * `{authenticated: false}` and nothing else, which is exactly what `main.ts`'s
 * pre-mount probe expects.
 */
export type SessionInfo = Schemas['SessionInfo']

/**
 * `GET /auth/mode` — which sign-in routes this deployment actually offers.
 *
 * One of the six `EXEMPT_PATHS` in `api/middleware.py`, because a login page
 * that cannot ask how to log in is a locked door.
 */
export type AuthMode = Schemas['AuthMode']
