<script setup lang="ts">
/**
 * Row two of the chrome: everything that belongs to the CURRENT ROUTE.
 *
 *   [ identity ] │ [ controls ··················· ] [ actions ]
 *    what you are    tabs, filters, scope toggles    what you can
 *    looking at      -- the sub-menu proper          do to it
 *
 * Three zones with three different jobs, and the split is the whole point.
 * `identity` states a fact and is never a control. `controls` change WHAT YOU
 * SEE. `actions` change SOMETHING, and are the only place a filled button
 * appears in the chrome -- at most two.
 *
 * ONE CONTROL VOCABULARY PER ZONE. Everything in `controls` is outlined:
 * interactive chips and toggles, which differ only in that a toggle carries a
 * state dot. Nothing there is ever filled, because a solid accent control
 * means "primary action". Without that rule "whole graph" and "Rebuild graph"
 * render identically while doing opposite kinds of work -- one changes what you
 * are looking at, the other changes the data.
 *
 * None of this belongs in row one beside the nav: Curation wants three tabs and
 * a Rebuild button, Graph wants a toggle and a way back, Settings wants
 * Apply/Save, so the
 * nav would shift, squeeze or wrap depending on the route. Navigation that moves
 * under the cursor when you navigate is the one thing a nav must not do.
 *
 * The bar is RECESSED -- `--surface-base` under the header's `--surface-raised`
 * -- and shorter, so row one reads as the application and row two as the view
 * inside it. Equal heights and equal ground read as two navs.
 */
import { computed, ref } from 'vue'

import { useContextBar } from '@/composables/useContextBar'

const context = useContextBar()

/** `AppShell` measures this frame to know how far the lift can go: the row
 *  wraps at narrow widths and takes no height at all on a route with nothing to
 *  say, so its height is a measurement and never the `--subnav-height` token. */
const frame = ref<HTMLElement | null>(null)
defineExpose({ el: frame })

/** A route with nothing to say takes no height, so the brief keeps its full
 *  reading column. This is `v-show`, never `v-if`: the teleport targets below
 *  have to keep resolving, because a `<Teleport>` whose target is missing
 *  throws -- the same reason `AppShell` collapses the header rather than
 *  unmounting it. */
const empty = computed(
  () =>
    !context.identity &&
    !context.parent &&
    !context.controls &&
    !context.search &&
    !context.toggles &&
    !context.actions,
)
</script>

<template>
  <!-- Left-aligned to `--edge`, so the bar hangs off the same vertical line as
       the page content beneath it, and padded to `--page-pad` on the right like
       every other row of chrome.

       Wrapping starts at `md`, not at the phone tier, because the squeeze does
       not wait for a phone. `#context-actions` is `flex-none` and the controls
       zone is `grow min-w-0`, so as the row narrows the tabs are what gives --
       and a tab strip that cannot shrink below its own labels paints straight
       over the actions beside it. Measured 2026-08-07 on an iPad in portrait (820px):
       "HISTORY" rendered on top of "LOOK UP ALL". Between `xs` and `md` is
       precisely the band that had neither the wrap nor the desktop's room.

       ABOVE `md` the wrap is gone and the same collision returns on a wider
       tablet -- measured at 1024px, Curation's "LOOK UP ALL" painted over the
       "QUESTIONS" tab. The track sizes are what decide it: the trailing track
       takes `minmax(auto,1fr)` so it can never be laid out narrower than the
       buttons it holds, and the middle takes `minmax(0,auto)` so the tab strip
       is what gives. That strip is `scrollable` and carries
       `overflow-x-auto`, so giving is something it can actually do; the
       actions cannot shrink at all, and a track smaller than its contents
       spills them LEFT under `justify-end`, straight over the tabs.

       Centring degrades before it breaks: while there is free space the two
       `1fr` tracks are equal and the middle sits on the window's centre, and
       when the actions need more than their share they take it and the tabs
       shift left rather than disappear under them. -->
  <!-- THREE elements, one job each, and the nesting is the point.

       `.context-frame` is the collapse and nothing else: a single-row grid
       animating between `1fr` and `0fr`. That is how a box of unknown height
       animates to nothing -- and this one IS of unknown height, because the row
       wraps below `md`, so there is no length to interpolate between.

       The clipper carries `min-h-0` and `overflow-hidden`. Both are required:
       a grid item's `min-height` is `auto`, so without the first the row cannot
       shrink past `.context`'s own `min-h-subnav` and the bar simply refuses to
       collapse.

       BOTH WRAPPERS CARRY `min-w-0`, for the reason `AppShell` states over its
       own rows: a grid item's `min-width` is `auto` too, so it refuses to lay
       out narrower than its content's min-content width, and one wide leaf
       widens the shared track past the viewport.

       `.context` itself is untouched, and that is deliberate rather than lazy.
       It stays the positioned ancestor, because `FilterField`'s expanded panel
       is `absolute inset-0` against exactly this element (see that component's
       header) -- the panel and the clipper are then the same box, so nothing is
       cropped. It keeps its ground and its `min-h-subnav`, which is what
       `scripts/ui_smoke.py` measures to prove row two reads as recessed under
       row one. And it keeps its three tracks, which every `md:col-start-*`
       below is placed against. -->
  <div
    ref="frame"
    v-show="!empty"
    class="context-frame grid min-w-0 grid-rows-[1fr] mt-(--chrome-lift-context)"
  >
    <div class="min-h-0 min-w-0 overflow-hidden">
      <div
        class="context relative min-w-0 gap-4 min-h-subnav py-1 px-page border-b border-b-border-subtle bg-surface-base items-center max-md:flex max-md:flex-wrap md:grid md:grid-cols-[minmax(0,1fr)_minmax(0,auto)_minmax(auto,1fr)]"
      >
        <!-- Parent and identity are one cluster: together they answer "where am I",
             and the link half is the only control this zone is allowed, because
             going back is not a verb and does not belong beside Hide. Never accent
             -- that is reserved for active, focus and the primary action.

             It TRUNCATES rather than sizing the bar. Sized to its own text it ran
             from 49px to 358px across the routes, and whatever sits beside it moves
             by the difference. Here the grid decides the geometry and the text
             fits itself to the track; the whole string stays in `title` and stays
             selectable, which a run's id needs. -->
        <div
          v-if="context.identity || context.parent"
          :title="context.identity || undefined"
          class="identity mono md:col-start-1 lg:ms-(--rail-margin) flex items-center gap-1 min-w-0 text-2xs tracking-label uppercase text-text-quiet whitespace-nowrap"
        >
          <template v-if="context.parent">
            <RouterLink
              :to="context.parent.to"
              class="parent flex-none text-text-quiet hover:text-text-secondary"
            >
              {{ context.parent.label }}
            </RouterLink>
            <span aria-hidden="true" class="flex-none">›</span>
          </template>
          <!-- `min-w-0` on both this and the row above it: a flex child's default
               `min-width: auto` refuses to shrink below its own text, so without it
               the ellipsis never resolves and the overflow lands outside the
               track. -->
          <span v-if="context.identity" class="min-w-0 truncate">{{ context.identity }}</span>
        </div>

        <!-- CENTRED ON THE WINDOW, in the middle track of three, and pinned there
             by the two `1fr` tracks either side rather than by whatever the
             identity happens to say.

             The bar is padded symmetrically for that reason, and the identity
             carries the margin rail as its own inset instead -- so it still starts
             on `--edge`, the line the nav above and the content below both hang
             off, while the middle track's centre is the centre of the viewport
             rather than of a lopsided box.

             The inset is `lg:`, not `md:`, because that is where `--edge` actually
             includes the rail margin: `tokens.css` collapses it to `--page-pad`
             below 1180, so an inset applied any lower would put the identity 104px
             right of a content column that had already moved left. What the reader chooses between sits in one place on
             every route; a strip that starts wherever the text to its left ended is
             navigation that moves under the cursor, which ADR 0071 rules out for
             row one and row two carries the tabs now.

             `col-start-2` explicitly, never by source order: the identity is
             `v-if`'d away on a route that has none, and auto-placement would slide
             this into the first track and un-centre the whole row. -->
        <div
          id="context-controls"
          class="controls md:col-start-2 min-w-0 max-w-full flex items-center justify-center gap-2 flex-wrap"
        />

        <!-- The trailing edge: the filter's glyph, then switches, then verbs. A `Toggle` stays on screen
             showing its state and is not one of the things you are choosing
             between, so it reads badly inside a tab strip and sits out here with
             the actions instead. Still OUTLINED, and still not a verb -- the
             vocabulary rule is per zone and this zone holds both, which is why the
             filled-control check reads `#context-actions` alone. -->
        <div class="trailing md:col-start-3 flex-none flex items-center justify-end gap-2 max-md:ml-auto">
          <div id="context-search" class="flex items-center gap-2" />
          <div id="context-toggles" class="flex items-center gap-2" />
          <!-- Glyphs between `md` and `lg`, reaching into `Button`'s own
               `btn-label` hook. A HYPHEN, not the BEM underscore the rest of that
               component uses: Tailwind rewrites `_` to a space inside an arbitrary
               variant, so `[&_.btn__label]` compiles to `& .btn  label` and matches
               nothing -- silently, with the labels simply staying visible. That band is the one where this row is a single
               grid line: below `md` it wraps and a second row of full words is
               more readable, above `lg` there is room for both. The rule is a
               descendant selector and `Button`'s is a bare utility, so this wins on
               specificity rather than on Tailwind's sort order -- which is the
               difference between a scoped override and a coin toss. -->
          <div
            id="context-actions"
            class="actions flex items-center gap-2 md:max-lg:[&_.btn-label]:hidden"
          />
        </div>
      </div>
    </div>
  </div>
</template>
