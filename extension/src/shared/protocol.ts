/** Messages across the content-script <-> service-worker port.
 *
 * Why a PORT and not `chrome.runtime.sendMessage`: the MV3 service worker is
 * terminated after ~30s idle, and `setTimeout`/`setInterval` do not keep it
 * alive. A connected port does. Phase 4 polls a run for up to 180 seconds; over
 * `sendMessage` that poll dies partway through and the card hangs on a spinner
 * with no error to show.
 *
 * The worker is the only thing that ever holds the bearer token or calls
 * `fetch`. A content script runs in the same process as a hostile page's DOM;
 * it gets answers, never credentials.
 */

import type { AnnotateResponse, Card } from './types'

/** Content script -> worker. `id` correlates the reply. */
export type Request =
  | {
      id: number
      kind: 'annotate'
      url: string
      // `hrefs` are `[start, end)` output ranges covered by a link. The text
      // stays whole -- deleting it instead makes non-adjacent words adjacent
      // and the index matches across a gap the page does not have.
      blocks: { id: string; text: string; hrefs: [number, number][] }[]
    }
  | { id: number; kind: 'cards'; ids: string[] }
  | { id: number; kind: 'health' }
  // The only two requests that spend money, and both are behind a click.
  | { id: number; kind: 'lookup'; entityId: string; surface: string; isEntity: boolean }
  | { id: number; kind: 'ingest'; url: string }
  | { id: number; kind: 'run'; runId: string }
  | { id: number; kind: 'appUrl' }
  // What the credential can do, so the card offers only buttons that work.
  | { id: number; kind: 'capabilities' }

/** Worker -> content script. Exactly one per request `id`. */
export type Response =
  | { id: number; ok: true; kind: 'annotate'; data: AnnotateResponse }
  | { id: number; ok: true; kind: 'cards'; data: { cards: Record<string, Card>; missing: string[] } }
  | { id: number; ok: true; kind: 'health'; data: { status: string } }
  | { id: number; ok: true; kind: 'lookup'; data: RunStarted }
  | { id: number; ok: true; kind: 'ingest'; data: RunStarted }
  | { id: number; ok: true; kind: 'run'; data: { status: string; error?: string | null } }
  | { id: number; ok: true; kind: 'appUrl'; data: { url: string } }
  // `full: false` hides the spending buttons; never-probed reads as full and
  // the first 403 corrects it. See `client.ts::Capability`.
  | { id: number; ok: true; kind: 'capabilities'; data: { full: boolean } }
  | { id: number; ok: false; error: string; status?: number }

/** Every 202 start endpoint returns this envelope (C11). `queued` IS in flight. */
export interface RunStarted {
  run_id: string
  status: string
  lane?: string
  coalesced?: boolean
}

/** `Omit<Request, 'id'>` would collapse the union into its common keys, losing
 * the discriminant's link to its payload -- so `{kind: 'cards', url: ...}` would
 * typecheck. Distributing over the union preserves each member. */
export type Unidentified<T> = T extends unknown ? Omit<T, 'id'> : never

export const PORT_NAME = 'venona'
