/** The base URL's host permission.
 *
 * `host_permissions` is what grants an extension cross-origin access, and it
 * names `example.com` and the two localhost ports and nothing else. Before this
 * existed, pointing the extension at any other deployment failed with
 * `TypeError: Failed to fetch` and nothing in the UI connected the editable
 * field to the permission it needed -- so the pattern this derives is the whole
 * fix, and getting it subtly wrong (dropping a port, keeping the path) asks for
 * a permission that does not cover the requests.
 */

import { describe, expect, it, vi } from 'vitest'

// The real file, imported rather than read: `resolveJsonModule` is already on,
// so this needs no `@types/node` and stays a compile-time reference to the
// manifest instead of a path that could quietly stop resolving.
import manifest from '../../manifest.json'

import { instanceFromDirectory, originPatternFor, PRESETS } from './client'

describe('instanceFromDirectory', () => {
  it('maps slug/display_name/public_url onto the Instance shape', () => {
    expect(
      instanceFromDirectory({
        slug: 'acme',
        display_name: 'Acme Watch',
        tagline: 'who is buying anvils',
        public_url: 'https://acme.example',
        status: 'live',
      }),
    ).toEqual({ id: 'acme', label: 'Acme Watch', baseUrl: 'https://acme.example/api' })
  })

  it('never doubles the slash — the hub stores origins unslashed, but a row is data', () => {
    expect(
      instanceFromDirectory({
        slug: 'x',
        display_name: 'X',
        tagline: '',
        public_url: 'https://x.example/',
        status: 'offline',
      }).baseUrl,
    ).toBe('https://x.example/api')
  })
})

describe('originPatternFor', () => {
  it('reduces a base URL to its origin', () => {
    expect(originPatternFor('https://example.com/api')).toBe('https://example.com/*')
  })

  it('keeps the port, which localhost depends on', () => {
    // :5173 and :8000 are DIFFERENT ORIGINS to Chrome, so the port has to
    // survive into the pattern. One is granted up front and the other is
    // optional, and a pattern that dropped the port would ask for a permission
    // Chrome does not match against the requests actually being made.
    expect(originPatternFor('http://127.0.0.1:5173/api')).toBe('http://127.0.0.1:5173/*')
    expect(originPatternFor('http://127.0.0.1:8000/api')).toBe('http://127.0.0.1:8000/*')
  })

  it('drops the path, however deep', () => {
    expect(originPatternFor('https://venona.example.com/deep/api')).toBe(
      'https://venona.example.com/*',
    )
  })

  it('is unaffected by a trailing slash', () => {
    // `loadSettings` strips these, but the options field is raw user input and
    // this runs before the save.
    expect(originPatternFor('https://example.com/api/')).toBe('https://example.com/*')
  })

  it('returns null rather than throwing on a half-typed value', () => {
    // This runs inside a click handler that then calls `permissions.request`.
    // Throwing there would lose the user's input and the gesture with it.
    expect(originPatternFor('')).toBeNull()
    expect(originPatternFor('example.com')).toBeNull()
    expect(originPatternFor('https://')).toBeNull()
  })
})

describe('PRESETS', () => {
  it('names only hosts the manifest already permits', () => {
    // Read the MANIFEST, do not restate it. The presets are offered as one-click
    // and are documented as never prompting, which is true only for as long as
    // `host_permissions` still covers them. Renaming the production host in one
    // file and not the other turns a button that works into a button that
    // prompts -- or, if the grant is declined, one that silently cannot reach
    // anything.
    for (const url of Object.values(PRESETS)) {
      expect(manifest.host_permissions).toContain(originPatternFor(url))
    }
  })
})

describe('instance storage', () => {
  /** Just enough of chrome.storage: object-form `get` fills defaults, `set`
   * merges. Installed per test; the migration under test is a storage shape. */
  function installFakeChrome(sync: Record<string, unknown>, local: Record<string, unknown>) {
    const area = (store: Record<string, unknown>) => ({
      async get(defaults: Record<string, unknown>) {
        const out: Record<string, unknown> = {}
        for (const key of Object.keys(defaults)) {
          out[key] = key in store ? store[key] : defaults[key]
        }
        return out
      },
      async set(values: Record<string, unknown>) {
        Object.assign(store, values)
      },
    })
    ;(globalThis as { chrome?: unknown }).chrome = {
      storage: { sync: area(sync), local: area(local) },
    }
    return { sync, local }
  }

  it('wraps a pre-multi-instance install as one instance, token staying local', async () => {
    const { sync, local } = installFakeChrome(
      { baseUrl: 'https://example.com/api' },
      { token: 'vmcp_secret' },
    )
    const { loadInstances, loadSettings } = await import('./client')

    const all = await loadInstances()
    expect(all.instances).toEqual([
      { id: 'default', label: 'example.com', baseUrl: 'https://example.com/api' },
    ])
    expect(all.tokens).toEqual({ default: 'vmcp_secret' })
    // the migrated shape is written back, and the legacy keys survive so a
    // rollback build still finds its settings
    expect(sync.instances).toBeTruthy()
    expect(local.tokens).toEqual({ default: 'vmcp_secret' })
    expect(sync.baseUrl).toBe('https://example.com/api')

    const flat = await loadSettings()
    expect(flat).toEqual({
      baseUrl: 'https://example.com/api',
      token: 'vmcp_secret',
      instanceId: 'default',
    })
  })

  it('a fresh install gets the local default instance', async () => {
    installFakeChrome({}, {})
    const { loadSettings, DEFAULTS } = await import('./client')
    const flat = await loadSettings()
    expect(flat.baseUrl).toBe(DEFAULTS.baseUrl)
    expect(flat.token).toBe('')
  })

  it('saveSettings edits the ACTIVE instance and its token only', async () => {
    const { local } = installFakeChrome(
      {
        instances: [
          { id: 'a', label: 'defense', baseUrl: 'https://example.com/api' },
          { id: 'b', label: 'ai', baseUrl: 'https://ai.example.com/api' },
        ],
        activeId: 'b',
      },
      { tokens: { a: 'tok-a', b: 'tok-b' } },
    )
    const { loadSettings, saveSettings } = await import('./client')
    await saveSettings({ token: 'tok-b2' })
    expect((local.tokens as Record<string, string>).b).toBe('tok-b2')
    expect((local.tokens as Record<string, string>).a).toBe('tok-a')
    const flat = await loadSettings()
    expect(flat).toEqual({ baseUrl: 'https://ai.example.com/api', token: 'tok-b2', instanceId: 'b' })
  })
})

describe('EXT_CHANNEL', () => {
  // The default is the one setting a store install never edits, so the wrong
  // channel here is either a store build pointed at a localhost nothing runs
  // on, or a dev checkout silently annotating against the live corpus.
  it('a store build defaults at local dev too', async () => {
    vi.stubEnv('VITE_EXT_CHANNEL', 'store')
    vi.resetModules()
    const store = await import('./client')
    expect(store.EXT_CHANNEL).toBe('store')
    expect(store.DEFAULTS.baseUrl).toBe(store.PRESETS.local)
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it('a dev build stays on the Vite proxy, which ext-smoke relies on', async () => {
    vi.resetModules()
    const dev = await import('./client')
    expect(dev.EXT_CHANNEL).toBe('dev')
    expect(dev.DEFAULTS.baseUrl).toBe('http://127.0.0.1:5173/api')
  })
})
