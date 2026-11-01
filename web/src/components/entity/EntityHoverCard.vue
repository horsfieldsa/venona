<script setup lang="ts">
import { computed, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import EntityCardBody from '@/components/entity/EntityCardBody.vue'
import Button from '@/components/ui/Button.vue'
import Icon from '@/components/ui/Icon.vue'
import IconButton from '@/components/ui/IconButton.vue'
import Spinner from '@/components/ui/Spinner.vue'
import { useDismissable } from '@/composables/useKeyboard'
import { useServiceHealth } from '@/composables/useServiceHealth'
import { dayTimeLabel } from '@/lib/dates'
import { entityColourVar } from '@/lib/entityColour'
import { toEntityRoute } from '@/router/entityRoute'
import { emptyIntent, toGraphQuery } from '@/router/graphRoute'
import { useBriefsStore } from '@/stores/briefs'
import { useEntitiesStore } from '@/stores/entities'
import { useHoverStore } from '@/stores/hover'
import { useLookupsStore } from '@/stores/lookups'
import { useToastsStore } from '@/stores/toasts'
import { useUiStore } from '@/stores/ui'
import { apiPatch, errorText } from '@/api/client'
import { useRelevanceStore } from '@/stores/relevance'
import { useSessionStore } from '@/stores/session'

/**
 * ONE instance for the whole app, teleported to <body>.
 *
 * This owns POSITIONING and the lookup state machine. It does not draw the
 * card: `EntityCardBody` does, for all three kinds (C8), so every surface that
 * shows one cannot drift apart.
 *
 * Positioning is hand-rolled rather than pulled from a library: the placement
 * rules here are simple (flip above when there is no room below, clamp
 * horizontally to the viewport) and this avoids a dependency on the reading
 * surface's hot path.
 */
const hover = useHoverStore()
const ui = useUiStore()
const entities = useEntitiesStore()
const briefs = useBriefsStore()
const router = useRouter()

const cardEl = ref<HTMLElement | null>(null)
const position = ref({ top: 0, left: 0, placement: 'below' })

const card = computed(() => (hover.activeId ? entities.get(hover.activeId) : null))

/**
 * "No card yet" is not the same claim as "still fetching".
 *
 * `entities.fetchOne` records a failed lookup in `missing` and resolves to
 * null, so a card that 404s -- routinely, when a brief references an entity
 * curation has since merged away -- never arrives. Deriving `loading` from
 * `card === null` alone meant those hovers spun forever and the `no data`
 * fallback below was unreachable. Ask the store what it knows instead.
 */
const loading = computed(
  () => hover.open && hover.activeId !== null && card.value === null && !entities.missing.has(hover.activeId),
)

/**
 * Refetch when the card goes missing under an OPEN card.
 *
 * The only other fetch is `hover.ts`'s hover-intent timer, which fires once on
 * the way in. `entities.invalidate()` empties the whole cache and does not
 * refetch — `runs.ts` calls it on every run `done` — so a card that was pinned
 * when a run finished lost its data and sat on the loading state permanently,
 * with no way back short of closing and re-hovering. `fetchOne` de-dupes in
 * flight and records a 404 in
 * `missing`, so this settles either way and cannot spin.
 */
watch(loading, (isLoading) => {
  const id = hover.activeId
  if (isLoading && id) void entities.fetchOne(id)
})

// 8 is `--space-2`, and the template's `max-w-[calc(100vw_-_2*var(--space-2))]`
// subtracts the same rung per side -- so on a screen narrower than `--card-w`
// the card's own width plus these gutters still fits, and the clamp below can
// always land it fully on screen.
const GAP = 8
/** Pre-measure fallback only, like `offsetHeight ?? 260` below. The width is
 *  `--card-w` and is applied as a class, so the real number comes from
 *  measuring -- which is also the only thing that works inside the extension's
 *  shadow root, where the tokens are on `:host` rather than on `:root`. */
const CARD_W_FALLBACK = 400

/**
 * Where the chrome ends, MEASURED.
 *
 * The floor is the bottom of the app chrome, not the top of the viewport --
 * clamping to `GAP` let a card anchored near the top of the page paint over the
 * nav. But it cannot be `--nav-height` either: that is what the header would
 * measure IF SHOWN, and the chrome collapses (ADR 0199), both when the reader
 * is deep in a brief and in the graph's full screen. Reading the token there
 * reserves a whole bar's height of empty window for a bar that is not up.
 *
 * `<main>` is what carries the marker, never one of the bars. `ContextBar` is
 * `v-show`-ed on a route with nothing to say, and `getBoundingClientRect()` on
 * a `display: none` element returns all zeros -- so measuring the bar would
 * give a ceiling of 0 while a 48px header was still up, and cards would paint
 * over the nav. `main`'s top edge is the answer in every state: collapsed,
 * shown, empty bar, and halfway through the transition between them.
 */
function chromeBottom(): number {
  const main = document.querySelector('[data-chrome-bottom]')
  return main ? main.getBoundingClientRect().top : 0
}

function place() {
  const anchor = hover.anchor
  if (!anchor) return
  const rect = anchor.getBoundingClientRect()
  const height = cardEl.value?.offsetHeight ?? 260
  const width = cardEl.value?.offsetWidth || CARD_W_FALLBACK

  const below = rect.bottom + GAP
  const flip = below + height > window.innerHeight && rect.top - GAP - height > 0

  let left = rect.left + rect.width / 2 - width / 2
  left = Math.max(GAP, Math.min(left, window.innerWidth - width - GAP))

  // Clamp so the card is always fully on screen. Without this the card is
  // placed before its content (definition, sources, mini-graph) has loaded, and
  // once it grows the bottom runs off the viewport -- which is exactly where
  // the mini-graph lives.
  let top = flip ? rect.top - GAP - height : below
  top = Math.max(chromeBottom() + GAP, Math.min(top, window.innerHeight - height - GAP))

  position.value = { top, left, placement: flip ? 'above' : 'below' }
}

watch(
  () => [hover.open, hover.activeId, card.value] as const,
  () => {
    if (hover.open) requestAnimationFrame(place)
  },
)

// The card changes height as async content lands (and as the SVG lays out), so
// a single placement pass is not enough.
let observer: ResizeObserver | undefined
watch(cardEl, (element) => {
  observer?.disconnect()
  if (!element) return
  observer = new ResizeObserver(() => place())
  observer.observe(element)
})

function onScrollOrResize() {
  if (hover.open) place()
}

/**
 * The chrome receding moves the anchor without moving the scroller.
 *
 * Nothing else here would notice. `place()` re-runs on scroll, on resize, and
 * when the card's own box changes -- and the chrome moving is none of those:
 * the document position is unchanged, the window is unchanged, and the card is
 * the same size. The mention it points at simply travels up the viewport and a
 * pinned card stays where it was.
 *
 * The lift is a length that changes every frame it is moving (ADR 0199), so
 * watching it places once per frame with no timer to guess at -- which also
 * covers `f` and the pointer edge, tweened frame by frame in the store.
 */
watch(
  () => ui.chromeGone,
  () => {
    if (hover.open) place()
  },
)

/**
 * A press anywhere that is neither the card nor a mention dismisses it.
 *
 * This is the TOUCH exit. A pinned card's other ways out are Escape and
 * "Open in graph" -- with a keyboard and a hover-out those are plenty, but on
 * an iPad the tap that pins is the only gesture there is, and the card could
 * not be dismissed at all. `pointerdown` rather than `click`: iOS does not
 * reliably synthesize clicks on non-interactive page regions, and closing on
 * the press matches every other popover. Capture phase, so a handler that
 * stops propagation cannot silently disable the exit. A press on another
 * mention is excluded -- BriefBody's own click handler is about to pin that
 * mention's card, and dismissing here first would race it.
 */
function onPointerDownOutside(event: PointerEvent) {
  if (!hover.open) return
  const target = event.target instanceof Element ? event.target : null
  if (!target) return
  if (cardEl.value?.contains(target)) return
  if (target.closest('[data-entity-id]')) return
  hover.dismiss()
}

/**
 * Escape goes through the shared dismissable stack rather than a listener of
 * its own. With a palette over a dialog over this card over a search dropdown,
 * every owner reacting to the same key closes all of them on one press; the
 * stack pops exactly the topmost.
 */
const dismissable = useDismissable(() => hover.dismiss())
watch(
  () => hover.open,
  (isOpen) => (isOpen ? dismissable.push() : dismissable.pop()),
)

onMounted(() => {
  window.addEventListener('scroll', onScrollOrResize, true)
  window.addEventListener('resize', onScrollOrResize)
  document.addEventListener('pointerdown', onPointerDownOutside, true)
})

/**
 * Navigating away dismisses. The card is TELEPORTED to `body`, and BriefView
 * lives under `<KeepAlive>` -- deactivation moves the view's own DOM into the
 * cache but teleported content is not part of that subtree, so a pinned card
 * stayed painted over the graph, the runs page, everywhere (the ContextBar
 * teleports in ViewShell document the same trap). The card is scoped to the
 * document that anchored it; leaving that document ends it.
 */
onDeactivated(() => hover.dismiss())
onUnmounted(() => {
  observer?.disconnect()
  // The store outlives the component: without this an unmount left
  // `hover.open` true and the next mount painted the stale card unprompted.
  hover.dismiss()
  dismissable.pop()
  window.removeEventListener('scroll', onScrollOrResize, true)
  window.removeEventListener('resize', onScrollOrResize)
  document.removeEventListener('pointerdown', onPointerDownOutside, true)
})

/** A definition already exists, so there is nothing to look up. One test for
 *  all three kinds: an acronym's expansion arrives in `definition` too (C8). */
const hasDefinition = computed(() => Boolean(card.value?.definition))

/** A glossary id is not a graph node. */
const canOpenInGraph = computed(() => Boolean(card.value) && !card.value!.id.startsWith('gloss:'))

/**
 * The deliberate route into the graph. Clicking the mention itself pins this
 * card instead of navigating, so this is the only way through.
 *
 * It lands in BRIEF mode: this entity against the other entities the brief
 * named, which is a readable graph of twenty or thirty nodes rather than the
 * whole corpus. The scope control is the way out to everything.
 *
 * It carries the BRIEF as well as the paragraph. `?from=<itemId>` alone named a
 * paragraph but not the document containing it, so the way back could only ever
 * go to the latest brief -- and arriving from an archived one landed the reader
 * on today's document holding an item id that is not in it. This card is only
 * ever mounted inside `BriefView`, so `briefs.current` IS the brief on screen;
 * the id is never parsed out of the item id, which C3 forbids.
 */
function openInGraph(entityId?: string) {
  // Defaults to the card's own entity; a neighbour from the connections graph
  // passes its id instead. Same destination, same breadcrumb.
  const id = entityId ?? hover.activeId
  const itemId = hover.anchor?.closest<HTMLElement>('[data-item-id]')?.dataset.itemId ?? null
  const briefId = briefs.current?.id ?? null
  hover.dismiss()
  if (!id || id.startsWith('gloss:')) return
  // Without a brief there is nothing to scope to; fall back to the entity's own
  // neighbourhood rather than to a mode that cannot load. The mode goes to
  // `emptyIntent` rather than being spread over it, so the intent picks up that
  // mode's defaults instead of `all`'s.
  const mode = briefId ? 'brief' : 'focus'
  void router.push({
    name: 'graph',
    query: toGraphQuery({ ...emptyIntent(mode), focusId: id, briefId, itemId }),
  })
}

/**
 * The name's own page.
 *
 * Dismisses the card first: it is a fixed-position overlay teleported to
 * `<body>`, so a navigation that left it open would carry it onto the next
 * route, anchored to an element that route never rendered.
 *
 * No `gloss:` guard, unlike `openInGraph`. An acronym is not a graph node, but
 * it does have a page, and for a term with no sourced definition that page is
 * the only surface that lists the articles it appeared in.
 */
function openPage(event?: MouseEvent) {
  // The title is a real `<a>` with a real address, so a modified click has
  // already been left to the browser; this is the plain case, which stays an
  // SPA navigation.
  event?.preventDefault()
  const id = hover.activeId
  hover.dismiss()
  if (!id) return
  void router.push(toEntityRoute(id))
}

/** The card's own name, as an address the title can carry. Resolved through the
 *  router so the one place that builds this URL stays `entityRoute.ts`. */
const pageHref = computed(() =>
  hover.activeId ? router.resolve(toEntityRoute(hover.activeId)).href : '',
)

/**
 * Tier-3 grounded lookup, for an acronym surface OR an entity id.
 *
 * Deliberately does NOT dismiss the card. The lookup is a search call plus up
 * to three page fetches plus a read call — the server returns 202 and the
 * result arrives seconds later. Closing the card would leave the user with no
 * indication that anything happened, and no way to see the answer.
 */
type LookupState = 'idle' | 'queued' | 'running' | 'grounded' | 'rejected' | 'error'
const session = useSessionStore()
const relevanceStore = useRelevanceStore()

// The Follow toggle reads `isFollowed` the moment a card opens, and the
// tailor page may never have been visited.
void relevanceStore.loadFollows()

/** One toggle in flight at a time, and OPTIMISTIC: the pressed state flips
 *  the moment the reader clicks, and the store's answer either confirms it
 *  or puts it back. A round-trip of silence on a 28px toggle reads as a
 *  dead button. The toast is the confirmation that survives the card. */
const followPending = ref(false)
const optimisticFollow = ref<{ id: string; on: boolean } | null>(null)

function isFollowing(id: string): boolean {
  return optimisticFollow.value?.id === id
    ? optimisticFollow.value.on
    : relevanceStore.isFollowed(id)
}

async function toggleFollow(id: string) {
  if (followPending.value) return
  followPending.value = true
  const wanted = !relevanceStore.isFollowed(id)
  optimisticFollow.value = { id, on: wanted }
  const name = card.value?.name ?? card.value?.surface ?? id
  try {
    if (wanted) await relevanceStore.follow(id)
    else await relevanceStore.unfollow(id)
  } finally {
    followPending.value = false
    optimisticFollow.value = null
  }
  const landed = relevanceStore.isFollowed(id)
  if (landed === wanted) {
    useToastsStore().push({
      kind: 'success',
      text: landed ? 'now following' : 'stopped following',
      subject: name,
    })
  } else {
    useToastsStore().push({
      kind: 'error',
      text: relevanceStore.error || 'could not update your follows',
      subject: name,
    })
  }
}

const lookups = useLookupsStore()

/**
 * Derived, never held: the in-flight half lives in the lookups store (so a
 * run in progress survives closing and reopening the card -- the spinner
 * belongs to the term, not to the card session), and the attempted half
 * lives on the server row. `last_attempt_at` is the durable record of a
 * lookup that searched, fetched and grounded nothing; without it, reopening
 * offered "Look this up" as though nothing had happened and the same failing
 * lookup could be paid for again.
 */
const lookupState = computed<LookupState>(() => {
  const entry = lookups.stateFor(hover.activeId)
  if (entry) return entry.status
  return card.value?.last_attempt_at ? 'rejected' : 'idle'
})
const lookupError = computed(() => {
  const entry = lookups.stateFor(hover.activeId)
  return entry && entry.status === 'error' ? entry.error : null
})

/**
 * Blocked only while THIS card's lookup is in flight.
 *
 * NOT on `runs.isRunning`. A lookup runs in the `light` lane (contract C11) and
 * starts alongside the pipeline, so blocking for the duration of a research run
 * leaves this button dead for tens of minutes -- exactly the window in which
 * someone is reading a brief and hits a term they do not know.
 *
 * If the light lane is full the run is QUEUED rather than refused, which the
 * footer says out loud — so there is still nothing to disable.
 */
const lookupBlocked = computed(() => lookupState.value === 'running' || lookupState.value === 'queued')
const lookupBusy = lookupBlocked

/** Kept OUT of `lookupBlocked`: that computed also drives the busy spinner,
 *  and a model outage is not a lookup in progress -- it disables the button
 *  and nothing else. */
const { modelDown } = useServiceHealth()

// ── the human escape hatch ───────────────────────────────────────────

const manualOpen = ref(false)
const manualExpansion = ref('')
const manualUrl = ref('')
const manualError = ref<string | null>(null)
const manualSaving = ref(false)

function openManual() {
  manualOpen.value = true
  manualError.value = null
  manualExpansion.value = ''
  manualUrl.value = ''
}

async function saveManual() {
  const id = card.value?.id
  if (!id || manualSaving.value) return

  manualSaving.value = true
  manualError.value = null
  try {
    // `active` in the same PATCH as the expansion: the server stamps
    // `decided_at` on a status change, and a manual definition that stayed
    // `undefined` would be re-grounded away by the next acronyms stage.
    await apiPatch(`/glossary/${encodeURIComponent(id)}`, {
      expansion: manualExpansion.value.trim(),
      grounding_url: manualUrl.value.trim(),
      status: 'active',
    })
    manualOpen.value = false
    // Same repaint the lookup path uses; `hasDefinition` takes it from there.
    entities.invalidate()
    if (hover.activeId) await entities.fetchOne(hover.activeId)
  } catch (err) {
    // The server refuses an expansion whose initials do not generate the
    // acronym, and says why. That message is the whole value of the failure.
    manualError.value = errorText(err, 'could not save')
  } finally {
    manualSaving.value = false
  }
}

/** The POST, the toast and the run-following all live in the lookups store;
 *  this only names the endpoint and the word the toasts should carry. */
async function lookup() {
  const current = card.value
  if (!current || lookupBlocked.value) return

  // Two endpoints, one button. An acronym is grounded by its SURFACE (the
  // letters are the question); an entity by its id.
  const path =
    current.kind === 'entity'
      ? `/entities/${encodeURIComponent(current.id)}/define`
      : `/glossary/${encodeURIComponent(current.surface ?? current.id)}/lookup`

  await lookups.start(current.id, path, current.surface ?? current.name ?? current.id)
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="hover.open"
      ref="cardEl"
      class="card fixed z-popover w-(--card-w) max-w-[calc(100vw_-_2*var(--space-2))] max-h-(--scroll-xl) overflow-y-auto px-4 pt-3 pb-0 bg-surface-overlay border border-border-default rounded-lg font-ui text-base leading-normal"
      :style="{
        top: `${position.top}px`,
        left: `${position.left}px`,
        borderColor: card ? entityColourVar(card) : undefined,
      }"
      @mouseenter="hover.hold()"
      @mouseleave="hover.release()"
    >
      <div v-if="loading" class="card__loading flex items-center gap-2 py-2 pb-3">
        <Spinner :size="12" /><span class="text-text-tertiary text-xs">loading</span>
      </div>

      <template v-else-if="card">
        <!-- ONE body for all three kinds. A neighbour in the connections graph
             goes where the button below goes: into the graph, seeded on it. -->
        <EntityCardBody
          :card="card"
          :graph-width="368"
          :title-href="pageHref"
          @open="openPage($event)"
          @select="openInGraph($event)"
        />

        <!-- `rejected` renders nothing here on purpose. The state keeps the
             footer button reading "Try again" so the card does not offer to
             spend money twice on the same question, but the search-and-
             rejection detail is operator material — the run trace under
             /admin/runs/:id is where it lives. -->
        <p
          v-if="lookupState === 'error'"
          class="card__note card__note--error text-xs mt-3 text-signal-critical"
        >
          {{ lookupError }}
        </p>

        <!-- No "unavailable while a run holds the budget" notice: contract C11
             puts lookups in their own lane. What can happen is a WAIT -- three
             lookups already in flight -- which is a different sentence, shown in
             the footer only once it applies. -->

        <!-- The human escape hatch. Invariant 3 waives the SENTENCE check for a
             person -- a curator who knows the term should not have to find a
             page that spells it out -- but not the audit trail, so the source
             URL is required and the initials check still runs server-side. -->
        <form
          v-if="manualOpen"
          class="card__manual flex flex-col gap-2 mt-3 pt-2 pb-3 border-t border-t-border-subtle"
          @submit.prevent="saveManual"
        >
          <input
            v-model="manualExpansion"
            class="card__input w-full px-2 py-1 text-sm font-[inherit] text-text-primary bg-surface-inset border border-border-subtle rounded-sm"
            placeholder="United States Code"
            aria-label="Expansion"
          />
          <input
            v-model="manualUrl"
            class="card__input w-full px-2 py-1 text-sm font-[inherit] text-text-primary bg-surface-inset border border-border-subtle rounded-sm"
            placeholder="https://… (source)"
            aria-label="Source URL"
          />
          <p v-if="manualError" class="card__note card__note--error text-xs text-signal-critical">
            {{ manualError }}
          </p>
          <div class="flex items-center gap-2">
            <Button
              variant="outline"
              icon="check"
              label="Save"
              :disabled="!manualExpansion.trim() || !manualUrl.trim() || manualSaving"
              @click="saveManual"
              >Save</Button
            >
            <Button variant="text" icon="close" label="Cancel" @click="manualOpen = false">Cancel</Button>
          </div>
        </form>

        <!-- ── actions ──────────────────────────────────────────────────
             An icon bar. Up to four actions have to fit a 400px prose card,
             and four worded buttons wrap to a second line that reads as prose.
             Each `label` is the accessible name AND the hover title, so the
             word is still there -- on the pointer and in the accessibility
             tree rather than in the box. `bordered`, because the bar sits as
             a row of boxed controls on an overlay panel, not inside a table
             row. None is filled: an accent-filled control means "primary
             action", and a hover card is not the page. -->
        <!-- STICKY to the foot of the scroll container. The card clamps at
             `--scroll-xl` and an entity with a definition, aliases, four sources
             and a graph is taller than that, so the actions sat below the fold
             -- buttons at the bottom of a document nobody scrolls are not
             buttons at the bottom.

             The negative inline margin lets the opaque background span the
             card's full width rather than stopping at the text column, and the
             card carries NO bottom padding: a scroll container's padding sits
             below a `sticky bottom-0` child, so content scrolling past showed
             through in the gap beneath the buttons. The footer owns that
             padding now. -->
        <footer
          v-else
          class="card__foot sticky bottom-0 flex items-center gap-2 flex-wrap mt-3 -mx-4 px-4 pt-2 pb-3 border-t border-t-border-subtle bg-surface-overlay"
        >
          <span v-if="lookupBusy" class="flex items-center gap-2 text-text-tertiary text-xs">
            <Spinner :size="12" />
            {{ lookupState === 'queued' ? 'waiting its turn…' : 'searching and reading sources…' }}
          </span>
          <template v-else>
            <!-- The name's own page, and it leads: a card is a glance, the page
                 is the whole of what Venona holds on a name (ADR 0232).
                 Available for an acronym too, which the graph button is not: a
                 term has a page even though it is not a node. -->
            <IconButton
              label="Open"
              bordered
              @click="openPage()"
            >
              <Icon name="external" :size="12" />
            </IconButton>
            <IconButton v-if="canOpenInGraph" label="Open in graph" bordered @click="openInGraph()">
              <Icon name="waypoints" :size="12" />
            </IconButton>
            <!-- The reader's own follow, on the surface where a name is met.
                 Their data, so no role gate. -->
            <IconButton
              v-if="card.kind === 'entity'"
              :label="isFollowing(card.id) ? 'Following' : 'Follow'"
              :active="isFollowing(card.id)"
              :disabled="followPending"
              bordered
              @click="toggleFollow(card.id)"
            >
              <Icon name="target" :size="12" />
            </IconButton>
            <!-- Both grounding paths are admin: the lookup spends, and the
                 manual expansion writes the glossary. A reader's card ends at
                 the graph button. -->
            <IconButton
              v-if="!hasDefinition && session.isAdmin()"
              :label="lookupState === 'idle' ? 'Look this up' : 'Try again'"
              :title="modelDown ? 'the model is not answering; see Runs' : undefined"
              :disabled="lookupBlocked || modelDown"
              bordered
              @click="lookup"
            >
              <Icon name="search" :size="12" />
            </IconButton>
            <!-- Only acronyms take a hand-entered expansion: the endpoint
                 behind it verifies initials, which has no meaning for a name. -->
            <IconButton
              v-if="card.kind !== 'entity' && !hasDefinition && session.isAdmin()"
              label="I know this one"
              bordered
              @click="openManual"
            >
              <Icon name="pencil" :size="12" />
            </IconButton>
            <!-- The durable record of a lookup that grounded nothing, said
                 out loud: "Try again" alone reads as a fresh offer, and the
                 reader cannot tell an unlooked-up term from a looked-up dry
                 one. `basis-full` gives it a line of its own under the bar --
                 inline it splits the icons around a sentence and wraps the
                 row. -->
            <span
              v-if="lookupState === 'rejected' && card.last_attempt_at && !hasDefinition"
              class="basis-full text-text-tertiary text-xs"
            >
              looked up {{ dayTimeLabel(card.last_attempt_at) }} · nothing found
            </span>
          </template>
        </footer>
      </template>

      <!-- Reachable because `loading` stops at a known-missing id. -->
      <div v-else class="text-text-tertiary text-xs pb-3">no data for this reference</div>
    </div>
  </Teleport>
</template>
