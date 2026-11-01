# Graph schema

<!-- GENERATED from config/ontology.yaml by `make schema`. Do not edit. -->

Ontology version **6**.

## Entity types

| Type | Id prefix | Design token | Description |
|---|---|---|---|
| `Company` | `company:` | `--entity-company` | A commercial firm: a prime, a defense-tech startup, an integrator, a supplier, or an investor putting money into any of those. |
| `GovernmentBody` | `govt:` | `--entity-govt` | A civilian arm of a state: a department, an agency, a program or acquisition office, a regulator, a legislative committee, or a multinational body with staff and a budget. |
| `MilitaryUnit` | `unit:` | `--entity-unit` | A uniformed service, combatant command, formation, or standing joint task force. |
| `ResearchOrg` | `research:` | `--entity-research` | A body that studies rather than sells or buys: an FFRDC, a national lab, a university, or a think tank. |
| `Program` | `prog:` | `--entity-prog` | A named acquisition or modernization effort with a sponsor and a budget line. |
| `Technology` | `tech:` | `--entity-tech` | A capability or technical approach, independent of who sells it. |
| `Platform` | `plat:` | `--entity-plat` | A specific product, system or vehicle that someone builds. |
| `Person` | `person:` | `--entity-person` | A named individual in a professional capacity. |
| `Country` | `country:` | `--entity-country` | A sovereign state or self-governing territory. The top-level actor an organisation belongs to. |

## Predicates

| Predicate | Domain | Range | Symmetric | Meaning |
|---|---|---|---|---|
| `PART_OF` | Company, GovernmentBody, MilitaryUnit, ResearchOrg, Program | Company, GovernmentBody, MilitaryUnit, ResearchOrg, Program |  | The subject is a constituent part of the object. |
| `MANAGES` | GovernmentBody, MilitaryUnit | Program |  | The organization holds acquisition authority for the program. |
| `COMPETES_FOR` | Company, ResearchOrg | Program |  | The organization is bidding on, or has been awarded work under, the program. |
| `USES_TECH` | Program, Platform | Technology |  | The program or platform depends on the technology. |
| `OFFERS` | Company, ResearchOrg | Technology |  | The organization sells or markets a capability in this area. |
| `PRODUCES` | Company, ResearchOrg | Platform |  | The organization builds the platform. |
| `IMPLEMENTS` | Platform | Technology |  | The platform is a concrete implementation of the technology. |
| `COMPONENT_OF` | Platform | Platform |  | The subject is a physical or software component of the object platform. |
| `VARIANT_OF` | Platform | Platform |  | The subject is a version of the object design -- a block, mark, increment or numbered model of it. |
| `FIELDS` | Program | Platform |  | The program is deploying the platform to units. |
| `PARTNERS_WITH` | Company, GovernmentBody, MilitaryUnit, ResearchOrg | Company, GovernmentBody, MilitaryUnit, ResearchOrg | yes | Teaming, reselling, or an integration agreement. |
| `COMPETES_WITH` | Company, GovernmentBody, MilitaryUnit, ResearchOrg | Company, GovernmentBody, MilitaryUnit, ResearchOrg | yes | The two organizations compete in the same market. |
| `ACQUIRED` | Company | Company |  | The subject acquired the object. |
| `BASED_IN` | Company, GovernmentBody, MilitaryUnit, ResearchOrg | Country |  | The organization's home country. |
| `WORKS_FOR` | Person | Company, GovernmentBody, MilitaryUnit, ResearchOrg |  | Employment or service affiliation. |
| `LEADS` | Person | Company, GovernmentBody, MilitaryUnit, ResearchOrg, Program |  | The person has named leadership responsibility. |

## Infrastructure edges

Written by the projector, never asserted by a model -- so they are
not in the whitelist `graph/writer.py` enforces and cannot be
produced by extraction. Declared here so the graph's whole shape is
in one document.

| Predicate | From | To |
|---|---|---|
| `MENTIONS` | Article | Entity |

## Rewrites

Ill-typed shapes `analyze` rewrites to the legal edge instead of
rejecting (`method='extraction_rewritten'`, counted under
`rewrite_<PREDICATE>`).

| When (subject → object) | Becomes | Swapped |
|---|---|---|
| Platform `PART_OF` Program | `FIELDS` | yes |
| Company, GovernmentBody, MilitaryUnit, ResearchOrg `PART_OF` Country | `BASED_IN` |  |
| Company, GovernmentBody, MilitaryUnit, ResearchOrg, Program `LEADS` Person | `LEADS` | yes |
| Company `PRODUCES` Technology | `OFFERS` |  |
| Company `PRODUCES` Program | `COMPETES_FOR` |  |
| Platform `PART_OF, IMPLEMENTS` Platform | `COMPONENT_OF` |  |

## Examples

- `PART_OF` — PEO C3N --PART_OF--> United States Army
- `MANAGES` — PEO C3N --MANAGES--> Next Generation Command and Control
- `COMPETES_FOR` — Anduril Industries --COMPETES_FOR--> Next Generation Command and Control
- `USES_TECH` — Next Generation Command and Control --USES_TECH--> edge inference
- `OFFERS` — Anduril Industries --OFFERS--> sensor fusion
- `PRODUCES` — Anduril Industries --PRODUCES--> Lattice
- `IMPLEMENTS` — Lattice --IMPLEMENTS--> edge inference
- `COMPONENT_OF` — Talon IQ --COMPONENT_OF--> Model 437
- `VARIANT_OF` — F-16 Block 70 --VARIANT_OF--> F-16
- `FIELDS` — Next Generation Command and Control --FIELDS--> Lattice
- `PARTNERS_WITH` — Anduril Industries --PARTNERS_WITH--> Palantir Technologies
- `COMPETES_WITH` — Anduril Industries --COMPETES_WITH--> Palantir Technologies
- `ACQUIRED` — Anduril Industries --ACQUIRED--> Dive Technologies
- `BASED_IN` — Thales --BASED_IN--> France
- `WORKS_FOR` — Alex Miller --WORKS_FOR--> United States Army
- `LEADS` — Alex Miller --LEADS--> Next Generation Command and Control

## Changelog

- **v1** (2026-08-04) — Initial ontology. Five entity types; Contract and Event deferred pending a PIID-based identity rule. Thirteen asserted predicates with domain/range.
- **v2** (2026-08-06) — Added the Country entity type and BASED_IN.
Countries were ALREADY in the graph, typed as Organizations -- org:china and org:taiwan active, org:united-states, org:ukraine, org:united-kingdom, org:poland and org:iran provisional. That left domain/range checking off for what is now the highest-degree node class: `China COMPETES_WITH Anduril` and `Anduril ACQUIRED China` were both well-typed and unrejectable. Country has the stable canonical surface Contract and Event lack, so the v1 deferral reasoning argues FOR it, not against.
BASED_IN is its own predicate rather than an extension of PART_OF's range. PART_OF is the recursive org chart that the path and neighbourhood queries walk; a country inside it turns every route between two US organisations into a two-hop trip through a supernode, and "how does NGC2 relate to divisions within the Army" starts returning every organisation on earth. As a separate predicate it is filterable -- the graph view builds its predicate chips from /api/meta/ontology -- so the org chart can be read with country edges switched off.
BASED_IN deliberately declares no `kinds`. `analyze.py::_kind_for` defaults to kinds[0] because the model is never asked for one, so a declared vocabulary would be 100% defaulted and would repeat the PART_OF.kind mistake recorded in docs/adr/.
No existing predicate was narrowed, so this is additive for every v1 assertion except those about the retyped countries: expect a handful to stop type-checking and appear in the projector's `rejected` count. Some are PART_OF claims that are really BASED_IN; at least two are inverted-direction curation errors the ontology could not see while countries were Organizations.
MEMBER_OF (Country -> Organization, for NATO/AUKUS/EU membership) is deliberately absent, and this is a decision rather than a gap now. It was deferred because "its one live instance has two provisional endpoints"; there is no live instance at all. `SELECT COUNT(*) FROM assertions WHERE predicate='MEMBER_OF'` returns 0 against 1,816 entities including 29 countries, so promoting it would add a whitelist entry, an ontology edit and a projection path in exchange for zero edges. See ROADMAP's "Not doing". A first sighting with resolved endpoints reopens it.
- **v3** (2026-08-16) — Split Organization into Company, GovernmentBody, MilitaryUnit and ResearchOrg, and removed Organization.
One type held 346 entities on the first full run: SpaceX and Bain Capital beside the 82nd Airborne Division, PEO USC, the Department of Education, GUR, Colorado School of Mines, `org:war-on-the-rocks` (a publisher we read, not an actor) and `org:anniston` (a place). Its own description said so -- "any named body that can act" -- and its four examples deliberately mixed a service, a program office, a company and an agency, which is what the analyze prompt taught the model.
The cost was expressiveness, not tidiness. With buyer and seller in one type, COMPETES_FOR had to accept any organisation, so "which companies are competing for the programs my customer runs" -- the question the relevance profile is built around -- could not be asked of the graph. It is now Company|ResearchOrg --COMPETES_FOR--> Program, MANAGES is GovernmentBody|MilitaryUnit only, PRODUCES and ACQUIRED are Company only, and PART_OF spans all four because the org chart genuinely crosses them (US Army PART_OF Department of War; PEO C3N PART_OF US Army).
Organization was REMOVED rather than kept as a residual, because all data was deleted in the same change. A residual type is the right answer when there are minted ids to strand; with none, keeping it would only give the model somewhere to retreat to, and the guidance fields are where the four are actually taught apart. The trade is that an uncertain extraction now picks one of four instead of a catch-all: a wrong type is visible in the curation queue, where the old bucket hid it.
Three category errors are named as non-entities in the entity_types comment -- publisher, event, place -- because each reached ACTIVE on the first run and none is a body that can act.
org_type is now emittable. It was declared here, rendered into the analyze prompt by `ontology.extraction_guide`, stored in `entities.org_type`, projected to Neo4j and typed in schema.d.ts -- while `analyze_schema()` omitted it and set `additionalProperties: false`. The model was told a vocabulary it was structurally forbidden from using, and all 346 rows had org_type IS NULL. The per-type vocabularies are narrower now that the type carries most of the distinction.
- **v4** (2026-08-18) — Added the `rewrites:` table, COMPONENT_OF (Platform -> Platform), and ResearchOrg to PRODUCES.domain.
Run 06a83e69 was the measurement the 2026-08-16 decision record (docs/adr/) asked for, and the answer was no: all five PART_OF rejections were shapes the prompt explicitly warns about (three Platform->Program, two org->Country), and FIELDS was used correctly three times against three misses. More prose is the approach that failed its own measurement, so the recurring ill-typed shapes with exactly one safe legal rendering are now rewritten deterministically in `analyze` instead of dropped. The table lives here rather than in Python for the same reason domain and range do: the loader validates that every `when` is ill-typed and every `then` is well-typed, which also makes it acyclic.
OFFERS(Company, Platform) -> PRODUCES was considered and REJECTED: it strengthens "markets" into "builds", and integrators, suppliers and investors offer platforms they do not build. USES_TECH(MilitaryUnit, Program) -> MANAGES was rejected for asserting acquisition authority the evidence does not support, and LEADS(MilitaryUnit, MilitaryUnit) -> swapped PART_OF because the right reading is not derivable from types.
PRODUCES gains ResearchOrg because Lincoln Laboratory built QZS-7 and the type already sits in OFFERS and COMPETES_FOR.
COMPONENT_OF is added on thin evidence, recorded as such: the decision record (docs/adr/) 2026-08-16 deferred it pending component relationships "appearing often" in the rejection log, and only ONE Platform->Platform instance exists (Talon IQ --IMPLEMENTS--> Model 437; the XM913/XM30 case is Platform->Program and falls under the FIELDS rewrite). Added anyway because it is additive and reversible, and without it that rejection has no home. It is a separate predicate from PART_OF so hardware never enters the org chart that `anchors.py::infer_transitive` walks and the graph view's predicate chips can switch it off. If the shape does not recur, retiring it is one line and the assertions stay on disk.
- **v5** (2026-08-28) — Each entity type declares `confusable_with`: the types it is routinely READ AS, which is not the same as the types it relates to. Two entities sharing a name across a confusable boundary are one thing typed twice and the curator picks the type; across a real boundary they are two things and the answer is keep apart. Nothing in the names separates those, and only the domain knows which of its own boundaries blur. Program/Platform is deliberately empty: that distinction is why the two types exist. Program/Technology is also left out, because it holds both a real collision (an EU loan instrument and an edge safety framework both called SAFE) and a typing slip (DODIN), and the safe direction is to ask.
- **v6** (2026-10-25) — Added VARIANT_OF (Platform -> Platform). A block, mark or numbered model of a design had no predicate, so a curator could only keep it apart, and the adjudicator's nearest legal answer was COMPONENT_OF: the run wrote `F-16 Block 70 COMPONENT_OF F-16`, `SM-3 Block IIA COMPONENT_OF SM-3` and about twenty more like them, while 39 of one day's 145 keep-aparts were the same shape. COMPONENT_OF keeps its meaning, a part fitted to the platform.
