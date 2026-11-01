/** The extension's fetch seam.
 *
 * `web/src/api/client.ts` cannot be reused: its `BASE` is a hardcoded relative
 * `/api`, it has no header injection point, and it authenticates with the
 * `venona_session` cookie. C12 fixes `allow_credentials=False` permanently, so
 * a cross-origin caller authenticates with `Authorization: Bearer` or not at
 * all. Same error model as the web client, deliberately -- `errorText` is the
 * one sanctioned way to turn a caught error into something a human reads.
 *
 * Imported by the service worker and the extension's own pages (options,
 * pairing). Nothing that runs in a HOST page's process ever sees
 * `settings.token`: the content script talks to the worker over a port and
 * never imports this module.
 */

const TIMEOUT_MS = 30_000

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly body?: unknown,
  ) {
    super(detail)
    this.name = 'ApiError'
  }
}

export class TimeoutError extends ApiError {
  constructor() {
    super(0, 'the API did not answer within 30 seconds')
    this.name = 'TimeoutError'
  }
}

export class UnauthorizedError extends ApiError {
  constructor(detail: string) {
    super(401, detail)
    this.name = 'UnauthorizedError'
  }
}

export interface Settings {
  /** e.g. `http://127.0.0.1:5173/api`. No trailing slash. */
  baseUrl: string
  /** `VENONA_AUTH_API_TOKEN`. Empty in dev, where auth is disabled. */
  token: string
  /** Which configured instance this is -- cache keys and the signed-out
   * badge are per instance, or one deployment's 401 would mute them all. */
  instanceId: string
}

/** One configured Venona deployment. There can be several -- one per domain
 * (docs/adr/0137) -- and the toolbar sends to the ACTIVE one. Labels are the
 * operator's words; `id` is stable and never shown. */
export interface Instance {
  id: string
  label: string
  baseUrl: string
}

export interface InstanceSettings {
  instances: Instance[]
  activeId: string
  /** By instance id. Lives in `chrome.storage.local` only -- see below. */
  tokens: Record<string, string>
}

/** The Vite proxy, not uvicorn.
 *
 * This is one setting doing two jobs: it is where requests go, and -- with
 * `/api` stripped, in `background.ts::appUrl` -- it is where "Open in Venona →"
 * points. Those are the same origin in production, where the reverse proxy serves the SPA
 * and the API together, and they are NOT the same origin in development, where
 * uvicorn on :8000 serves the API and knows nothing about the app. Defaulting
 * to :8000 made every card's link land on a 404 from a server that has no
 * frontend, which is not a broken link so much as a category error.
 *
 * :5173 is the origin that is both, because Vite proxies `/api` through with
 * `changeOrigin: false` -- so the API still sees the extension's own `Origin`
 * and the CORS allowlist works unchanged. It is also the origin `CLAUDE.md`
 * already insists health checks go through rather than hitting uvicorn direct.
 */
const LOCAL_DEV = 'http://127.0.0.1:5173/api'

/** Which build this is. `store` is what `make ext-package` uploads; everything
 * else -- `make ext-build`, ext-smoke, a checkout loaded unpacked -- is `dev`.
 * Resolved at build time; under vitest the env is absent and this reads `dev`. */
export const EXT_CHANNEL: 'dev' | 'store' =
  import.meta.env.VITE_EXT_CHANNEL === 'store' ? 'store' : 'dev'

/** Local on every channel: `scripts/ext_smoke.py` never sets `baseUrl` and
 * relies on it, and a deployment is a URL the reader types on the options page. */
export const DEFAULTS: Settings = {
  baseUrl: LOCAL_DEV,
  token: '',
  instanceId: 'default',
}

/** What the options page offers as one click. Local dev is in
 * `host_permissions`, so choosing it never prompts; any other host is reachable
 * through `optional_host_permissions` and a prompt (see `originPatternFor`),
 * and further instances arrive through the hub directory rather than presets.
 */
export const PRESETS = {
  local: LOCAL_DEV,
} as const

/** Where the instance directory lives. Editable on the options page (a
 * self-hosted hub is a legitimate deployment, and `make hub-dev` serves it on
 * localhost:8180); persisted under `sync` because it is a preference. */
export const HUB_DEFAULT = 'http://localhost:8180'
export const HUB_URL_KEY = 'hub:url'

/** One row of `GET {hub}/public/instances`. Hand-written: the hub publishes
 * no OpenAPI document, so there is nothing to generate from. */
export interface DirectoryInstance {
  slug: string
  display_name: string
  tagline: string
  public_url: string
  /** `live | offline`. Offline rows stay addable -- the hub lists them
   * precisely so extension presets survive an outage. */
  status: string
}

/** Map a directory row onto a configured instance. Pure, for the spec. */
export function instanceFromDirectory(row: DirectoryInstance): Instance {
  return {
    id: row.slug,
    label: row.display_name,
    baseUrl: `${row.public_url.replace(/\/+$/, '')}/api`,
  }
}

/**
 * The directory listing. NOT through `request()`: its `baseUrl` contract means
 * ".../api" plus a bearer, and the hub has neither -- the endpoint is public
 * and unauthenticated. An extension-context fetch is governed by host
 * permissions, so the caller must hold (or have just requested) the hub's
 * origin before calling.
 */
export async function fetchDirectory(hubUrl: string): Promise<DirectoryInstance[]> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15_000)
  try {
    const response = await fetch(`${hubUrl.replace(/\/+$/, '')}/public/instances`, {
      credentials: 'omit',
      signal: controller.signal,
    })
    if (!response.ok) throw new Error(`the hub answered ${response.status}`)
    const body = (await response.json()) as { instances?: DirectoryInstance[] }
    return body.instances ?? []
  } finally {
    clearTimeout(timer)
  }
}

/**
 * The host permission a base URL needs, as a Chrome match pattern.
 *
 * `host_permissions` is what grants an extension cross-origin access
 * (ADR 0099, measured rather than assumed), and it only names the local dev
 * port. Any other deployment was
 * therefore unreachable: the base URL was editable, the permission for it was
 * not, and the symptom was `TypeError: Failed to fetch` with nothing in the
 * UI connecting the two.
 *
 * Returns null when the URL will not parse, so a half-typed value asks for
 * nothing rather than throwing inside a click handler.
 */
export function originPatternFor(baseUrl: string): string | null {
  try {
    return `${new URL(baseUrl).origin}/*`
  } catch {
    return null
  }
}

/** Token lives in `local`, never `sync`.
 *
 * `chrome.storage.sync` replicates through a Google account. The API token is
 * all-or-nothing -- `.env.production.example` puts it plainly: "anything
 * holding this token is you" -- so it does not leave this machine. The base URL
 * is not a credential and syncs happily.
 */
/** The full instance list, migrating the legacy single-instance shape lazily.
 *
 * An install that predates instances holds `sync.baseUrl` + `local.token`;
 * the first load wraps them as one instance (`id: 'default'`) and writes the
 * list shape. The legacy keys stay in place so a rollback build still finds
 * its settings.  comment-history: ok -- this describes live migration
 * behaviour the code below performs, not an edit.
 */
export async function loadInstances(): Promise<InstanceSettings> {
  const [sync, local] = await Promise.all([
    chrome.storage.sync.get({ baseUrl: '', instances: null, activeId: '' }),
    chrome.storage.local.get({ token: '', tokens: null }),
  ])

  let instances = sync.instances as Instance[] | null
  let activeId = String(sync.activeId || '')
  let tokens = (local.tokens as Record<string, string> | null) ?? {}

  if (!instances || !instances.length) {
    const baseUrl = String(sync.baseUrl || DEFAULTS.baseUrl).replace(/\/+$/, '')
    let label = 'Venona'
    try {
      label = new URL(baseUrl).host
    } catch {
      /* keep the generic label; the URL is fixable on the options page */
    }
    instances = [{ id: 'default', label, baseUrl }]
    activeId = 'default'
    tokens = { default: String(local.token || '') }
    await Promise.all([
      chrome.storage.sync.set({ instances, activeId }),
      chrome.storage.local.set({ tokens }),
    ])
  }
  if (!instances.some((i) => i.id === activeId)) activeId = instances[0].id
  return { instances, activeId, tokens }
}

export async function saveInstances(next: InstanceSettings): Promise<void> {
  await Promise.all([
    chrome.storage.sync.set({
      instances: next.instances.map((i) => ({ ...i, baseUrl: i.baseUrl.replace(/\/+$/, '') })),
      activeId: next.activeId,
    }),
    chrome.storage.local.set({ tokens: next.tokens }),
  ])
}

/** What is known about a token beyond its value: how it arrived and when it
 * ends. Written by pairing, cleared when a token is pasted over it, and read
 * by the worker's startup expiry check and by the options page's "connected
 * until" line. In `local` beside `tokens`, for the same reason they are. */
export interface TokenMeta {
  scope: string
  /** ISO-8601 UTC. */
  expiresAt: string
}

/** The session flag the worker raises on a 401, keyed per instance. Shared so
 * the options page can lower it the moment a re-pair lands. */
export const authBlockedKey = (instanceId: string): string => `auth:blocked:${instanceId}`

const TOKEN_META_KEY = 'tokenMeta'

export async function loadTokenMeta(instanceId: string): Promise<TokenMeta | null> {
  const stored = await chrome.storage.local.get({ [TOKEN_META_KEY]: {} })
  const all = stored[TOKEN_META_KEY] as Record<string, TokenMeta>
  return all[instanceId] ?? null
}

export async function saveTokenMeta(instanceId: string, meta: TokenMeta | null): Promise<void> {
  const stored = await chrome.storage.local.get({ [TOKEN_META_KEY]: {} })
  const all = stored[TOKEN_META_KEY] as Record<string, TokenMeta>
  if (meta) all[instanceId] = meta
  else delete all[instanceId]
  await chrome.storage.local.set({ [TOKEN_META_KEY]: all })
}

/** What the credential can reach, per instance.
 *
 * A paired token is always `annotate`-scoped, so `limited` is the steady state
 * for every paired user; `full` exists for the operator pasting the root
 * bearer. `null` -- never probed -- is treated as `full` by the reader
 * (`background.ts`), because hiding working buttons while a probe is pending
 * is worse than showing them one 403 too long; the first refusal downgrades.
 */
export type Capability = 'full' | 'limited'

const CAPS_KEY = 'caps'

export async function loadCapability(instanceId: string): Promise<Capability | null> {
  const stored = await chrome.storage.local.get({ [CAPS_KEY]: {} })
  const all = stored[CAPS_KEY] as Record<string, Capability>
  return all[instanceId] ?? null
}

export async function saveCapability(
  instanceId: string,
  capability: Capability | null,
): Promise<void> {
  const stored = await chrome.storage.local.get({ [CAPS_KEY]: {} })
  const all = stored[CAPS_KEY] as Record<string, Capability>
  if (capability) all[instanceId] = capability
  else delete all[instanceId]
  await chrome.storage.local.set({ [CAPS_KEY]: all })
}

/** Classify one probe of an admin-only read (`GET /runs/types`).
 *
 * 200 is an unrestricted admin credential; 403 is a reach or role block, and
 * both mean the gated buttons would fail; 401 is not a capability at all.
 * Pure, for the spec -- the fetch wrapper below owns the network half.
 */
export function classifyProbe(status: number): Capability | 'invalid' {
  if (status === 401) return 'invalid'
  return status === 403 ? 'limited' : 'full'
}

/** Ask the server what this instance's credential can do, and remember it.
 *
 * `/runs/types` rather than a made-up run id: identical classification with
 * no error-shaped 404s minted into the server's logs. A network failure keeps
 * whatever was stored -- reachability is the Test button's question, not this
 * one's.
 */
export async function probeCapability(settings: Settings): Promise<Capability | null> {
  let capability: Capability
  try {
    await request<unknown>(settings, '/runs/types', { method: 'GET' })
    capability = 'full'
  } catch (err) {
    const status = (err as { status?: number }).status
    if (!status) return loadCapability(settings.instanceId)
    const classified = classifyProbe(status)
    if (classified === 'invalid') {
      await saveCapability(settings.instanceId, null)
      return null
    }
    capability = classified
  }
  await saveCapability(settings.instanceId, capability)
  return capability
}

export async function setActiveInstance(id: string): Promise<void> {
  const all = await loadInstances()
  if (all.instances.some((i) => i.id === id)) {
    await chrome.storage.sync.set({ activeId: id })
  }
}

function flatten(all: InstanceSettings, id: string): Settings {
  const instance = all.instances.find((i) => i.id === id) ?? all.instances[0]
  return {
    baseUrl: instance.baseUrl.replace(/\/+$/, '') || DEFAULTS.baseUrl,
    token: String(all.tokens[instance.id] || ''),
    instanceId: instance.id,
  }
}

/** The ACTIVE instance, flat -- what every request-making call site takes.
 * Kept as the module's main entry so the background's call sites read the
 * same as they did with one instance. */
export async function loadSettings(): Promise<Settings> {
  const all = await loadInstances()
  return flatten(all, all.activeId)
}

export async function settingsFor(id: string): Promise<Settings> {
  const all = await loadInstances()
  return flatten(all, id)
}

/** Edit the ACTIVE instance. The options page binds its base-URL and token
 * fields to whichever instance is selected there and calls this. */
export async function saveSettings(next: Partial<Omit<Settings, 'instanceId'>>): Promise<void> {
  const all = await loadInstances()
  const active = all.instances.find((i) => i.id === all.activeId) ?? all.instances[0]
  if (next.baseUrl !== undefined) active.baseUrl = next.baseUrl.replace(/\/+$/, '')
  if (next.token !== undefined) all.tokens[active.id] = next.token
  await saveInstances(all)
}

/** FastAPI's `detail` is a string, an object, or `{detail: {message}}`.
 * `api/errors.py` standardises on `{error, message}`; older handlers do not. */
function extractDetail(body: unknown, fallback: string): string {
  if (typeof body === 'string' && body) return body
  if (body && typeof body === 'object') {
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail) return detail
    if (detail && typeof detail === 'object') {
      const message = (detail as { message?: unknown }).message
      if (typeof message === 'string' && message) return message
    }
  }
  return fallback
}

async function request<T>(settings: Settings, path: string, init: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS)

  const headers = new Headers(init.headers)
  if (init.body && !headers.has('content-type')) headers.set('content-type', 'application/json')
  // C12: bearer beats cookie in `principal_for`, and the browser will not
  // attach a cookie cross-origin anyway. Sending an empty header would be a
  // malformed credential rather than an absent one, so omit it entirely.
  if (settings.token) headers.set('authorization', `Bearer ${settings.token}`)

  let res: Response
  try {
    res = await fetch(`${settings.baseUrl}${path}`, {
      ...init,
      headers,
      signal: controller.signal,
      // Never send cookies. C12 pins `allow_credentials=False` server-side, so
      // this only makes the client agree with the server about why.
      credentials: 'omit',
    })
  } catch (err) {
    if (controller.signal.aborted) throw new TimeoutError()
    throw new ApiError(0, `could not reach ${settings.baseUrl}: ${(err as Error).message}`)
  } finally {
    clearTimeout(timer)
  }

  if (res.status === 204) return undefined as T

  const body = await res.json().catch(() => null)
  if (!res.ok) {
    const detail = extractDetail(body, `${res.status} ${res.statusText}`)
    if (res.status === 401) throw new UnauthorizedError(detail)
    throw new ApiError(res.status, detail, body)
  }
  return body as T
}

export function apiGet<T>(settings: Settings, path: string): Promise<T> {
  return request<T>(settings, path, { method: 'GET' })
}

export function apiPost<T>(settings: Settings, path: string, body?: unknown): Promise<T> {
  return request<T>(settings, path, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

/** RFC 6749 wants the token endpoint's parameters form-encoded, not as JSON.
 * The one non-JSON body in the extension, so it is a variant here rather than
 * an option every call site has to not use. */
export function apiPostForm<T>(
  settings: Settings,
  path: string,
  form: Record<string, string>,
): Promise<T> {
  return request<T>(settings, path, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(form).toString(),
  })
}

export function errorText(err: unknown, fallback = 'something went wrong'): string {
  if (err instanceof ApiError) return err.detail
  if (err instanceof Error) return err.message
  return fallback
}
