/** The pure half of pairing: PKCE arithmetic and URL construction.
 *
 * The flow itself needs a browser, a popup and a human; what a spec can pin is
 * that the challenge is the S256 the server verifies, and that the URLs are
 * the exact shapes `_safe_next` and the consent screen accept -- both of which
 * fail as "the sign-in did not work" with nothing connecting cause to effect.
 */

import { describe, expect, it } from 'vitest'

import {
  authorizePath,
  callbackParams,
  challengeFor,
  launchUrlFor,
  newVerifier,
} from './pairing'

describe('challengeFor', () => {
  it('produces the RFC 7636 appendix B vector', async () => {
    // The one published verifier/challenge pair; oauth_service.verify_challenge
    // implements the same definition, so agreeing with the RFC is agreeing
    // with the server.
    expect(await challengeFor('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk')).toBe(
      'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM',
    )
  })
})

describe('newVerifier', () => {
  it('is base64url with no padding, inside RFC 7636 length bounds', () => {
    const verifier = newVerifier()
    expect(verifier).toMatch(/^[A-Za-z0-9_-]+$/)
    expect(verifier.length).toBeGreaterThanOrEqual(43)
    expect(verifier.length).toBeLessThanOrEqual(128)
  })

  it('encodes the bytes it is given, so the spec can pin one', () => {
    expect(newVerifier(new Uint8Array([251, 255, 191]))).toBe('-_-_')
  })
})

describe('authorizePath', () => {
  const path = authorizePath({
    clientId: 'client:1',
    redirectUri: 'https://abcdefghijklmnop.chromiumapp.org/',
    challenge: 'c-h_allenge',
    state: 'st-1',
  })

  it('is a same-site path with its query — the shape _safe_next passes intact', () => {
    expect(path.startsWith('/api/oauth/authorize?')).toBe(true)
    expect(path.startsWith('//')).toBe(false)
  })

  it('carries every parameter the consent screen validates', () => {
    const query = new URLSearchParams(path.split('?')[1])
    expect(query.get('client_id')).toBe('client:1')
    expect(query.get('redirect_uri')).toBe('https://abcdefghijklmnop.chromiumapp.org/')
    expect(query.get('response_type')).toBe('code')
    expect(query.get('code_challenge')).toBe('c-h_allenge')
    expect(query.get('code_challenge_method')).toBe('S256')
    expect(query.get('scope')).toBe('annotate')
    expect(query.get('state')).toBe('st-1')
  })
})

describe('launchUrlFor', () => {
  it('enters at login with the authorize path url-encoded into next', () => {
    expect(launchUrlFor('https://defense.example.com/api', '/api/oauth/authorize?a=b&c=d')).toBe(
      'https://defense.example.com/api/auth/login?next=%2Fapi%2Foauth%2Fauthorize%3Fa%3Db%26c%3Dd',
    )
  })

  it('round-trips: what next carries decodes back to the exact path', () => {
    const path = authorizePath({
      clientId: 'client:1',
      redirectUri: 'https://x.chromiumapp.org/',
      challenge: 'c',
      state: 's',
    })
    const launch = launchUrlFor('http://127.0.0.1:5173/api', path)
    const next = new URL(launch).searchParams.get('next')
    expect(next).toBe(path)
  })
})

describe('callbackParams', () => {
  it('reads code and state from the chromiumapp answer', () => {
    expect(
      callbackParams('https://abcdefghijklmnop.chromiumapp.org/?code=xyz&state=st-1'),
    ).toEqual({ code: 'xyz', state: 'st-1' })
  })

  it('surfaces an OAuth error answer as its parameters', () => {
    const params = callbackParams(
      'https://x.chromiumapp.org/?error=access_denied&error_description=the+request+was+declined&state=s',
    )
    expect(params.error).toBe('access_denied')
    expect(params.error_description).toBe('the request was declined')
  })
})
