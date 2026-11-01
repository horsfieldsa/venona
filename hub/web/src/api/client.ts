/** The one place this app talks to the hub.
 *
 * EVERY CALL CARRIES THE SESSION COOKIE AND NOTHING ELSE. There is no bearer
 * here: `HUB_ADMIN_TOKEN` gates the machine surface, and a token in a bundle is
 * a token every reader of the page has. The cookie is `SameSite=Lax`, so it is
 * withheld from any cross-site request that is not a top-level GET navigation,
 * which is what protects the writes below.
 *
 * WRITES SEND JSON, and that is a second lock rather than a convenience.
 * `application/json` is not a CORS-simple content type, so a cross-origin write
 * must preflight -- and the hub grants `Access-Control-Allow-Credentials`
 * nowhere at all, so the preflight has nothing to answer with. An HTML form on
 * another origin cannot produce this request at any price.
 */
const BASE = '/admin/data'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** The hub's envelope is `{detail: {error, message}, request_id}`. A body that
 *  is not that shape is still an error; it just has to be described by its
 *  status, because nothing in it can be trusted to be a sentence. */
function describe(status: number, body: unknown): ApiError {
  const detail = (body as { detail?: { error?: string; message?: string } } | null)?.detail
  return new ApiError(
    status,
    detail?.error ?? 'error',
    detail?.message ?? `The hub answered ${status}.`,
  )
}

async function send(path: string, init: RequestInit): Promise<unknown> {
  const response = await fetch(`${BASE}${path}`, { credentials: 'same-origin', ...init })
  const body = response.status === 204 ? null : await response.json().catch(() => null)
  if (!response.ok) throw describe(response.status, body)
  return body
}

export function get<T>(path: string): Promise<T> {
  return send(path, { method: 'GET' }) as Promise<T>
}

export function post<T>(path: string, payload: unknown = {}): Promise<T> {
  return send(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  }) as Promise<T>
}
