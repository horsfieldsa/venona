---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — A platform is never `PART_OF` a program; the program `FIELDS` it


Four of the second run's twelve rejected relationships were one shape:

```
Rogue 1 [Platform]          --PART_OF--> Low Altitude Stalking and Strike Ordnance [Program]
Strigo [Platform]           --PART_OF--> PrSM [Program]
XM913 Bushmaster [Platform] --PART_OF--> XM30 [Program]
ORP Wicher [Platform]       --PART_OF--> Miecznik [Program]
```

**Decided: a guidance problem, not a gap in the ontology.** `PART_OF` keeps its
range and `Platform` stays out of it.

Two reasons, and the second is the one that would have been expensive to
discover later.

**Nothing is lost.** `FIELDS` (Program → Platform) is the exact inverse and
already exists, so every one of these facts has a lossless rendering — `LASSO
--FIELDS--> Rogue 1`. The ontology was not missing an edge; the model was
reaching for the wrong end of one it had.

**`PART_OF` is the org chart, and only the org chart.** Its own guidance says
this single recursive relationship carries the whole hierarchy, and every path
and neighbourhood query walks it transitively. Admitting a platform means
"what is X part of" answers two incompatible questions — organisational
containment and programme membership — and a transitive walk climbs from a
piece of hardware into the Department of War. That is not a widening; it is
two relations sharing one edge type.

**Why the model kept doing it**, which is the part worth keeping: *"the Rogue 1
is part of the LASSO program"* is ordinary English. The extraction guide is
injected verbatim into the analyze prompt and already handles exactly this class
of miss for a different case — *"a national military is NOT part of its country
… the answer is BASED_IN"*. `PART_OF.guidance` now names the platform case the
same way, and `FIELDS` gained guidance of its own, having had none at all: the
direction is the entire difficulty, and the predicate that fixes it was
documented only by a one-line description and an example.

**One real gap, deliberately left open.** `XM913 Bushmaster --PART_OF--> XM30`
is not the same mistake. The Bushmaster is the gun *on* the XM30 — a component
of a platform, not a platform a programme fields — and there is no
Platform → Platform composition predicate. Adding one is a real decision about
whether venona models hardware breakdown at all, and it is not made here.
`FIELDS.guidance` tells the model to leave it rather than force it, so the fact
is dropped rather than recorded wrongly. If component relationships start
appearing often in the rejection log, that is the trigger to decide.

Unverified against a run at the time of writing: the next one measures whether
the rejection count for this shape goes to zero.
