---
status: accepted
date: 2026-09-02
supersedes: 0198
---

# 2026-09-02 — The chrome is a length the reader scrolls, not a state that animates

ADR 0198 built the recede as a boolean handed to a CSS transition: read past a
threshold and the bars run their own 120ms collapse. **The chrome is a length
now — how far the bars have been lifted, in pixels — and the reader's own
scrolling is the only thing that moves it.** There is no state to cross, so
there is nothing to cross it suddenly.

The reader's words: *"I want the header to disappear at the rate of scrolling.
So rather than a sudden disappear, it scrolls away."*

## The 86 pixels nobody asked for

Measured on the threshold build, sampling every 8ms through one three-tick
wheel gesture on a real brief:

```
t=1959  header 48   scrollTop 300   paragraph 328     <- the gesture ends here
t=1984  header 41   scrollTop 300   paragraph 316
t=2005  header 20   scrollTop 300   paragraph 278
t=2037  header  5   scrollTop 300   paragraph 252
t=2085  header  0   scrollTop 300   paragraph 242
```

300px of input, 387px of movement. The last 86 arrive after the reader has
stopped, on a clock, and they arrive because the bars leaving hands their height
to the TOP of the scroll box. That is the lurch, and no amount of easing fixes
it: the movement is real and it is nobody's.

The same 86 is why a proportional collapse is not enough on its own. Lift the
bars at the rate of scrolling and the box grows at the rate of scrolling too, so
the text moves at TWICE the rate for the whole of the first 86px. The lift has
to be cancelled, and where that cancellation lives is the next section — the
obvious place is wrong.

## The cancellation is CSS, because writing `scrollTop` halts a fling

The obvious answer is to hand each pixel of lift straight back as a pixel of
`scrollTop`, in the same frame. It is exact, and on a phone it is unusable: **an
assignment to `scrollTop` during momentum stops the momentum dead.** Reported
from a phone as a flick that stops just before the break and then carries
normally once the header has gone — which is precisely the window that code
writes in, in both directions.

So `ViewShell`'s scroller carries the lift as top padding instead. The box gains
the lift at its top edge and the content gains the same back inside it; the two
cancel exactly, the reader's position is never touched, and the reading moves by
the scroll and nothing else. Verified with a trap on the element's `scrollTop`
setter through a full read down and back: **zero writes**, the paragraph exactly
−200 per 200px gesture, the side rail motionless at 112.

It also holds the scrollable distance CONSTANT at every lift, which is what
makes the end of a brief safe rather than merely tuned — there is no moving
limit for the browser to clamp against, so no clamp can arrive disguised as an
upward scroll.

**Both ends, because the footer counts too.** The footer collapsing to a
hairline grows the same box by 30px at its BOTTOM edge. That moves no text, but
it drops the scrollable distance by 30, the browser clamps, and the clamp reads
as a 29px scroll up: measured at the end of a brief, `scrollTop` 9386 → 9357 and
the chrome edging back 10px on arrival. The scroller's bottom padding takes that
30 as well, transitioned on the footer's own curve and duration so the two
cancel every frame. Instant would be right in one direction and wrong by the
whole 30 in the other.

**A sticky element is pinned below the scroller's padding**, so the rail's
offset is now `0`. `--chrome-gone` there counts the lift twice and walks the
rail down the window as the bars leave: measured at a lift of 67, the rail moved
112 → 179 while the prose beside it held still.

## A third of a pixel of bar per pixel scrolled

`CHROME_RATE = 1/3`, and 1:1 was tried first and is too fast. At 1:1 the chrome
is exactly a band of the page and leaves over its own height — which is a single
wheel tick, so on a mouse the whole 86px goes in one frame and the departure is
as abrupt as the state machine it replaced. The input is quantised at 100px;
nothing tracking it exactly can be smoother than it is.

At a third it takes about 260px of reading, and the reader keeps moving down the
document the whole time: only the pixels the chrome actually takes are cancelled
by the padding, so two thirds of every gesture still advances the page. At 1:1
the first 86px advance it not at all, which is its own kind of wrong — the page
feels stuck to the top while the bars slide.

**The reading is exact at any rate**, which is what makes this a feel and not a
correctness knob: the lift moves `RATE * delta` and the padding cancels the
same, so the line under the reader's eye moves by `delta` whatever the rate is.
Measured on this build, three wheel ticks: the chrome showing 86 → 53 → 19 → 0,
`scrollTop` 0 → 67 → 134 → 215, and the paragraph exactly −100 on every one of
them, with the side rail motionless at 112 throughout.

**And the top of a document always has its chrome, which is a CEILING and not a
nudge.** Coming back up returns a third of a bar per pixel, so a reader who
reaches `scrollTop: 0` other than by reading all the way back — a jump from the
contents list, a restored position — arrives with the chrome still part-lifted
and no scroll left underneath them to lower it with. At 1:1 that could not
happen.

Handing that last stretch to a timer was tried and is what a reader called a
bounce at the top: a movement on the chrome, at the top of every document, that
they had not asked for and could not steer. The lift is capped at
`scrollTop * RATE` instead. It has no clock and closes smoothly — it is exactly
what a downward read produces anyway, so it never binds on the way down, and on
the way back it tightens with every pixel until it reaches zero at the top.

**It is also the answer to the rubber band.** A phone lets `scrollTop` past both
ends and springs back, and the spring back at the TOP is a positive delta,
indistinguishable from reading down — so arriving at the top LIFTED the chrome,
which is the same bounce from the other direction. The ceiling is zero there, so
it cannot; and neither end's spring is treated as the reader at all.

## Lifted, not shrunk, and the lift is split across the two bars

Each bar keeps its height and takes a negative `margin-top`. The shell's rows
are `auto`, so the track shrinks by the same amount and everything below travels
with it; the part that leaves the window is clipped by the shell's own seal.

**The lift is split because a grid track cannot go negative.** One margin on the
header slides both bars until the header's own row reaches zero and then stops
dead — measured at exactly 48px of the 86 needed. Each bar gives up its own
height and no more, which sums to the whole, which is why this needs no wrapper
element and does not touch the shell grid.

How far it can go is MEASURED off the two bars, never `--nav-height` plus
`--subnav-height`: the sub-menu wraps to two rows on a phone, takes no height on
a route with nothing to say, and grows when a view teleports a long control set
into it. A `ResizeObserver` was the obvious way to keep that current and is the
wrong one — it delivers at the end of a rendering update, so the first value it
reports is the one from before the route filled the sub-menu in, and on a tab
that is not painting it reports nothing at all. Measured: an observer installed
at mount held the sub-menu at 0 for the life of the page and the chrome stopped
38px short of gone. The store reads `offsetHeight` at the moment it moves the
lift instead. Nothing feeds back — a negative margin does not resize the box it
is on.

## What this deletes

Four mechanisms from 0198 exist only because a boolean has to decide, and go:

- **The engage threshold.** A header that is part of the page starts leaving on
  the first pixel. Nothing flickers, because five pixels of scroll is five
  pixels of header.
- **The intent threshold.** A trackpad's two-pixel reversal now returns two
  pixels of bar, which is invisible and self-cancelling, instead of being a
  decision.
- **The settle window.** It existed because collapsing at the end of a document
  made the browser clamp `scrollTop`, and that clamp came back as an upward
  delta indistinguishable from the reader. The scrollable distance is the same
  at every lift now, so there is no limit to clamp against and nothing to
  mistake for a reader.
- **Every CSS transition on the chrome.** A transition is a clock and the
  reader's hand is the clock. The two movements that are NOT gestures — `f` and
  the pointer edge — are run frame by frame in the store, on the same curve,
  because a CSS transition on those properties would be fighting the scroll for
  them.

What survives intact: `suppress()` for the two programmatic scrolls, the
selection guard, the short-document floor, the `focusin` reveal, and the rule
that a bar is lifted rather than unmounted, because `ContextBar` holds the
teleport targets.

**Entering focus mode clears the reveal.** Reaching for the switch is focus
landing outside the reading, so the reveal is already set when the mode turns
on; without clearing it the pointer edge has nothing left to set and the one
gesture that brings the chrome back inside the mode does nothing. That is
ADR 0118's trap one layer up, and 0198 introduced it.

## The reveal is sticky now, and the return is symmetric

Moving off the top edge no longer snatches the chrome away. There is no
remembered scroll amount to snatch it back to — the lift IS the state — and
handing the chrome back is the safe direction: the reader lifts it again with
the gesture they already have, whereas a chrome that leaves as a cursor departs
the top of the window is movement nobody asked for.

Reading back up lowers the bars at the same rate, and holds the reading still
while it does. So the first 86px of an upward gesture buys the chrome rather
than the document. That is the same bargain as the way down, and it is what
makes the two directions one mechanism instead of two.

## Consequences

- `chromeHidden` is gone. `chromeGone` (px), `chromeNav`, `chromeContext` and
  `chromeHeight` replace it in `stores/ui.ts`, which is now the only writer of
  `--chrome-gone`, `--chrome-lift-nav` and `--chrome-lift-context`. It writes
  them onto `<html>` imperatively: this runs inside a scroll handler's frame and
  the bars and the scroller have to see the same lift in it, which a template
  binding a tick later cannot give them.
- `chromeCollapsed` still exists and means one thing only: the top chrome has
  ENTIRELY gone. The footer, the reading hairline and the toast stack are at the
  far edge or are not chrome, so they have no travel to share and take that bit.
  The footer keeps its own 120ms collapse to a hairline.
- `ViewShell` owns both ends of the cancellation — `--chrome-gone` in its top
  padding, the footer's 30px in its bottom — and its sticky rail loses both its
  `transition: top` and its offset. Its style budget goes 171 → 188, and the
  reason grew rather than the file.
- `EntityHoverCard` watches the length rather than the flag, which places once
  per frame with no timer to guess at.
- `AppHeader` loses `overflow-hidden`. The bar leaves the window rather than
  shrinking inside it, so there is nothing left to clip — and the clip was
  cropping the account menu to the height of the bar it hangs from, which is
  what a phone reader saw of Settings. The menu also moves off a raw `z-30`,
  the one panel in the app that was not on the z scale, onto `z-popover`.
- `usePopover` is new: one loose panel at a time, app-wide. Every panel
  dismissing on a press outside itself leaves the one you pressed open
  underneath the one that was already there, and on a phone the nav drawer's
  scrim covered the account menu that opened into it, so the only visible effect
  of the tap was the drawer staying put.
- `scripts/ui_smoke.py` measures the top of `<main>`, never a bar's height: the
  bars keep their height in every state now, so a check written against `.bar`
  passes on a build where nothing moves at all.
