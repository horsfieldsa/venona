import { describe, expect, it } from 'vitest'

import { type GuardState, type GuardTarget, redirectFor } from '@/router/guards'

const READING: GuardState = {
  setupRequired: false,
  isAdmin: false,
  hasSession: true,
  billing: null,
  relevanceTailored: true,
  welcomeDismissed: true,
}

function at(name: string, over: Partial<GuardTarget> = {}): GuardTarget {
  return { name, isPublic: false, isOperator: false, ...over }
}

function billing(over: Partial<NonNullable<GuardState['billing']>> = {}) {
  return { enforced: true, entitled: false, status: 'none', ...over }
}

describe('the paywall redirect', () => {
  it('sends a reader who has never subscribed to the offer', () => {
    expect(redirectFor(at('brief'), { ...READING, billing: billing() })).toBe('settings-billing')
  })

  it('does NOT fire for an admin who does not pay', () => {
    /* An administrator reads `none` and is entitled BY ROLE. Keying on the
     * status word alone bounced every admin to the subscribe page, and they
     * never saw the app at all. */
    const state = { ...READING, isAdmin: true, billing: billing({ entitled: true }) }
    expect(redirectFor(at('brief'), state)).toBeNull()
  })

  it('does not fire for a lapsed reader, who keeps an archive', () => {
    const state = { ...READING, billing: billing({ status: 'lapsed' }) }
    expect(redirectFor(at('brief'), state)).toBeNull()
  })

  it('does not fire on an instance that takes no money', () => {
    expect(redirectFor(at('brief'), READING)).toBeNull()
  })

  it('does not fire when billing is off but a stale row says none', () => {
    const state = { ...READING, billing: billing({ enforced: false }) }
    expect(redirectFor(at('brief'), state)).toBeNull()
  })

  it('lets the reader reach the pages that can help them', () => {
    const state = { ...READING, billing: billing() }
    for (const name of ['settings-billing', 'account', 'not-found']) {
      expect(redirectFor(at(name), state), name).toBeNull()
    }
    expect(redirectFor(at('login', { isPublic: true }), state)).toBeNull()
  })

  /* THE WHOLE SETTINGS RAIL, because the server keeps all of it open to an
   * unsubscribed reader and this guard is the only thing that can contradict
   * it. Disconnecting an assistant especially: `api/authz.py` has a test named
   * for the promise that it never needs a subscription, and a bounce here made
   * that screen unreachable while the API would still have served it. */
  it('never bounces a reader off a screen the server keeps open', () => {
    const state = { ...READING, billing: billing() }
    for (const name of [
      'account',
      'settings-relevance',
      'settings-notifications',
      'settings-agents',
      'settings-billing',
    ]) {
      expect(redirectFor(at(name), state), name).toBeNull()
    }
  })
})

describe('the two redirects together', () => {
  /* THE HANG. The paywall sent `/` to billing, the invitation sent billing to
   * `/welcome`, and the paywall sent `/welcome` back to billing. A navigation
   * that never settles is a hung tab, and it hit every new reader, because a
   * new reader has by definition dismissed nothing. */
  const NEW_READER: GuardState = {
    ...READING,
    relevanceTailored: false,
    welcomeDismissed: false,
    billing: billing(),
  }

  it('settles rather than trading the reader back and forth', () => {
    let name = 'brief'
    const seen: string[] = [name]
    for (let hop = 0; hop < 10; hop += 1) {
      const next = redirectFor(at(name), NEW_READER)
      if (next === null) break
      expect(seen, `revisited ${next}`).not.toContain(next)
      seen.push(next)
      name = next
    }
    expect(seen.at(-1)).toBe('settings-billing')
    expect(redirectFor(at('settings-billing'), NEW_READER)).toBeNull()
  })

  it('still invites a paying reader who has not tuned relevance', () => {
    const state = { ...NEW_READER, billing: billing({ entitled: true, status: 'active' }) }
    expect(redirectFor(at('brief'), state)).toBe('welcome')
  })

  it('never invites anyone from a wrong address', () => {
    expect(redirectFor(at('not-found'), NEW_READER)).toBeNull()
  })
})

describe('the other two', () => {
  it('sends an admin of an unconfigured instance to setup', () => {
    const state = { ...READING, setupRequired: true, isAdmin: true }
    expect(redirectFor(at('brief'), state)).toBe('setup')
    expect(redirectFor(at('setup'), state)).toBeNull()
  })

  it('lets an admin reach the model page before the instance is configured', () => {
    /* The wizard's first act is to START A RUN, and a run needs a model
       service. With this route behind the setup redirect, a fresh instance
       had no way to connect one: the wizard could not run, and the page that
       would fix it bounced back to the wizard. */
    const state = { ...READING, setupRequired: true, isAdmin: true }
    expect(redirectFor(at('admin-model', { isOperator: true }), state)).toBeNull()
  })

  it('leaves a reader on an unconfigured instance alone', () => {
    expect(redirectFor(at('brief'), { ...READING, setupRequired: true })).toBeNull()
  })

  it('keeps a reader out of operator routes', () => {
    expect(redirectFor(at('admin', { isOperator: true }), READING)).toBe('brief')
    expect(redirectFor(at('admin', { isOperator: true }), { ...READING, isAdmin: true })).toBeNull()
  })

  it('setup wins over the paywall', () => {
    /* Order is the rule. An instance with no descriptor cannot sell anything. */
    const state = { ...READING, setupRequired: true, isAdmin: true, billing: billing() }
    expect(redirectFor(at('brief'), state)).toBe('setup')
  })
})
