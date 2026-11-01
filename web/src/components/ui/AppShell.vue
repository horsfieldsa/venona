<script setup lang="ts">
/**
 * Application frame: one header, the sub-menu bar, and the routed content.
 *
 * Views own their own scrolling (`ViewShell` decides who), so the frame itself
 * never scrolls.
 *
 * Three things here are load-bearing and easy to break:
 *
 * 1. The chrome recedes by each bar being LIFTED out of the window on a
 *    negative margin inside an `auto` grid track, which shrinks the track by
 *    the same amount so everything below travels with it. It must never be
 *    `v-if`'d away -- `ContextBar` holds the `<Teleport>` targets that every
 *    `ViewShell` writes its controls and actions into, and a teleport whose
 *    target is missing throws. Lift, do not unmount.
 *
 *    Both bars are handed to the ui store, which measures them whenever it
 *    moves the lift: how far the chrome can go is their own height, and that is
 *    not `--nav-height` plus `--subnav-height` on a phone, on a route with no
 *    sub-menu, or under a view that teleports a long control set into it.
 *
 * 2. `<KeepAlive>` covers the brief and the graph. The brief keeps its scroll
 *    position across a round trip through the graph, and the graph keeps its
 *    Cytoscape instance and every hop the user expanded. Both views hook
 *    `onActivated`/`onDeactivated` accordingly.
 *
 * 3. `overflow-hidden` on the frame, so the DOCUMENT can never scroll. The
 *    router says window scrolling does not happen here; this is what makes that
 *    true rather than assumed. While the chrome animates, the rows are being
 *    re-solved every frame and the frame can overshoot the viewport by a few
 *    pixels for two or three of them -- enough for the document to take a
 *    scrollbar, which costs 10px of width, which narrows the reading column,
 *    which REWRAPS THE PROSE. What a reader sees of that chain is the text
 *    twitching sideways as the bars come back. It is on the frame rather than
 *    on `body` because the public routes render outside this component and do
 *    scroll the document.
 *
 * 4. `<main>` marks where the chrome ENDS. `data-chrome-bottom` is on the
 *    element whose top edge is that line in every state -- lifted, down, empty
 *    bar, part-way -- which is what `EntityHoverCard` needs and what a height
 *    token cannot say. How far the chrome has been lifted travels separately,
 *    as `--chrome-gone` on `<html>`, written by the ui store: it runs inside a
 *    scroll handler's frame and the bars and the scroller have to see the same
 *    value in it, which a binding a tick later cannot give them.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import AppHeader from '@/components/ui/AppHeader.vue'
import StatusBar from '@/components/marks/StatusBar.vue'
import CommandPalette from '@/components/nav/CommandPalette.vue'
import ContextBar from '@/components/nav/ContextBar.vue'
import KeyboardHelp from '@/components/nav/KeyboardHelp.vue'
import BillingNotice from '@/components/ui/BillingNotice.vue'
import Dialog from '@/components/ui/Dialog.vue'
import ToastHost from '@/components/ui/ToastHost.vue'
import { useAppPolling } from '@/composables/useAppPolling'
import { useBillingNotice } from '@/composables/useBillingNotice'
import { installKeyboard, setNavigator } from '@/composables/useKeyboard'
import { useRunStream } from '@/composables/useRunStream'
import { useGraphStore } from '@/stores/graph'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
const router = useRouter()

/**
 * The dunning banner. In the SHELL rather than on a view, because a failing
 * card is true on every page and a reader who never opens Settings is exactly
 * the one who needs telling. Not a toast: a toast is transient, and this must
 * not be missable.
 */
const {
  notice: billingNotice,
  dismiss: dismissBilling,
  target: billingTarget,
  modal: billingModal,
  closeModal: closeBillingModal,
} = useBillingNotice()

/**
 * ONE modal, on the last retry only, once a session.
 *
 * The banner is the whole dunning surface for every other state, deliberately:
 * a modal on the first failed payment gets dismissed reflexively and teaches
 * people to ignore the next one. This is the last moment a card can be fixed
 * before the subscription ends, so it is the one place the interruption pays
 * for itself.
 */
function confirmBillingModal(): void {
  closeBillingModal()
  actOnBilling()
}

function actOnBilling(): void {
  const to = billingTarget()
  // An absolute URL is the hub's, and a hub URL is a real navigation off this
  // origin -- the router cannot carry it.
  if (to.startsWith('http')) window.location.href = to
  else void router.push(to)
}
const paletteOpen = ref(false)
const helpOpen = ref(false)

/**
 * The two bars, handed to the store so it can measure them.
 *
 * Elements rather than numbers: how far the lift can go is their own height,
 * and that changes with the width, with the route, and with what a view
 * teleports into the sub-menu. The store reads them at the moment it moves the
 * lift, which is the only moment the answer has to be right.
 */
const headerRef = ref<{ el: HTMLElement | null } | null>(null)
const contextRef = ref<{ el: HTMLElement | null } | null>(null)

function onResize() {
  ui.refreshChrome()
}

onMounted(() => {
  ui.bindChromeBars(headerRef.value?.el ?? null, contextRef.value?.el ?? null)
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  ui.bindChromeBars(null, null)
  window.removeEventListener('resize', onResize)
})

setNavigator((path) => void router.push(path))
installKeyboard({
  openPalette: () => (paletteOpen.value = true),
  openHelp: () => (helpOpen.value = true),
})
useAppPolling()
// Entity colour for every surface, not just the graph: the ontology fetch
// carries the slot assignments `lib/entityTokens.ts` turns into live CSS, and
// the brief renders mentions before anyone visits the graph view.
void useGraphStore().loadOntology()
useRunStream()

/**
 * How far through the reading, drawn where the header was.
 *
 * A `StatusBar`, not a second bar of its own: a proportion `--spine-h` tall is
 * exactly what that mark is, and the brief's own freshness meter already makes
 * this argument. Only while the chrome is away AND there is progress to
 * report, so the graph's full screen -- which has nothing to be partway
 * through -- stays a clean edge.
 */
const readPercent = computed(() => Math.round(ui.readProgress * 100))
const progressSegments = computed(() => [
  { value: ui.readProgress, color: 'var(--accent-primary)' },
  { value: 1 - ui.readProgress, color: 'var(--border-subtle)' },
])
</script>

<template>
  <!-- `shell--fullscreen` carries NO styling and is not decoration: it is the
       state `scripts/ui_smoke.py` reads to assert that `f` enters the mode,
       that `Esc` leaves it and that `/` leaves it to reveal the search. A
       marker class with no CSS rule looks dead to a grep over the stylesheets,
       which is exactly why it says so here.

       It tracks `fullscreen` alone, never `chromeCollapsed` -- the scroll-
       driven recede is not a mode and nothing should be able to assert that it
       is one. -->
  <div
    class="shell relative grid h-full overflow-hidden shell-rows"
    :class="ui.fullscreen ? 'shell--fullscreen' : ''"
  >
    <!-- Collapsed, never unmounted: ContextBar holds the `<Teleport>` targets
         that every ViewShell fills, and they have to keep resolving while the
         chrome is away. Each bar shrinks its own height; the tracks are `auto`
         and follow. -->
    <AppHeader ref="headerRef" @palette="paletteOpen = true" />

    <!-- Row two: the route's own identity, controls and actions. Collapses to
         nothing on a route that has none. -->
    <ContextBar ref="contextRef" />

    <!-- Row three: money. ALWAYS RENDERED, like the two bars above and for a
         related reason -- `shell-rows` is a fixed five-track template, so a
         `v-if` on the row itself would shift the footer into main's track. The
         wrapper is zero-height when there is nothing to say. -->
    <div class="billing min-w-0">
      <BillingNotice
        v-if="billingNotice"
        :notice="billingNotice"
        @act="actOnBilling"
        @dismiss="dismissBilling"
      />
    </div>

    <Dialog
      :open="billingModal !== null"
      title="Your subscription is about to end"
      :confirm-label="billingModal?.action ?? 'Update your card'"
      @update:open="closeBillingModal"
      @confirm="confirmBillingModal"
    >
      {{ billingModal?.text }}
    </Dialog>

    <!-- min-w-0 on every row of the shell grid: a single-column grid sizes
         its track by the items' MIN-CONTENT, so one wide leaf anywhere in
         main -- a segmented control's untruncated row was the one that
         proved it -- widens the shared track past the viewport and every
         bar stretches with it. The page then scrolls sideways because of
         one element three panels deep, which no per-view fix can see. -->
    <!-- Where the header was. Absolute rather than a fifth row, so the shell
         grid is untouched and nothing reflows when it appears. -->
    <StatusBar
      v-if="ui.chromeCollapsed && ui.readProgress > 0"
      class="progress absolute inset-x-0 top-0 z-sticky"
      :segments="progressSegments"
      :label="`${readPercent}% of this brief read`"
    />

    <!-- `data-chrome-bottom`: this element's top edge IS where the chrome ends,
         in every state. `EntityHoverCard` measures it rather than reading
         `--nav-height`, which is what the header would be if it were shown. -->
    <main data-chrome-bottom class="content overflow-hidden min-h-0 min-w-0">
      <RouterView v-slot="{ Component }">
        <KeepAlive include="BriefView,GraphView">
          <component :is="Component" />
        </KeepAlive>
      </RouterView>
    </main>

    <CommandPalette v-model:open="paletteOpen" />
    <KeyboardHelp v-model:open="helpOpen" />
    <ToastHost />
  </div>
</template>
