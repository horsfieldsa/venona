---
id: brief:2026-08-04
date: "2026-08-04"
title: "NGC2 architecture fight moves to the edge"
dek: "The Army's command-and-control rebuild is converging on a disconnected-first design, and the vendor field is sorting itself accordingly."
sources:
  - id: doc:seed-ngc2-overview
    title: "Army details NGC2 architecture as prototype phase closes"
    url: "https://example.test/ngc2-architecture"
    publisher: "DefenseScoop"
    published_at: "2026-08-03T14:20:00Z"
  - id: doc:seed-ngc2-competition
    title: "Anduril and Palantir split NGC2 prototype awards"
    url: "https://example.test/ngc2-awards"
    publisher: "Breaking Defense"
    published_at: "2026-08-03T09:05:00Z"
  - id: doc:seed-titan-award
    title: "TITAN ground stations reach first unit equipped"
    url: "https://example.test/titan-fue"
    publisher: "C4ISRNET"
    published_at: "2026-08-02T17:40:00Z"
  - id: doc:seed-edge-ai
    title: "DIU pushes inference onto the vehicle"
    url: "https://example.test/diu-edge-inference"
    publisher: "Defense News"
    published_at: "2026-08-01T11:00:00Z"
unresolved_mentions:
  - "Project Convergence Capstone 6"
# THE ITEM FORMAT, and this fixture had none of it for months.
#
# `brief_items` is how the brief has rendered since the format changed; the
# `brief_blocks` below it are the LEGACY path, kept because briefs written before
# the change still have to render. A seeded instance therefore exercised only the
# path that exists for backwards compatibility, and never the one that runs today
# -- `make seed`, open the brief in a browser, and the whole item rail was absent.
#
# `scripts/ui_smoke.py` caught it as "the brief's rail carries a top block --
# {'block': False, 'items': 0}" and reported it as a rendering fault, which is
# what a missing fixture looks like from the outside.
#
# `top_rank` is 1..3 and drives the "Top N" rail; `ord` is the render order, the
# column the Postgres port had to add because there is no rowid to fall back on.
items:
  - section: "COMMAND & CONTROL"
    cluster_id: cluster:seed
    headline: "NGC2's architecture is now disconnected-first, and MOSA is a gate"
    state: new
    top_rank: 1
    top_line: >-
      The Army stopped treating the network as reliable, which reprices every
      vendor that assumed it was.
  - section: "COMMAND & CONTROL"
    cluster_id: cluster:doc:seed-ngc2-competition
    headline: "Anduril takes the edge tier, Palantir the enterprise layer"
    state: new
    state_note: "the split, not the winners, is the news"
    top_rank: 2
    top_line: >-
      The prototype split follows the architecture: edge to Anduril, integration
      to Palantir.
  - section: "EDGE AI & AUTONOMY"
    cluster_id: cluster:doc:seed-edge-ai
    headline: "DIU asks for inference on the vehicle and weights degraded-mode behaviour first"
    state: new
    top_rank: 3
    top_line: >-
      Most submissions cleared TRL 6 in a laboratory and had never run on a
      moving platform.
  - section: "EDGE AI & AUTONOMY"
    cluster_id: cluster:doc:seed-titan-award
    headline: "TITAN reaches first unit equipped at PEO IEW&S"
    state: follow_up
    state_note: "a fielded reference for the sensor-fusion pattern"
---

## Command and control

The Army's Next Generation Command and Control program closed its prototype
phase this week, and the architecture that emerged is more opinionated than the
one PEO C3N described a year ago. Officials now talk about NGC2 as a
data-centric layer that must keep functioning when the link to a data centre is
gone, rather than as a thin client onto cloud services. Alex Miller framed the
shift bluntly: the network is the thing most likely to fail, so nothing critical
can assume it.

That has consequences for the stack. A Modular Open Systems Approach is now
mandated rather than encouraged, and PM Tactical Network is being asked to
specify interfaces before capability. The practical effect is that MOSA
compliance is becoming a gate rather than a talking point, and vendors who
treated it as one are re-architecting.

## Vendors

Anduril and Palantir split the prototype awards, which is the outcome most
observers expected but not the split. Anduril's Lattice was selected for the
edge tier, where DDIL conditions dominate; Palantir took the enterprise
integration layer. Lockheed Martin remains in the competition but is now clearly
in the second tier.

The award went out under OTA, which compresses the timeline considerably and
means the follow-on decision arrives sooner than the traditional milestone
calendar would suggest. Vendors still need an ATO before anything touches an
operational network, and that remains the real schedule risk.

## Edge versus cloud

DIU's latest solicitation is the clearest signal yet on direction. It asks for
inference on the vehicle, explicitly, and the evaluation criteria weight
degraded-mode behaviour above throughput. Most submissions cleared TRL 6 in a
laboratory but had never run on a moving platform — which is exactly the gap
that separates a demo from a program.

TITAN reached first unit equipped at PEO IEW&S, and its sensor fusion pipeline
is the closest thing the Army has to a fielded reference for this pattern.
Replicator continues to pull in the same direction. Whether CJADC2 converges on
the same assumptions is still open.
