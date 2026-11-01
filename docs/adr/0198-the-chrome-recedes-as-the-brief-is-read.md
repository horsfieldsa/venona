---
status: superseded
superseded-by: 0199
date: 2026-09-02
---

# 2026-09-02 — The chrome recedes as the brief is read, and the bars collapse themselves

`AppHeader` (48px) and `ContextBar` (38px) sit above every route, including the
one route the product exists to produce. That is 86px of permanent furniture
over a document of 2,000 to 3,000 words that takes eight to twelve minutes to
read — past the working rule that a persistent header stays under a tenth of
the viewport, and well past it on a phone.

**The chrome now withdraws while the reading goes down the page and returns
when it comes back up.** No control, no mode, nothing to learn. `f` on the
brief holds it withdrawn regardless of direction and folds the orientation rail
to a strip, for a reader who wants the stillness.

The document does not change. Same `--measure`, same scale, same runs, same
spans. Nothing rewraps in either state, so no mention re-measures and no hover
anchor can drift horizontally — which is what makes this chrome work rather
than a second rendering of the brief.

## Two flags, and only one of them is a mode

`fullscreen` is asked for, so it is dismissable and `Esc` leaves. `chromeHidden`
is the page receding under someone who is only reading, so it must never reach
the dismissable stack: `useDismissable` pops the topmost entry alone, and a flag
pushing and popping on every change of scroll direction would sit on top of an
open dialog and eat that dialog's `Esc`.

A third value settles a fault this inherited. `chromeRevealed` beats both, and
without it full screen has no escape at all: the obvious
`chromeHidden || fullscreen` means reaching the top edge of the window can only
clear the flag that is not the mode, so the control that leaves the mode stays
in a bar the mode has made unreachable. ADR 0118 recorded that trap for the
graph and did not fix it. The brief's folded rail also carries the way out
where the rail used to be.

## The bars collapse themselves; the tracks are `auto`

ADR 0118 zeroed the two chrome grid TRACKS. That is a state, not a movement: a
fixed `--nav-height` track holds its 48px however short the header inside it
gets, so nothing can animate and the page snaps when the track finally changes.
Both tracks are `auto` now and each bar animates its own height —
`transition-[height]` for the header, whose height is a length, and a single-row
`1fr`/`0fr` grid for `ContextBar`, whose height is not, because the row wraps
below `md`.

Three rules survive intact and none of them is negotiable:

- **Collapse, never `v-if`.** `ContextBar` holds the four `<Teleport>` targets
  every `ViewShell` writes into, and a teleport with a missing target throws.
- **Clipped, never `invisible`.** Zero height with `overflow-hidden` already
  stops a bar painting over the page, and `visibility: hidden` would take the
  nav out of the tab order — which is exactly the reader the `focusin` reveal
  exists to serve.
- **`.context` keeps its own box.** The collapse is two wrappers ABOVE it, so
  it stays the positioned ancestor `FilterField`'s `absolute inset-0` panel
  resolves against, keeps the ground and height `ui_smoke` measures, and keeps
  the three tracks every `md:col-start-*` is placed against.

`shell--fullscreen` carries no CSS and is not dead: four `ui_smoke` checks read
it to assert that `f` enters the mode, `Esc` leaves it, and `/` leaves it to
reveal the search. It says so in the template now, because a marker class with
no rule looks like debris to a grep over the stylesheets.

## Three guards and one hatch, and they are the whole of the difficulty

Each is a real failure rather than defensive padding.

- **Settle.** Collapsing makes the scroller ~86px taller, and at the bottom of
  the document the browser answers by clamping `scrollTop` down by the same
  amount — arriving as a scroll event indistinguishable from an 86px upward
  scroll. Reveal is asymmetric and clamps nothing, so this is not an endless
  flicker; it is one spurious un-collapse per scroll attempt near the end of
  every brief, which is worse, because watching a still screen never shows it.
  The check is to scroll DOWN at the bottom and assert the chrome stays away.
- **Short document.** Below the engage threshold there is no recede at all. On
  a quiet-day brief or the first-run screen, hiding the bars creates the scroll
  room that revealing them takes away, with no bottom to settle against.
- **Not by size.** A caller that MOVES the reader says so, through
  `suppress()`. `restoreScroll` returns a reader to the exact item they left
  for the graph, and `jumpToItem` answers the contents list; either would
  otherwise read as vigorous downward reading and slide that paragraph 86px out
  from under them. Inferring it from a size threshold was tried and is wrong in
  both directions: a smooth jump arrives as a run of ordinary small deltas and
  slips under any ceiling, while a hard flick of the wheel arrives as one event
  of several hundred pixels and trips it — so the chrome stops responding to
  whoever scrolls fastest. Measured in a browser: a three-tick scroll can
  arrive as a single event.
- **Selection.** Dragging a selection down the page autoscrolls, and collapsing
  under the drag moves the text past the cursor mid-gesture.

## The page holds still, and the chrome waits for you to

Two rules, and they are what make this readable rather than merely compact.

**The chrome only leaves when the page is still.** A scroll re-arms the timer;
the bars go once nothing has moved for 140ms. Collapsing mid-flick puts two
motions on the same pixels, which is most of what reads as a lurch — worst on a
phone, where a momentum scroll is the normal way to move. It also says
something truer than a threshold does: the chrome goes when you settle in to
read.

**And the last screen is left alone.** Within 320px of the end the chrome comes
back and then stops changing. That band is the one place where moving the
chrome also moves the SCROLL RANGE -- the box grows by the chrome's height, the
scrollable distance shrinks by the same amount, and the browser clamps
`scrollTop` to the new limit. Every collapse there is arguing with that clamp,
and every bounce off the end arrives as a scroll and flickers the bars. Handing
the chrome back on arrival is also what the reader wants: finishing a brief is
when the previous and next buttons matter. The band is well wider than the
chrome so the change happens in open water, where the hold works and there is
no clamp to argue with.

**And the reading position does not move when it does.** The bars leaving makes
the scroll box taller AT THE TOP, so the line under the reader's eye rises by
the whole of it — 86px on a laptop, about 120px on a phone where the sub-menu
wraps to two rows. `scrollTop` moves by the same amount on the same frames, so
nothing on screen moves and the room the chrome gives up shows as more of the
document. Measured across a collapse: the header animates 39, 24, 12, 6, 2 and
a paragraph's position on screen reads 1093 on every frame.

The target is ABSOLUTE, computed from where the chrome actually is, never a
running delta. At the end of a document the browser clamps `scrollTop` by
itself when the box grows, and a delta applied on top of that clamp corrects
twice and walks the reader backwards. Overriding the reader is only safe
because of the quiet gate — they are not scrolling — and a genuine scroll
during the transition abandons the hold, because fighting them is worse than
the shift ever was.

## The frame is sealed, and that is not belt-and-braces

`overflow: hidden` on the shell, so the DOCUMENT can never scroll. The router
already said window scrolling does not happen here; this makes it true rather
than assumed.

While the bars animate, the frame's rows are re-solved every frame and it can
overshoot the viewport by a few pixels for two or three of them. That is enough
for the document to take a scrollbar, which costs 10px of width, which narrows
the reading column, which REWRAPS THE PROSE. Measured on the reveal before the
seal: `documentElement.clientWidth` 1654 to 1644 and the column 1109 to 1099,
for three frames out of roughly two thousand. What a reader sees of that chain
is the text twitching sideways as the chrome comes back, which is nothing like
its cause — and no before/after check can see it, because both endpoints are
correct and only the journey is wrong.

It goes on the frame rather than on `body`: the public routes render outside
`AppShell` and do scroll the document.

## Consequences

`EntityHoverCard` measures where the chrome actually ends —
`data-chrome-bottom` on `<main>` — instead of reading `--nav-height`, which is
what the header would be IF SHOWN. It also re-places across the collapse, per
frame: nothing else would notice, because the document position, the window and
the card are all unchanged and only the anchor has moved. Both faults were
already live in the graph's full screen.

Below `lg` the rail's two halves take rows either side of the article rather
than the whole rail sitting above it. The contents list is navigation and leads;
the meter and the references are orientation and follow. A phone reader used to
meet about two screens of orientation before the first sentence.

`ViewShell`'s stylesheet budget goes 145 to 165, and the reason grew rather than
the file: the rail track is animated now, which a `minmax()` cannot be.
