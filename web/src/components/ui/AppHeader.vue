<script setup lang="ts">
/**
 * Row one of the chrome: WHO YOU ARE and WHERE YOU CAN GO. Nothing else.
 *
 *   [Venona] [Brief Graph Archive Lookup Learn Admin] ····· [⌘K ? 👤]
 *
 * There is exactly one of these and it is IDENTICAL on every route: route-scoped
 * identity, filters and actions belong in `ContextBar`, which is free to be as
 * busy as the route needs (ADR 0181). Carrying any of that here shifts,
 * squeezes or wraps the nav depending on where you are, and navigation that
 * moves under the cursor when you navigate is the one thing a nav must not do.
 * One bar rather than a nav rail plus a per-view command bar is ADR 0071.
 *
 * The bar hangs off the SAME two vertical lines as the content: the wordmark
 * sits in the margin rail and the nav starts at `--edge`.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import Wordmark from '@/components/brand/Wordmark.vue'
import { useFocusTrap } from '@/composables/useFocusTrap'
import Kbd from '@/components/ui/Kbd.vue'
import IconButton from '@/components/ui/IconButton.vue'
import UserMenu from '@/components/ui/UserMenu.vue'
import { usePopover } from '@/composables/usePopover'
import { useRunProgress } from '@/composables/useRunProgress'
import { IS_MAC } from '@/lib/platform'
import { useHealthStore } from '@/stores/health'
import { useSessionStore } from '@/stores/session'
import Chip from '@/components/ui/Chip.vue'
import Icon from '@/components/ui/Icon.vue'

const health = useHealthStore()

// Prompts are edited in the configuration editor under Admin, reached from its
// rail. Settings -- the reader's own account -- lives in the account menu on
// the right.
//
// `Lookup` is a top-level destination rather than a utility, and a longer list
// is the price. Every other way in is a search box or a hover card in prose,
// which would leave the one surface that answers "what does Venona NOT know"
// -- the undefined acronyms, as a set -- reachable only by typing the URL. A
// destination nobody can find is not a hierarchy, it is a missing feature.
//
// `Archive` is here because the design bundle's chrome has it, and because the
// only other way to reach it is a button in the brief's own context bar, which
// leaves it unreachable from anywhere except the one page that already shows
// you a brief.
//
// ACTIVE IS KEYED ON ROUTE NAME, not on the URL, because the URL cannot express
// this app's shape. `router-link-active` matches by PREFIX, so `/briefs` lights
// up on `/briefs/:id` -- which is a brief being READ, not the archive; and
// vue-router special-cases `to="/"` to exact, so `Brief` goes dark on exactly
// the page it names. The route's own name says what is meant: two routes render
// BriefView and both are "Brief", every page under /admin is Admin, and the
// archive is only itself.
// `operator` marks the entries a READER must not be offered. It mirrors
// `meta.operator` on the route rather than deriving from it, because a route's
// meta is not reachable from a link's `to` without resolving it -- and the guard
// in `router/index.ts` is what actually stops the navigation. This only stops the
// link being drawn.
const NAV = [
  { to: '/', label: 'Brief', names: ['brief', 'brief-by-id'], operator: false },
  { to: '/graph', label: 'Graph', names: ['graph'], operator: false },
  { to: '/briefs', label: 'Archive', names: ['archive'], operator: false },
  { to: '/lookup', label: 'Lookup', names: ['lookup'], operator: false },
  { to: '/learn', label: 'Learn', names: ['learn'], operator: false },
  // Last, and the only operator entry: everything the instance's
  // administrator works on hangs off it, reached from `AdminRail` rather
  // than from this row.
  //
  // `names` HAS TO LIST EVERY ROUTE THE ENTRY OWNS, not just the ones with a
  // link pointing at them, because `isActive` matches on the name alone: a
  // route missing from every entry renders the whole bar with no item lit, on
  // a page the reader reached from that very row.
  // `tests/test_frontend_drift.py::TestOperatorNav` compares these lists
  // against the router, so a new route has to be claimed here or named as
  // deliberately unlisted.
  {
    to: '/admin',
    label: 'Admin',
    names: [
      'admin',
      'admin-model',
      'admin-people',
      'admin-agents',
      'admin-data',
      'admin-notifications',
      'admin-config',
      'admin-config-doc',
      'admin-curation',
      'admin-runs',
      'admin-run-detail',
      'admin-run-metrics',
      'setup',
    ],
    operator: true,
  },
] as const

// The phone drawer's `<Teleport>` makes this a multi-root component, and Vue
// drops attribute fallthrough on fragments SILENTLY. The bar is the root that
// must receive whatever a parent binds, so it takes `$attrs` by hand.
defineOptions({ inheritAttrs: false })

const session = useSessionStore()

/**
 * What this reader is offered. UX, never the gate: `api/authz.py` refuses the
 * request server-side and the router guard refuses the navigation. Filtering here
 * is only so a reader is not shown a link to a 403.
 */
const nav = computed(() => NAV.filter((item) => !item.operator || session.isAdmin()))

const route = useRoute()

function isActive(names: readonly string[]): boolean {
  return names.includes(String(route.name ?? ''))
}

/** `⌘K` on a Mac, `^ K` everywhere else: the caret rather than the word
 *  "Ctrl", which would be the widest control in the bar. */
const paletteHint = computed(() => (IS_MAC ? '⌘K' : '^ K'))

/* NO ANNOTATION ON THIS ROW. A count belongs beside the destination it
 * describes, and every operator destination is on `AdminRail`, where both the
 * curation backlog and the pipeline spine are drawn. What this bar keeps is the
 * run-status pill below: the one in-flight signal available from any page,
 * which is why it says the stage in words rather than a bare dot. */
const { progress, progressLabel } = useRunProgress()

const activeRun = computed(() => health.data?.active_run ?? null)

/**
 * The run, in words, for the header readout.
 *
 * Prefers the stage over a generic "running": while a run is in flight the
 * question is always "how far", and this is the only place that answers it for
 * a reader who is not already standing under /admin, where the rail's spine
 * answers it positionally. Falls back while the stage list has not arrived --
 * the health poll and the run store settle independently, and a readout that
 * flickered to empty between them would be worse than one that is briefly
 * vague.
 */
const runStateLabel = computed(() => {
  if (!activeRun.value) return ''
  const p = progress.value
  if (!p) return 'running'
  // WAITING REPLACES THE STAGE, rather than sitting beside it. The row has no
  // width to spare, and while a call is sitting out a provider's bad minute
  // "which stage" is the less useful of the two facts: the stage has not
  // changed and will not until the wait ends.
  return p.waiting ? 'waiting' : `${p.stage} · ${p.index + 1}/${p.total}`
})

/**
 * The phone drawer (ADR 0152). Below `--breakpoint-nav` the row has no room, so
 * the same list renders as a stacked panel under the header. State lives here
 * and not in a store: the drawer is chrome, and the only thing that needs to
 * close it from outside -- navigation -- flows through the route watcher below.
 */
const drawerOpen = ref(false)
usePopover(drawerOpen)

// Navigating closes the drawer, including back/forward navigations no link
// click can see. Clicking the current route's own link changes nothing, so the
// links also close on click.
watch(() => route.fullPath, () => (drawerOpen.value = false))

/**
 * The drawer covers the page on a phone, so it is modal and traps Tab.
 *
 * The ref goes on the TELEPORTED element, not on this component's root: the
 * drawer is rendered into `body`, and a trap pointed at the header would cycle
 * focus through the bar behind the scrim. `useFocusTrap` also restores focus to
 * whatever was focused before, which is the hamburger — otherwise closing the
 * drawer drops focus on `<body>` and the next Tab starts from the top of the
 * document.
 */
const drawer = ref<HTMLElement | null>(null)
useFocusTrap(drawer, drawerOpen)

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') drawerOpen.value = false
}
onMounted(() => document.addEventListener('keydown', onKeydown))
onUnmounted(() => document.removeEventListener('keydown', onKeydown))

defineEmits<{ (e: 'palette'): void }>()

/** `AppShell` measures the bar to know how far the lift can go. Exposed rather
 *  than reached for through `$el`: this component's template is a fragment --
 *  the bar plus the teleported phone drawer -- so `$el` is whichever node Vue
 *  happens to anchor it on, which is not the bar. */
const bar = ref<HTMLElement | null>(null)
defineExpose({ el: bar })
</script>

<template>
  <!-- It keeps its height and is LIFTED out of the window by a negative margin;
       the shell's row is `auto` and shrinks by the same amount, so everything
       below travels with it. The lift is split across this bar and the one
       under it; `stores/ui.ts::paint` carries why and writes
       `--chrome-lift-nav`.

       NO `overflow-hidden`, and it is the shell's seal that makes that safe:
       the bar leaves the window rather than shrinking inside it, so there is
       nothing left to clip, and a clip here would crop the account menu to the
       height of the bar it hangs from. It was cropping it on a phone.

       Nothing is transitioned: the reader's hand is the clock (ADR 0199), and
       the two movements that are NOT gestures -- `f` and the pointer edge --
       are run frame by frame in the store instead. -->
  <header
    ref="bar"
    v-bind="$attrs"
    class="bar min-w-0 flex items-center gap-5 px-page h-nav border-b border-b-border-subtle bg-surface-raised mt-(--chrome-lift-nav)"
  >
    <!-- `select-none` here and on the nav links, nowhere else in the bar.
         `::selection` over a nav label reads as a green rectangle sitting ON the
         item rather than as selected text, and a drag starting in the page and
         running up into the bar caught it constantly. The ContextBar carries a
         brief's date and a run id, which are worth copying, so the exclusion
         stops at the navigation chrome.

         `flex`, not the default inline layout: an inline-flex lockup inside an
         inline anchor still gets the anchor's own line-box descender space
         beneath it, which hangs the whole thing ~4px above the header's optical
         centre while its box measures perfectly centred. -->
    <RouterLink
      to="/"
      aria-label="Venona, go to the latest brief"
      class="brand flex items-center flex-none w-rail-margin h-control text-accent-primary no-underline hover:no-underline"
    >
      <!-- 28, not the component's default 16. `size` is the LOCKUP BOX height and
           the mark is 104 of its 124 units, so the drawn word scales with it: at
           16 its x-height is ~4.1px, under the brand kit's own 16px floor for the
           horizontal lockup, and it reads as a smudge. 28 fills the slot this bar
           already reserves -- `--control-h` tall, inside `--rail-margin`'s 104px
           at 97px wide, measured 2026-08-07 -- and puts the x-height at ~7.2px. -->
      <!-- `label=""`: the anchor above already carries the accessible name, and
           a named `role="img"` inside a named link is announced twice. -->
      <Wordmark :size="28" label="" />
    </RouterLink>

    <!-- `gap-5` on the bar separates the ZONES; `gap-4` inside the nav separates
         items within one. A 2:1 ratio at 13px does not read as grouping. -->
    <!-- The desktop row. Below `--breakpoint-nav` it has no room and the drawer
         takes over; `ui_smoke` asserts this element's box is identical across
         routes, and separately that it is visible at desktop width. -->
    <nav class="nav flex-none max-nav:hidden flex items-center gap-4">
      <RouterLink
        v-for="item in nav"
        :key="item.to"
        :to="item.to"
        :class="isActive(item.names) ? 'nav-on' : ''"
        active-class="nav-router-unused"
        exact-active-class="nav-router-exact-unused"
        class="link relative inline-flex items-center gap-1 h-control select-none whitespace-nowrap text-sm text-text-tertiary no-underline border-b border-b-transparent hover:text-text-secondary hover:no-underline [&.nav-on]:text-text-primary [&.nav-on]:border-b-accent-primary"
      >
        {{ item.label }}
      </RouterLink>
    </nav>

    <!-- Everything route-scoped belongs to ContextBar, so what stands between
         the nav and the utility cluster is a spacer and nothing else. -->
    <span class="grow" />

    <div class="utility flex items-center gap-2 flex-none">
      <!-- RUN STATUS in words -- the stage and how far through, since nothing
           else on a reader's page says it, and an id is not a status. Drawn
           only while something is actually running: an idle app should not
           spend header room saying nothing is happening. -->
      <!-- `capitalize` here rather than in `runStateLabel`: the stage names are
           lowercase literals in the store, and capitalising in CSS leaves
           `progressLabel` -- the aria-label and the title -- reading as a
           sentence to a screen reader. The only other content is digits, so the
           transform cannot reach anything else. -->
      <!-- REPLAY MODE, which had nowhere else to be said. Venona is serving
           recorded envelopes rather than asking anything, so every figure on
           every page is a fixture: a reader who does not know that is reading
           yesterday's answers as today's. It sits beside the run chip because
           both answer "what is this app doing right now", and it is absent the
           rest of the time -- which is almost always. -->
      <Chip v-if="health.data?.offline" tone="info" title="replaying recorded data">
        offline
      </Chip>

      <RouterLink
        v-if="activeRun"
        :to="`/admin/runs/${activeRun}`"
        class="runstate inline-flex items-center gap-2 h-control px-2 rounded-sm font-ui text-2xs capitalize text-accent-primary no-underline hover:bg-surface-inset hover:no-underline"
        :title="progressLabel || 'a run is in flight'"
        :aria-label="progressLabel || 'a run is in flight'"
      >
        <span
          class="dot w-(--dot-sm) h-(--dot-sm) rounded-pill bg-accent-primary animate-pulse-stage"
          aria-hidden="true"
        />
        <!-- THE DOT SURVIVES A PHONE AND THE WORDS DO NOT. Below `nav` this
             row also carries the drawer toggle, and `Research · 1/2` is wide
             enough to push it off the right edge -- so a reader loses the whole
             navigation for as long as a run lasts, which is exactly when they
             are most likely to go looking at it. The link, the pulse and the
             tap target stay; the stage is on the `title` and the aria-label,
             and the Runs page one tap away says it in full. -->
        <span class="max-nav:hidden">{{ runStateLabel }}</span>
      </RouterLink>

      <!-- A control, not a chip. `Chip interactive` is the graph's FILTER
           affordance; using the same component for "open the palette" made a
           global action look like a toggle in a set. `Kbd` is what a key looks
           like, and it exists. -->
      <button
        class="palette inline-flex items-center gap-1 h-control px-1 bg-transparent border-0 rounded-sm cursor-pointer hover:bg-surface-inset"
        type="button"
        title="Search and jump"
        @click="$emit('palette')"
      >
        <!-- ONE cap, not two. The chord is a single gesture and the design
             bundle draws it as a single key (`⌘K`); two adjacent caps read as
             "press Ctrl, then press K", which is a different instruction. The
             space sits only in `^ K` — a caret hugging the K reads as the
             control-character notation `^K`, not as a chord. -->
        <Kbd>{{ paletteHint }}</Kbd>
      </button>
      <!-- The user guide, beside the gear rather than in it: it explains the
           whole app, not the settings, and burying help inside a settings page
           is how it stops being found.

           NOT the same thing as the `?` overlay, which lists the keyboard
           bindings for the current view. That one is a reference for someone
           already working; this is the document. They are named differently on
           purpose -- "Keyboard" against "User guide" -- because two question
           marks meaning two things is the confusion worth avoiding. -->
      <RouterLink to="/guide" class="help inline-flex no-underline hover:no-underline">
        <IconButton label="User guide" as="span"><Icon name="help" /></IconButton>
      </RouterLink>
      <!-- The account menu: the signed-in address, Settings, Sign out. -->
      <UserMenu />

      <!-- The drawer toggle, phone only. A bare button like the palette's: a
           44px target (the touch rung) where every other header control is 28,
           because this one exists only where thumbs do. -->
      <button
        class="drawer-toggle nav:hidden inline-flex items-center justify-center w-(--control-h-touch) h-(--control-h-touch) bg-transparent border-0 rounded-sm cursor-pointer text-text-secondary hover:bg-surface-inset"
        type="button"
        aria-label="Navigation menu"
        :aria-expanded="drawerOpen"
        aria-controls="nav-drawer"
        @click="drawerOpen = !drawerOpen"
      >
        <Icon name="menu" :size="16" />
      </button>
    </div>
  </header>

  <!-- The phone drawer: the same `nav` list, stacked. Teleported so it is not
       a flex child of the bar; the scrim starts under the header so the toggle
       stays reachable while it is open. -->
  <Teleport to="body">
    <template v-if="drawerOpen">
      <div
        class="drawer-scrim nav:hidden fixed inset-x-0 top-(--nav-height) bottom-0 z-overlay bg-scrim"
        @click="drawerOpen = false"
      />
      <nav
        ref="drawer"
        id="nav-drawer"
        class="drawer nav:hidden fixed inset-x-0 top-(--nav-height) z-overlay flex flex-col py-2 bg-surface-raised border-b border-b-border-subtle"
        aria-label="Navigation"
      >
        <RouterLink
          v-for="item in nav"
          :key="item.to"
          :to="item.to"
          :class="isActive(item.names) ? 'nav-on' : ''"
          active-class="nav-router-unused"
          exact-active-class="nav-router-exact-unused"
          class="drawer-link flex items-center gap-2 h-(--control-h-touch) px-page select-none text-md text-text-tertiary no-underline border-l border-l-transparent hover:text-text-secondary hover:no-underline [&.nav-on]:text-text-primary [&.nav-on]:border-l-accent-primary"
          @click="drawerOpen = false"
        >
          {{ item.label }}
        </RouterLink>
      </nav>
    </template>
  </Teleport>
</template>
