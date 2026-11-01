---
status: accepted
date: 2026-09-04
---

# 2026-09-04 — The still field loses the race on purpose (amends 0204)

0204 made the two properties' crossfade one number and assumed that was the
defect. It was not. Matching the durations only made both surfaces do the same
wrong thing at the same speed, and the report came back for both: "a graph that
looks more zoomed in appears and then disappears."

**The swap itself is the defect, not its duration.** The two layers are not the
same picture at different opacities. The still is a hand-placed composition on a
1600x1000 viewBox drawn `slice`, so its seven hubs land at radii around 13 — an
order of magnitude larger than the canvas's 1.5 to 7 device pixels, and its
edges are correspondingly heavier. Fading one into the other does not read as a
crossfade. It reads as a zoomed-in graph arriving and leaving, and no duration
makes that a thing a reader wants to watch.

**So the still waits, and on any ordinary load it never appears at all.** It
carries `animation: <name> var(--motion-reveal) var(--ease)
var(--cadence-field-arrive) backwards`, and the `backwards` fill is the whole
mechanism: through the delay the element takes the `from` keyframe and sits at
zero. The live class the canvas sets cancels the animation outright
(`animation: none` beside the `opacity: 0` that was already there), so winning
the race costs the still nothing — it is already invisible and the cascade
agrees.

400ms, against a measured gap of about 85ms between first paint and the live
class on both properties. The still is the fallback and this is what makes it
behave like one: reach the delay first and it fades in and stays, which is the
scripts-off page; lose and it was never there. A slow script gets the fade it
always got rather than a cut. `prefers-reduced-motion` cancels the wait through
marketing's blanket rule and, on the hub, through the rule that stands in for it
— that reader gets the still immediately, which is the right answer for someone
who asked for stillness.

The rejected alternative was to keep shortening the crossfade. It cannot reach
zero: the handover has to be visible for at least a frame, and at the sizes
involved one frame of both drawings at once is exactly the artifact.

## Consequences

- `--cadence-field-arrive` is a marketing token like the rest of the field's
  motion, and reaches the hub through `MOTION_SOURCE` in
  `scripts/graph_field.py`. One number, three surfaces, still.
- The app's copy of the field (`web/src/styles/base.css`) does NOT wait. It has
  no canvas and nothing to lose the race to, so a delay there would only be a
  page that renders blank for 400ms.
- Anything that cancels the arrival must also leave the still at zero, or the
  cancel becomes a flash of its own. Both surfaces put `animation: none` and
  `opacity: 0` in the same rule for that reason.
- The verification is deterministic and does not need a visible tab: with the
  live class present the still computes to `opacity: 0` with zero animations,
  and with it removed the arrival is a `backwards`-filled animation whose delay
  holds the element at zero. Both were checked on both surfaces.
