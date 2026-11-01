/**
 * Single fetch seam. Every request goes through here so base URL, JSON
 * handling, and error shape are decided once.
 */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    /**
     * The parsed error body, when there was one.
     *
     * Some failures are structured and recoverable: a 409 from
     * `/glossary/{surface}/lookup` carries `{error, run_id, message}` so the
     * caller can say *which* run is in the way. Collapsing that to
     * `String(detail)` yields `"409: [object Object]"` and silently destroys
     * every such recovery path.
     */
    readonly body?: unknown,
  ) {
    super(`${status}: ${detail}`)
    this.name = 'ApiError'
  }
}

/**
 * What to say when the server wrote nothing a person can read.
 *
 * A 5xx from a proxy has no JSON body and no reason phrase, so there is no
 * sentence to prefer -- and the number alone is not one. The outage screen is
 * what a reader should see here, which `ViewShell` renders off the health poll;
 * this is the sentence for the seconds before that poll agrees.
 */
function unreachable(status: number): string {
  if (status >= 502 && status <= 504) return 'the server is not answering'
  if (status >= 500) return 'something went wrong on the server'
  return `the request was refused (${status})`
}

/** FastAPI's `detail` is a string OR an object. Narrow before stringifying. */
function extractDetail(body: unknown, fallback: string): string {
  if (typeof body !== 'object' || body === null) return fallback
  const record = body as Record<string, unknown>
  const detail = record.detail ?? record.error
  if (typeof detail === 'string') return detail
  if (typeof detail === 'object' && detail !== null) {
    const message = (detail as Record<string, unknown>).message
    if (typeof message === 'string') return message
  }
  return fallback
}

/** The machine-readable half of an error body: `detail.error`, or "". */
function extractCode(body: unknown): string {
  if (typeof body !== 'object' || body === null) return ''
  const detail = (body as Record<string, unknown>).detail
  if (typeof detail !== 'object' || detail === null) return ''
  const code = (detail as Record<string, unknown>).error
  return typeof code === 'string' ? code : ''
}

/**
 * A 401, distinctly. A subclass rather than a flag so every existing
 * `instanceof ApiError` — including the 409 recovery path above — keeps
 * working untouched.
 */
export class UnauthorizedError extends ApiError {
  constructor(detail: string, body?: unknown) {
    super(401, detail, body)
    this.name = 'UnauthorizedError'
  }
}

/**
 * Refused on ROLE, not on identity. An `ApiError` subclass so every existing
 * `instanceof ApiError` recovery path keeps working, and its own class so a call
 * site that wants to hide a control rather than show a message can tell the
 * difference.
 */
export class ForbiddenError extends ApiError {
  constructor(detail: string, body?: unknown) {
    super(403, detail, body)
    this.name = 'ForbiddenError'
  }
}

/**
 * The one way to turn a caught error into text for a human.
 *
 * There were two idioms and neither was right everywhere. Fourteen sites did
 * `err instanceof ApiError ? err.detail : '<fallback>'`, which is correct but
 * loses a non-ApiError entirely. Eleven did `err instanceof Error ? err.message
 * : String(err)`, and `ApiError.message` is `` `${status}: ${detail}` `` — so a
 * Neo4j outage rendered across the graph canvas as the literal string
 * `500: Internal Server Error`.
 *
 * This prefers `detail`, which is the sentence the server wrote for a person,
 * and falls back through `message` to the caller's own wording.
 */
export function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError && err.detail) return err.detail
  if (err instanceof Error && err.message) return err.message
  return fallback
}

let onUnauthorized: (() => void) | null = null
let onForbidden: ((path: string, refusal: { code: string; message: string }) => void) | null =
  null

/**
 * Wired once, in `main.ts`. A module-level hook rather than an import of the
 * router, which would close the cycle router → views → stores → client.
 *
 * The payoff is that no store needs editing: every one of them already lets an
 * error propagate out of its `catch`, so the redirect happens for all of them
 * from this single seam.
 */
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn
}

/**
 * The 403 sibling. Wired once, in `main.ts`, for the same reason and through the
 * same seam.
 *
 * Without it a refused write renders as whatever sentence the server wrote, at
 * whichever of ~92 call sites the reader happened to hit -- and roughly fifty of
 * those are in views and components rather than stores, so there is no store
 * layer that could catch them. That is the whole argument for a hook here: an
 * authorization decision made "in the stores" would miss half the app.
 *
 * A 403 is NOT a redirect. The reader is signed in and the page they are on is
 * theirs; only the action was refused. Bouncing them to `/login` would tell them
 * to fix the one thing that is not wrong.
 */
export function setForbiddenHandler(
  fn: (path: string, refusal: { code: string; message: string }) => void,
): void {
  onForbidden = fn
}

/**
 * Exported so `sse.ts` builds its stream URL from the same string rather than a
 * second literal. `EventSource` is not `fetch` and deliberately does not go
 * through `apiGet` -- the stream is held open for the length of a run and is
 * exempt from `TIMEOUT_MS` -- but the prefix is one fact and belongs in one
 * place.
 */
export const BASE = '/api'

/**
 * How long any single request may hang before it is given up on.
 *
 * There was no timeout at all, and no `AbortController` anywhere in `web/src`.
 * A hung request left its store's `loading` flag set until the browser's own
 * network timeout — which can be minutes — so the symptom was a spinner that
 * never resolved and never errored.
 *
 * Generous, because `/api/health` legitimately probes Neo4j and the claude CLI.
 */
const TIMEOUT_MS = 30_000

/** SSE is exempt: `/runs/{id}/stream` is held open for the length of a run. */
export class TimeoutError extends ApiError {
  constructor(path: string) {
    super(0, `the server did not respond within ${TIMEOUT_MS / 1000}s`, { path })
    this.name = 'TimeoutError'
  }
}

/**
 * The caller gave up, which is not a failure.
 *
 * Distinct from `TimeoutError` because the two look identical at the `fetch`
 * boundary -- both surface as `AbortError` -- and treating them the same is how
 * "you navigated away" would render as "the server did not respond within 30s".
 * Anything that cancels deliberately is expected to swallow this rather than
 * showing it.
 */
export class CancelledError extends ApiError {
  constructor(path: string) {
    super(0, 'the request was cancelled', { path })
    this.name = 'CancelledError'
  }
}

/**
 * The request never reached a server.
 *
 * `fetch` rejects with a bare `TypeError` for a DNS failure, a refused
 * connection or a dropped network, and its `message` is the BROWSER's wording --
 * "Failed to fetch" in Chromium, "NetworkError when attempting to fetch
 * resource" in Firefox. Neither is a sentence anyone here wrote, both are
 * jargon, and they differ per browser.
 *
 * Converting it here rather than sniffing for `TypeError` in `errorText` keeps
 * the conversion where we KNOW the cause: a `TypeError` thrown from anywhere
 * else is a real programming error and must not be disguised as an outage.
 *
 * An `ApiError` subclass so `instanceof ApiError` recovery paths keep working;
 * callers wanting to distinguish "never answered" from "answered and said no"
 * check for this type specifically.
 */
export class NetworkError extends ApiError {
  constructor(path: string) {
    super(0, 'could not reach the server', { path })
    this.name = 'NetworkError'
  }
}

/**
 * In-flight coalescing for bare GETs: two components asking the same question
 * at the same moment share one round trip.
 *
 * ONLY the `init === undefined` shape. A caller passing `init` is either not a
 * GET (`apiPost` and friends route through here) or carries its own `signal`,
 * and a shared request must not be abortable by one of its riders. The SECOND
 * consumer gets a `structuredClone`: the callers were written against private
 * responses, and handing two stores one object graph would let a sort or an
 * in-place edit in one silently reorder the other's rows.
 */
const inflightGets = new Map<string, Promise<unknown>>()

export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  if (init !== undefined) return request<T>(path, init)
  const pending = inflightGets.get(path)
  if (pending) return structuredClone(await pending) as T
  const flight = request<T>(path, undefined).finally(() => inflightGets.delete(path))
  inflightGets.set(path, flight)
  return flight
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // No `credentials:` option on purpose. `fetch` sends same-origin cookies by
  // default, and the SPA is same-origin with /api under both the vite proxy and
  // The reverse proxy. Setting `include` would be a change with no upside and would need a
  // CORS policy to mean anything.
  const timeout = new AbortController()
  const timer = setTimeout(() => timeout.abort(), TIMEOUT_MS)

  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: { accept: 'application/json' },
      ...init,
      // AFTER the spread, not before. With `...init` last, a caller passing its
      // own `signal` silently replaces the timeout's and the 30s ceiling stops
      // existing for that request -- invisibly, because no
      // caller passed one yet. Composed rather than chosen: both the timeout and
      // the caller must be able to abort.
      signal: init?.signal ? AbortSignal.any([timeout.signal, init.signal]) : timeout.signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      // WHICH abort. `fetch` reports both the same way, and a caller that
      // cancelled on navigation must not be told the server was slow.
      throw timeout.signal.aborted ? new TimeoutError(path) : new CancelledError(path)
    }
    // `fetch` rejects with a bare TypeError when it never got a response at all.
    if (err instanceof TypeError) throw new NetworkError(path)
    throw err
  } finally {
    // Cleared on every path. Kept as a manual timer rather than
    // `AbortSignal.timeout()` precisely so this can happen: that helper holds
    // its timer for the full 30s after even a fast response, and at the 3s live
    // poll interval that is ten standing timers. `client.spec.ts` asserts it.
    clearTimeout(timer)
  }

  if (!response.ok) {
    let body: unknown
    try {
      body = await response.json()
    } catch {
      // Non-JSON error body; the status line is all we have.
    }
    if (response.status === 401) {
      onUnauthorized?.()
      throw new UnauthorizedError(extractDetail(body, 'sign in to continue'), body)
    }
    // 403 is NOT 401 and must not be treated as one. The reader is signed in and
    // the page is theirs; only the action was refused. Throwing
    // `UnauthorizedError` here would send them to `/login` to fix the one thing
    // that is not wrong -- and on a shared instance they would loop, because
    // signing in again returns the same role.
    if (response.status === 403) {
      // THE REASON, not just the path. A role refusal and a paywall are both
      // 403 and need opposite sentences: one is "this is not yours to change",
      // the other is "this is part of a subscription". The handler cannot tell
      // them apart from a path, and the body has said which all along.
      const message = extractDetail(body, 'your account cannot do that on this instance')
      onForbidden?.(path, { code: extractCode(body), message })
      throw new ForbiddenError(message, body)
    }
    // NOT `response.statusText`, which HTTP/2 does not carry: a reason phrase
    // is an HTTP/1 field, so a gateway failure in front of this API produced
    // the empty string and `ApiError.message` read `502:` with nothing after
    // it. That string reached the archive, the lookup list and the learn page
    // as their whole error state.
    throw new ApiError(response.status, extractDetail(body, unreachable(response.status)), body)
  }

  // 202 and 204 have no body worth parsing.
  if (response.status === 204) return undefined as T

  return (await response.json()) as T
}

function withBody(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { 'content-type': 'application/json', accept: 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return apiGet<T>(path, withBody('POST', body))
}

export async function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return apiGet<T>(path, withBody('PUT', body))
}

export async function apiPatch<T>(path: string, body?: unknown): Promise<T> {
  return apiGet<T>(path, withBody('PATCH', body))
}

export async function apiDelete<T>(path: string, body?: unknown): Promise<T> {
  return apiGet<T>(path, withBody('DELETE', body))
}
