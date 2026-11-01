import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

/**
 * Light, Dark, System -- and System is the default.
 *
 * ONE CHOICE ACROSS EVERY PROPERTY. The value lives in a cookie, scoped to
 * `SHARE_DOMAIN` when that is set, so the hub and every instance under it read
 * what the reader picked on any of them. `localStorage` is a per-origin mirror: it answers the
 * first load after the cookie arrives, and it answers when cookies are refused.
 * The precedence rule, the cookie's attributes and what Safari costs are in
 * docs/adr/0177.
 *
 * SYSTEM IS A STORED VALUE, not an absence. With absence meaning System, a
 * reader who chose System here would have it undone by another origin's stale
 * mirror healing the shared cookie back to an explicit choice.
 *
 * SYSTEM IS STILL THE ABSENCE OF THE ATTRIBUTE, which is a different question.
 * `tokens.css` declares `color-scheme: light dark` on the bare `:root` and
 * overrides it in `:root[data-theme='light'|'dark']`, so removing the attribute
 * IS System -- there is no third rule and no JavaScript resolves it. That is why
 * `<html>` in `index.html` ships NO `data-theme`: with one, the pre-boot
 * document is pinned for everybody and the unstyled first moment disagrees with
 * the operating system.
 */
export type ThemeChoice = 'light' | 'dark' | 'system'

/** Shared with `public/theme-boot.js`, `hub/hub/pages.py`
 *  and the consent screen in `api/routers/oauth.py`, none of which can import
 *  it. `tests/test_theme_boot.py` is what holds them together. */
const KEY = 'venona.theme'

/** Your registrable domain (`example.com`) when the hub and instances run on
 *  its subdomains; empty keeps the cookie host-only. A literal, and checked
 *  against the host rather than derived from it: the last two labels of a
 *  hosting provider's subdomain are a public suffix, which a browser rejects as
 *  a cookie Domain, so a derived value would simply never persist there. */
const SHARE_DOMAIN = ''

const YEAR_SECONDS = 31536000

/** The order the single cycling button walks. Light -> Dark -> System -> Light:
 *  the two explicit choices first, System last, so a reader who just wants the
 *  other palette gets there in one press from either side. */
const CYCLE: readonly ThemeChoice[] = ['light', 'dark', 'system'] as const

function isChoice(value: unknown): value is ThemeChoice {
  return value === 'light' || value === 'dark' || value === 'system'
}

function fromCookie(): ThemeChoice | null {
  let found: ThemeChoice | null = null
  try {
    /* LAST match, not first. RFC 6265 orders same-name cookies by longer path
       then EARLIER creation, so a stale host-only cookie sorts ahead of the
       shared one. */
    for (const pair of document.cookie.split(';')) {
      const eq = pair.indexOf('=')
      if (eq > 0 && pair.slice(0, eq).trim() === KEY) {
        const value = pair.slice(eq + 1).trim()
        if (isChoice(value)) found = value
      }
    }
  } catch {
    /* sandboxed frame or cookies refused; the mirror answers instead */
  }
  return found
}

function fromMirror(): ThemeChoice | null {
  try {
    const raw = localStorage.getItem(KEY)
    return isChoice(raw) ? raw : null
  } catch {
    /* private mode; System is the right answer when we cannot remember */
    return null
  }
}

/** The cookie first, this origin's mirror second. Its own `try` each, because
 *  the cookie read can throw and a shared guard would skip the fallback. */
function load(): ThemeChoice {
  return fromCookie() ?? fromMirror() ?? 'system'
}

function writeCookie(choice: ThemeChoice): void {
  try {
    const secure = location.protocol === 'https:' ? '; secure' : ''
    /* The host-only spelling goes first. Two cookies of one name at different
       scopes are indistinguishable in `document.cookie`, and once both exist
       there is no recovering from it. */
    document.cookie = `${KEY}=; path=/; max-age=0${secure}`
    const host = location.hostname
    const scoped = host === SHARE_DOMAIN || host.endsWith(`.${SHARE_DOMAIN}`)
    document.cookie =
      `${KEY}=${choice}; path=/; max-age=${YEAR_SECONDS}; samesite=lax` +
      (scoped ? `; domain=${SHARE_DOMAIN}` : '') +
      secure
  } catch {
    /* cookies refused; the mirror still carries the choice on this origin */
  }
}

/**
 * Write the choice to the document, to the shared cookie, and to the mirror.
 *
 * Every load rewrites both, which is what refreshes the cookie's `Max-Age`
 * against Safari's seven-day cap on script-written storage, and what heals a
 * cookie that was answered from the mirror.
 */
function apply(choice: ThemeChoice): void {
  if (choice === 'system') delete document.documentElement.dataset.theme
  else document.documentElement.dataset.theme = choice
  writeCookie(choice)
  try {
    localStorage.setItem(KEY, choice)
  } catch {
    /* quota or private mode; the document and the cookie are already correct */
  }
}

export const useThemeStore = defineStore('theme', () => {
  const choice = ref<ThemeChoice>(load())

  /* `flush: 'sync'` is not needed and would be wrong -- nothing reads the
     attribute synchronously after a click. But `immediate` IS: the boot script
     set the attribute from the same cookie, and if the two ever disagree (a
     corrupt value, a cookie written by hand) the store's reading is the one that
     should win, rather than the document keeping a state nothing can name. */
  watch(choice, apply, { immediate: true })

  /**
   * What System currently resolves to.
   *
   * Only for the toggle's accessible name -- "System (currently dark)" -- which
   * is the one place the app has to SAY what the browser worked out. Nothing
   * else reads it, and nothing should: every surface in the app resolves System
   * through `color-scheme` with no JavaScript, and a second answer computed here
   * is a second answer that can be wrong.
   */
  const systemResolves = ref<'light' | 'dark'>('dark')
  if (typeof window !== 'undefined') {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    systemResolves.value = media.matches ? 'dark' : 'light'
    media.addEventListener('change', (event) => {
      systemResolves.value = event.matches ? 'dark' : 'light'
    })

    /* A cookie fires no storage event, so a tab open while the reader chooses on
       another property has to look again. All three matter: `focus` is the only
       one for two tabs side by side in separate windows, `visibilitychange` is
       the one for tabs in the same window, and `pageshow` is the one for a
       bfcache restore, which resurrects a page without running the boot script.
       Writing the same value back is a no-op, so this cannot feed back. */
    const follow = (): void => {
      choice.value = load()
    }
    window.addEventListener('focus', follow)
    window.addEventListener('pageshow', follow)
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) follow()
    })
  }

  /** What is actually on screen, whichever way it was decided. */
  const resolved = computed<'light' | 'dark'>(() =>
    choice.value === 'system' ? systemResolves.value : choice.value,
  )

  const next = computed<ThemeChoice>(
    () => CYCLE[(CYCLE.indexOf(choice.value) + 1) % CYCLE.length] ?? 'system',
  )

  function cycle(): void {
    choice.value = next.value
  }

  function set(value: ThemeChoice): void {
    choice.value = value
  }

  return { choice, resolved, systemResolves, next, cycle, set }
})
