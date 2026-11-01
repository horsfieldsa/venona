import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

import { useDismissable } from '@/composables/useKeyboard'
import type { GraphSpacing } from '@/composables/useCytoscape'

/**
 * Chrome state that outlives a view.
 *
 * These flags live here rather than in the views for one reason: the chrome has
 * to collapse the app header and the footer, and those are rendered by
 * `AppShell`, above the router. A view cannot reach them.
 *
 * IMPORTANT: a collapse is expressed purely as sizes -- grid track sizes on
 * `GraphView`, the bars' own heights on `AppShell`. It must never be a `v-if`
 * on an ancestor of `<RouterView>`, a `<Teleport>`, or a re-parent for
 * `requestFullscreen()` -- any of those unmount `GraphView`, which calls
 * `destroyCytoscape()`, so pressing `f` would wipe the exploration the user
 * pressed it to look at, and any of them takes `ContextBar`'s teleport targets
 * with it, which throws.
 *
 * TWO FLAGS COLLAPSE THE CHROME AND ONLY ONE OF THEM IS A MODE.
 *
 * `fullscreen` is deliberate: the reader asked for it, so it is dismissable and
 * `Esc` leaves. `chromeGone` is the brief receding under a reader who is simply
 * reading down the page; it is a rendering detail they never asked for, so it
 * must NEVER reach the dismissable stack. `useDismissable` pops the topmost
 * entry only, so a value that pushed and popped on every change of scroll
 * direction would sit on top of an open dialog and eat that dialog's `Esc`.
 *
 * AND THE RECEDE IS A LENGTH, NOT A FLAG. A boolean can only be handed to a CSS
 * transition, which is a clock -- so the bars left on their own schedule after
 * the reader had stopped, which is 86px of movement they did not ask for and
 * cannot steer. `chromeGone` is how far the bars have been lifted, in pixels,
 * and the reader's own scrolling is what moves it.
 */
/* Bump the version when the SHAPE changes. A stale value under a reused key is
 * spread over the new defaults and silently keeps `graphRailLeft` open. */
const KEY = 'venona.ui.2'

/** `--motion-base`. Duplicated in TypeScript because the one movement the app
 *  runs by hand is this one, and it has to match the curve every other
 *  transition uses or the chrome reads as a different component. */
const MOTION_BASE_MS = 120

interface Persisted {
  /**
   * The graph's filter rail, and ONLY the graph's.
   *
   * It starts collapsed: the graph opens on the whole corpus and a 272px column
   * of filters is not what someone arriving at it needs first. It was briefly
   * the shared `railLeft`, which Settings also uses -- and there the left rail
   * is the document list, the entire navigation of the view. Collapsing that by
   * default hid every editable document behind a chevron.
   */
  graphRailLeft: boolean
  graphRailRight: boolean
  /** Settings' document list. Open by default; it is that view's navigation. */
  railLeft: boolean
  graphSpacing: GraphSpacing
}

const DEFAULTS: Persisted = {
  graphRailLeft: false,
  graphRailRight: true,
  railLeft: true,
  graphSpacing: 'normal',
}

function load(): Persisted {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return { ...DEFAULTS, ...JSON.parse(raw) }
  } catch {
    /* private mode or corrupt value; the defaults are fine */
  }
  return { ...DEFAULTS }
}

export const useUiStore = defineStore('ui', () => {
  const saved = load()

  const railLeft = ref(saved.railLeft)
  const graphRailLeft = ref(saved.graphRailLeft)
  const graphRailRight = ref(saved.graphRailRight)

  /** How much room the graph's layout gives each node. Chrome, not content --
   *  it changes how the graph is DRAWN, not what is shown, which is why it
   *  lives here and not in the route's query string. */
  const graphSpacing = ref<GraphSpacing>(saved.graphSpacing)

  /** Deliberately NOT persisted. Reloading into a chromeless window with no
   *  visible way out is a trap; `f` is cheap to press again. */
  const fullscreen = ref(false)

  /**
   * How much of the top chrome is currently AWAY, in pixels.
   *
   * A LENGTH, not a flag, and that is the whole of the change from a header
   * that snapped: the bars are lifted by however far the reader has scrolled,
   * so the chrome leaves at exactly the rate their fingers move and never on a
   * timer of its own. Nothing here is transitioned in CSS -- a transition is a
   * clock, and a clock cannot follow a wheel.
   *
   * Owned by whichever view installs `useChromeRecede`, which must return it to
   * zero on deactivate: a leaked lift strands every OTHER route without its
   * header. Not persisted and not dismissable, for the reasons above.
   */
  const chromeGone = ref(0)

  /**
   * The two bars' NATURAL heights: how far the lift is allowed to go.
   *
   * MEASURED, never `--nav-height` plus `--subnav-height`. The sub-menu wraps
   * to two rows on a phone, takes no height at all on a route with nothing to
   * say, and grows a row when a view teleports a long control set into it -- so
   * the token pair is the right answer in exactly one layout, and being 38px
   * short of the truth leaves a strip of bar stuck to the top of the window.
   *
   * Read from the elements at the moment the lift is about to move, rather than
   * watched. A `ResizeObserver` is the obvious tool and it is the wrong one
   * here: it delivers at the end of a rendering update, so the first value it
   * ever reports is the one from before the route filled the sub-menu in, and
   * on a tab that is not painting it reports nothing at all. Measured 2026-09-02: an
   * observer installed at mount had the header at 48 and the sub-menu at 0 for
   * the life of the page, so the chrome stopped 38px short of gone. Their own
   * heights cannot be disturbed by the lift -- a negative margin does not
   * resize the box it is on -- so there is no feedback to guard against.
   */
  let navEl: HTMLElement | null = null
  let contextEl: HTMLElement | null = null
  let chromeObserver: ResizeObserver | null = null
  const chromeNav = ref(0)
  const chromeContext = ref(0)
  const chromeHeight = computed(() => chromeNav.value + chromeContext.value)

  /**
   * `AppShell` owns both bars; nothing else may claim them.
   *
   * The observer is a TRIGGER and never a source. The note above says why its
   * value cannot be trusted, and nothing here reads one: it only says "that
   * box is a different size now", and the height is then read imperatively the
   * way every other path reads it. It also only acts in full screen, where the
   * lift is a fixed decision rather than a gesture being followed.
   *
   * Without it the lift is whatever the bar measured at the moment `f` was
   * pressed, and the ContextBar keeps changing after that -- the graph's
   * identity grows as counts arrive, and on a phone it wraps. A bar that grew
   * by a row leaves that row stuck to the top of the window with the canvas
   * under it and the exit control drawn over it.
   *
   * A negative margin does not resize the box it is on, so the paint below
   * cannot retrigger this.
   */
  function bindChromeBars(nav: HTMLElement | null, context: HTMLElement | null): void {
    navEl = nav
    contextEl = context
    chromeObserver?.disconnect()
    chromeObserver = null
    if (typeof ResizeObserver !== 'undefined' && (nav || context)) {
      chromeObserver = new ResizeObserver(() => {
        if (!fullscreen.value || chromeRevealed.value) return
        refreshChrome()
        setChromeGone(chromeHeight.value)
      })
      if (nav) chromeObserver.observe(nav)
      if (context) chromeObserver.observe(context)
    }
    refreshChrome()
  }

  /** Re-measure, and put the lift back inside whatever the new heights allow.
   *  A window narrow enough to wrap the sub-menu grows the chrome under a lift
   *  that is already at its old maximum. */
  function refreshChrome(): void {
    if (navEl) chromeNav.value = navEl.offsetHeight
    if (contextEl) chromeContext.value = contextEl.offsetHeight
    const clamped = Math.max(0, Math.min(chromeGone.value, chromeHeight.value))
    if (clamped === chromeGone.value) return
    chromeGone.value = clamped
    paint(clamped)
  }

  /** How far through the reading the reader is, 0..1. Zero on every route that
   *  is not something you read through, so the hairline is inert there. */
  const readProgress = ref(0)

  /**
   * The reader asking, at the top edge of the window, for the chrome back.
   *
   * Without it focus mode is a trap and the escape hatch cannot exist: the
   * reader can only lower the bars by scrolling, and the mode's whole job is to
   * ignore that -- so the switch that leaves it lives in a bar the mode has
   * made unreachable, which is the fault ADR 0118 recorded and did not fix.
   *
   * It is not a mode of its own: nothing persists it and `Esc` does not see it.
   * Outside focus mode it does not lapse either, and that is deliberate --
   * there is no remembered amount to snatch the chrome back to, so the bars
   * stay down until the reader's next scroll lifts them at their own rate.
   */
  const chromeRevealed = ref(false)

  /**
   * What surfaces that cannot be lifted read instead.
   *
   * The footer, the reading hairline and the toast stack are at the far edge or
   * are not chrome at all, so they have no travel to share -- they take the one
   * bit the lift can still tell them: the top chrome has entirely gone. It is
   * deliberately the END of the movement rather than a halfway mark, so nothing
   * flips while the bars are still on their way out.
   */
  const chromeCollapsed = computed(
    () => chromeHeight.value > 0 && chromeGone.value >= chromeHeight.value - 0.5,
  )

  /**
   * The lift reaches the DOM as three custom properties, written here.
   *
   * Written imperatively, on `<html>`, rather than bound into a template, and
   * both halves of that matter. IMPERATIVELY, because this runs inside a scroll
   * handler's frame and every consumer has to see the same lift in it -- the
   * two bars move and the scroller's padding cancels the movement, and a
   * binding lands a tick later, which is a frame with the bars in one position
   * and the reading compensated for another. ON `<html>`, because those
   * consumers are on three different branches of the tree and a custom property
   * is the only thing that reaches all of them without a prop drilled through
   * five components.
   *
   * The lift is SPLIT across the two bars because a grid track cannot go
   * negative: a single margin on the header slides both bars until the header's
   * own track reaches zero and then stops dead, measured 2026-09-02 at exactly 48px of the
   * 86 needed. Each bar gives up its own height and no more, which sums to the
   * whole and is why nothing here needs a wrapper element.
   */
  function paint(px: number): void {
    const root = document.documentElement.style
    const nav = Math.min(px, chromeNav.value)
    root.setProperty('--chrome-lift-nav', `${-nav}px`)
    root.setProperty('--chrome-lift-context', `${-(px - nav)}px`)
    root.setProperty('--chrome-gone', `${px}px`)
  }

  /** Set the lift and have it on screen before this call returns. Returns what
   *  was actually applied, which is not what was asked for at either end of the
   *  range: the caller may be several hundred pixels of scroll past the point
   *  where the bars ran out of travel. */
  function setChromeGone(px: number): number {
    cancelTween()
    refreshChrome()
    const next = Math.max(0, Math.min(px, chromeHeight.value))
    if (next === chromeGone.value) return next
    chromeGone.value = next
    paint(next)
    return next
  }

  /**
   * The lift for everything that is NOT a scroll.
   *
   * `f`, `Esc`, the pointer at the top edge and focus landing outside the
   * reading are all decisions rather than gestures: there is no finger to
   * follow, so these are the one place the movement gets a clock. 120ms on the
   * app's own curve, hand-run rather than handed to CSS, because a transition
   * on the same property would then be fighting every scroll frame for it.
   */
  let tween = 0
  function cancelTween(): void {
    if (tween) cancelAnimationFrame(tween)
    tween = 0
  }

  function tweenChromeGone(px: number): void {
    cancelTween()
    refreshChrome()
    const from = chromeGone.value
    const to = Math.max(0, Math.min(px, chromeHeight.value))
    if (to === from) return
    const started = performance.now()
    const step = () => {
      const t = Math.min(1, (performance.now() - started) / MOTION_BASE_MS)
      // The token's curve is `cubic-bezier(0.2, 0, 0.2, 1)`; this is its shape
      // to within a pixel over 86, and a solver for four control points would
      // be more code than the whole of the movement it serves.
      const eased = 1 - (1 - t) * (1 - t) * (1 - t)
      chromeGone.value = from + (to - from) * eased
      paint(chromeGone.value)
      tween = t < 1 ? requestAnimationFrame(step) : 0
    }
    tween = requestAnimationFrame(step)
  }

  /**
   * The two things that move the chrome without a reader's hand on it.
   *
   * Watches rather than calls inside `toggleFullscreen`, for the reason the
   * dismissable stack below is also a watch: `GraphView` clears the mode from
   * `onDeactivated`, and a path that bypassed this would leave the bars lifted
   * on the route the reader arrived at.
   *
   * Leaving either state lowers the bars all the way rather than returning them
   * to some remembered scroll amount. Nothing remembers one -- the lift IS the
   * state -- and handing the chrome back is the safe direction: the reader can
   * lift it again with the same gesture they already know, whereas a chrome
   * that snaps away as a cursor leaves the top of the window is movement nobody
   * asked for.
   */
  watch(
    fullscreen,
    (on) => {
      // Entering CLEARS the reveal, and without that the mode is a trap.
      // Reaching for the switch is focus landing outside the reading, which is
      // how every overlay in the app asks to be seen -- so the reveal is
      // already set by the time the mode turns on, the pointer edge has nothing
      // left to set, and the one gesture that brings the chrome back inside
      // focus mode does nothing at all. That is ADR 0118's trap, one layer up.
      if (on) chromeRevealed.value = false
      tweenChromeGone(on ? chromeHeight.value : 0)
    },
    { flush: 'sync' },
  )
  watch(
    chromeRevealed,
    (on) => {
      if (on) tweenChromeGone(0)
      else if (fullscreen.value) tweenChromeGone(chromeHeight.value)
    },
    { flush: 'sync' },
  )

  // Every persisted key has to appear HERE as well as in `DEFAULTS` -- this
  // writes a fixed shape, so a key added only to the defaults would be read back
  // correctly once and then dropped on the first rail toggle.
  watch([railLeft, graphRailLeft, graphRailRight, graphSpacing], () => {
    try {
      localStorage.setItem(
        KEY,
        JSON.stringify({
          railLeft: railLeft.value,
          graphRailLeft: graphRailLeft.value,
          graphRailRight: graphRailRight.value,
          graphSpacing: graphSpacing.value,
        } satisfies Persisted),
      )
    } catch {
      /* quota or private mode; rail state is a nicety */
    }
  })

  /** Settings' document list. It has one rail, so it takes no side -- a
   *  `side` parameter one caller always passes the same value for is an
   *  invitation to pass the other one and get silence. */
  function toggleRail() {
    railLeft.value = !railLeft.value
  }

  function toggleGraphRail(side: 'left' | 'right') {
    if (side === 'left') graphRailLeft.value = !graphRailLeft.value
    else graphRailRight.value = !graphRailRight.value
  }

  function toggleFullscreen() {
    fullscreen.value = !fullscreen.value
  }

  /** Leaving the graph must not strand the app without its header. */
  function exitFullscreen() {
    fullscreen.value = false
  }

  /**
   * Esc leaves full screen.
   *
   * It did not before: `Esc` is registered globally to pop the dismissable
   * stack, and full screen never pushed onto it -- so the only way out was `f`,
   * with the exit toggle living in the header that full screen hides. Nothing
   * on screen said so.
   *
   * Driven by a WATCH, never an imperative push inside `toggleFullscreen`.
   * `GraphView` calls `exitFullscreen()` from `onDeactivated` on every
   * navigation away, which would bypass an imperative pop and leave a dead
   * entry on top of the stack -- killing Esc for every dialog and palette in
   * the app. A watch fires on both paths.
   */
  const dismissable = useDismissable(exitFullscreen)
  watch(fullscreen, (on) => (on ? dismissable.push() : dismissable.pop()))

  return {
    railLeft, graphRailLeft, graphRailRight, fullscreen, graphSpacing,
    chromeGone, chromeNav, chromeContext, chromeHeight,
    readProgress, chromeRevealed, chromeCollapsed,
    setChromeGone, tweenChromeGone, bindChromeBars, refreshChrome,
    toggleRail, toggleGraphRail, toggleFullscreen, exitFullscreen,
  }
})
