# Research prompt

Drives discovery — the part that goes looking for what the feeds did not carry.
**This file is yours. Edit it freely.**

Each `kind: discovery` source in `config/sources.yaml` names one sweep area in
its `tags`; this file says what a good find looks like in any of them. Adding a
sweep area is a `sources.yaml` edit plus a heading here.

Discovery returns **candidate URLs only**. It never summarises: the pipeline
fetches, extracts and reads each page itself, so anything asserted here would be
a summary of a summary and could not be grounded.

---

## Sweep areas

### Command & control
NGC2 (Next Generation Command and Control), JADC2, CJADC2, ABMS, Project
Convergence, IBCS and IBCS-Maneuver, TITAN, Maven Smart System. Architecture
decisions, tier assignments, awards, protests, interface and data-standard
mandates, and which vendor owns which layer.

### Edge AI & autonomy
On-device inference, sensor fusion, ISR processing, model deployment forward,
autonomy stacks, operator-authored or forward-retrained models. Requirements
language implying on-platform compute is the tell worth catching: "at the
edge", "disconnected", "DDIL", "degraded", "denied", "without reachback",
"on-platform processing", "size weight and power".

Include **counter-signals**: programs consolidating processing into cloud or
enterprise data layers, or walking back edge requirements. A trend breaking
is exactly as newsworthy as a trend confirmed.

### UAS, drone & counter-UAS
SkyFoundry, LASSO, company-level sUAS, Group 1–3 programs, counter-UAS
(FS-LIDS, M-LIDS, Coyote, NGCM), Replicator and its follow-ons, DAWG drone
spending. The detect-classify-track layer matters more than the effector.

### Undersea & maritime autonomy
XLUUV/Orca, LUSV, MUSV, Ghost Shark, Dive-XL, mine countermeasures, undersea
ISR autonomy, AUKUS Pillar 2 payloads, persistent USV surveillance missions.
Undersea is the most extreme DDIL environment there is — no comms at all for
most of a mission — so onboard decision-making here is a leading indicator.

### Space & satellite
SDA tranches, Golden Dome, SB-AMTI, GMTI, proliferated LEO, tactical downlink,
on-orbit processing. The relevance is downstream: every sensor constellation
produces a track stream that has to be processed and thinned somewhere, and
contested bandwidth makes "send it all home" untenable.

### Service technology programs
Army, Navy, Air Force, Marine Corps, Space Force, SOCOM. Plus DIU, CDAO, and
acquisition-reform moves that change *how* programs buy software — OTAs, CSOs,
the software acquisition pathway, ATO reform, middle-tier acquisition.

### Competitors & market
Anduril, Palantir, Shield AI, Scale AI, Applied Intuition, Govini, Vannevar
Labs, Primer, Saronic, Skydio — and new entrants winning work that overlaps
edge inference. Contract wins, program selections, teaming, capability
launches, and earnings where they disclose government-segment movement.

### International & allied
NATO, AUKUS, Five Eyes, UK, Australia, Japan. Ukraine lessons-learned **only**
where they drive a US or allied requirement, program or acquisition decision.
Allied policy naming startup partnership or commercial autonomy is scaffolding
for market entry.

---

## What counts as a find

A **specific, attributable development**: an award, a solicitation, a protest,
an architecture decision, a named official stating a direction, a fielding
milestone, a budget line, a teaming arrangement.

Prefer the item with a number, a name and a date attached over the item with a
theme. *"Army opened sourcing for a sub-$150K counter-sUAS missile, RFI
responses due Aug 20"* is a find. *"The Army continues to modernise C2"* is not.

## Recency

**A page older than 48 hours is discarded before anyone reads it.** Not
deprioritised — dropped, by publication date, after the page is fetched. So an
older result is not a weaker answer, it is a wasted slot.

Prioritise the **last 24 hours**; 48 is the hard edge.

This paragraph used to say older material "is filtered out downstream", which
was true only at the brief — after the fetch, the extraction and a scoring call
had all been paid for. It is enforced at fetch now, so the instruction and the
behaviour finally agree.

Two consequences worth stating plainly, because they run against the instinct to
return a full list:

- **Returning three recent items beats returning eight where five are old.** The
  slots are not a quota.
- **A theme with nothing new is a real answer.** Return nothing for it. A quiet
  sweep is not a failed sweep, and reaching further back to fill the list is
  precisely the failure — the older pages that rank well are usually ones our
  own RSS feeds already carried, which means the slot bought nothing at all.

## Sources worth reaching for

DefenseScoop, Breaking Defense, Defense News, Inside Defense, C4ISRNET, Naval
News, USNI News, Air & Space Forces Magazine, Defense Daily, National Defense
Magazine, Military Times, ExecutiveGov, GovConWire, army.mil / navy.mil /
spaceforce.mil, SAM.gov notices, and company press releases.

A hint about where program news lives, not a whitelist. A good find on a site
not listed here is still a good find. Prefer primary sources — program office
releases, solicitation notices, official statements — over aggregators.

## Do not return

- **Pure geopolitics.** General war, conflict or diplomatic coverage belongs
  here only when it directly drives a US or allied program, technology or
  acquisition decision.
- **Opinion columns and think-pieces.** Analysis of a real development is fine;
  a columnist's view is not itself a development.
- **Vendor marketing.** A release announcing a real award or capability is a
  find; a blog post about digital transformation is not.
- **Market-research-report spam.** "The global C4ISR market will reach $X
  billion by 2030" is never a find.
- **A restatement of something already widely covered**, unless it adds a fact.
- **Anything you cannot point at a real URL for.** No inferred links, no
  reconstructed URLs, no "this is probably at".
