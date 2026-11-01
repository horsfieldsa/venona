/** Settings, and an honest error when they are wrong.
 *
 * There is no diagnostic wizard here on purpose. But "it doesn't work" has
 * three distinguishable causes -- an unreachable base URL, an origin the API
 * will not answer, and a token it will not accept -- and they present
 * identically in a console. So the test button reports the status code and the
 * server's own message verbatim rather than paraphrasing them.
 *
 * The same rule now governs two more surfaces:
 *
 *   - THE SERVER'S AUTH MODE. An empty token box is not self-explanatory. On a
 *     `make dev` server it means "none needed" and on a production one it means
 *     "you have not configured this yet", and the box looks identical either
 *     way. `GET /auth/mode` is unauthenticated (it is in `EXEMPT_PATHS`) so it
 *     can always be asked, and the answer is rendered as a sentence.
 *   - THE SITE LIST. With auth on and no token yet, the catalog fetch is a 401,
 *     and an empty list reads as "the server has no sources" -- the identical
 *     defect one section down. It says which failure it was.
 */

import {
  apiGet,
  authBlockedKey,
  DEFAULTS,
  type DirectoryInstance,
  errorText,
  fetchDirectory,
  HUB_DEFAULT,
  HUB_URL_KEY,
  instanceFromDirectory,
  type InstanceSettings,
  loadInstances,
  loadTokenMeta,
  originPatternFor,
  PRESETS,
  probeCapability,
  saveCapability,
  saveInstances,
  saveTokenMeta,
} from '../shared/client'
import { pair } from '../shared/pairing'
import {
  type AnnotatableSite,
  type Catalog,
  enabledPatternFor,
  fetchCatalog,
  loadCatalog,
  loadEnabled,
  loadPaused,
  saveEnabled,
  savePaused,
} from '../shared/sites'
import { installFonts } from '../shared/fonts'
import { applyTheme, watchTheme } from '../shared/theme'

interface AuthMode {
  enabled: boolean
  google: boolean
  token: boolean
}

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T

const baseUrl = $<HTMLInputElement>('baseUrl')
const token = $<HTMLInputElement>('token')
const instancePicker = $<HTMLSelectElement>('instancePicker')
const instanceLabel = $<HTMLInputElement>('instanceLabel')

/** The list being edited. The picker's selection IS the active instance --
 * selecting is switching, which is the whole point of the control. */
let editing: InstanceSettings = { instances: [], activeId: '', tokens: {} }

function selected() {
  return editing.instances.find((i) => i.id === editing.activeId) ?? editing.instances[0]
}

function renderPicker(): void {
  instancePicker.replaceChildren(
    ...editing.instances.map((i) => {
      const option = document.createElement('option')
      option.value = i.id
      option.textContent = i.label || i.baseUrl
      option.selected = i.id === editing.activeId
      return option
    }),
  )
  const current = selected()
  instanceLabel.value = current?.label ?? ''
  baseUrl.value = current?.baseUrl ?? DEFAULTS.baseUrl
  token.value = editing.tokens[current?.id ?? ''] ?? ''
}
const status = $<HTMLParagraphElement>('status')
const sitesStatus = $<HTMLParagraphElement>('sitesStatus')
const sitesList = $<HTMLUListElement>('sites')

/** The five hosts that shipped in `manifest.json`'s `content_scripts.matches`.
 *
 * Kept ONLY to label them. `matches` grants injection, not host permission, so
 * `permissions.contains` is false for them and there is nothing to migrate
 * silently -- someone upgrading sees five familiar names and no explanation for
 * why they went quiet. This is the explanation.
 */
const PREVIOUSLY_BUILT_IN = new Set([
  'defensescoop.com',
  'breakingdefense.com',
  'defensenews.com',
  'defenseone.com',
  'c4isrnet.com',
])

function say(message: string, tone: 'ok' | 'bad' | 'busy'): void {
  status.textContent = message
  status.dataset.tone = tone
}

function saySites(message: string, tone: 'ok' | 'bad' | 'busy' | ''): void {
  sitesStatus.textContent = message
  sitesStatus.dataset.tone = tone
}

// ── the directory ───────────────────────────────────────────────────

const hubUrl = $<HTMLInputElement>('hubUrl')
const directoryStatus = $<HTMLParagraphElement>('directoryStatus')
const directoryList = $<HTMLUListElement>('directory')

function sayDirectory(message: string, tone: 'ok' | 'bad' | 'busy' | ''): void {
  directoryStatus.textContent = message
  directoryStatus.dataset.tone = tone
}

function directoryRow(entry: DirectoryInstance): HTMLLIElement {
  const row = document.createElement('li')

  const name = document.createElement('span')
  name.className = 'domain'
  name.textContent = entry.display_name

  const meta = document.createElement('span')
  meta.className = 'meta'
  // Offline rows stay addable on purpose: the hub lists them precisely so
  // presets survive an outage.
  meta.textContent = [entry.tagline, entry.status === 'offline' ? 'offline right now' : null]
    .filter(Boolean)
    .join(' · ')

  const already = editing.instances.some((i) => i.id === entry.slug)
  const add = document.createElement('button')
  add.type = 'button'
  add.className = 'pause'
  add.textContent = already ? 'Added' : 'Add'
  add.disabled = already
  add.addEventListener('click', () => {
    const candidate = instanceFromDirectory(entry)
    const pattern = originPatternFor(candidate.baseUrl)
    // Started before any await -- `permissions.request` needs the live gesture.
    const granting = pattern
      ? chrome.permissions.request({ origins: [pattern] }).catch(() => false)
      : Promise.resolve(false)
    void (async () => {
      const granted = await granting
      editing.instances.push(candidate)
      editing.tokens[candidate.id] = ''
      await saveInstances(editing)
      renderPicker()
      add.textContent = 'Added'
      add.disabled = true
      sayDirectory(
        granted
          ? `added ${candidate.label}; select it above and connect`
          : `added ${candidate.label}, but its host permission was declined; grant it on Save`,
        granted ? 'ok' : 'bad',
      )
    })()
  })

  row.append(name, meta, add)
  return row
}

$<HTMLButtonElement>('browseDirectory').addEventListener('click', () => {
  const hub = (hubUrl.value.trim() || HUB_DEFAULT).replace(/\/+$/, '')
  const pattern = originPatternFor(hub)
  // Started before any await -- same gesture rule as everywhere else here.
  const granting = pattern
    ? chrome.permissions.request({ origins: [pattern] }).catch(() => false)
    : Promise.resolve(false)
  void (async () => {
    if (!(await granting)) {
      sayDirectory('permission for the directory was declined', 'bad')
      return
    }
    await chrome.storage.sync.set({ [HUB_URL_KEY]: hub })
    sayDirectory('asking the directory…', 'busy')
    try {
      const entries = await fetchDirectory(hub)
      directoryList.replaceChildren(...entries.map(directoryRow))
      sayDirectory(
        entries.length
          ? `${entries.length} listed`
          : 'nothing listed yet',
        entries.length ? '' : 'busy',
      )
    } catch (err) {
      sayDirectory(`could not reach the directory: ${errorText(err)}`, 'bad')
    }
  })()
})

function settingsFromForm() {
  return {
    baseUrl: (baseUrl.value.trim() || DEFAULTS.baseUrl).replace(/\/+$/, ''),
    token: token.value.trim(),
    instanceId: selected()?.id ?? 'default',
  }
}

// ── what the server says about itself ───────────────────────────────

async function renderAuthMode(): Promise<void> {
  const element = $<HTMLParagraphElement>('authMode')
  const pairingRow = $<HTMLDivElement>('pairingRow')
  let mode: AuthMode
  try {
    mode = await apiGet<AuthMode>(settingsFromForm(), '/auth/mode')
  } catch {
    // Unreachable is already the Test button's job to explain; saying it twice
    // in different words is worse than saying it once.
    element.textContent = ''
    pairingRow.hidden = true
    return
  }

  if (!mode.enabled) {
    element.textContent =
      'This server has authentication turned off, so no token is needed. That is what `make dev` runs.'
    pairingRow.hidden = true
    return
  }

  pairingRow.hidden = !mode.google
  if (mode.google) {
    // A Google session cannot ride a cross-origin request (contract C12 pins
    // `allow_credentials=False`), so connecting runs the server's own OAuth
    // flow and lands a scoped bearer of the extension's own -- shared/pairing.ts.
    $<HTMLSpanElement>('pairHint').textContent =
      'Sign in with the Google account you use for this domain.'
  }

  const meta = token.value.trim() ? await loadTokenMeta(settingsFromForm().instanceId) : null
  if (meta) {
    const until = new Date(meta.expiresAt)
    element.textContent =
      until.getTime() > Date.now()
        ? `Connected until ${until.toLocaleDateString()}. Reconnect any time, and you can end this connection under Settings, Agents in Venona.`
        : 'Your connection has ended; press Connect with Google to reconnect.'
    return
  }
  // The no-sign-in branch names a terminal command AT A READER, and that is the
  // right trade only because it is the branch where signing in is not offered:
  // somebody on this server has to type it either way, and naming it is how the
  // reader knows what to ask for. Where sign-in exists, it leads.
  element.textContent = mode.google
    ? 'This server uses Google sign-in. Connect below, or paste a key instead.'
    : 'This server has no sign-in configured, so it needs a key. Whoever runs it can make one with `venona mcp token new --scope annotate`.'
}

// ── the site list ───────────────────────────────────────────────────

async function granted(pattern: string): Promise<boolean> {
  try {
    return await chrome.permissions.contains({ origins: [pattern] })
  } catch {
    return false
  }
}

function siteRow(
  site: AnnotatableSite,
  isGranted: boolean,
  isPaused: boolean,
  /** The pattern this row actually controls -- the catalog's wildcard, or the
   *  narrower origin the toolbar granted. See `enabledPatternFor`. */
  pattern: string,
): HTMLLIElement {
  const row = document.createElement('li')

  const label = document.createElement('label')
  const box = document.createElement('input')
  box.type = 'checkbox'
  box.checked = isGranted
  box.addEventListener('change', () => void toggleSite(site, box, pattern))

  const name = document.createElement('span')
  name.className = 'domain'
  name.textContent = site.domain

  const meta = document.createElement('span')
  meta.className = 'meta'
  const bits = [site.publisher, `${site.documents} doc${site.documents === 1 ? '' : 's'}`]
  if (!site.configured) bits.push('found by a sweep')
  if (PREVIOUSLY_BUILT_IN.has(site.domain)) bits.push('was built in')
  meta.textContent = bits.filter(Boolean).join(' · ')

  label.append(box, name, meta)
  row.append(label)

  // Pausing is only meaningful once the permission exists, and offering it
  // before then would imply the site is running.
  if (isGranted) {
    const pause = document.createElement('button')
    pause.type = 'button'
    pause.className = 'pause'
    pause.textContent = isPaused ? 'Resume' : 'Pause'
    pause.title = isPaused
      ? 'Start annotating this site again'
      : 'Stop annotating without withdrawing permission'
    pause.addEventListener('click', () => void togglePause(pattern, isPaused))
    row.append(pause)
  }

  return row
}

/** What is on screen right now, readable WITHOUT awaiting.
 *
 * `chrome.permissions.request` must be called while the user gesture is still
 * live, and an `await` on any chrome API can spend it -- `toggleSite` carries
 * that warning and then broke it, reading `loadEnabled()` before asking. A
 * declined-because-no-gesture rejection is indistinguishable from a declined
 * prompt, so the failure reads as "I clicked it and it did not stick".
 *
 * `renderSites` has already paid for these reads, so the click handlers take
 * them from here and do their storage work AFTER the prompt returns.
 */
let shown: { catalog: Catalog | null; enabled: Set<string>; paused: Set<string> } = {
  catalog: null,
  enabled: new Set(),
  paused: new Set(),
}

async function renderSites(catalog: Catalog | null, note = ''): Promise<void> {
  sitesList.replaceChildren()
  if (!catalog) {
    shown = { catalog: null, enabled: new Set(), paused: new Set() }
    return
  }

  const [enabled, paused] = await Promise.all([loadEnabled(), loadPaused()])
  shown = { catalog, enabled: new Set(enabled), paused: new Set(paused) }
  const isPaused = new Set(paused)

  for (const site of catalog.sites) {
    // NOT `isEnabled.has(site.match)`. A site granted from the toolbar is stored
    // under its exact origin (`https://www.defensenews.com/*`), not the
    // catalog's wildcard, so asking for the catalog's spelling reported a
    // working site as off. `enabledPatternFor` finds whichever spelling is
    // actually there, and the row then toggles THAT pattern rather than one
    // nobody enabled.
    const active = enabledPatternFor(site.domain, enabled)
    // The browser is the authority on what is granted; the stored list is only
    // intent. Asking it here means a permission withdrawn from
    // `chrome://extensions` shows as unticked without any bookkeeping.
    const isGranted = active !== null && (await granted(active))
    sitesList.append(
      siteRow(site, isGranted, isPaused.has(active ?? site.match), active ?? site.match),
    )
  }

  const age = Date.now() - catalog.at
  const stale = age > 60_000 ? ` (cached ${Math.round(age / 60_000)} min ago)` : ''
  saySites(note || `${catalog.sites.length} sites${stale}`, note ? 'bad' : '')
}

async function refreshSites(): Promise<void> {
  saySites('asking Venona…', 'busy')
  try {
    await renderSites(await fetchCatalog(settingsFromForm()))
  } catch (err) {
    const code = (err as { status?: number }).status
    // The cached copy still renders underneath. A list the user has already
    // granted against should not vanish because the API blinked.
    const cached = await loadCatalog(settingsFromForm().instanceId)
    await renderSites(
      cached,
      code === 401
        ? 'the token was refused; set it above, then refresh'
        : `could not load the site list: ${errorText(err)}`,
    )
  }
}

async function toggleSite(
  site: AnnotatableSite,
  box: HTMLInputElement,
  pattern: string,
): Promise<void> {
  // `shown.enabled`, NOT `await loadEnabled()`. The read below the prompt is
  // free; this one was not -- it yielded before `permissions.request`, which is
  // exactly what the comment under it forbids. See `shown`.
  const enabled = new Set(shown.enabled)

  if (box.checked) {
    // FIRST, before any await. `permissions.request` requires a live user
    // gesture and rejects once the stack has yielded, and that rejection is
    // indistinguishable from the user saying no.
    let ok = false
    try {
      ok = await chrome.permissions.request({ origins: [pattern] })
    } catch (err) {
      saySites(`the browser refused that pattern: ${errorText(err)}`, 'bad')
      box.checked = false
      return
    }
    if (!ok) {
      box.checked = false
      saySites(`permission for ${site.domain} was declined`, 'bad')
      return
    }
    enabled.add(pattern)
  } else {
    enabled.delete(pattern)
    // Pausing a site whose permission has just been revoked would leave a stale
    // entry that silently suppresses it if the user re-grants later.
    await savePaused((await loadPaused()).filter((p) => p !== pattern))
    await chrome.permissions.remove({ origins: [pattern] })
  }

  // The worker reconciles off this write -- see `background.ts`. Saving is what
  // makes it take effect; there is no separate message.
  await saveEnabled([...enabled])
  await renderSites(await loadCatalog(settingsFromForm().instanceId))
}

async function togglePause(pattern: string, isPaused: boolean): Promise<void> {
  const paused = new Set(await loadPaused())
  if (isPaused) paused.delete(pattern)
  else paused.add(pattern)
  await savePaused([...paused])
  await renderSites(await loadCatalog(settingsFromForm().instanceId))
}

$<HTMLButtonElement>('refresh').addEventListener('click', () => void refreshSites())

$<HTMLButtonElement>('enableAll').addEventListener('click', () => {
  void (async () => {
    // `shown.catalog`, read synchronously: `loadCatalog()` yielded before
    // `permissions.request`, which can spend the user gesture. Same fix as
    // `toggleSite` -- see `shown`.
    const catalog = shown.catalog
    // SAYS SO. This was a bare `return`: with no catalog -- the API was down
    // when this page first opened, so the fetch failed and the cache stayed
    // empty -- the button did nothing at all, reported nothing, and left the
    // extension annotating nowhere with no indication why. A control that
    // silently declines to act is worse than one that is disabled.
    if (!catalog?.sites.length) {
      saySites('no site list yet; press Refresh first, then try again', 'bad')
      return
    }
    const wanted = catalog.sites.map((site) => site.match)
    // One request for the whole set, so the browser shows one prompt listing
    // every host rather than making the user approve fifteen in a row.
    let ok = false
    try {
      ok = await chrome.permissions.request({ origins: wanted })
    } catch (err) {
      saySites(`the browser refused that request: ${errorText(err)}`, 'bad')
      return
    }
    if (!ok) {
      saySites('permission was declined', 'bad')
      return
    }
    await saveEnabled(wanted)
    await renderSites(catalog)
  })()
})

// A permission withdrawn from `chrome://extensions` while this page is open
// should not leave a tick behind.
chrome.permissions.onRemoved.addListener(() => void renderSites(null).then(() => init()))

// ── connection ──────────────────────────────────────────────────────

async function init(): Promise<void> {
  // First, and before the first paint: `options.css` asks for the same two
  // families the app does and the extension shipped neither, so this page has
  // been rendering in the platform's mono since it was written.
  installFonts()

  editing = await loadInstances()
  renderPicker()
  $<HTMLSpanElement>('origin').textContent = `chrome-extension://${chrome.runtime.id}`

  const hubStored = await chrome.storage.sync.get({ [HUB_URL_KEY]: HUB_DEFAULT })
  hubUrl.value = String(hubStored[HUB_URL_KEY] || HUB_DEFAULT)

  await renderAuthMode()
  const cached = await loadCatalog(settingsFromForm().instanceId)
  if (cached) await renderSites(cached)
  else await refreshSites()
}

/** Make sure we may actually talk to the base URL before saving it.
 *
 * `host_permissions` names the local dev port and nothing else, so any other deployment was unreachable no matter what was typed here:
 * the field accepted it, the fetch failed with `TypeError: Failed to fetch`, and
 * nothing connected the two. `optional_host_permissions` covers every https
 * host, so the grant is available -- it just has to be asked for, from inside
 * this click. (Spelling that pattern out here would end this comment: it
 * contains the closing delimiter.)
 *
 * A declined prompt is not a failed save. The URL is still stored, because the
 * user may be about to grant it from `chrome://extensions`, and refusing to
 * remember what they typed would be its own small insult.
 */
async function ensureReachable(url: string): Promise<boolean> {
  const pattern = originPatternFor(url)
  if (!pattern) return false
  try {
    if (await chrome.permissions.contains({ origins: [pattern] })) return true
    return await chrome.permissions.request({ origins: [pattern] })
  } catch {
    // `http://` on a host that is not localhost is not covered by
    // `optional_host_permissions`, and asking throws rather than returning false.
    return false
  }
}

instancePicker.addEventListener('change', async () => {
  // Selecting IS switching: the toolbar sends to the active instance, and two
  // controls for "which one is selected here" versus "which one is live"
  // would drift apart the first time someone forgot the second click.
  editing.activeId = instancePicker.value
  await saveInstances(editing)
  renderPicker()
  await renderAuthMode()
  await refreshSites()
})

$<HTMLButtonElement>('addInstance').addEventListener('click', async () => {
  const id = `inst-${Date.now().toString(36)}`
  editing.instances.push({ id, label: 'new domain', baseUrl: DEFAULTS.baseUrl })
  editing.activeId = id
  await saveInstances(editing)
  renderPicker()
  say('added; set its label and address, then Save and connect', 'busy')
})

$<HTMLButtonElement>('removeInstance').addEventListener('click', async () => {
  if (editing.instances.length <= 1) {
    say('the last one cannot be removed; edit it instead', 'bad')
    return
  }
  const gone = selected()
  editing.instances = editing.instances.filter((i) => i.id !== gone.id)
  delete editing.tokens[gone.id]
  editing.activeId = editing.instances[0].id
  await saveInstances(editing)
  await saveTokenMeta(gone.id, null)
  await saveCapability(gone.id, null)
  renderPicker()
  await renderAuthMode()
  await refreshSites()
})

$<HTMLButtonElement>('save').addEventListener('click', async () => {
  const url = baseUrl.value.trim() || DEFAULTS.baseUrl
  const reachable = await ensureReachable(url)

  const current = selected()
  current.baseUrl = url
  current.label = instanceLabel.value.trim() || current.label
  const previousToken = editing.tokens[current.id] ?? ''
  editing.tokens[current.id] = token.value.trim()
  await saveInstances(editing)
  // A pasted token is not the paired one, so what pairing recorded about
  // expiry -- and what a probe learned about reach -- would now be describing
  // a different credential.
  if (token.value.trim() !== previousToken) {
    await saveTokenMeta(current.id, null)
    await saveCapability(current.id, null)
    if (token.value.trim()) await probeCapability(settingsFromForm())
  }
  renderPicker()
  if (reachable) {
    say('saved', 'ok')
  } else {
    say(
      `saved, but this browser has no permission for ${originPatternFor(url) ?? url}; ` +
        'grant it here or in chrome://extensions, or requests will fail as "could not reach"',
      'bad',
    )
  }

  // Both depend on the settings that just changed, and a stale "this server has
  // auth off" after pointing at a production API would be actively misleading.
  await renderAuthMode()
  await refreshSites()
})

// One click, because typing a URL from memory is how you get a silent typo.
// Local dev is in `host_permissions`, so choosing it never prompts.
for (const [id, url] of [
  ['useLocal', PRESETS.local],
] as const) {
  $<HTMLButtonElement>(id).addEventListener('click', () => {
    baseUrl.value = url
    say('not saved yet; press Save', 'busy')
  })
}

$<HTMLButtonElement>('pair').addEventListener('click', () => {
  const settings = settingsFromForm()
  say('opening the sign-in window…', 'busy')
  void (async () => {
    let paired
    try {
      paired = await pair(settings)
    } catch (err) {
      say(errorText(err, 'the sign-in was not completed'), 'bad')
      return
    }
    editing.tokens[settings.instanceId] = paired.token
    token.value = paired.token
    await saveInstances(editing)
    await saveTokenMeta(settings.instanceId, {
      scope: paired.scope,
      expiresAt: paired.expiresAt,
    })
    // No probe needed: the scope came back on the token response, and
    // annotate IS the limited shape.
    await saveCapability(settings.instanceId, paired.scope === 'annotate' ? 'limited' : 'full')
    // The block flag and the badge are the worker's, but the fix is this
    // page's: a re-pair must un-say "signed out" now, not on the next call.
    try {
      await chrome.storage.session.remove(authBlockedKey(settings.instanceId))
      await chrome.action.setBadgeText({ text: '' })
    } catch {
      /* the worker repaints on its next call either way */
    }
    say('connected', 'ok')
    await renderAuthMode()
    await refreshSites()
  })()
})

$<HTMLButtonElement>('test').addEventListener('click', async () => {
  say('trying…', 'busy')
  const settings = settingsFromForm()
  try {
    // `/health/live` is one of the paths in `EXEMPT_PATHS`, so it answers
    // without a token. Reaching it proves the URL and the origin are fine and
    // narrows any later 401 to the token alone.
    await apiGet<{ status: string }>(settings, '/health/live')
  } catch (err) {
    say(`cannot reach it: ${errorText(err)}`, 'bad')
    return
  }
  try {
    // A guarded path INSIDE the annotate scope, so this is the half of the
    // test that exercises the token. `/meta/ontology` sat here before and is
    // outside `ANNOTATE_REACH`, so a perfectly good scoped token reported as
    // broken -- the test must ask a question the expected credential can
    // answer.
    await apiGet<unknown>(settings, '/sources/sites')
    say('connected, and the token works', 'ok')
  } catch (err) {
    const status = (err as { status?: number }).status
    if (status === 401) {
      say('reachable, but the token was refused; connect with Google or paste a new token', 'bad')
    } else {
      say(`reachable, but: ${errorText(err)}`, 'bad')
    }
    return
  }
  // And remember what KIND of credential it is, so the card and the toolbar
  // offer only what will work. See `client.ts::probeCapability`.
  await probeCapability(settings)
})

/* Follow the reader's choice, the same one the app and the card use.
 *
 * Outside `init()` and not awaited: the generated `:root` block declares
 * `color-scheme: light dark`, so this page already renders System correctly
 * before the first storage read lands. There is nothing to flash away from and
 * nothing here that can fail in a way the settings form should wait on. */
watchTheme((choice) => {
  applyTheme(document.documentElement, choice)
})

void init()
