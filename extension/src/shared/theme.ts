/**
 * Which theme Venona's own chrome renders in, inside somebody else's browser.
 *
 * TWO INDEPENDENT QUESTIONS, and conflating them is the mistake this file
 * exists to keep separate:
 *
 *   GROUND  what colour is the article we landed on. MEASURED, by
 *           `content/ground.ts`, because a white article stays white for a
 *           reader whose OS is in dark mode. It decides the underline hues
 *           spliced into the host page's own prose, and nothing here touches it.
 *   THEME   which palette the reader chose for Venona. It decides the hover
 *           card, the options page and the toolbar badge -- surfaces that are
 *           Venona's, wherever they happen to be drawn.
 *
 * A dark card on a white article is correct. Light underlines on a dark article
 * are not. That is the whole distinction.
 *
 * WHY THIS IS STORED RATHER THAN ASKED.
 *
 * The card and the options page have a DOM, so `color-scheme: light dark` in
 * the generated token block resolves System for them with no JavaScript at all
 * -- everything below is only needed for the two EXPLICIT choices. The badge is
 * the exception and it is the reason this file exists: an MV3 service worker has
 * no DOM and no `matchMedia`, so `chrome.action.setBadgeBackgroundColor` cannot
 * ask the browser which side of `light-dark()` applies. It needs a resolved
 * answer, and the only resolved answer available to it is a stored one.
 *
 * Under System with the app never opened, the badge takes the dark side. That is
 * the brand aqua, which Chrome auto-contrasts its own badge text against.
 */

export type ThemeChoice = 'light' | 'dark' | 'system'

/** `chrome.storage.sync`, beside `baseUrl` -- a preference, not a secret, and it
 *  should follow the reader to another machine the way the rest of the options
 *  do. */
export const THEME_KEY = 'theme'

function isChoice(value: unknown): value is ThemeChoice {
  return value === 'light' || value === 'dark' || value === 'system'
}

export async function readTheme(): Promise<ThemeChoice> {
  const stored = await chrome.storage.sync.get({ [THEME_KEY]: 'system' })
  const value: unknown = stored[THEME_KEY]
  // Anything unrecognised is System rather than a throw. This key is written by
  // a content script copying a value out of the app's localStorage, which a
  // reader can edit in a devtools console; a bad value should degrade to the
  // default, not break the badge.
  return isChoice(value) ? value : 'system'
}

/**
 * Stamp the choice onto an element, as an INLINE `color-scheme`.
 *
 * Inline rather than a `data-` attribute with a stylesheet rule behind it, and
 * the card is what forces it. Its host element carries
 * `style="all: initial; position: fixed; …"` in the OUTER tree, so that a host
 * page's `div { margin: … }` cannot shift a card anchored to it -- and an inline
 * declaration outranks every author rule, `:host([data-…])` included. An
 * attribute-driven override would be generated, matched and lost.
 *
 * So there is one mechanism, and it is the one that always wins. The generated
 * token blocks declare `color-scheme: light dark` -- System, the default -- and
 * this overwrites it only for an explicit choice.
 *
 * `light dark`, not `''`, for System: clearing the property would fall back to
 * whatever the element's own inline `all: initial` left behind, which is
 * `normal` -- and under `normal`, `light-dark()` returns the LIGHT side. Writing
 * System explicitly is what makes returning to it a real transition rather than
 * a silent pin to light.
 */
export function applyTheme(element: HTMLElement, choice: ThemeChoice): void {
  element.style.colorScheme = choice === 'system' ? 'light dark' : choice
}

/** Follow the stored choice for as long as the surface is alive.
 *
 * Returns immediately having scheduled the first read; the caller does not have
 * to await anything, because every surface here renders correctly under System
 * before the first read lands. */
export function watchTheme(onChange: (choice: ThemeChoice) => void): void {
  void readTheme().then(onChange)
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'sync' || !(THEME_KEY in changes)) return
    const next: unknown = changes[THEME_KEY]?.newValue
    onChange(isChoice(next) ? next : 'system')
  })
}
