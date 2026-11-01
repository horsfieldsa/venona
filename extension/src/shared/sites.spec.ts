/** The reconciler is the one piece of the dynamic allowlist that fails silently.
 *
 * Every other part of this feature announces itself: a bad match pattern is a
 * Chrome error, an unreachable API is a message in the options page, a missing
 * icon fails the build. Registering the WRONG set just means the extension runs
 * somewhere it should not, or stops running somewhere it should -- and neither
 * shows up anywhere until someone notices underlines missing on a site they
 * granted, or present on one they revoked.
 *
 * `make ext-smoke` covers the round trip but needs a live API and a headless
 * browser. This needs neither.
 */

import { describe, expect, it } from 'vitest'

import {
  type AnnotatableSite,
  catalogCovers,
  enabledPatternFor,
  reconcile,
  routeForHost,
  touchesSites,
} from './sites'

const WOTR = 'https://*.warontherocks.com/*'
const GAO = 'https://*.gao.gov/*'
const API = 'http://127.0.0.1:5173/*'

describe('reconcile', () => {
  it('registers what is both wanted and granted', () => {
    expect(reconcile([WOTR, GAO], [WOTR, GAO], [])).toEqual([GAO, WOTR])
  })

  it('drops a pattern the browser has not granted', () => {
    // The user ticked the box and then declined Chrome's prompt. Intent alone
    // must never register: `registerContentScripts` would reject, and if it did
    // not, we would be injecting somewhere nobody consented to.
    expect(reconcile([WOTR, GAO], [WOTR], [])).toEqual([WOTR])
  })

  it('drops a pattern the user revoked from chrome://extensions', () => {
    // Revocation happens outside the extension entirely, so intent is stale by
    // definition. The browser is the authority; this is the check that defers.
    expect(reconcile([WOTR], [], [])).toEqual([])
  })

  it('drops a paused pattern without touching its grant', () => {
    // Pausing must not drop the permission -- resuming would re-prompt, and the
    // whole point of the control is to stop annotating a site you still trust.
    expect(reconcile([WOTR, GAO], [WOTR, GAO], [GAO])).toEqual([WOTR])
  })

  it('never registers the API origin, even if it somehow lands in the list', () => {
    // `permissions.getAll().origins` includes the manifest's own
    // `host_permissions`, which is why `grantedAmong` asks about the patterns we
    // MEAN rather than reading that list. If the API origin ever reached intent,
    // the content script would annotate the venona app itself.
    expect(reconcile([WOTR], [WOTR, API], [])).toEqual([WOTR])
  })

  it('is order-independent and deduplicated on output', () => {
    expect(reconcile([GAO, WOTR], [WOTR, GAO], [])).toEqual(reconcile([WOTR, GAO], [GAO, WOTR], []))
  })
})

describe('touchesSites', () => {
  it('fires for the two keys the worker cares about', () => {
    expect(touchesSites({ 'sites:enabled': {} }, 'sync')).toBe(true)
    expect(touchesSites({ 'sites:paused': {} }, 'sync')).toBe(true)
  })

  it('ignores the base URL, which lives in sync too', () => {
    // `baseUrl` is in `sync` alongside these. Reconciling on every settings save
    // would be harmless but pointless; more to the point, this asserts the guard
    // is keyed on the name rather than just the area.
    expect(touchesSites({ baseUrl: {} }, 'sync')).toBe(false)
  })

  it('ignores local and session, where the token and caches live', () => {
    expect(touchesSites({ 'sites:enabled': {} }, 'local')).toBe(false)
    expect(touchesSites({ 'sites:catalog:default': {} }, 'local')).toBe(false)
  })
})

/** Auto-routing's two pure halves: does a catalog claim a host, and which
 * instance wins. Wrong silently, like `reconcile` -- a misroute sends an
 * ingest to the wrong corpus and nothing on screen says so.
 */
const site = (domain: string): AnnotatableSite =>
  ({ domain, match: `https://*.${domain}/*`, configured: true, documents: 1 }) as AnnotatableSite

describe('catalogCovers', () => {
  it('claims an exact host', () => {
    expect(catalogCovers('warontherocks.com', [site('warontherocks.com')])).toBe(true)
  })

  it('claims a subdomain', () => {
    expect(catalogCovers('www.warontherocks.com', [site('warontherocks.com')])).toBe(true)
  })

  it('does not claim a host that merely ENDS with the name', () => {
    expect(catalogCovers('notdefensenews.com', [site('defensenews.com')])).toBe(false)
  })

  it('claims nothing from an empty catalog', () => {
    expect(catalogCovers('warontherocks.com', [])).toBe(false)
  })
})

describe('routeForHost', () => {
  const catalogs = [
    { instanceId: 'defense', sites: [site('defensenews.com')] },
    { instanceId: 'ai', sites: [site('theinformation.com')] },
  ]

  it('routes to the single instance whose catalog claims the host', () => {
    expect(routeForHost('www.theinformation.com', catalogs, 'defense')).toBe('ai')
  })

  it('falls back to the active instance when nothing claims the host', () => {
    expect(routeForHost('example.com', catalogs, 'defense')).toBe('defense')
  })

  it('falls back to the active instance on a tie', () => {
    // Two corpora both track the site: a genuine ambiguity, and the context
    // menu stays the disambiguator rather than a silent guess.
    const both = [
      { instanceId: 'defense', sites: [site('breakingdefense.com')] },
      { instanceId: 'ai', sites: [site('breakingdefense.com')] },
    ]
    expect(routeForHost('breakingdefense.com', both, 'ai')).toBe('ai')
  })

  it('falls back to the active instance with no parseable host', () => {
    expect(routeForHost(null, catalogs, 'ai')).toBe('ai')
  })
})

/** The two vocabularies.
 *
 * The options page enables the catalog's wildcard; the toolbar button enables
 * the exact origin the browser prompted for. Both end up in `sites:enabled`,
 * and a row that cannot recognise the other spelling renders a working site as
 * off -- and then cannot turn it off either, because unticking removes a
 * pattern nobody enabled.
 */
describe('enabledPatternFor', () => {
  it('finds the catalog wildcard', () => {
    expect(enabledPatternFor('warontherocks.com', [WOTR])).toBe(WOTR)
  })

  it('finds an exact origin granted from the toolbar', () => {
    const origin = 'https://www.defensenews.com/*'
    expect(enabledPatternFor('defensenews.com', [origin])).toBe(origin)
  })

  it('finds a bare origin with no subdomain', () => {
    const origin = 'https://breakingdefense.com/*'
    expect(enabledPatternFor('breakingdefense.com', [origin])).toBe(origin)
  })

  it('returns null when nothing covers the domain', () => {
    expect(enabledPatternFor('defensenews.com', [WOTR, GAO])).toBeNull()
  })

  it('does not match a domain that merely ENDS with the name', () => {
    // `notdefensenews.com` ends with `defensenews.com` as a STRING. Matching on
    // a bare `endsWith` would tick the wrong row and, worse, hand the wrong
    // pattern to `chrome.permissions.remove`.
    expect(enabledPatternFor('defensenews.com', ['https://notdefensenews.com/*'])).toBeNull()
  })

  it('does not match a domain the pattern is a SUFFIX of', () => {
    // Enabling `gao.gov` must not tick a hypothetical `data.gao.gov` row: the
    // grant is narrower than the row, so the row is not on.
    expect(enabledPatternFor('data.gao.gov', ['https://gao.gov/*'])).toBeNull()
  })

  it('ignores the API origin, which is never a catalog domain', () => {
    expect(enabledPatternFor('warontherocks.com', [API])).toBeNull()
  })
})
