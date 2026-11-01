import { onActivated, onBeforeUnmount, onDeactivated, watch } from 'vue'

import { useUiStore } from '@/stores/ui'

/**
 * The chrome is lifted away by the reader's own scrolling, at a fixed rate.
 *
 * Not a threshold and not a transition. The bars are a band of the page that
 * happens to sit above the reading: scroll down and it travels up, scroll back
 * and it comes down again, and it only ever moves while the reader is moving.
 * There is no state to cross, so there is nothing to cross it suddenly.
 *
 * MEASURED, and on the VIEW'S OWN SCROLLER -- `ViewShell` with `scroll="page"`
 * sets `overflow-y: auto` on itself and `AppShell`'s content region is
 * `overflow: hidden`, so `window` never scrolls and a listener on it would
 * never fire. `GuideView`'s reading-line spy is the same shape and the same
 * rAF coalescing, for the same reason: a handler that measures on every scroll
 * event reads layout dozens of times a frame.
 *
 * NOTHING HERE MOVES THE READER, and that is the whole of the difficulty.
 * Lifting the bars makes the scroll box taller AT THE TOP, so the line under
 * the reader's eye would rise by the lift as well as by the scroll -- two
 * pixels of movement bought with one. The obvious answer is to hand each pixel
 * of lift back as a pixel of `scrollTop`, and it is the wrong one: an
 * assignment to `scrollTop` during momentum HALTS THE FLING. On a phone every
 * flick died at the moment the chrome began to move and came back to life once
 * it had gone, which is exactly the window this code writes in.
 *
 * So the cancellation lives in CSS instead. `ViewShell`'s scroller carries
 * `--chrome-gone` as top padding, the box gains the lift at its top edge and
 * the content gains the same back, and the reading holds still with no write at
 * all. It holds the scroll RANGE still as well, which is what makes the end of
 * a brief safe: the scrollable distance is the same at every lift, so there is
 * no limit for the browser to clamp `scrollTop` against and no phantom upward
 * delta to mistake for the reader.
 */

/**
 * How much bar moves per pixel scrolled.
 *
 * A THIRD, not one for one. At 1:1 the chrome is a band of the page and leaves
 * over exactly its own height -- which is a single wheel tick, so on a mouse
 * the whole 86px goes in one frame and the departure is as abrupt as the state
 * machine it replaced. The input is quantised at 100px; nothing tracking it
 * exactly can be smoother than it is.
 *
 * At a third it takes about 260px of reading, and the reader keeps moving down
 * the document the whole time: only the pixels the chrome actually takes are
 * cancelled, so two thirds of every gesture still advances the page.
 * At 1:1 the first 86px advance it not at all, which is its own kind of wrong
 * -- the page feels stuck to the top while the bars slide.
 *
 * The reading is exact at any rate. The lift moves `RATE * delta` and the
 * scroller's padding cancels the same, so the line under the reader's eye moves
 * by `delta` whatever this is set to. It is a feel, not a correctness knob.
 */
const CHROME_RATE = 1 / 3

/** A document has to have this much room to spare, over and above the chrome,
 *  before any of it is allowed to leave. There is nothing to gain on a page
 *  that fits: no reader on it is short of room, and all a rate below 1:1 can
 *  produce there is a bar stopped a third of the way up and staying there. A
 *  quiet-day brief and the first-run screen are both this case. */
const SLACK_PX = 80

/** How close to the top of the window the pointer has to come to ask for the
 *  chrome back. The escape hatch that keeps focus mode from being a trap. */
const EDGE_PX = 8

/**
 * @param get  The scroll container, as a getter -- `ViewShell` exposes its
 *             element through `defineExpose`, and it does not exist until the
 *             view has rendered its non-error branch.
 */
export function useChromeRecede(get: () => HTMLElement | null | undefined) {
  const ui = useUiStore()

  let previousTop = 0
  let suppressUntil = 0
  let pointerDown = false
  let queued = 0
  let live = true
  let bound = false

  /** A reader dragging a selection down the page auto-scrolls, and lifting the
   *  bars under that drag slides the text past their cursor mid-gesture. */
  function selecting(): boolean {
    if (!pointerDown) return false
    const selection = document.getSelection()
    return Boolean(selection && !selection.isCollapsed)
  }

  function measure() {
    const el = get()
    if (!live || !el) return

    const travel = el.scrollHeight - el.clientHeight
    const top = el.scrollTop
    const delta = top - previousTop
    previousTop = top
    ui.readProgress = travel > 0 ? Math.min(1, Math.max(0, top / travel)) : 0

    // The mode owns the lift outright; a reader who asked for stillness does
    // not get the bars back for reading, in either direction.
    if (ui.fullscreen) return

    // A page that fits has nothing to give.
    if (travel <= ui.chromeHeight + SLACK_PX) {
      ui.tweenChromeGone(0)
      return
    }

    // HOW MUCH THE READER HAS EARNED, FROM POSITION ALONE, and it is a ceiling
    // rather than a nudge. At a rate below 1:1 the chrome cannot be lowered by
    // the same gesture that reaches the top -- coming back up returns a third
    // of a bar per pixel, so a reader arrives at `scrollTop: 0` with it still
    // part-lifted and no scroll left underneath them to lower it with. Handing
    // that last stretch to a timer put a movement at the top of every document
    // that the reader had not asked for and could not steer.
    //
    // A ceiling has no clock and closes smoothly: it is exactly what a downward
    // read produces anyway, so it never binds on the way down, and on the way
    // back it tightens with every pixel until it reaches zero at the top.
    //
    // IT ALSO ANSWERS THE RUBBER BAND. A phone lets `scrollTop` go past both
    // ends and springs back, and the spring back at the TOP is a positive delta
    // -- indistinguishable from reading down, so it lifted the chrome as the
    // reader arrived. The ceiling is zero there, so it cannot.
    const ceiling = Math.min(ui.chromeHeight, Math.max(0, top) * CHROME_RATE)

    // And the spring itself is not the reader, at either end.
    const springing = top < 0 || top > travel
    const steady =
      delta !== 0 && !springing && performance.now() >= suppressUntil && !selecting()

    ui.setChromeGone(Math.min(steady ? ui.chromeGone + delta * CHROME_RATE : ui.chromeGone, ceiling))
  }

  function onScroll() {
    // SCROLLING TAKES THE REVEAL BACK. The reveal pins the lift at zero, so one
    // left latched stops the bars moving ever again. It fires on focus landing
    // outside the reading, which is what touching the brief toggle or a nav
    // link does; nothing else can take it back in time, because the pointer
    // release only fires while the mouse is MOVING, so a reader scrolling with
    // a wheel never triggers it and a phone carries no pointer listener at all.
    // A reader who scrolls is reading.
    if (ui.chromeRevealed) ui.chromeRevealed = false

    if (queued) return
    queued = requestAnimationFrame(() => {
      queued = 0
      measure()
    })
  }

  /**
   * The top edge of the window is where you ask for the chrome back.
   *
   * This sets the OVERRIDE rather than lowering the bars directly, and the
   * difference is the whole escape hatch: in focus mode the lift belongs to the
   * mode, so putting it down by hand would be undone on the next frame and the
   * switch that leaves the mode would live in a bar the mode had made
   * unreachable.
   *
   * Moving off the edge does NOT take it away again outside focus mode. There
   * is no remembered amount to snatch it back to -- the lift is wherever the
   * reader last scrolled it, and the reader is the one who moves it. So the
   * bars stay down until the next scroll lifts them, at that reader's own rate,
   * instead of being pulled out from under a travelling cursor.
   */
  function onPointerMove(event: PointerEvent) {
    if (!live) return
    if (event.clientY <= EDGE_PX) ui.chromeRevealed = true
    else if (ui.fullscreen) ui.chromeRevealed = false
  }

  /**
   * Asking for a thing is a request to see it (docs/DESIGN.md).
   *
   * ONE listener rather than a call in six places. A keyboard user tabbing out
   * of the article needs the nav to exist, and the palette, the keyboard help,
   * `FilterField`'s `/`, the phone drawer and every `Dialog` all move focus
   * outside the scroller when they open -- so all of them are covered, and
   * none of them can be forgotten later.
   *
   * The `contains` test is what makes it safe: mentions are `tabindex="0"`, so
   * a bare `focusin` would flash the bars back on every pinned card.
   */
  function onFocusIn(event: FocusEvent) {
    const el = get()
    const target = event.target as Node | null
    if (!live || !el || !target) return
    if (el.contains(target)) {
      // Focus came back to the reading. A keyboard reader has no pointer to
      // move away, so this is the only thing that can lapse the reveal for
      // them, and without it focus mode would never resume.
      ui.chromeRevealed = false
      return
    }
    ui.chromeRevealed = true
  }

  function onPointerDown() {
    pointerDown = true
  }

  function onPointerUp() {
    pointerDown = false
  }

  function bindWindow() {
    // `onActivated` also fires on first mount under `<KeepAlive>`, so this runs
    // twice on arrival; the flag is cheaper than reasoning about whether
    // `addEventListener` deduped four listeners with four sets of options.
    if (bound) return
    bound = true
    // A phone has no top edge to reach for, so it does not carry the listener.
    if (window.matchMedia('(hover: hover)').matches) {
      window.addEventListener('pointermove', onPointerMove, { passive: true })
    }
    window.addEventListener('focusin', onFocusIn, true)
    window.addEventListener('pointerdown', onPointerDown, { passive: true, capture: true })
    window.addEventListener('pointerup', onPointerUp, { passive: true, capture: true })
  }

  function unbindWindow() {
    bound = false
    window.removeEventListener('pointermove', onPointerMove)
    window.removeEventListener('focusin', onFocusIn, true)
    window.removeEventListener('pointerdown', onPointerDown, true)
    window.removeEventListener('pointerup', onPointerUp, true)
  }

  /** Whatever else happens, the app does not keep a lifted header. This view is
   *  cached, so leaving it does not unmount anything -- a leaked lift would
   *  strand every OTHER route without its chrome, which looks like a bug in the
   *  route you arrived at rather than in the one you left. */
  function release() {
    live = false
    if (queued) cancelAnimationFrame(queued)
    queued = 0
    pointerDown = false
    ui.setChromeGone(0)
    ui.chromeRevealed = false
    ui.readProgress = 0
    unbindWindow()
  }

  watch(
    get,
    (el, previous) => {
      previous?.removeEventListener('scroll', onScroll)
      el?.addEventListener('scroll', onScroll, { passive: true })
      previousTop = el?.scrollTop ?? 0
      if (el) requestAnimationFrame(measure)
    },
    { immediate: true },
  )

  bindWindow()

  onActivated(() => {
    live = true
    previousTop = get()?.scrollTop ?? 0
    // The chrome is shown on arrival and the reader has to earn its retreat
    // again. Restoring it lifted would land them on a page whose bars come down
    // for no reason the moment they move.
    ui.setChromeGone(0)
    bindWindow()
    requestAnimationFrame(measure)
  })

  /**
   * Hold everything still for one gesture's worth of time.
   *
   * A caller that MOVES the reader says so. Nothing is inferred from the size
   * of the deltas it produces, and that is the point: a big jump and a fast
   * flick of the wheel are the same event to a scroll listener, so a rule that
   * ignored large deltas would quietly stop responding to anyone who scrolls
   * hard. Measured 2026-09-02 -- a synthetic three-tick scroll arrives as ONE event of
   * about 300px.
   *
   * Both of the brief's programmatic scrolls need it. `restoreScroll` returns
   * a reader to the item they left for the graph, and `jumpToItem` answers the
   * contents list over several hundred milliseconds of small deltas that sum
   * downward -- so the chrome would go the whole way in one jump, and land them
   * short of the item they asked for, past a `scroll-mt` measured against a
   * taller chrome.
   */
  function suppress(ms = 600) {
    suppressUntil = Math.max(suppressUntil, performance.now() + ms)
    previousTop = get()?.scrollTop ?? previousTop
  }

  onDeactivated(release)
  onBeforeUnmount(() => {
    release()
    get()?.removeEventListener('scroll', onScroll)
  })

  return { suppress }
}
