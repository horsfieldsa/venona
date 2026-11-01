---
status: accepted
date: 2026-08-31
---

# 2026-08-31 — Row two has a grammar, the identity may name its parent, and one component is the filter

ADR 0071 split the chrome into a fixed nav and a per-route bar, and 0074 anchored
both to `--edge`. Neither said what row two may CONTAIN or in what order, and
over about forty routes-worth of work the answer diverged five ways.

Sub-navigation was drawn in the bar as a `SegmentedControl` (Brief, Curation,
Learn, Guide, Graph), in a `Panel` header as a `SegmentedControl` (Curation's
merge kind, People's access queue), in the page body as a row of
`Chip interactive` (Lookup's all / sourced / undefined), in the page body as a
`SegmentedControl` (Agents' client picker), and as a vertical rail (Admin,
Settings, the guide's contents). Search was worse, because it had no component:
`ArchiveView` assembled a field, a width and a loose `Spinner` inline;
`GraphSearch` assembled the same row under a different class name;
`RelevanceTailor` assembled it a third time; `PathFinder` twice more. Three views
registered `/` with three different labels and two different group names.
`Lookup` and `Search` ended up with an almost empty bar above a page carrying all
of their chrome, which is the design inverted.

**Row two now has one order, and it is the same on every route:**

```
[ parent › identity ] │ [ tabs ] [ scope chips / toggles ] [ ⌕ filter ]   [ actions ]
```

Tabs are always a `SegmentedControl`, always first, and deep-linkable through a
query param. `actions` hold verbs only.

The filter is always `FilterField`, and it waits at the trailing edge as a
GLYPH until a click or `/` asks for it, at every width. It was tried open and
inline first, at 16rem, and that spends the widest control in the row on the
thing that is idle most of the time: you arrive at a page to read it and narrow
it second. What opens covers the whole row, identity and tabs included --
sized to its own zone it would be a sliver, since everything at the trailing
edge is `flex-none` and the row gives elsewhere.

It closes on `Esc` and on a press outside, and **closing does not clear**. That
combination needs the glyph to carry an applied filter: a collapsed control
over a list that is short for a reason nothing on screen states is worse than
the open field it replaced. `IconButton`'s `active` is a text colour rather
than a fill, so it says so without breaking the no-filled-control rule. The
outside press is capture-phase `pointerdown` (`EntityHoverCard`'s pattern), and
it cannot eat the press that opens the panel because the glyph opens on
`click`, which lands after `pointerdown` has been and gone.

**And the order is not enough on its own.** Sized to its own text, the identity
zone ran from 49px on `/search` to 358px on `/briefs`, and the controls begin
where it ends -- so the tab strip landed anywhere between x=226 and x=535 and
visibly jumped on every navigation. That is the failure ADR 0071 names for row
one, and row two is navigation now.

The bar is therefore a three-track grid, `1fr auto 1fr`, with the controls
**centred on the window**. A reserved track for the identity was tried first
and pinned the controls at an arbitrary x=433, which reads as neither aligned
with anything nor deliberate; centring gives the same fixed position a reason.
The bar is padded symmetrically and the identity carries the margin rail as its
own `lg:` inset so it still starts on `--edge`, and the middle track is placed
with an explicit `col-start-2` -- the identity is `v-if`'d away on routes that
have none, and auto-placement would slide the controls into the first track and
un-centre the row. `ui_smoke.py` compares that centre across routes, and checks
it is the middle of the window.

**A `Toggle` moved to the trailing edge**, into its own `#toggles` zone beside
the actions: full screen on the graph, "N hidden" on the runs. A switch that
stays on screen showing its state is not one of the things you are choosing
between, and a tab strip is harder to read with one inside it. It is not an
action either -- it changes what you see, not what is -- so `actions` keeps its
verbs-only rule and the filled-control check now reads every zone but that one.

Two identities were rewritten rather than truncated, because both were cut
mid-number and a half-written year reads as a bug rather than as an
abbreviation: the archive drops its date span (it is in the table's first and
last rows), and the lookup leads with the proportion it exists to report,
`143 undefined of 264`. A run's id still overruns the track; it keeps the whole
string in `title` and stays selectable, and its head -- the part anyone
eyeballs to match a run -- is what shows.

**`FilterField` owns the assembly, not the field.** `Input` was already shared;
what had diverged was the search glyph, the clear affordance, the in-flight
spinner and the width around it. Two consequences worth stating because they
are the kind of thing that gets "tidied" back:

- The `/` binding lives in the component. It is per-field and never global —
  ADR 0070's rule stands, and the two views that hid the `?` overlay row with an
  empty-label getter no longer need one, because a hidden filter is an unmounted
  component.
- Padding and height moved INTO `Input` as `leading` / `trailing` / the phone
  tap rung, rather than arriving as classes from the call site. A utility here
  and its override there tie on specificity and Tailwind's sort order decides.
  Adding the rung inside also fixed every other boxed field in the app, all of
  which were shipping a 28px target to a thumb.

**The identity may name its parent, as a link — its one control.** Back-links
were in `actions` beside Hide and Clear history, which made "go back" render
identically to a verb; `Run detail` carried four. The parent label is the
destination's NAME, never a sentence: the zone is uppercase mono with label
tracking. It is neutral rather than accent, because the accent means active,
focus or the primary action.

**The graph's search is in the bar too, and full screen is handled rather than
worked around.** Full screen renders `shell-rows-collapsed` and stamps
`overflow-hidden invisible` on the bar, and a `visibility: hidden` input cannot
take focus -- so a field up there is dead in the mode where finding a node
matters most. Leaving it on the canvas was tried first and rejected: one route
keeping its own search is exactly the inconsistency this record exists to end,
and the same file already had the better answer. `revealRail` leaves full
screen and opens a rail on `[` and `]`, on the grounds that asking for a panel
is a request to see it; `/` now does the same for the search. It is registered
in the view rather than through `FilterField`'s own `hotkey`, because that one
goes straight to `focus()` and this has somewhere to be first.

**One placement exception remains.** The graph's return-to-the-brief control
stays in `actions` rather than becoming the identity's `parent`, because
`backToBrief` stamps `returnItemId` before it navigates -- the assignment that
anchors the reader to the item they left -- and a `RouterLink` has nowhere to
put it.

**What was NOT changed, deliberately.**

- `docs/DESIGN.md` caps `actions` at two. Four routes carry three: Brief (older,
  newer, Rewrite), Config (spinner, Apply, Save), Run detail before this change,
  and People. Run detail is fixed here; the other three are debt, and the count
  stays a review guideline rather than a lint — a count check would go red on
  work this pass deliberately did not do. What IS checked is that at most one
  control in `actions` is accent-filled, which is the half that carries the
  meaning.
- `SetupView` keeps a hand-written grid. The wizard also runs as a first-boot
  page with no rail, and `RailLayout`'s second track is where the content goes,
  so rendering it without a rail would put the wizard in the 16rem column.
- `ContextBar`'s empty state stays, though every in-app view sets `identity` and
  so it has never fired. It is what makes `AppShell`'s `auto` track honest.

**Found while auditing, and fixed here.** `AppHeader::NAV` matches the active
item with `names.includes(route.name)`, and three routes — `/admin/agents`,
`/setup` and a run's metrics — appeared in no entry's list, so each rendered the
whole nav unlit. `TestOperatorNav` compared only the `operator` flag and could
not see it. It now asserts every declared route name is claimed by an entry or
named in an `UNLISTED` table with the reason it is reached another way.

**Consequence.** `web/src/components/ui/FilterField.vue`, `Rail.vue` and
`RailLayout.vue` are new; `AdminRail` moves to `components/nav/` beside
`SettingsRail`, and the guide's rail joins them on `Rail`.
`tests/test_frontend_drift.py::TestChromeGrammar` fails on a `type="search"`
outside `FilterField` and on an `Input` with a `Spinner` sibling — the second is
the load-bearing half, because `PathFinder` and `RelevanceTailor` were
filter-shaped fields that never carried the attribute. `scripts/ui_smoke.py`
walks `CHROME_ROUTES` at 1500, 820 and 390 rather than three hand-written
subsets that had drifted apart, generalises the zone-overlap probe off
`/curation` alone, and gains the 700px collapse pass.
