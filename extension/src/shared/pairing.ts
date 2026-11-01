/** Pairing: Connect with Google, through the server's own OAuth flow.
 *
 * Runs in the OPTIONS PAGE, not the worker: `launchWebAuthFlow` keeps its
 * popup open for as long as a human takes to sign in, and an MV3 worker can be
 * killed under it. The PKCE verifier and `state` are locals here and are never
 * stored anywhere.
 *
 * The flow enters at `/api/auth/login?next=<authorize path>` because
 * `authorize` requires a session and answers a bare 401 without one (contract
 * C12); `login` is how a session comes to exist, and `_safe_next` passes a
 * same-site path with its query intact. Registration is per pairing attempt:
 * the redirect URI derives from the runtime extension id, so a fresh RFC 7591
 * registration self-heals an id change and a database reset alike (ADR 0161).
 */

import { apiPost, apiPostForm, type Settings } from './client'

interface RegisteredClient {
  client_id: string
}

interface TokenResponse {
  access_token: string
  expires_in: number
  scope: string
}

export interface Paired {
  token: string
  scope: string
  /** ISO-8601 UTC, computed from `expires_in` at redemption. */
  expiresAt: string
}

function base64url(bytes: Uint8Array): string {
  let raw = ''
  for (const b of bytes) raw += String.fromCharCode(b)
  return btoa(raw).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** RFC 7636 wants 43-128 chars from the unreserved set; 64 random bytes as
 * base64url is 86. The parameter seam exists for the spec, which has no
 * `crypto.getRandomValues`-free way to pin a vector otherwise. */
export function newVerifier(
  bytes: Uint8Array = crypto.getRandomValues(new Uint8Array(64)),
): string {
  return base64url(bytes)
}

export async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
  return base64url(new Uint8Array(digest))
}

/** The same-site path `login`'s `next` carries. Query and all: the consent
 * screen needs every parameter to survive the sign-in round trip. */
export function authorizePath(args: {
  clientId: string
  redirectUri: string
  challenge: string
  state: string
}): string {
  const query = new URLSearchParams({
    client_id: args.clientId,
    redirect_uri: args.redirectUri,
    response_type: 'code',
    code_challenge: args.challenge,
    code_challenge_method: 'S256',
    scope: 'annotate',
    state: args.state,
  })
  return `/api/oauth/authorize?${query.toString()}`
}

export function launchUrlFor(baseUrl: string, path: string): string {
  const origin = baseUrl.replace(/\/api$/, '')
  return `${origin}/api/auth/login?next=${encodeURIComponent(path)}`
}

/** Whatever came back on the chromiumapp.org URL, as a plain record. The
 * caller checks `state` against the value it sent. */
export function callbackParams(responseUrl: string): Record<string, string> {
  return Object.fromEntries(new URLSearchParams(new URL(responseUrl).search))
}

export async function pair(settings: Settings): Promise<Paired> {
  const redirectUri = chrome.identity.getRedirectURL()
  // Register and redeem without a bearer: both paths are EXEMPT, and sending
  // whatever stale token this instance holds would only confuse a reader of
  // the server's logs.
  const anonymous: Settings = { ...settings, token: '' }

  const client = await apiPost<RegisteredClient>(anonymous, '/oauth/register', {
    client_name: 'Venona browser extension',
    redirect_uris: [redirectUri],
  })

  const verifier = newVerifier()
  const state = newVerifier(crypto.getRandomValues(new Uint8Array(16)))
  const path = authorizePath({
    clientId: client.client_id,
    redirectUri,
    challenge: await challengeFor(verifier),
    state,
  })

  const responseUrl = await chrome.identity.launchWebAuthFlow({
    url: launchUrlFor(settings.baseUrl, path),
    interactive: true,
  })
  if (!responseUrl) throw new Error('the sign-in window was closed before it finished')

  const params = callbackParams(responseUrl)
  if (params.error) {
    throw new Error(
      params.error === 'access_denied'
        ? 'the request was declined on the consent page'
        : params.error_description || params.error,
    )
  }
  if (params.state !== state) throw new Error('the sign-in answer did not match the request')
  if (!params.code) throw new Error('the sign-in finished without a code')

  const token = await apiPostForm<TokenResponse>(anonymous, '/oauth/token', {
    grant_type: 'authorization_code',
    code: params.code,
    redirect_uri: redirectUri,
    client_id: client.client_id,
    code_verifier: verifier,
  })

  return {
    token: token.access_token,
    scope: token.scope,
    expiresAt: new Date(Date.now() + token.expires_in * 1000).toISOString(),
  }
}
