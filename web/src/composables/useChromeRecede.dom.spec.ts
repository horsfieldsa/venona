// @vitest-environment happy-dom
/**
 * The chrome is a length driven by scroll position, and this is the only place
 * it can be tested as one.
 *
 * It shipped broken three times before this file existed, and every one of them
 * was the same failure of method rather than of reasoning: the decision path
 * runs inside `requestAnimationFrame`, a browser driven over CDP throttles rAF
 * when the tab is not painting, so the runs that "passed" were the ones where
 * the code under test had never executed. A harness that cannot make the
 * behaviour happen cannot witness it either, and repeated green from such a
 * harness is worth less than nothing -- it is a false negative that reads as
 * evidence.
 *
 * So: fake timers, a fake scroller, a hand-driven rAF, and one assertion per
 * behaviour that a reader would name.
 *
 * THE SCROLLER GROWS AS THE BARS LEAVE, and the fake models that rather than
 * holding a constant `clientHeight`. It is the whole of the difficulty here:
 * lifting the chrome hands its height to the scroll box, which moves the
 * reading and moves the scroll limit, and a harness with a fixed viewport
 * cannot see either. The end-of-document case is only a case at all because of
 * it.
 *
 * NOT covered here: the cleanup on leaving the view. It hangs off
 * `onDeactivated`, which Vue only registers inside a component instance, and
 * this file mounts no components by design (see `vitest.config.ts`). The
 * KeepAlive round trip through the graph is `ui_smoke`'s to hold.
 */
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'

import { useChromeRecede } from './useChromeRecede'

import { useUiStore } from '@/stores/ui'

/** Big enough that the short-document floor is not in play unless a test puts
 *  it there. */
const CONTENT = 8000
const VIEWPORT = 800
const NAV = 48
const CONTEXT = 38
const CHROME = NAV + CONTEXT
/** `CHROME_RATE`: how much bar moves per pixel scrolled. Stated here so the
 *  arithmetic below reads as the rule rather than as five magic numbers. */
const RATE = 1 / 3
/** What it costs the reader to put the whole chrome away. */
const CLEARS = CHROME / RATE

let frames: Array<() => void> = []
let scope: ReturnType<typeof effectScope>
let el: HTMLElement

/** A bar of a fixed height, for the store to measure. It is `offsetHeight` the
 *  store reads, and happy-dom lays nothing out, so the number is declared. */
function makeBar(height: number): HTMLElement {
  const node = document.createElement('div')
  Object.defineProperty(node, 'offsetHeight', { get: () => height, configurable: true })
  return node
}

/**
 * A scroller whose geometry we control outright, modelling BOTH halves of the
 * cancellation.
 *
 * `clientHeight` is the window minus the chrome that is still down, so every
 * pixel the chrome gives up is a pixel the box gains at its top edge.
 * `scrollHeight` carries the same pixel as top padding, which is `ViewShell`'s
 * half. Together they hold the scrollable distance CONSTANT at every lift, and
 * a harness with a fixed viewport can see neither.
 */
function makeScroller(content = CONTENT, viewport = VIEWPORT): HTMLElement {
  const node = document.createElement('div')
  let topValue = 0
  const gone = () => useUiStore().chromeGone
  const client = () => viewport - CHROME + gone()
  // A phone lets `scrollTop` past both ends and springs back. Nothing a reader
  // does can produce that, so it needs a door of its own.
  Object.defineProperty(node, 'spring', {
    value: (v: number) => {
      topValue = v
    },
    configurable: true,
  })
  Object.defineProperties(node, {
    scrollHeight: { get: () => content + gone(), configurable: true },
    clientHeight: { get: client, configurable: true },
    scrollTop: {
      get: () => topValue,
      set: (v: number) => {
        topValue = Math.max(0, Math.min(v, content + gone() - client()))
      },
      configurable: true,
    },
  })
  return node
}

/** One frame of the rAF queue the composable coalesces its work onto. */
function frame(): void {
  const due = frames
  frames = []
  for (const fn of due) fn()
}

/** Move the scroller and let the composable see it, the way a browser would: a
 *  scroll event, then the frame its handler was deferred to. */
function scrollTo(top: number): void {
  vi.advanceTimersByTime(16)
  el.scrollTop = top
  el.dispatchEvent(new Event('scroll'))
  frame()
}

/**
 * Read in reader-sized steps rather than one jump.
 *
 * A FIXED NUMBER of steps, and reader-sized ones. A single large assignment
 * arrives as one scroll event, and one event of 900px is not what a person
 * reading down a page produces -- a check written that way would pass on a
 * build that only ever responded to flicks.
 */
function readDown(px: number, step = 20): void {
  for (let i = 0; i < Math.ceil(px / step); i += 1) scrollTo(el.scrollTop + step)
}

function readUp(px: number, step = 20): void {
  for (let i = 0; i < Math.ceil(px / step); i += 1) scrollTo(el.scrollTop - step)
}

/** Put the scroller where only a rubber band could, and let the composable see
 *  it. Past an end, and then back. */
function spring(top: number): void {
  vi.advanceTimersByTime(16)
  ;(el as unknown as { spring(v: number): void }).spring(top)
  el.dispatchEvent(new Event('scroll'))
  frame()
}

/** Run out a hand-driven movement -- `f` and the pointer edge are tweened over
 *  `--motion-base` rather than following a gesture. */
function settle(): void {
  for (let i = 0; i < 20; i += 1) {
    vi.advanceTimersByTime(16)
    frame()
  }
}

/** Where a line of the document sits on screen: the chrome still showing above
 *  it, plus the padding that stands in for the chrome that has gone, less how
 *  far the reader has scrolled. This is what the reader watches, and the only
 *  number that can tell a lurch from a scroll. */
function lineOnScreen(): number {
  const gone = useUiStore().chromeGone
  return CHROME - gone + gone - el.scrollTop
}

function install(scroller: HTMLElement = el) {
  const ui = useUiStore()
  ui.bindChromeBars(makeBar(NAV), makeBar(CONTEXT))
  scope = effectScope()
  return scope.run(() => useChromeRecede(() => scroller))!
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
  frames = []
  vi.stubGlobal('requestAnimationFrame', (fn: () => void) => {
    frames.push(fn)
    return frames.length
  })
  vi.stubGlobal('cancelAnimationFrame', () => {})
  el = makeScroller()
})

afterEach(() => {
  scope?.stop()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the chrome recedes as the brief is read', () => {
  /** A THIRD of the scroll, not all of it. At 1:1 the chrome leaves over its
   *  own height, which is a single wheel tick -- so on a mouse the whole 86px
   *  goes in one frame and the departure is as abrupt as the state machine it
   *  replaced. */
  it('leaves at a fraction of the rate the reader scrolls', () => {
    install()
    const ui = useUiStore()

    readDown(60)

    expect(ui.chromeGone).toBe(60 * RATE)
    expect(ui.chromeCollapsed).toBe(false)
  })

  /**
   * THE ONE THAT MAKES IT SMOOTH RATHER THAN FAST.
   *
   * The bars leaving hands their height to the top of the scroll box, so
   * without this the line under the reader's eye moves twice as far as they
   * scrolled for as long as the chrome is on its way out. Measured 2026-09-02 on the build
   * before this: a single wheel gesture scrolled 300px and then moved the text
   * a further 86 with no input at all.
   */
  it('and the reading moves by what the reader scrolled and no more', () => {
    install()
    const before = lineOnScreen()

    readDown(60)

    expect(lineOnScreen()).toBe(before - 60)
  })

  /**
   * THE ONE THAT COSTS A FLING.
   *
   * An assignment to `scrollTop` during momentum halts it, in both directions.
   * So a cancellation written as a `scrollTop` handed back on every frame the
   * chrome moves kills every flick at the moment the chrome starts moving, and
   * the scroll comes back to life once it has gone -- reported from a phone as
   * a scroll that stops just before the break. `ViewShell`'s top padding does
   * the same arithmetic in CSS; the rule here is that nothing touches the
   * reader's position at all.
   */
  it('never moves the reader, so a flick keeps its momentum', () => {
    install()

    scrollTo(60)
    const first = el.scrollTop
    readDown(120)

    expect(first).toBe(60)
    expect(el.scrollTop).toBe(180)
  })

  it('is gone once the reader has read far enough to pay for it', () => {
    install()
    const ui = useUiStore()

    readDown(CLEARS + 40)

    expect(ui.chromeGone).toBe(CHROME)
    expect(ui.chromeCollapsed).toBe(true)
  })

  it('comes back at the same rate on the way up', () => {
    install()
    const ui = useUiStore()
    readDown(CLEARS)
    expect(ui.chromeCollapsed).toBe(true)

    readUp(60)

    expect(ui.chromeGone).toBeCloseTo(CHROME - 60 * RATE)
    expect(ui.chromeCollapsed).toBe(false)
  })

  /**
   * Focus outside the reading is how every overlay in the app asks to be seen,
   * so it reveals the chrome -- and the reader touching ANY control does it
   * too: the brief toggle, a nav link, the focus switch. If that reveal
   * outlives the gesture, the chrome can never go away again, and on a phone
   * there is no pointer to move away and take it back.
   */
  it('still leaves after the reader has touched a control', () => {
    install()
    const ui = useUiStore()

    const control = document.createElement('button')
    document.body.appendChild(control)
    control.dispatchEvent(new Event('focusin', { bubbles: true }))
    settle()

    readDown(CLEARS)

    expect(ui.chromeCollapsed).toBe(true)
  })

  /**
   * Coming back up returns a third of a bar per pixel, so a reader who reaches
   * the top from part-way down would arrive with the chrome still part-lifted
   * and no scroll left underneath them to lower it with. The ceiling closes it
   * instead, and no clock: a movement at the top of every document that the
   * reader had not asked for and could not steer is what a clock produced.
   */
  it('never leaves more chrome lifted than the reader has scrolled for', () => {
    const chrome = install()
    const ui = useUiStore()
    readDown(CLEARS)
    expect(ui.chromeCollapsed).toBe(true)

    // The contents list answering a click. The reader is MOVED, so the deltas
    // are not theirs and the lift does not follow them -- but it may not stay
    // above what their new position has paid for either.
    chrome.suppress()
    scrollTo(40)

    expect(ui.chromeGone).toBe(40 * RATE)
  })

  /**
   * A phone lets `scrollTop` past the top and springs back, and the spring back
   * is a POSITIVE delta -- indistinguishable from reading down, so it lifted
   * the chrome at the very moment the reader arrived at the top. Reported as a
   * bounce at the top.
   */
  it('is not lifted by a phone springing back from the top', () => {
    install()
    const ui = useUiStore()
    readDown(CLEARS)
    readUp(CLEARS)
    expect(ui.chromeGone).toBe(0)

    spring(-40)
    spring(0)

    expect(ui.chromeGone).toBe(0)
  })

  /**
   * What a reader reported, in their words: the chrome should stay away once
   * they reach the bottom, and come back only when they mean it.
   *
   * The box growing at the end drops the scrollable distance by the chrome's
   * height and the browser clamps `scrollTop` to the new limit. That clamp used
   * to arrive as an upward delta and hand the chrome straight back, and every
   * bounce against the end did it again. It cannot here: the scroller's padding
   * grows by exactly what the box gains, so the scrollable distance is the same
   * at every lift and there is no limit to be clamped against. The fake models
   * both sides, which is the only reason this test can see it.
   */
  it('keeps the chrome away at the end of the brief', () => {
    install()
    const ui = useUiStore()
    readDown(CLEARS)
    expect(ui.chromeCollapsed).toBe(true)

    scrollTo(CONTENT)
    scrollTo(CONTENT)

    expect(ui.chromeCollapsed).toBe(true)
  })

  it('and hands it back when the reader means to scroll up', () => {
    install()
    const ui = useUiStore()
    readDown(CLEARS)
    scrollTo(CONTENT)
    expect(ui.chromeCollapsed).toBe(true)

    readUp(CLEARS)

    expect(ui.chromeGone).toBe(0)
  })

  /**
   * Lifting the bars CREATES the scroll room that lowering them takes away, so
   * on a document with barely any the two chase each other with no bottom to
   * settle against -- and at the limit the scroller stops firing events at all,
   * which strands the chrome away with no gesture left that could bring it
   * back.
   */
  it('never recedes on a document with nowhere to go', () => {
    el = makeScroller(VIEWPORT + 40)
    install()
    const ui = useUiStore()

    readDown(40, 10)
    settle()

    expect(ui.chromeGone).toBe(0)
    expect(ui.chromeCollapsed).toBe(false)
  })

  /**
   * Reaching for the focus switch is focus landing outside the reading, which
   * is how every overlay in the app asks the chrome to be seen -- so the reveal
   * is already set by the time the mode turns on. If entering did not clear it,
   * the pointer edge would have nothing left to set and the one gesture that
   * brings the chrome back inside focus mode would do nothing at all: the mode
   * hides the switch that leaves it, which is ADR 0118's trap one layer up.
   */
  it('enters focus mode from its own switch and can still be left', () => {
    install()
    const ui = useUiStore()

    const control = document.createElement('button')
    document.body.appendChild(control)
    control.dispatchEvent(new Event('focusin', { bubbles: true }))
    ui.fullscreen = true
    settle()
    expect(ui.chromeCollapsed).toBe(true)

    window.dispatchEvent(new PointerEvent('pointermove', { clientY: 2 }))
    settle()

    expect(ui.chromeCollapsed).toBe(false)
  })

  /** Focus mode is the one movement with no hand on it, so it is the one that
   *  gets a clock -- run frame by frame in the store rather than handed to a
   *  CSS transition, which would then be fighting every scroll frame. */
  it('holds the chrome away in focus mode however the reader reads', () => {
    install()
    const ui = useUiStore()
    ui.fullscreen = true
    settle()
    expect(ui.chromeCollapsed).toBe(true)

    readUp(200)

    expect(ui.chromeCollapsed).toBe(true)
  })
})
