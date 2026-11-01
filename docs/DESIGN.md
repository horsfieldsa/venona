# Design

## Intent

An instrument, not a dashboard.

This is read every morning, at length, by one person who already knows the
domain. That points at a specific set of choices: dense but not cramped, quiet
enough that colour *means* something when it appears, and typographically
serious where there is prose to read.

Concretely — flat surfaces, hairline borders, no gradients, no elevation.
Motion is ≤120ms and only ever confirms an interaction; nothing animates
decoratively. **A terminal is drawn on a character grid, so corners stay nearly
square:** 0 / 3 / 4px, with a single 6px rung for the four surfaces that read as
glass rather than as a box — `Panel`, `Dialog`, the entity hover card and the ⌘K
palette.

*Amended 2026-08-15.* This set the radius scale at 0 / 2 / 3px with a note that
anything above 3px starts to read friendly. **The scale moved up one step** with
the aqua refresh, to carry the shift from field manual toward instrument. It is
still a closed scale, still enforced, and `--radius-hair` is still 0 — the run
spine, the freshness meter and the corner ticks are square, because a radius on
a 4px bar rounds it into a lozenge.

**One glow now exists**, and exactly one: `--ring-focus`, drawn at
`:focus-visible` and nowhere else. Shadow-as-elevation is still banned outright
— there is no `shadow-` utility to write, because `theme.css` clears the
namespace, and `ring-focus` is a named utility precisely so it cannot become a
general one. It is additionally gated on `[data-modality='key']`; the
2026-08-15 entry in `docs/adr/` explains why an ungated `:focus-visible` ring
is not an option here, and the 2026-08-08 entry it answers explains what
happened last time.

The reference points are a signals instrument and a NATO map legend, held in one
value: **the aqua of the mark's own plate, `#1891ac`, with the interface accent
one rung brighter at `#3fbdd6`.** The hue is taken from the logo rather than
invented, so the app and the hub state the same identity. This
replaces a phosphor green desaturated until it also read as field olive — a
truer terminal reference, and the wrong one: a P1 CRT is #33ff33 and unusable as
an interface accent, tactical black-and-orange is costume, and field olive
turned out to say *manual* where this should say *instrument*. This has to be
read every morning for an hour.

## Token architecture

Two tiers, and the separation is load-bearing. See `web/src/styles/tokens.css`.

**Tier 1 — primitives.** Raw values (`--grey-850`, `--aqua-400`). Referenced
only by tier 2. They carry no meaning: `--grey-850` does not know it is a panel
background.

**Tier 2 — semantic.** The only names a component may reference
(`--surface-raised`, `--text-secondary`, `--entity-prog`). Every visual decision
is expressed here.

The payoff, now cashed: light mode **is** a re-map of the tier-2 block — every
colour became `light-dark(light, dark)` in that one block and no component's
colour changed.

Two things it did not cover, both worth naming because the promise read as
covering them. Neither is visible from `tokens.css`:

- **Anything that reads a token's value instead of using it.** A custom
  property's computed value is its token stream, so the Cytoscape stylesheet —
  which draws to a canvas and cannot take `var()` — was reading twenty colours
  that would have come back as the literal text `light-dark(...)`. Cytoscape does
  not error on an unparseable colour; it substitutes its own default.
- **Anything that resets `color-scheme`.** The extension's card is a shadow root
  behind `all: initial`, whose initial value for `color-scheme` is `normal` —
  under which `light-dark()` returns the light side, on every page, silently.

### Which palette, and where the reader's answer lives

`data-theme` on `<html>` is the switch, and it takes exactly two values.
`:root` declares `color-scheme: light dark` and `:root[data-theme='light'
|'dark']` overrides it; those two rules declare `color-scheme` and nothing else.
**System is the absence of the attribute**, so no JavaScript resolves it and a
document that ships no attribute already agrees with the operating system.

The reader's choice is one cookie named `venona.theme`, holding `light`, `dark`
or `system`, and scoped to `SHARE_DOMAIN` in `web/src/stores/theme.ts` when that
is set. One choice then covers the hub and every instance under that domain
rather than one per origin (docs/adr/0177), and `localStorage` is a per-origin
mirror behind it. Five files read that cookie and
none can import the name from another, so `tests/test_theme_boot.py` holds them
equal:

| Surface | Reads it in | Writes it |
|---|---|---|
| App | `web/public/theme-boot.js`, before first paint | `web/src/stores/theme.ts` |
| Hub — public pages | `hub/hub/pages.py`, on the server | never |
| Hub — operator app | `hub/web/public/theme-boot.js`, before first paint | never |
| Consent screen | `api/routers/oauth.py`, on the server | never |

ONE WRITER, and the rest only read. Adding a second is a decision about the
shared channel rather than about a surface's chrome, which is why the hub's
operator app has no toggle even though it is an app and could carry one.

The server-rendered surfaces carry no script and are not going to gain one --
auth pages must not depend on a bundle loading (epic D), and these are the only
surfaces whose theme survives JavaScript being switched off. They each keep
their own copy of the two `[data-theme]` rules beside their inlined
`light-dark()` values. `tests/test_theme_boot.py` holds all seven holders of the
name in step.

A component never asks which theme is on. Anything that genuinely cannot resolve
`light-dark()` — a canvas, a badge painted by a service worker — goes through a
probe element or a generated per-theme map, never a second answer computed in
JavaScript.

That property survives only if it is enforced, so `make lint-tokens` fails the
build on:

1. a raw color literal outside `tokens.css`,
2. a tier-1 primitive referenced from a component,
3. a tier-2 name that is not declared — **the important one**, because a typo'd
   custom property is not a CSS error. It silently resolves to nothing and the
   element renders transparent.

Entity color tokens are named by the C4 id prefix (`--entity-org`,
`--entity-prog`, `--entity-tech`, `--entity-plat`, `--entity-person`,
`--entity-glossary`, `--entity-undefined`). That lets every consumer —
`EntityMention`, `EntityTypeChip`, the Cytoscape stylesheet, the mini-graph SVG
— resolve a colour with `var(--entity-${id.split(':')[0]})` and no lookup table
anywhere. Renaming these is therefore a cross-cutting change; don't.

## Tailwind, as a closed theme

*Reversed 2026-08-06. This section used to argue against Tailwind; the argument
and what changed are in docs/adr/.*

The reason to adopt it here is **enforcement, not speed**. `tokens.css` had a
spacing scale from the first commit and nothing ever checked it:
`lint_tokens.py` validated colour only, this document specified a type scale and
said nothing about spacing, and `ui_smoke.py`'s only geometry assertions were
"at most two control heights in the header" and "no horizontal scroll". So
`padding: 2px 4px`, `max-height: 460px` and `font-size: 10px` all passed every
gate, and the app drifted to nine distinct `padding-top` values, eight gaps,
five unrelated `max-height` clamps, three hand-typed overlay widths and four
different gaps inside the ONE header bar.

`styles/theme.css` clears every default Tailwind scale with `initial` before
declaring anything, so the generated utilities are venona's rungs and nothing
else. **`p-9` is not a value that renders slightly wrong — it does not exist,
and the build says so.** `bg-red-500` and the tier-1 `bg-grey-850` are equally
unwritable, so the two-tier contract holds at the utility layer too.

Note the class names kept their spelling but not Tailwind's values: `p-5` is
24px here, not 20px. `@theme inline` makes a utility emit `var(--space-5)`
rather than a copy, so `tokens.css` stays the system of record and light mode
is a re-map of one file rather than a rewrite.

The prose surface is still real CSS. `prose.css` and the `.mention` system are
not utilities and are not going to be: class soup is worst exactly where fine
control over measure, leading and inline mention styling matters most, and
`BriefBlock` builds `mention--${prefix}` at runtime. `base.css` is `@layer base`
and `prose.css` is `@layer components`, so utilities outrank the reset and the
reading surface outranks nothing it should not.

Still no component library. Material/Prime elevation, ripples and rounded chrome
read as "SaaS dashboard", and they fight the token layer by plumbing theme
values into JS separately.

## Spacing

**Eight rungs and one sub-rung, on a 4px base at the bottom.** `--space-1..8`
is 4/8/12/18/26/36/48/64. The ladder widens as it rises: 4-8-12-18 is for
packing controls, 26-36-48-64 is for separating regions, and the widening gaps
are deliberate — a value between rungs would be used to avoid choosing.

**`--space-half` (2px) is the only value below the floor**, and it exists for
one job: controls that must not change the height of the row they annotate —
key caps, chips, dense table rows. There used to be 1px *and* 2px doing that
job across 139 elements. One value below the floor is a rung; two is the drift
the floor exists to prevent.

**Within a bar, one gap.** The header separates its four ZONES at `--space-5`
(26) and items *within* a zone at `--space-2` (8); the nav's `--space-3` (12)
is the grouping rung between them. It previously ran 2/4/8/12 in one header
bar.

**Every control in a bar shares `--control-h`.** There is one smaller size,
`--control-h-sm`, for a rail's collapse affordance and a table row's action.
`ui_smoke.py` asserts a route's header controls have exactly one height.

**A worded button's leading glyph comes through `Button`'s `icon` prop** —
one glyph per semantic cluster, from `Icon.vue`'s curated set (ADR 0159).
A keyboard-first row carries a `Kbd` cap instead, never both; an icon-only
control is `IconButton`, whose required `label` keeps the word as the
accessible name and the hover title.

**Sizes that are not spacing get their own names** rather than borrowing a
rung: `--dot-sm/-md`, `--spine-h`, `--radius-hair`, `--label-micro`,
`--field-xs/sm/md/lg` for dense field widths, and `--scroll-sm/md/lg/xl` for
embedded scroll regions. A radius routed through the spacing scale rounds a 3px
bar into a lozenge, which is how that last one was found.

Enforced by `make lint-tokens`: no `<style>` block outside a two-file
allowlist, no arbitrary dimension (`p-[7px]`, `h-[460px]`), no arbitrary colour.
A var reference — `h-(--control-h)` — is not arbitrary; it resolves through a
token and remaps with it.

## Typography

**The faces are self-hosted, and this is not optional.** For a long time this
table named Charter and Inter while neither was bundled: on Linux `Inter var`
resolved to Liberation Sans, `Charter` resolved to Liberation *Sans*, and the
brief fell through its whole stack to generic `ui-serif` — a Times clone. Worse,
Liberation Sans ships **400 and 700 only**, so every `font-weight: 500/600/650`
in the codebase snapped or synthesised: the app had two weights where the design
assumed four, and no amount of colour work could make a heading out-rank body
text. Anything that names a family must ship it.

**TWO families, not three, and the split is the whole voice of the interface.**
A terminal reads as a terminal because the *chrome* is fixed pitch, not because
a few ids are — so `--font-ui` and `--font-mono` are the same family. Sustained
reading in mono is a pose, so the one surface read for minutes at a time gets a
proportional face built for it. Source Serif 4 left with the serif reading
column it was bought for.

| Context | Face | Size / leading |
|---|---|---|
| Brief body | `--font-prose` — Inter Variable | 16px / 1.7, `--measure`, `--tracking-tight` |
| Brief title | `--font-ui` | 24px / 1.4 |
| UI chrome | `--font-ui` — JetBrains Mono Variable | 12–16px / 1.4–1.55 |
| Ids, dates, counts | `--font-mono` — JetBrains Mono Variable | 11–14px, `tabular-nums` |
| Micro-label | `--font-ui`, uppercase | `--text-2xs` + `--tracking-label` |

`tabular-nums` on every counter is not a nicety — run progress counters that
reflow as they tick are genuinely hard to read.

**The scale has eight rungs and only four of them make hierarchy.** 11/12/13/14
are four genuine density contexts — footer and chips, tables and rails, UI body,
controls — and are not interchangeable with each other or with anything above.
16/18/24/32 are where ranking happens. The whole scale sits a rung above a
conventional dense UI because fixed pitch needs the room, and the gaps at the
top are deliberate: 14/16/20/26
sat too close to rank anything.

**A ninth rung sits outside that scale.** `--text-3xl` is 44px and is display
size, not hierarchy: the empty-corpus state, the login lockup and the
request-access heading are the three places the app speaks at display scale
rather than at interface scale, and all three were doing it at `--text-2xl` with
a paragraph's tracking. Nothing inside a populated view uses it. If a fourth
call site appears that is not one of those, the rung is being used as hierarchy
and the fix is `--text-2xl`.

**Three tracking values, not six.** `--tracking-label` for uppercase
micro-labels, `--tracking-brand` for the wordmark, `--tracking-display` for
`--text-3xl` and nothing smaller — at −0.035em it closes copy up rather than
tightening a heading. Nothing else tracks.

The brief body never compresses, and it is the brightest sustained text on the
page (`--text-body`). On a reading tool, prose dimmer than the chrome around it
is backwards.

## Composition

Content is **left-anchored at `--edge` on every route**, never centred. Centring
a 38rem column in a 1500px window puts ~470px of black on each side and gives
the page one vertical axis with nothing placed against it; symmetry with nothing
either side is emptiness, not minimalism.

```
[ --rail-margin ][ --measure ][ gap ][ --rail-side ]
   marginalia       reading            orientation
```

The header hangs off the same two lines — the wordmark in the margin rail, the
nav starting at `--edge` — so it is part of the page rather than an object
floating over it.

Below `lg` (1180) the rail structure leaves every route: the prose grid
centres its column, and `--edge` itself collapses to `--page-pad` in
`tokens.css`, so wide routes and the ContextBar fall back to the outer gutter
together. Measured: the admin rail sits at 140px on desktop and 36px on an
iPad; paying the 104px margin rail there was alignment with a line no route
draws.

## Borders

A **border** (four edges, a radius) means *this floats, or you can press it*. A
**rule** (one edge) marks a region boundary. Nothing else gets a line.

This is a rule about meaning, not decoration, and it was learned the hard way:
read as "everything gets a border", the runs list carried nine outlined status pills,
five outlined stage pills, two outlined buttons, two outlined panels and three
outlined footer chips — forty-odd rectangles, none out-ranking any other, on a
page with one actual button. `Panel variant="bare"` and `Chip variant="bare"`
are the default; the boxed forms are for `Dialog`, `CommandPalette`,
`KeyboardHelp`, `EntityHoverCard`, `GraphSearch`'s dropdown, `ToastHost`, `Kbd`
and the two inline notices.

## The accent

The accent means exactly three things: **active** (nav, selected row), **focus**,
and **the primary action**. Nothing else.

*Amended 2026-08-15.* It was phosphor green and is now aqua; no component
changed, because none of them names a hue. Note that **focus** in that list has
been aspirational since 2026-08-08 — the ring was removed and nothing draws one
today. Two entity hues moved with the accent, for this rule's sake:
`--entity-prog` and `--entity-tech` sat at 202° and 181° against an accent at
192°, so on the graph canvas a tech node and a *selected* node were nearly the
same colour and "you can act on this" stopped being legible. They are indigo
(232°) and orchid (315°) now.

Reading structure is neutral. Section rules and the brief's `why` bar used to be
accented with a bold accent label, which made a section divider and an aside the two
loudest things on the page while every real control was quieter than both. In a
single-accent system the accent has to mean "you can act on this".

## Chrome: two rows, one footer

The chrome is **split**, and the split is what keeps it from breaking as routes
get busier:

| Row | Height | Carries | Changes with route? |
|---|---|---|---|
| `AppHeader` | `--nav-height` 48px | wordmark, up to six destinations, ⌘K, gear | **No.** Identical everywhere — but a READER sees five of the six. |
| `ContextBar` | `--subnav-height` 38px | the route's identity, controls, actions | Yes — and collapses to nothing when a route has none. |

Both rows sit in `auto` grid tracks and are **lifted out of the window on a
negative margin** rather than removed: the brief's chrome travels up with the
page as the reading goes down and comes back down as it goes up, a third of a
pixel of bar for every pixel scrolled, and `f` there holds it away and folds the
rail. The
lift is split across the two bars, because a grid track cannot go negative and
one margin stops dead at the header's own height. Neither bar is ever `v-if`'d
(`ContextBar` holds the teleport targets) and neither is ever
`visibility: hidden` (that takes the nav out of the tab order). A
reading-progress hairline draws where the header was. ADR 0199.

There are six — Brief, Graph, Archive, Lookup, Learn, Admin — and only the last
carries `meta: { operator: true }`, so a reader is offered the first five. That
is the one thing the nav is allowed to differ on, and it differs by ROUTE MARKER
rather than by a second list:
`tests/test_frontend_drift.py::TestOperatorNav` fails if an entry and its route
disagree. `ui_smoke.py` compares the nav's bounding box across routes and still
passes, because an added item shifts all of them identically.

**An entry's `names` list has to claim every route it owns.** `isActive` is
`names.includes(route.name)` and nothing else, so a route no entry claims
renders the bar with nothing lit — on a page the reader reached from that very
row. `TestOperatorNav::test_every_route_name_is_claimed` holds it, with an
`UNLISTED` table for the routes reached from the palette, the gear or a rail.
The `Admin` entry is the one that carries a crowd: every operator destination
is reached from `AdminRail` rather than from this row, so all of them have to
be claimed by that single entry or the bar goes dark on eleven pages.

```
row 1  [venona] [Brief Graph Archive Lookup Learn Admin] ···· [⌘K ⚙]
row 2  identity │ controls ······························· actions
```

An earlier design had a global nav rail *plus* a per-view command bar. That is
82px of horizontal chrome, and the graph canvas needed a floating-bar exception
to survive it — needing an exception for the most demanding view is evidence the
design is wrong, not a carve-out. The redundancy was the **view title**: a bar
reading "Brief" underneath a nav where Brief is already active says nothing.

Route-scoped identity, filters and actions then lived in row one beside the nav,
and *that* is what made it fragile: Curation wants three tabs and a Rebuild
button, Graph wants two toggles, Settings wants Apply/Save — so the nav shifted,
squeezed or wrapped depending on where you were. **Navigation that moves under
the cursor when you navigate is the one thing a nav must not do.** `ui_smoke.py`
now measures the nav's bounding box on all six routes and fails if it differs.

**Three zones, three jobs.** `identity` states what you are looking at.
`controls` change *what you see*: tabs, filter chips, scope toggles, the filter
field. `actions` change *something*, and are the only place a filled button
appears in the chrome; at most two.

**One order AND one position, on every route.** The zones are not a bag; they
read left to right in one sequence, and the sequence starts on the same line
whichever route you land on:

```
[ parent › identity ]        [ tabs ] [ scope chips ]        [ ⌕ ] [ toggles ] [ actions ]
        left                  centred on the window                  trailing
```

- **tabs** are always a `SegmentedControl`, always `scrollable`, always first.
  A one-of-N is never a row of chips: three separate boxes say nothing about
  belonging to each other, or that picking one un-picks the rest. A tab set is
  deep-linkable through a query param (`?section=`, `?status=`, `?queue=`) —
  that makes a tab shareable, and it is the only way the phone smoke pass can
  reach a non-default tab.
- **the filter** is always `FilterField`, and it waits at the trailing edge as
  a **glyph** until it is asked for — by a click or by `/`. At every width, not
  just narrow ones: a filter is the least of the three things the bar carries,
  since you arrive at a page to read it and narrow it second, and a 16rem field
  sitting open on every route spends the widest control in the row on the one
  that is idle most of the time. What opens covers the whole row, identity and
  tabs included, rather than squeezing in beside them — everything at the
  trailing edge is `flex-none`, so a field sized to its own zone would be a
  sliver. `Esc` closes it and gives focus back to the glyph, and so does a press
anywhere outside it — capture-phase `pointerdown`, the same exit
`EntityHoverCard` uses and for the same reasons. **Closing does not clear**, so
a collapsed glyph over a filtered list is the only thing on screen saying the
list is filtered: it carries `IconButton`'s `active` state and says so in its
accessible name. That state is a text colour rather than a fill, which keeps it
outside the "no filled control" rule the bar's other zones live under. One component owns
  the search mark, the clear affordance, the in-flight spinner and the width,
  because those had diverged across five call sites while the field inside them
  was already shared.
- **a `Toggle` sits at the TRAILING edge**, in `#toggles`, not among the tabs.
  It is a switch that stays on screen showing its state, not one of the things
  you are choosing between, and a tab strip is harder to read with one inside
  it. Still outlined and still not a verb: the vocabulary rule is per zone, and
  this zone holds switches and verbs side by side.
- **`actions` hold verbs.** Going somewhere is not a verb. A back-link is the
  identity's `parent`; a forward link to a sibling view is a control, because
  it changes what you see.

**The controls are centred on the window, and that is what makes the position
fixed rather than merely the order.** The bar is a three-track grid — `1fr auto
1fr` — so the middle track is pinned to the middle of the viewport whatever the
identity beside it happens to say. Sized to its own text that zone ran from
49px to 358px across the routes, which put the tab strip anywhere between x=226
and x=535. *Navigation that moves under the cursor when you navigate is the one
thing a nav must not do* — row one has been held to that since ADR 0071, and
row two carries the tabs now.

Three details hold it up. The bar is padded **symmetrically** and the identity
carries the margin rail as its own `lg:` inset, so it still starts on `--edge`
while the centre stays the centre of the window rather than of a lopsided box;
the inset is `lg:` because that is where `tokens.css` stops collapsing `--edge`.
The middle track is placed with an explicit `col-start-2`, never by source
order, because the identity is `v-if`'d away on a route that has none and
auto-placement would slide the controls left and un-centre the row. And the
identity **truncates**, keeping its whole string in `title` and staying
selectable — a run's id is the case that needs it. `ui_smoke.py` compares the
zone's centre across routes, and checks that centre is the middle of the
window.

**The bar holds the route's sections; a `Panel` header holds a filter scoped to
that one panel.** Curation's merge-kind and People's access queue narrow one
panel's rows, not the route, and they sit on that panel's own header line.

**One control vocabulary per zone.** Everything in `controls` is outlined —
interactive chips and toggles, which differ only in that a toggle carries a
state dot (filled = on, hollow ring = off). Nothing there is ever filled,
because a solid accent control means "primary action". Without that rule
"whole graph" and "Rebuild graph" render identically while doing opposite kinds
of work: one changes what you are looking at, the other changes the data. In
`actions`, at most one thing is filled: a zone with two primary actions has
none.

**The identity may name its parent, and that is its one control.** `runs ›
run 4f2a`, where the first half is a link. Everything else there is a fact. The
label is the destination's *name*, never a sentence — the zone is uppercase
mono with label tracking, so "back to that brief" shouts and widens a
`flex-none` zone at the width with least room. It is neutral, never accent: the
accent means active, focus, or the primary action, and a parent link is none of
those.

**Full screen makes the bar unreachable, and the answer is to reveal it, not to
route around it.** Full screen collapses the ContextBar to a zero-height
`invisible` row, and a hidden input cannot take focus — so the graph's
find-a-node field, which sits in the bar with every other route's filter, would
be dead in the mode where finding a node matters most. `/` therefore leaves
full screen and focuses it, the rule `revealRail` already follows for `[` and
`]`: asking for a thing is a request to see it.

**One placement exception.** The graph's return-to-the-brief control stays in
`actions` rather than becoming the identity's `parent`, because it stamps the
item to scroll back to before it navigates, which a `RouterLink` has nowhere to
do.

The sub-menu is **recessed** — `--surface-base` under the header's
`--surface-raised` — and shorter, so row one reads as the application and row
two as the view inside it. Equal heights and equal ground read as two navs.

One rule survives from the single bar and is still load-bearing:

- **No view title *in a view*.** Route-level identity is the ContextBar, never a
  heading up there.

  This rule was originally written as a ban on any page-level heading at all,
  and that went too far: the top-left of every content column — where the eye
  lands first — was blank, and the first thing it found was a 12px uppercase
  panel label. What the rule actually means is "do not repeat the active nav
  item". A **content masthead** (`components/shared/Masthead.vue`) at the top of
  the column is not a repeat: it is the page's subject.

  With the ContextBar stating each route's identity, that scope narrowed again:
  Curation and Runs lost their mastheads, because the bar says the same thing
  and two headings for one page is worse than none. Runs keeps its `StatBlock`
  row, which is the page's data rather than its title.

  The brief held a dateline of its own for a while, on the argument that
  `DAILY BRIEF · 2026-08-06 · 24 ITEMS` is part of the document rather than
  chrome. It no longer does, and the reason is worth keeping: once times render
  in the reader's zone (ADR 0184), a dateline built from the instant and a
  ContextBar built from the editorial date are DIFFERENT DAYS for a reader far
  enough east. Two identities that can disagree are worse than one, so the bar
  carries the whole of it — `brief · 2026-08-22 01:39 GMT+12 · 24 items` —
  including the time, which is what tells two briefs generated on one day
  apart. `Masthead` still serves the derived reading list, where it frames
  rather than dates.

`AppFooter` (`--footer-height`) is permanent, including in the graph's full
screen, where it collapses to a 2px status hairline rather than disappearing. A
status line that vanishes when the user is most absorbed cannot report the thing
it exists to report. It carries global hints only — offline mode, theme, the
keyboard help — and no per-service health readout: an outage is reported by the
surface it breaks (`GraphView` for Neo4j, `RunsView` for the model, every view's
offline notice for the API), each derived from `useServiceHealth` over the one
app-level poll (`useAppPolling`), because an interval created in a component's
`onMounted` stops the moment anything hides it.

## Views

Every route renders exactly one `ViewShell`, on two axes: `width`
(`prose` | `wide` | `full`) and `scroll` (`page` | `none`). The second is the one
that breaks things — Curation and Graph are fixed frames with an inner scroll
region, and both depend on their content being a *direct* child, so
`scroll="none"` renders the default slot with no wrapper. `ViewShell` also owns
the loading, error and empty states, so the app degrades one way rather than one
way per view. See the "Add a view" checklist in `docs/EXTENDING.md`.

**A loading indicator is a line at the top of the section it describes, never
centred in it.** A spinner floating mid-pane reads as the app hanging; a line
where the content is about to appear reads as the section loading. The trap is
never the indicator itself — it is the container: a grid/flex item stretches to
its siblings' height, and any `items-center` on it then centres the row in the
stretch. `ViewShell`'s state (both scroll modes) and `PaneState` both anchor
top for exactly this reason; a new loading state inside a stretched child needs
`self-start` or an equivalent before it needs anything else.

## Language

Every string a person can read is part of the design, and the person reading
it has never developed this app. Two rules, both learned the expensive way
(the landing page and /learn each shipped in schema words and were
rewritten on operator feedback, 2026-08-24):

- **Reader words, never repo words.** "Corpus", "entity", "assertion",
  "gloss", "lane", "projection", "freshness", "instance", "MCP" name our
  internals; on a surface they become Venona's reading, a name, a
  relationship, a definition, a run in progress. This includes /admin and
  /settings — an admin is a user, not a developer. When a wire value must
  render, translate it at the edge: `web/src/lib/learnCopy.ts` is the
  pattern.
- **No em or en dashes in copy.** Recast with a period, comma, semicolon or
  a word; `·` separates labels. The standalone `—` empty-cell placeholder is
  typography and stays. `tests/test_copy_style.py` enforces this half.
- **The name is Venona, capital V, in every sentence of copy — and it is a
  company name, never pluralized or counted.** "Your Venonas" and "this
  Venona" are wrong; a deployment is a "domain" ("Your domains", "every
  token here"). Only literals stay lowercase: commands
  (`venona graph rebuild`), domains (venona.example.com), and identifiers.
  Also enforced by `tests/test_copy_style.py`.

## Responsive

**Every page on every surface works on a phone.** The floor is 390px (ADR
0152): each view renders cleanly there in both themes with no horizontal
document scroll, and 360px is best-effort. This is part of a page's definition
of done, not a pass that happens later — a view that only renders at laptop
width is not finished, on the web app and the hub alike.

Mobile is a **simpler experience, not desktop parity.** Stacking, hiding a
decorative rail, collapsing a multi-pane layout to one column, and an honest
"open on a larger screen" fallback are all legitimate answers; the bar is
*usable*. The graph explorer is the worked example: its two rails cannot fit a
phone and its nodes are not tap targets, so below the phone tier it hides the
rails and offers read-only pan and zoom rather than a reflowed redesign.

The scale and the rungs are tokens, never literals in a component:

- **Breakpoints** live in `theme.css` — `xs` 480px is the phone tier (`xs:` /
  `max-xs:`), `sm` 720, `nav` 800 (the header row's own collapse point, derived
  from its min-content width), `md` 860, `lg` 1180. A media query cannot read a
  token, so the one place breakpoint values appear literally outside
  `theme.css` is the pair of `@media` blocks in `tokens.css` — `--edge`
  collapses at 1180, `--page-pad` drops to `--space-4` at 480 — and
  `hub/hub/styles/public.css`, which carries the phone block by hand because the
  pages it styles ship with no bundle to read a breakpoint from. Its token
  VALUES are emitted from `tokens.css` rather than copied, and the hub's
  operator app is on the real thing: it imports `theme.css` and gets every
  breakpoint with it.
- **`--control-h-touch` (44px)** is the tap rung. Every interactive control
  rises to it under the phone tier; nothing ships 28px targets to a thumb.
- **`--edge` collapses to `--page-pad`** under the phone tier, in one `@media`
  in `tokens.css`. Every route re-anchors through it; nothing re-derives it.

The patterns, so a new view reaches for an existing answer:

- **Navigation** becomes a drawer below `nav` (`AppHeader.vue`), reusing the
  same list the desktop row renders.
- **Tables** either scroll inside their own `overflow-x: auto` container
  (`Table.vue` — the app's answer, because its tables are wide and read-only)
  or stack into one card per row with each cell captioned by its column header
  (the hub's `table.grid`, because a triage queue with forms in it cannot be
  side-scrolled on a phone). A bare `<table>` with neither is a phone defect.
- **`ContextBar`** wraps: identity and actions stack at the phone tier.
- **Unbreakable words** — emails, slugs, ids — get `overflow-wrap: anywhere`
  on their container, because a word that cannot wrap widens the page instead.
- **Hover-only affordances** need a tap path.

Where the check lives: `scripts/ui_smoke.py`'s phone pass runs the app at an
emulated 390×844 (the window cannot go below 500px headless — emulation is
what sets the width) and asserts per route that the document does not scroll
horizontally, the drawer replaces the nav row, the graph rails are hidden, and
tables sit in a scroll container. `tests/hub/test_ops.py` holds the hub's
stacking contract. A new view gets a line in the phone pass when it has
something the generic overflow probe cannot see.

## The error pages, and the field behind them

Every surface answers a wrong turn with a page of its own: the hub's
`pages.error()` (which every refusal already routed through), and the
app's `NotFoundView`. Behind each of them is one drawing.

**One geometry, two vocabularies.** The field cannot be a shared file: the
surfaces name colours differently, and the one that draws it on the hub is
the half with no build step.
`scripts/graph_field.py` holds the geometry and a token map per surface and
emits a copy per surface (`make field`, diffed by `make lint-generated`). The app's
copy names `--entity-slot-N`, never a type name, because decoration must stay
domain-invariant. The hub's copy is resolved out of `web/src/styles/tokens.css`
rather than hand-copied, as the rest of that surface's values now are
(`scripts/hub_public_styles.py`).

**Motion, per surface.** The hub gets two layers: a still SVG with
`hub/canvas/constellation.ts` fading a canvas over it. `make field` bundles
`constellation.hub.ts` into `hub/hub/static/constellation.js`, because the hub's
server-rendered pages have no bundler.
The hub's CSP carries `script-src 'self'` for it and nothing else (ADR 0203),
and it fetches the bundle from a URL naming the bundle's own bytes, which is
the one thing the hub answers as cacheable. The durations and easings are
resolved out of `hub/canvas/tokens.css` the way the colours are (ADR 0204). The still WAITS before showing itself, so on an
ordinary load the canvas has taken over before it ever appears and there is no
swap between two drawings at very different scales; scripts off, it fades in
and stays (ADR 0205). The canvas is laid out in the still's own viewBox and
framed by the same `xMidYMid slice` rule, so it is ONE drawing at every size
and on every page rather than one the viewport computes (ADR 0206). The app gets the still only. Scripts off and
`prefers-reduced-motion` both keep a composed graph rather than an empty
rectangle, on every surface.

**Where the hub draws it.** Every public page — sign-in, the chooser,
request-access, and every refusal — and none of the operator pages. The front door
should look like it belongs to the same product; `/admin` is a working surface, and a
drawing behind a table of addresses competes with what is being read. The field
was once refusal-pages-only on weight grounds; measured, it is 7.6KB raw and
1.65KB gzipped, taking sign-in from 5.2KB to 6.8KB over the wire, which is a
price worth paying for a coherent front door. `tests/hub/test_hub_errors.py`
holds both halves.

**Who gets a page.** The hub answers browsers and machines on the same paths,
so `hub/errors.py` negotiates on `Accept`: a browser gets HTML, everything else
keeps the JSON envelope byte for byte, and `/admin/instances/*` is never
negotiated whatever it asks for. The app's SPA 404 ships under a 200, which is
ordinary for an SPA and the alternative is duplicating the route list in Python.

## The graph route's query string

`/graph` is the one route whose query is a contract rather than an accident:
four components write it and the view reads it back under `<KeepAlive>`.
`web/src/router/graphRoute.ts` owns encode and decode, and they are inverses.
Nothing else builds this URL.

| key | shape | meaning | absent means | written |
|---|---|---|---|---|
| `mode` | `all` \| `focus` \| `brief` | what the canvas is scoped to | `focus` if a subject is named, else `brief` (the latest brief when `brief` is absent; `all` on an instance with no brief yet, ADR 0253) | always |
| `focus` | entity id | `focus`: the subject. `brief`: the node to highlight | nothing focused | when set |
| `depth` | 1–3 | hops from the subject | 1 | `mode=focus`, above 1 |
| `brief` | brief id | the brief the reader came from; in `brief` mode, the set to induce over | not from a brief | when set |
| `item` | `{brief_id}:iNN` | the paragraph to return to | no paragraph | when set |
| `types` | comma list | entity-type filter | no filter | when non-empty |
| `preds` | comma list | predicate filter | no filter | when non-empty |
| `minSrc` | 1–5 | minimum corroborating publishers | 1 | above 1 |
| `orphans` | `show` \| `hide` | override the mode's unconnected default | the mode's default | when it differs |
| `unconfirmed` | `show` | draw the names only one source has given us, hollow | confirmed names only | when set |
| `seed`, `from` | — | read for ever as `focus` / `item`; never written | | never |

Three rules hold it together.

**Encode and decode are inverses**, which is what makes the view unable to feed
itself: it watches the decoded intent's key, so writing state it already holds
leaves the watch source unchanged and the watcher never fires. There is no
"am I applying" flag to forget to clear, and browser back/forward still works —
which a flag breaks.

**`brief` is a separate key from `item`**, never sliced out of it. The item id
happens to start with the brief id, but C3 says a brief id is a string and never
a structure, and the component that builds the link has `briefs.current.id` for
free.

**Every value degrades to a default rather than blanking the view.** A `mode`
this build has never heard of falls back to `focus` or `brief`, which is how a
URL from a newer build stays harmless.

## Components

`components/ui/` is the only place bare HTML controls are styled. Everything
else composes them.

Current inventory: `AppShell`, `AppHeader`, `AppFooter`, `ViewShell`, `Panel`,
`SectionHead`, `Button`, `Chip`, `Toggle`, `SegmentedControl`, `ThemeToggle`,
`Input`, `FilterField`, `IconButton`, `Kbd`, `Table`, `Rail`, `RailLayout`,
`ListRail`, `RailLink`, `EmptyState`, `StateNotice`, `StatusNote`, `Dialog`,
`ToastHost`, `Spinner`, `Icon`. Plus
`components/nav/ContextBar`, `components/brand/Wordmark`, and
`components/marks/`.

`components/marks/` holds the two things that are allowed to look like
ornament, each with a rule that stops it becoming any:

| Mark | What it does | The rule |
|---|---|---|
| `Bracket` | Four `--tick` corner arms around a region | **Once per region.** No fill, no full rule, no radius. A `Panel` border says "this floats, or you can press it"; a bracket says neither, and marks a region as an instrument readout without adding a fourth box to a page that already has three. |
| `StatusBar` | A proportion `--spine-h` tall | **Always carries an `aria-label` spelling the split out in words.** The bar is the fast read, not the only one. The brief's freshness meter is one of these. |

`Rail` is the box those rows sit in — the inset ground, the radius, the
`data-rail` hook — and `RailLayout` is the page shape that places one beside a
pane. Three rails exist (Settings, Admin, the guide's contents) and the frame
was written out three times before, one of them under a comment reading "SAME
SHAPE AS THE ADMIN AND SETTINGS RAILS". Width, stickiness and any breakpoint at
which a rail disappears stay with the page that places it. **`RailLayout` wraps
neither slot**: both children have to be direct grid children, and under
`scroll="none"` it has to stay the shell's direct last child. `SetupView` is
the one page that does not use it, because the wizard also runs as a first-boot
page with no rail at all and a two-column grid would put it in the 16rem track.

`RailLink` is one row of a sidebar rail: the accent left edge and selected
ground that mean "you are here" everywhere in this app. It exists because that
treatment had three implementations -- the settings rail's destinations, the same
rail's indented documents, and the guide's contents column, whose comment already
said "SAME SHAPE AS `SettingsRail`" and then restated all six utilities anyway.
Splitting Settings and Admin into two rails would have made it four. It renders a
`RouterLink` for `to` and an `<a>` for `href`, because the guide's rows are
in-page anchors. **It is not `ListRail`**, which is full-bleed, bordered and
collapsible for the graph's long lists; these two look adjacent and merging them
would give the graph a rail it cannot collapse.

`SectionHead` is the quiet uppercase label that leads a rail, a panel, a card
section or a settings group, with whatever control belongs to its right. It
exists because that one role had SIX typographic treatments across 27 sites --
thirteen at 10px/medium and thirteen at 11px/semibold, which is a coin flip
rather than a density decision. It owns no outer margin: separation belongs to
the parent, where it genuinely varies.
Still unbuilt, and only worth building when something needs them: `Field`,
`Select`, `Slider`, `Tooltip`, `Menu`.

`components/shared/` holds cross-surface components used by brief, graph and
runs alike: `AgeBadge`, `StatusChip`. Written once, imported everywhere, never
re-derived per view.

**A second implementation of an existing primitive is a review failure.** If a
primitive nearly fits, extend it with a prop rather than forking it — `Panel`
gained `fill` rather than letting Curation keep reaching in with `:deep()`.

## Keyboard

One registry (`composables/useKeyboard.ts`), one capture-phase `window`
listener. Views register bindings; the `?` overlay is *generated* from the
registry, so it cannot drift from what actually fires. (Settings used to render
the same list a second time. The config workspace is `Admin → Config` now, and a
shortcut reference is neither configuration nor an account setting — the overlay
is the one place.)

**The global layer emits no bare letters.** `MergeReview` binds `r` to *relate*
and `DefinitionQueue` binds `r` to *reject*; both write to the database, and two
`window` listeners cannot be separated by `stopPropagation`. A Linear-style `g`
chord would have fired the route jump *and* the write. Globals therefore carry a
modifier (`⌘K`, `Alt+1..8`, `⌘,`) or are unambiguous punctuation (`?`, `Esc`).
`Esc` pops a LIFO stack of dismissables rather than broadcasting.

`/` focuses a filter and is registered by `FilterField` itself, never globally:
it exists exactly where there is a field to focus, and it carries no
`allowInInput`, so once the caret is in the box `/` types a slash.

## Accessibility

- Every entity hue is verified ≥3:1 against `--surface-base`.
- **Never colour alone.** Type chips always carry a text label. Single-source
  graph edges are dashed, so their epistemic weakness survives a colour-blind
  reader or a greyscale print.
- **The focus ring is drawn only for keyboard focus.** One rule in `base.css`,
  one token (`--ring-focus`), gated on `html[data-modality='key']`. A 3px
  translucent glow OUTSIDE the box, so focus never reflows a row — it replaced
  six `focus:border-accent-primary` utilities that swapped a 1px border colour
  and moved the control by a pixel.

  The gate is not optional and is the whole reason this exists again. An
  ungated ring was removed on 2026-08-08 because Chromium's per-frame
  keyboard-modality flag made it appear on links the user had *clicked*, every
  time the window regained focus. `lib/focusModality.ts` tracks the real
  modality — set by `Tab` and the arrows, cleared by any pointerdown — which is
  the approach that entry named as the way back. Read both the 2026-08-08 and
  2026-08-15 entries in `docs/adr/` before widening the selector.

  Still not covered: SVG children. `box-shadow` does not apply to them, so the
  mini-graph's `tabindex="0"` nodes remain without a visible focus position.
- `prefers-reduced-motion` is honoured globally.
- Entity mentions in prose are `role="button"` with `tabindex="0"` and respond
  to Enter — hover is an enhancement, not the only route.

## Specific choices worth knowing

**Entity mentions use a 1px dotted underline, never a background fill.**
Background fills on inline mentions destroy reading rhythm; in a paragraph with
fifteen mentions the page becomes a highlighter accident. The dotted rule reads
as annotation.

**The relevance note gets a left accent rule in the hover card.** It is the one
field that is read every single day, and it needs to survive a fast scan.

**Graph edge width encodes source count; single-source edges are dashed.** An
intelligence graph you cannot audit is a rumour mill, so epistemic strength is
visible before you click anything.

**An outage is reported by the surface it breaks.** Most "why is nothing
loading?" moments are Neo4j being down or the CLI being logged out, and both
should be answerable without opening a terminal — so the graph explorer says
when Neo4j stopped answering, the Runs page says when the model did, and every
control that would spend through a dead service is disabled with the reason. A
row of dots in the footer named the sick service, but on a different screen
from the symptom.
