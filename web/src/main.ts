import { createPinia } from 'pinia'
import { createApp } from 'vue'

import { setForbiddenHandler, setUnauthorizedHandler } from '@/api/client'
import { useSessionStore } from '@/stores/session'
import { useToastsStore } from '@/stores/toasts'
import { trackFocusModality } from '@/lib/focusModality'
import App from '@/App.vue'
import { router } from '@/router'

/**
 * Typefaces, self-hosted.
 *
 * These are the project's first frontend dependencies, and they earn it: until
 * now `--font-ui: 'Inter var'` resolved to Liberation Sans and `--font-prose:
 * Charter` resolved to Liberation *Sans* too, falling through the whole stack
 * to generic `ui-serif` -- a Times clone. The app was designed against Inter
 * and Charter and had never rendered either.
 *
 * The weight axis is the part that matters most. Liberation Sans ships 400 and
 * 700 only, so every `font-weight: 500 / 600 / 650` in the codebase snapped or
 * synthesised: the app had two weights where the design assumed four, which is
 * why headings barely out-ranked body text no matter what the colour tokens
 * did. These are variable fonts, so the whole axis is real and costs one file.
 *
 * `index.css` carries `unicode-range` per subset, so a latin-only page fetches
 * only the latin file. Inter gets its italic too -- `prose.css` sets
 * blockquotes in italic, and a synthesised oblique is exactly the kind of
 * detail that reads as cheap without being nameable.
 *
 * TWO families now, not three. The terminal language sets the whole of the
 * chrome in JetBrains Mono (`--font-ui` AND `--font-mono` resolve to it) and
 * leaves Inter for the one surface that is read for minutes at a time. Source
 * Serif 4 is gone with the serif reading column it was bought for; do not name
 * a family that is not imported here, which is how `Inter var` and `Charter`
 * both silently resolved to Liberation Sans for the life of the project.
 */
import '@fontsource-variable/inter'
import '@fontsource-variable/inter/wght-italic.css'
import '@fontsource-variable/jetbrains-mono'

/* Order is load-bearing.
 *
 * `theme.css` pulls in Tailwind, and Tailwind pulls in preflight -- a reset
 * that zeroes border widths, unstyles headings and lists, and normalises form
 * controls. `base.css` has to come AFTER it or preflight silently undoes the
 * hand-drawn checkbox, the underlined number inputs and the focus ring, none
 * of which anything else would have caught.
 *
 * `tokens.css` comes first because `theme.css` reads every one of its values.
 * `prose.css` comes last because it is the reading surface and outranks the
 * chrome: `.prose` is deliberately NOT built out of utilities, so it has to be
 * able to win against them. */
import '@/styles/tokens.css'
import '@/styles/theme.css'
import '@/styles/base.css'
import '@/styles/prose.css'

/**
 * A 401 from ANY request bounces to the login page, carrying where you were.
 *
 * Wired here rather than in `client.ts` so the API layer never imports the
 * router — that would close the cycle router → views → stores → client. The
 * payoff is that not one of the nine stores needed editing: each already lets
 * its error propagate, so they all inherit this.
 */
setUnauthorizedHandler(() => {
  if (router.currentRoute.value.meta.public) return
  const next = router.currentRoute.value.fullPath
  void router.replace({ name: 'login', query: next === '/' ? {} : { next } })
})

/**
 * A 403 from ANY request says so once, and does not redirect.
 *
 * The 401 sibling above bounces to `/login`. This must not: a 403 means the
 * reader IS signed in and the page is theirs -- only the action was refused --
 * and sending them to sign in again would tell them to fix the one thing that is
 * not wrong, then return them with the same role.
 *
 * Wired here for the same reason the 401 hook is: without it, a refused write
 * surfaces at whichever of ~92 call sites the reader hit, and about fifty of
 * those are in views and components rather than stores. There is no store layer
 * that could have caught them.
 */
setForbiddenHandler((_path, refusal) => {
  if (refusal.code === 'subscription_required') {
    // A DIFFERENT REFUSAL, and the one case where a 403 is worth a redirect.
    // For a role refusal signing in again returns the same role, so there is
    // nothing to do and a bounce would loop. Here there IS something to do, and
    // the server has already written the two sentences that tell a lapsed
    // reader from somebody who never subscribed.
    useToastsStore().push({ kind: 'error', key: 'forbidden', text: refusal.message })
    void router.push({ name: 'settings-billing' })
    return
  }
  useToastsStore().push({
    kind: 'error',
    // Keyed because refusals can still arrive together -- several views on one
    // screen, a retry behind a slow response. The burst this was written for,
    // three admin-only requests fanning out at mount behind the optimistic
    // `isAdmin()`, is gone: every fetch now waits for the probe through
    // `session.isKnownAdmin()`. Keeping the key is cheap and the guarantee is
    // not one this file can make on its own.
    key: 'forbidden',
    text: 'This account can read Venona, but not change it.',
  })
})

/**
 * One session probe before mount, so a cold load of `/graph` lands on `/login`
 * instead of flashing an empty canvas and then bouncing.
 *
 * Deliberately NOT a `router.beforeEach` guard: that would put a round trip in
 * front of every route change to solve a problem the 401 hook already solves
 * for free, on the one navigation that actually needs it.
 */
async function start() {
  // Before mount and before the session probe: the focus ring in `base.css` is
  // gated on the attribute this sets, so a keypress during the probe's round
  // trip must already be observed. See `lib/focusModality.ts`.
  trackFocusModality()

  const app = createApp(App)
  // Pinia is installed HERE rather than in the `.use(...).use(...).mount()` chain
  // at the bottom, because the session probe below needs a store and a store
  // needs an active pinia. Same instance, one line earlier.
  app.use(createPinia())

  // The SAME probe that already existed, now also feeding the session store --
  // one round trip, reused rather than added. It has to run after `app.use(pinia)`
  // so the store exists, and before mount so a reader never sees an admin nav
  // flash and then vanish.
  //
  // THE ONLY THING FIRST PAINT WAITS ON. The session payload carries
  // `setup_required`, so the router's first-boot gate reads it from this one
  // probe -- health is the poll loop's business (`useAppPolling` ticks at
  // mount) and must never sit in front of a page: it probes Neo4j and the
  // claude CLI, and a slow probe was the whole app painting nothing.
  //
  // The probe never throws: a 401 is already handled by the hook above, and
  // anything else is the server being unreachable, which the app renders its
  // own error states for.
  await useSessionStore().load()

  /**
   * The last line. Without it a render-time exception in any view blanks that
   * subtree with nothing but a console message -- and on the brief, which is
   * the whole reading surface, "the page is empty" and "there was no news
   * today" look identical.
   *
   * Vue swallows the error either way; this makes it visible and keeps the rest
   * of the app usable, because the header and nav live outside the failed
   * subtree.
   */
  app.config.errorHandler = (err, _instance, info) => {
    console.error('[Venona] unhandled render error', info, err)
    useToastsStore().push({ kind: 'error', text: 'Something on this page failed to render. Try reloading.' })
  }

  // Anything that escapes a promise nobody awaited -- a store action fired with
  // `void`, a background refresh. Logged rather than toasted: these are often
  // benign cancellations, and a toast per aborted fetch would be worse than
  // silence.
  window.addEventListener('unhandledrejection', (event) => {
    console.error('[Venona] unhandled rejection', event.reason)
  })

  app.use(router).mount('#app')
}

void start()
