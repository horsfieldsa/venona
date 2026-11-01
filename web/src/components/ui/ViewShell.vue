<script setup lang="ts">
/**
 * The one page scaffold. Every route renders exactly one of these, so padding,
 * heading treatment and who scrolls are decided once rather than per view.
 *
 * TWO AXES, and conflating them is what breaks things:
 *
 *   `width`   how wide the content column is.
 *   `scroll`  WHO scrolls. This is the dangerous one.
 *
 * `scroll="page"` -- this element is the scroll container. Ordinary.
 *
 * `scroll="none"` -- this element is a fixed frame and something INSIDE it
 * scrolls. Two views depend on that arrangement in ways a wrapper element would
 * silently break:
 *
 *   - `CurationView`'s rail grid takes its height from
 *     `.view--scroll-none > :deep(:last-child)` below, which only reaches a
 *     DIRECT child of the flex column. Wrap the slot and the queue collapses to
 *     content height, taking the action bar off screen.
 *   - `GraphView`'s canvas is a grid item stretching against an unbroken
 *     `height:100%` chain. A scroll container anywhere in that chain measures
 *     the canvas at 0 and Cytoscape renders nothing at all. (The hazard is the
 *     chain, not the mechanism -- see the comment at the container for why
 *     `position:absolute;inset:0` must not come back.)
 *
 * So `scroll="none"` renders the default slot with NO wrapper. That is why the
 * template branches instead of using one container with a modifier class.
 */
import { computed, onActivated, onDeactivated, onUnmounted, ref, useSlots, watchEffect } from 'vue'

import ApiUnreachable from '@/components/shared/ApiUnreachable.vue'
import Spinner from '@/components/ui/Spinner.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import {
  claimContextBar,
  releaseContextBar,
  type ContextBarParent,
} from '@/composables/useContextBar'
import { useServiceHealth } from '@/composables/useServiceHealth'

const props = withDefaults(
  defineProps<{
    /** `prose` = the reading measure, `wide` = `--measure-page`, `full` = edge to edge. */
    width?: 'prose' | 'wide' | 'full'
    scroll?: 'page' | 'none'
    loading?: boolean
    /** Renders the standard error state, so views do not hand-roll one each. */
    error?: string | null
    /**
     * The ContextBar's identity zone: what you are looking at, stated as a
     * fact. `curation · 147 open`, `runs · 9 total`. Lowercase, because it is
     * rendered uppercase with label tracking and the value often carries an id
     * or a filename that must not be transformed twice.
     *
     * This is NOT a page heading. Where a view had a `<Masthead>` saying the
     * same thing, the masthead goes -- two headings for one page is worse than
     * none. The brief keeps its dateline, which is part of the document rather
     * than part of the chrome.
     */
    identity?: string
    /**
     * Where this route hangs off, rendered as a link before the identity:
     * `runs ›` on a run, `run 4f2a ›` on its metrics.
     *
     * This is how going BACK leaves the actions zone, which is for verbs. A
     * back-link rendered beside Hide and Clear history reads as one of them.
     *
     * Only for a parent that is a plain navigation. A control that has to do
     * something on its way out -- the graph's return to the brief stamps the
     * item to scroll to -- stays a button in `actions`.
     */
    parent?: ContextBarParent | null
  }>(),
  {
    width: 'wide',
    scroll: 'page',
    loading: false,
    error: null,
    identity: '',
    parent: null,
  },
)

/**
 * The ContextBar's content is teleported, but whether the bar takes any height
 * at all is decided from here -- see `useContextBar` for why ownership is held
 * by token rather than by mount order.
 */
const slots = useSlots()
const token = Symbol('view-shell')

/**
 * The identity, with a count that is only an artifact of the failure removed.
 *
 * These read `<page> · <n> <things>`, and the number comes from a store that
 * holds zero when its load failed: the archive announced `archive · 0 briefs`
 * directly above the screen saying the API was unreachable, the one sentence on
 * that page making a claim about the corpus and it was false. The page's own
 * name survives, because that part is still true.
 */
const identity = computed(() =>
  props.error && apiDown.value ? props.identity.split(' · ')[0] : props.identity,
)

function claim(): void {
  claimContextBar(token, {
    identity: identity.value,
    // Unconditional, `null` included -- see the note on `ContextBarState.parent`.
    parent: props.parent ?? null,
    controls: Boolean(slots.controls),
    search: Boolean(slots.search),
    toggles: Boolean(slots.toggles),
    actions: Boolean(slots.actions),
  })
}

// A view's identity is live -- the brief's dateline and the run counts change
// under it without the view remounting -- so the claim is an effect, not a
// one-shot on mount.
watchEffect(claim)

/**
 * Whether this view is the one on screen.
 *
 * Under `<KeepAlive>` a deactivated component's DOM is moved into a hidden
 * container -- but TELEPORTED content is not part of that subtree. It lives in
 * the target, so it stays exactly where it is. `AppShell` caches `BriefView` and
 * `GraphView`, so the brief's prev/next arrows and its `Rewrite` button sat in
 * the ContextBar while the reader was looking at the graph, mixed in with the
 * graph's own controls.
 *
 * `releaseContextBar` already ran on deactivate, so the bar's HEIGHT was right
 * the whole time -- which is why this survived: the layout looked correct and
 * only the contents were wrong.
 *
 * Starts `true` and stays `true` for a view that is not cached, since neither
 * hook fires for those.
 */
const active = ref(true)
onActivated(() => {
  active.value = true
  claim()
})
onDeactivated(() => {
  active.value = false
  releaseContextBar(token)
})
onUnmounted(() => releaseContextBar(token))

/** The scroll container, so a view can save and restore its position -- the
 *  brief needs this to survive a round trip through the graph, and `window`
 *  scroll restoration cannot help because the frame never scrolls. */
const scroller = ref<HTMLElement | null>(null)
defineExpose({ scroller })

const wrapped = computed(() => props.scroll === 'page')

// Read here rather than taken as a prop: every caller would pass the same
// derivation, and one that forgot is a view that reports an outage as its own
// failure. `PaneState` reads the health store from `components/ui` already.
const { apiDown } = useServiceHealth()
</script>

<template>
  <!-- The ContextBar's two content zones. A store cannot hold VNodes, so the
       content is teleported; the targets live in ContextBar, which stays
       mounted even in full screen precisely so these keep resolving.

       `v-if="active"` is load-bearing, whatever it looks like. Teleported
       content is NOT part of the component's own subtree,
       so `<KeepAlive>` does not take it away on deactivate -- see the block
       comment on `active`. The `v-if` goes on the Teleport, never on an
       ancestor of the target: a Teleport whose target is missing throws.

       `controls` change WHAT YOU SEE and are all outlined; `actions` change
       SOMETHING and are the only filled controls in the chrome. Putting a
       filter in `actions` is how this bar drifts back into the pile it
       replaced. -->
  <Teleport v-if="active" to="#context-controls">
    <slot name="controls" />
  </Teleport>
  <!-- The filter, as a glyph until it is asked for. Trailing rather than among
       the tabs: it is the least of what the bar carries, and it opens over the
       whole row when it is wanted. -->
  <Teleport v-if="active" to="#context-search">
    <slot name="search" />
  </Teleport>
  <!-- A switch that stays on screen showing its state belongs at the trailing
       edge, not among the tabs: it is not one of the things you are choosing
       between, and reading a tab strip is harder with a switch inside it. -->
  <Teleport v-if="active" to="#context-toggles">
    <slot name="toggles" />
  </Teleport>
  <Teleport v-if="active" to="#context-actions">
    <slot name="actions" />
  </Teleport>

  <div
    ref="scroller"
    class="view"
    :class="[
      `view--${width}`,
      `view--scroll-${scroll}`,
    ]"
  >
    <!-- Full width, above the body, below the bar. Some notices are a
         paragraph of hard-won explanation and cannot survive being crushed
         into the ContextBar as a count chip. -->
    <div v-if="$slots.notice" class="notice">
      <slot name="notice" />
    </div>

    <div v-if="loading" class="state flex items-center gap-2">
      <slot name="loading">
        <Spinner /><span class="text-text-secondary">loading</span>
      </slot>
    </div>

    <!-- A SUPPLIED `#error` slot is rendered raw; only the default is wrapped.
         A `StatusNote` around both is right for the fallback (a bare message
         string) and wrong for a view supplying its own content: `BriefView`'s
         first-run screen is not an error, and wrapped it takes the BODY of an
         error note -- its padding, its left rule and `--signal-critical`. -->
    <div v-else-if="error" class="state">
      <!-- AN OUTAGE IS NOT THIS VIEW'S ERROR, and the default state says so
           before it says anything else. Every view hands its store's failure
           straight here, so without this each one renders the status line it
           happened to catch: on an HTTP/2 gateway failure there is no reason
           phrase, so that was the literal string `502:`. -->
      <slot name="error">
        <ApiUnreachable v-if="apiDown" />
        <StatusNote v-else kind="error">{{ error }}</StatusNote>
      </slot>
    </div>

    <!-- Duplicated on purpose: only one branch ever renders for a given view,
         and `scroll="none"` must not introduce a wrapper (see the block
         comment above). -->
    <template v-else-if="wrapped">
      <div class="column">
        <slot />
      </div>
      <!-- The side rail. Only `width="prose"` has one; it holds the things that
           orient a reader without interrupting them -- the contents list, how
           recent the reading is, the references.

           TWO SLOTS, because the rail's contents do not all want the same thing
           on a phone. `side-lead` is the part that is NAVIGATION and has to
           reach the reader before the article does; `side` is orientation, and
           a reader who wants it will look for it. Below the breakpoint the two
           part company around the column.

           Each slot gets a WRAPPER THIS COMPONENT OWNS. That is what lets the
           rules below place them: slotted content carries the parent's scope id,
           so a selector aimed at the caller's own elements would need `:deep`
           and would reach into markup this file has no business knowing. -->
      <aside v-if="$slots.side || $slots['side-lead']" class="side">
        <div v-if="$slots['side-lead']" class="side-lead">
          <slot name="side-lead" />
        </div>
        <div v-if="$slots.side" class="side-rest">
          <slot name="side" />
        </div>
      </aside>
    </template>
    <slot v-else />
  </div>
</template>

<style scoped>
.view {
  height: 100%;
  min-height: 0;
  min-width: 0;
}

.view--scroll-page {
  overflow-y: auto;
  padding: var(--space-5) var(--page-pad) var(--space-8);
  /* THE CHROME'S OWN SPACE, AT THE TOP OF THE DOCUMENT. Lifting the bars makes
     this box taller at its TOP edge, which would carry the reading up with it;
     the same pixels of padding put the content back, so the two cancel exactly
     and the scrollable distance is the same at every lift. In CSS because the
     obvious answer -- writing `scrollTop` back -- HALTS A FLING on a phone, so
     every flick died the moment the chrome began to move. ADR 0199. */
  padding-top: calc(var(--space-5) + var(--chrome-gone));
  /* THE BOTTOM EDGE IS A FIXED `--space-8` AND MUST STAY ONE. A padding change
     there moves no text, but it drops the scrollable distance, so the browser
     clamps `scrollTop` and the clamp arrives as an upward scroll. Measured
     2026-09-02 at the end of a brief, against 30px of it: 9386 clamped to 9357
     and the chrome edged back 10px, once, on arrival. Anything that ever varies
     this with the chrome is transitioned on the same curve and duration as
     whatever it cancels; instant is right in one direction and clamps by the
     whole of it in the other. */
}

/* ── composition ──────────────────────────────────────────────────────
 *
 * Content is LEFT-ANCHORED at `--edge` on every route, not centred. Centring a
 * `--measure` column in a 1500px window puts over 400px of black on each side
 * and gives the page one vertical axis with nothing placed against it --
 * symmetry with nothing either side is emptiness, not minimalism.
 *
 *   [ --rail-margin ][ 1fr ][ --rail-margin ][ --rail-side ]
 *      marginalia    reading      gap          orientation
 */
.view--prose {
  display: grid;
  /* TWO EQUAL GUTTERS with the reading column as the `fr` track between them,
     so the column absorbs every spare pixel and the margins stay symmetric at
     any width. Not a fixed left margin with the right gutter floored at
     `--space-6`: that gave 140px against 36px on a wide window (2026-08-07), a
     reading column visibly off its own axis.

     The grid is NOT capped at the sum of its tracks. Capped, a 1500px window
     gave a 568px column of prose with 432px of dead black to the right of the
     side rail (2026-08-07): a narrow strip pinned to the left edge of a screen
     that is mostly empty. The reading column takes the space instead and the
     orientation rail sits against the right gutter, which is where a reader
     looks for it. */
  grid-template-columns:
    var(--rail-margin)
    minmax(0, 1fr)
    var(--rail-margin)
    minmax(0, var(--rail-side));
  grid-template-areas: '. column . side';
  justify-content: start;
  align-content: start;
  padding-left: var(--page-pad);
  /* THE TRACKS DO NOT MOVE, and that is deliberate rather than unfinished.
     Narrowing the rail track to fold it hands the freed width to the `1fr`
     reading column, which pushes the folded strip rightwards across the
     window -- so the one element the reader is watching travels while the
     prose it sits beside rewraps under it. Folding is a change of CONTENTS.
     The column keeps its measure, nothing reflows, and the strip appears
     exactly where the rail's first line was. */
}
.view--prose .column {
  grid-area: column;
  min-width: 0;
}
.view--prose .side {
  grid-area: side;
  min-width: 0;
  /* Sticky under the app header, so the contents list stays with the reader.
     `0`, and it has to be: a sticky element is pinned below the scroller's own
     top PADDING, which already carries the lift, so an offset of
     `--chrome-gone` here counts it twice and walks the rail down the window as
     the bars leave. Measured 2026-09-02 at gone: 67 -- the rail moved 112 to 179 while the
     prose beside it held still. A small block of orientation sliding past the
     corner of your eye is the most distracting thing on the page. */
  position: sticky;
  top: 0;
  align-self: start;
}

/* Below this there is no room for a side rail; it becomes a preamble above the
   article and the marginalia collapse (see BriefItem).

   CENTRED, unlike everywhere else -- and the left-anchor rule above is not
   being broken, it is out of jurisdiction: that rule anchors content against
   the rail structure, and in this band the rail structure is gone. What a
   fixed left anchor produced here was the exact failure the desktop grid's
   comment names -- a strip of prose pinned to the left of an iPad that is
   mostly empty -- so the column widens to `--measure-wide` and the leftover
   splits evenly, which is where a tablet reader's hands expect a page. */
@media (max-width: 1180px) {
  .view--prose {
    grid-template-columns: minmax(0, var(--measure-wide));
    grid-template-areas: 'lead' 'column' 'rest';
    justify-content: center;
    padding-left: var(--page-pad);
  }
  /* The rail stops being a rail and its two halves take rows of their own, one
     either side of the article. Navigation leads and orientation follows,
     because the whole of it above the column puts a meter, a contents list and
     a paragraph about references between a phone reader and the first sentence
     -- about two screens of orientation with nothing yet to orient. */
  .view--prose .side {
    display: contents;
  }
  .view--prose .side-lead {
    grid-area: lead;
    margin-bottom: var(--space-6);
  }
  .view--prose .side-rest {
    grid-area: rest;
    margin-top: var(--space-6);
  }
}

/* `wide` anchors to the SAME line as `prose`, so the eye finds the same left
   edge whichever route it lands on. Below 1180 that line is gone (the prose
   grid centres) and `--edge` itself collapses in tokens.css, so this follows. */
.view--wide.view--scroll-page {
  padding-left: var(--edge);
}
.view--wide .column {
  max-width: var(--measure-page);
}

/* A fixed frame: something inside scrolls, this never does. */
.view--scroll-none {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  overflow: hidden;
}

/* The body has to take the remaining height, or the region inside it has
   nothing to scroll within and the action bar at its foot walks off screen.
   Slotted content carries the PARENT component's scope id, never this one, so
   a plain `> *` here would silently match nothing -- hence `:deep`. */
.view--scroll-none > :deep(:last-child) {
  flex: 1 1 auto;
  min-height: 0;
}
/* Anchored to `--edge` like every other route, not centred. A view starting
   150px in beside one starting at 96px is what three alignment systems looked
   like (2026-08-06). */
.view--scroll-none.view--wide {
  padding: var(--space-4) var(--page-pad) var(--space-4) var(--edge);
  max-width: calc(var(--measure-page) + var(--edge));
  width: 100%;
}

.column {
  min-width: 0;
}

.view--full {
  padding: 0;
}

.notice {
  flex: none;
  margin-bottom: var(--space-3);
}

.state {
  /* The reading column; without it this auto-places into the margin rail and
     renders 104px wide. No padding either: a second inset over the shell's own
     put the first-run screen 26px off `--edge` on the Brief (2026-08-06). */
  grid-area: column;
}

/* A line at the top, like every scroll="page" route. While loading shows, the
   state div is this flex column's ONLY child, so the stretch rule above would
   otherwise make it the full-height last child and centre the spinner
   mid-screen. The inset is the shell's own (`view--full` carries none). */
.view--scroll-none > .state {
  flex: 0 0 auto;
  padding: var(--space-4) var(--page-pad) 0 var(--edge);
}
</style>
