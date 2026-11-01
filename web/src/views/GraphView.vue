<script setup lang="ts">
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import EdgeProvenancePanel from '@/components/graph/EdgeProvenancePanel.vue'
import EntityCardBody from '@/components/entity/EntityCardBody.vue'
import GraphFocusPanel from '@/components/graph/GraphFocusPanel.vue'
import GraphModeControl from '@/components/graph/GraphModeControl.vue'
import GraphSearch from '@/components/graph/GraphSearch.vue'
import GraphViewOptions from '@/components/graph/GraphViewOptions.vue'
import PathFinder from '@/components/graph/PathFinder.vue'
import ApiUnreachable from '@/components/shared/ApiUnreachable.vue'
import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import Icon from '@/components/ui/Icon.vue'
import IconButton from '@/components/ui/IconButton.vue'
import Kbd from '@/components/ui/Kbd.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ListRail from '@/components/ui/ListRail.vue'
import Panel from '@/components/ui/Panel.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import Spinner from '@/components/ui/Spinner.vue'
import StateNotice from '@/components/ui/StateNotice.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import Toggle from '@/components/ui/Toggle.vue'
import EmptyCorpus from '@/components/shared/EmptyCorpus.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import {
  destroyCytoscape,
  getCy,
  graphDebugOn,
  graphDiagnostics,
  mergeElements,
  initCytoscape,
  resizeToContainer,
  toElements,
} from '@/composables/useCytoscape'
import { useIsPhone } from '@/composables/useBreakpoint'
import { useKeyBindings } from '@/composables/useKeyboard'
import { useServiceHealth } from '@/composables/useServiceHealth'
import { useGraphLayout } from '@/composables/graph/useGraphLayout'
import { useGraphScope } from '@/composables/graph/useGraphScope'
import { useGraphSelection } from '@/composables/graph/useGraphSelection'
import { useGraphVisibility } from '@/composables/graph/useGraphVisibility'
import { toEntityRoute } from '@/router/entityRoute'
import {
  fromGraphQuery,
  graphIntentKey,
  type GraphMode,
} from '@/router/graphRoute'
import { useBriefsStore } from '@/stores/briefs'
import { useCorpusEmpty } from '@/composables/useCorpusEmpty'
import { useGraphStore } from '@/stores/graph'
import { useUiStore } from '@/stores/ui'

/**
 * STATED, not inferred. `<KeepAlive include="BriefView,GraphView">` in
 * `AppShell.vue` matches by component name, and with `<script setup>` that name
 * is whatever `@vitejs/plugin-vue` infers from the filename. This route is
 * `import()`-split (`router/index.ts`), so the name is load-bearing across a
 * module boundary: lose it and the view silently stops being cached, and every
 * return to /graph pays a full cold fcose layout, which `docs/GRAPH-PERF.md`
 * measures as the most expensive thing the app does.
 */
defineOptions({ name: 'GraphView' })

const graph = useGraphStore()
/** One shared answer to "is there anything yet" -- see `useCorpusEmpty`. */
const { filling } = useCorpusEmpty()

/** Neo4j is the only service this view depends on; the API matters to it the
 *  way it matters everywhere. See `useServiceHealth` for the derivations. */
const { apiDown, neo4jDown, neo4jDetail } = useServiceHealth()

/** The canvas has nothing AND the corpus is still filling. Paired so a graph
 *  filtered down to zero on a populated corpus keeps its own explanation --
 *  `emptyCanvas` below already distinguishes filtered-to-nothing from
 *  unprojected, and telling that reader to start a run would be wrong.
 *
 *  Gated off while Neo4j is down: a failed fetch leaves the node count at
 *  zero, and this branch unmounts the whole grid -- overlays included -- so
 *  without the gate an outage during a run renders the first-run screen and
 *  the outage notice never mounts. A zero count from a database that is not
 *  answering is not an empty corpus. */
const showEmptyCorpus = computed(() => filling.value && graph.nodeCount === 0 && !neo4jDown.value)
const briefs = useBriefsStore()
const ui = useUiStore()

/** The canvas already carries the counts as a readout; the ContextBar states
 *  them as the route's identity so every route names itself the same way. */
const identity = computed(() => {
  const counted = `${visibleNodes.value} nodes · ${visibleEdges.value} edges`
  // The scope belongs here, not only in a rail that starts collapsed: with
  // clicking as the way into focus mode, "why am I looking at nine things"
  // needs an answer that is always on screen.
  if (graph.mode === 'focus' && focusSubjectNode.value) {
    return `graph · around ${focusSubjectNode.value.name} · ${counted}`
  }
  if (graph.mode === 'brief') return `graph · this brief · ${counted}`
  return `graph · ${counted}`
})
const route = useRoute()
const router = useRouter()

/** The selected name's own page. The href on the title is real, so a modified
 *  click has already been left to the browser and this is the plain case. */
function openSelectedName(event: MouseEvent, id: string) {
  event.preventDefault()
  void router.push(toEntityRoute(id))
}
const canvas = ref<HTMLElement | null>(null)

/** What is drawn, and every readout derived from it. It takes nothing from
 *  here and gives back the counts, the emphasis controller and the one repaint
 *  function. */
const {
  emphasis,
  refreshVisibility,
  visibleNodes,
  visibleEdges,
  unconnectedHidden,
  filteredOut,
  shownAsContext,
  typeOptions,
  predicateOptions,
  activeFilterCount,
  emptyCanvas,
} = useGraphVisibility()

/**
 * Selection and scope are MUTUALLY dependent, and the arrows are what resolve it.
 *
 * Selection needs `focusOn`/`exploreBeyondBrief` (a mini-graph neighbour focuses
 * the graph); scope needs `selectNode` (a focus carries a selection). Across two
 * composables that is late binding, and the arrows defer the lookup until after
 * both exist. Neither is called during setup, so there is no temporal-dead-zone
 * hazard.
 */
const { selectNode, selectFromMiniGraph, selectedCard, stepSelection } = useGraphSelection({
  focusOn: (id) => scope.focusOn(id),
  exploreBeyondBrief: (id) => scope.exploreBeyondBrief(id),
})

/**
 * The drawing pipeline. `currentIntent` and `refreshVisibility` go IN because
 * `scopeKey` must name the same scope the route does, and visibility runs before
 * every layout; `observe`/`disconnect` come OUT as plain functions, so this
 * view's `onMounted` keeps deciding when the canvas exists.
 */
const { render, relayout, persistPositions, observe, disconnect, arranging } = useGraphLayout({
  currentIntent: () => scope.currentIntent(),
  refreshVisibility,
})

/** A fetch slow enough to notice covers the canvas as well. The whole graph is
 *  over a megabyte, and the previous scope sitting there that long reads as the
 *  click having done nothing. */
const slowLoad = ref(false)
let slowLoadTimer: ReturnType<typeof setTimeout> | undefined
watch(
  () => graph.loading,
  (on) => {
    clearTimeout(slowLoadTimer)
    slowLoad.value = false
    if (on) slowLoadTimer = setTimeout(() => (slowLoad.value = true), 400)
  },
)

/** `?graphdebug=1`: what the canvas looks like from inside this browser, for
 *  a device nobody here can attach a debugger to. */
const debugOn = graphDebugOn()
const debugText = ref('')
const debugTimer = debugOn
  ? setInterval(() => {
      debugText.value = Object.entries(graphDiagnostics())
        .map(([k, v]) => `${k}: ${v}`)
        .join('\n')
    }, 1000)
  : undefined
onUnmounted(() => clearInterval(debugTimer))

const canvasCover = computed(() => {
  if (arranging.value) return `arranging ${arranging.value.toLocaleString()} names`
  return slowLoad.value ? 'loading the graph' : null
})

/** The scope machine: route codec, loaders, the focus walk, filters, the way
 *  back. Its watchers stay below, beside the lifecycle hooks they interleave
 *  with. */
const scope = useGraphScope({ refreshVisibility, render, relayout, selectNode })
const {
  syncUrl,
  applyIntent,
  setMode,
  setDepth,
  toggleFilter,
  onMinSourceChange,
  setIncludeUnconfirmed,
  resetFilters,
  focusOn,
  focusEntity,
  focusUp,
  focusUpTo,
  focusOnSelection,
  setHideIsolated,
  isDefaultView,
  resetView,
  setSpacing,
  backLabel,
  backToBrief,
  exploreBeyondBrief,
  cameFrom,
  focusTrailNodes,
  focusSubjectNode,
} = scope

/**
 * The phone fallback (ADR 0152): the graph degrades, it does not reflow. Below
 * the phone breakpoint both rail tracks are forced to zero and the canvas
 * becomes a read-only pan/zoom view -- the rails' own open/collapsed state
 * survives untouched for the next larger screen, exactly as it does through
 * full screen.
 */
const isPhone = useIsPhone()


// ── watchers ───────────────────────────────────────────────────────────────

/** Path mode dims everything not on a path, rather than switching layout
 *  engines — keeping one spatial arrangement is worth more than a prettier
 *  chain. */
watch(
  () => graph.pathResult,
  (result) => {
    const cy = getCy()
    if (!cy) return
    cy.batch(() => {
      cy.elements().removeClass('on-path dimmed')
      if (!result?.found) return
      const nodeIds = new Set(result.paths.flatMap((p) => p.node_ids))
      const edgeIds = new Set(result.paths.flatMap((p) => p.edge_ids))
      mergeElements(toElements(result.nodes, result.edges))
      cy.elements().addClass('dimmed')
      cy.nodes().forEach((n) => {
        if (nodeIds.has(n.id())) n.removeClass('dimmed').addClass('on-path')
      })
      cy.edges().forEach((e) => {
        if (edgeIds.has(e.id())) e.removeClass('dimmed').addClass('on-path')
      })
    })
    if (result?.found) cy.fit(cy.elements('.on-path'), 80)
    // Emphasis stands down while a path is showing and comes back when it
    // clears; two dimming systems on one canvas is worse than either.
    emphasis.refresh()
  },
)

/** Every route to a selection produces the same emphasis: canvas tap, search,
 *  palette, mini-graph, keyboard. Hover is an enhancement over this, never the
 *  only way to see what a node connects to. */
watch(
  () => graph.selectedNode?.id,
  () => emphasis.refresh(),
)

/**
 * Selecting something opens the detail rail, because a selection nobody can see
 * is not a selection. With the rail collapsed an edge click was the worst of
 * it: provenance was fetched over the wire into a panel that was not on screen.
 *
 * EDGES ARE IN THE WATCHED VALUE ON PURPOSE. Edge taps call `graph.selectEdge`
 * directly rather than going through `selectNode`, so watching the node alone
 * misses exactly the case that motivated this. It watches `selectedEdge` and
 * not `graph.provenance` because provenance lands asynchronously and stays null
 * when the fetch fails -- the rail would open late, or not at all. The store
 * keeps the two mutually exclusive, so one id is never ambiguous.
 *
 * Only on the null -> selected transition, never on a change of selection: in
 * focus mode clicking a neighbour re-centres, so the selection changes on every
 * hop, and re-opening each time would make the rail impossible to keep shut.
 *
 * Assignment, not `toggleGraphRail`. The rail is open by default, and a toggle
 * would close it in the common case.
 */
watch(
  () => graph.selectedNode?.id ?? graph.selectedEdge?.id ?? null,
  (selected, previous) => {
    if (selected && !previous) ui.graphRailRight = true
  },
)

/**
 * The URL is the view's input.
 *
 * Watching the decoded intent's KEY rather than `route.query` is what makes
 * `syncUrl`'s own `router.replace` inert: it writes a query that decodes to the
 * intent we already hold, so the source does not change and this never fires.
 *
 * Watching `route.query.seed` alone and guarding on equality is not enough:
 * under `<KeepAlive>`, where `onMounted` runs exactly once, arriving from a
 * second brief with the same entity is a total no-op and the way back still
 * points at the first one.
 */
watch(
  () => graphIntentKey(fromGraphQuery(route.query)),
  () => {
    if (route.name !== 'graph' || !getCy()) return
    void applyIntent(fromGraphQuery(route.query))
  },
)

onMounted(async () => {
  // FIRED, not awaited. The ontology fills the filter rail's chips; the graph
  // fetch below does not read it and does not wait for it. Awaiting it here
  // puts two independent round trips in series in front of the first paint.
  // Both are awaited together at the foot of this function, so the view is
  // still fully loaded before it claims to be.
  const ontology = graph.loadOntology()
  // Cached and a no-op after the first call. `backToBrief` needs `latestId` to
  // decide between `/` and `/briefs/:id`, and with an empty index it would
  // always choose the non-canonical form.
  void briefs.loadIndex()
  if (!canvas.value) return

  const cy = initCytoscape(canvas.value)
  observe(canvas.value)
  emphasis.attach(cy)

  cy.on('tap', 'node', (event) => {
    const id = event.target.id()
    selectNode(id)
    cy.nodes().removeClass('selected')
    event.target.addClass('selected')

    // CLICKING FOCUSES. Select-and-expand does nothing once the whole corpus
    // arrives resident -- every node is already expanded. Focusing is what you
    // want from a node in a field of three hundred: it walks (clicking a
    // neighbour re-centres on it), and `reset` is the one way back rather than
    // a mode chip that only works the first time.
    //
    // Brief mode is the exception and stays a plain selection. Focusing would
    // leave the frame the view exists to provide, and the canvas would stop
    // matching its own address; "explore beyond the brief" in the detail rail
    // is the deliberate way out.
    if (graph.mode === 'brief') return
    if (graph.mode === 'focus' && graph.focusSubject === id) return
    void focusOn(id)
  })

  cy.on('tap', 'edge', (event) => {
    graph.selectEdge(graph.edges.get(event.target.id()) ?? null)
    cy.edges().removeClass('selected')
    event.target.addClass('selected')
  })

  cy.on('tap', (event) => {
    if (event.target !== cy) return
    // Cytoscape fires `tap` only for a click that did not drag, so panning the
    // canvas does not count as clicking off a node.
    if (graph.mode === 'focus') {
      void focusUp()
      return
    }
    selectNode(null)
    cy.elements().removeClass('selected')
  })

  await Promise.all([applyIntent(fromGraphQuery(route.query)), ontology])
  syncUrl()
})

/**
 * Under `<KeepAlive>` this view is cached, not destroyed, which is the point:
 * three hops of exploration survive a trip to Runs and back. So Cytoscape's
 * lifecycle splits -- positions are snapshotted on DEACTIVATE, and the instance
 * is only destroyed on a real unmount.
 */
onDeactivated(() => {
  void persistPositions()
  // Never leave the app without its header because the user navigated away
  // from the only view that can restore it.
  ui.exitFullscreen()
})

onActivated(() => {
  // The box may have changed size while this was cached (a rail collapsed on
  // another route, the window resized).
  requestAnimationFrame(resizeToContainer)
  // And the query may be a completely different one: `onMounted` runs once, so
  // this is the only place a cached view learns it was navigated to again.
  if (getCy()) void applyIntent(fromGraphQuery(route.query))
})

onUnmounted(() => {
  disconnect()
  emphasis.dispose()
  destroyCytoscape()
})

/**
 * Ask for a rail.
 *
 * Full screen hides both, so in full screen these keys have nothing to toggle
 * that anyone can see -- and toggling anyway changes the reader's rail state
 * invisibly, so that leaving full screen springs open a panel they never
 * opened. Asking for a panel is a request to see it, so it leaves full screen
 * and OPENS the rail: an assignment, not a toggle, for the same reason the
 * selection watcher above uses one.
 */
function revealRail(side: 'left' | 'right') {
  if (!ui.fullscreen) {
    ui.toggleGraphRail(side)
    return
  }
  ui.exitFullscreen()
  if (side === 'left') ui.graphRailLeft = true
  else ui.graphRailRight = true
}

/**
 * Ask for the search.
 *
 * The same shape as `revealRail`, and for the same reason: full screen
 * collapses the ContextBar to a zero-height `invisible` row, and a hidden input
 * cannot take focus. Asking to find a node is a request to see the field, so it
 * leaves full screen and focuses it rather than doing nothing.
 *
 * Registered HERE rather than through `FilterField`'s own `hotkey`, because
 * that one goes straight to `focus()` and this has somewhere to be first.
 */
const search = ref<InstanceType<typeof GraphSearch> | null>(null)

function revealSearch() {
  if (ui.fullscreen) ui.exitFullscreen()
  void nextTick(() => search.value?.focus())
}

useKeyBindings([
  { keys: ['/'], label: 'Find a name', group: 'graph', run: revealSearch },
  {
    keys: ['['],
    label: 'Toggle the filter rail',
    group: 'graph',
    run: () => revealRail('left'),
  },
  {
    keys: [']'],
    label: 'Toggle the detail rail',
    group: 'graph',
    run: () => revealRail('right'),
  },
  { keys: ['f'], label: 'Full screen', group: 'graph', run: () => ui.toggleFullscreen() },
  { keys: ['r'], label: 'Reset the view', group: 'graph', run: () => void resetView() },
  { keys: [','], label: 'Up one level', group: 'graph', run: () => void focusUp() },
  { keys: ['n'], label: 'Select the next name', group: 'graph', run: () => stepSelection(1) },
  {
    keys: ['p'],
    label: 'Select the previous name',
    group: 'graph',
    run: () => stepSelection(-1),
  },
  {
    keys: ['.'],
    label: 'Focus on the selection, or leave focus',
    group: 'graph',
    run: () => {
      const id = graph.selectedNode?.id
      if (graph.mode === 'focus' && (!id || id === graph.focusSubject)) void setMode('all')
      else void focusOnSelection()
    },
  },
])
</script>

<template>
  <!-- The first-run screen is a PAGE, not a canvas: `width`/`scroll` switch
       with it so it inherits the same `--edge` and `--space-5` every other
       route uses. Positioning it with a padding class instead is what left
       the same component at four different offsets (measured 2026-08-06:
       166px on the Brief, 140px on the Archive, 26px here). -->
  <ViewShell
    :width="showEmptyCorpus ? 'wide' : 'full'"
    :scroll="showEmptyCorpus ? 'page' : 'none'"
    :identity="identity"
  >

    <!-- All three scopes change WHAT YOU SEE, so all three are outlined and
         none is filled. A solid accent control in this zone means "primary
         action", which is the opposite kind of work. -->
    <!-- Off on the first-run screen: it scopes a canvas that has no nodes. -->
    <template v-if="!showEmptyCorpus" #controls>
      <GraphModeControl
        :model-value="graph.mode"
        :can-focus="Boolean(graph.selectedNode?.id || graph.focusSubject)"
        :can-brief="Boolean(cameFrom.briefId)"
        @update:model-value="(m: GraphMode) => setMode(m, { explicit: true })"
      />
    </template>

    <!-- Where every route's filter sits. Finding a node is the same act as
         narrowing a list; that it acts on a canvas rather than on rows is the
         view's business, not the bar's. -->
    <template v-if="!showEmptyCorpus" #search>
      <GraphSearch ref="search" @pick="focusEntity" />
    </template>

    <template v-if="!showEmptyCorpus" #toggles>
      <Toggle :model-value="ui.fullscreen" @update:model-value="ui.toggleFullscreen()">
        full screen
      </Toggle>
    </template>

    <template v-if="!showEmptyCorpus" #actions>
      <Button v-if="cameFrom.briefId || cameFrom.itemId" variant="text" icon="left" label="Back" @click="backToBrief">
        {{ backLabel }}
      </Button>
      <!-- `reset` DOES a thing and returns to rest, which is what this zone is
           for -- the scope segments and the full-screen switch beside it only
           change what you are looking at. It sat among them because it was
           read as graph chrome; on the right it sits with every other button in
           the app, which is where the hand looks for one.

           Always rendered and disabled at rest: a control that appears only
           once you have something to undo teaches nothing about the way back.
           Outlined, not filled -- nothing here spends or writes. -->
      <Button variant="outline" :disabled="isDefaultView" @click="resetView">
        reset <Kbd muted>R</Kbd>
      </Button>
    </template>

    <EmptyCorpus v-if="showEmptyCorpus" />

    <!-- Full screen takes the rails with it. `f` means "the canvas and nothing
         else", and hiding the app header alone leaves 272px of filters and
         336px of detail on either side (`tokens.css`) -- the one gesture that
         exists to give the graph the whole window giving it two thirds of one.

         A track WIDTH, not a `v-if`. The rail is `overflow-hidden`, so a zero
         track clips it away completely and the reader's own rail state survives
         untouched; unmounting it would throw away the path finder's inputs and
         the detail rail's scroll, and the note above `fullscreen` in
         `stores/ui.ts` explains why unmounting anything around this view is how
         you lose the Cytoscape instance. -->
    <div v-else class="grid" :style="{ '--left': ui.fullscreen || isPhone ? 'var(--rail-hidden)' : ui.graphRailLeft ? 'var(--rail-width)' : 'var(--rail-collapsed)', '--right': ui.fullscreen || isPhone ? 'var(--rail-hidden)' : ui.graphRailRight ? 'var(--rail-width-wide)' : 'var(--rail-collapsed)' }">
      <ListRail
        side="left"
        label="filters"
        :collapsed="!ui.graphRailLeft"
        :badge="activeFilterCount"
        @toggle="ui.toggleGraphRail('left')"
      >
        <GraphFocusPanel
          v-if="graph.mode === 'focus'"
          :subject="focusSubjectNode"
          :trail="focusTrailNodes"
          :depth="graph.focusDepth"
          :visible-nodes="visibleNodes"
          :visible-edges="visibleEdges"
          @update:depth="setDepth"
          @up="focusUpTo"
          @exit="setMode('all')"
        />

        <GraphViewOptions
          :spacing="ui.graphSpacing"
          :hide-isolated="graph.filters.hideIsolated"
          :include-unconfirmed="graph.filters.includeUnconfirmed"
          @update:include-unconfirmed="setIncludeUnconfirmed"
          @update:spacing="setSpacing"
          @update:hide-isolated="setHideIsolated"
        />

        <div class="section p-3 border-b border-b-border-subtle">
          <SectionHead as="h4" class="mb-2" label="types" />
          <div class="flex items-center flex-wrap gap-1">
            <Chip
              v-for="t in typeOptions"
              :key="t.name"
              interactive
              dot
              :selected="graph.filters.types.includes(t.name)"
              :style="{ '--chip-dot': `var(${t.token})` }"
              @click="toggleFilter(graph.filters.types, t.name)"
            >
              {{ t.name }}
            </Chip>
          </div>
        </div>

        <div class="section p-3 border-b border-b-border-subtle">
          <SectionHead as="h4" class="mb-2" label="relationships" />
          <div class="flex items-center flex-wrap gap-1">
            <Chip
              v-for="p in predicateOptions"
              :key="p.name"
              interactive
              size="sm"
              :selected="graph.filters.predicates.includes(p.name)"
              :title="p.description"
              @click="toggleFilter(graph.filters.predicates, p.name)"
            >
              {{ p.name }}
            </Chip>
          </div>
        </div>

        <div class="section p-3 border-b border-b-border-subtle">
          <SectionHead as="h4" class="mb-2" label="minimum sources" />
          <div class="flex items-center gap-2">
            <input
              v-model.number="graph.filters.minSourceCount"
              type="range" class="flex-1"
              min="1"
              max="5"
              aria-label="Minimum number of sources"
              @change="onMinSourceChange"
            />
            <span class="mono text-xs">{{ graph.filters.minSourceCount }}</span>
          </div>
          <p class="text-text-tertiary text-xs">dashed edges are single-source</p>
        </div>

        <PathFinder />
      </ListRail>

      <main class="canvas-wrap relative min-w-0">
        <!-- The canvas fills its wrapper by HEIGHT AND WIDTH, deliberately not
             `absolute inset-0`, which is what made it invisible.

             Cytoscape injects `.__________cytoscape_container{position:relative}`
             into the document at runtime, UNLAYERED. Tailwind emits `.absolute`
             inside `@layer utilities`, and an unlayered declaration beats a
             layered one no matter its specificity or source order -- so the
             library's rule won, `inset-0` stopped applying, and since the only
             children here are Cytoscape's own out-of-flow canvases the box
             collapsed to 950x0 (measured 2026-08-06). Cytoscape rendered the
             whole graph into it, correctly, at zero height.

             So sizing does not go through `position` at all. `h-full w-full`
             resolves against the wrapper, which has a definite size as a
             stretched item of the rail grid, and `height`/`width` are
             properties Cytoscape never touches.

             Not `grid` on the wrapper with this as a stretched item, which is
             the other obvious spelling: `grid` is BOTH a Tailwind utility and
             this component's own scoped rule, so the wrapper picked up
             `grid-template-columns: var(--left) 1fr var(--right)` and the
             canvas landed in the collapsed rail's 32px column.

             `relative` stays on the wrapper: the overlays position against it. -->
        <div ref="canvas" class="canvas h-full w-full" />

        <!-- Opaque, so the half-arranged canvas is never what a reader sees.
             Before the overlay stack in source order, so the readout and its
             count stay on top of it. -->
        <div
          v-if="canvasCover"
          class="canvas-cover absolute inset-0 flex items-center justify-center bg-surface-base"
        >
          <p class="flex items-center gap-2 m-0 text-sm text-text-secondary">
            <Spinner :label="canvasCover" /><span>{{ canvasCover }}</span>
          </p>
        </div>

        <pre
          v-if="debugOn"
          class="graph-debug absolute right-3 bottom-3 m-0 max-w-coverage p-2 whitespace-pre-wrap break-all bg-surface-overlay border border-border-subtle rounded-sm mono text-2xs text-text-secondary pointer-events-none"
        >{{ debugText }}</pre>

        <div class="overlay absolute top-3 left-3 flex flex-col gap-2 pointer-events-none [&>*]:pointer-events-auto">
          <!-- The phone's way INTO full screen: the ContextBar toggle still
               exists, but on a phone the canvas is the point, so the
               affordance sits with the canvas controls. In the overlay STACK
               rather than a bottom corner -- everything anchored to the bottom
               edge at 390px lands on the hint. -->
          <Button
            v-if="isPhone && !ui.fullscreen"
            class="enter-fullscreen self-start bg-surface-overlay"
            variant="outline"
            icon="maximize-2"
            label="Full screen"
            @click="ui.toggleFullscreen()"
          >
            full screen
          </Button>

          <!-- The canvas describes itself. Two clauses, two independent undos:
               `show` restores the unconnected entities and nothing else, while
               `reset` clears the chips and the slider. One link that undid both
               would be a bug with a delivery date. `shown for context` has no
               undo because it is not a thing that was done to the reader: it
               names the names on screen that no chip of theirs selected, which
               are there because an edge they DID ask for arrives at them. -->
          <p class="readout mono flex items-center max-xs:flex-wrap gap-1 text-xs text-text-tertiary pointer-events-none">
            <span class="readout__n text-text-secondary">{{ visibleNodes }}</span> nodes
            <span class="readout__sep text-border-strong mx-1">·</span>
            <span class="readout__n text-text-secondary">{{ visibleEdges }}</span> edges
            <template v-if="graph.truncated">
              <span class="readout__sep text-border-strong mx-1">·</span><span class="capped text-signal-high">capped</span>
            </template>
            <template v-if="unconnectedHidden">
              <span class="readout__sep text-border-strong mx-1">·</span>{{ unconnectedHidden }} unconnected hidden
              <Button class="readout__reset pointer-events-auto ml-2" variant="text" @click="setHideIsolated(false)">
                show
              </Button>
            </template>
            <template v-if="shownAsContext">
              <span class="readout__sep text-border-strong mx-1">·</span>{{ shownAsContext }} shown for context
            </template>
            <template v-if="filteredOut">
              <span class="readout__sep text-border-strong mx-1">·</span>{{ filteredOut }} filtered
              <Button class="readout__reset pointer-events-auto ml-2" variant="text" @click="resetFilters">reset</Button>
            </template>
            <Spinner v-if="graph.loading" :size="11" />
          </p>

          <!-- The two outages this view can hit, in the overlay stack rather
               than in place of the canvas: the Cytoscape instance and whatever
               is already drawn survive underneath, and recovery is the notice
               disappearing on a health tick. API first -- when the whole API
               is unreachable, blaming Neo4j would be a guess about a server
               that is not answering. -->
          <ApiUnreachable v-if="apiDown" class="max-w-hint">
            Reading what is already drawn still works. Venona keeps trying on its own.
          </ApiUnreachable>

          <StateNotice
            v-else-if="neo4jDown"
            tone="high"
            status="graph database down · retrying"
            heading="The graph database stopped answering"
            class="max-w-hint"
          >
            Reading briefs still works. The graph explorer's database is down;
            it usually comes back on its own, and the graph can always be
            rebuilt from Venona's records.
            <span class="mono">{{ neo4jDetail }}</span>
            <template #action>
              <span class="flex items-center gap-2">
                <Spinner /><span class="font-ui text-2xs text-text-quiet">retrying</span>
              </span>
            </template>
          </StateNotice>

          <!-- A blank canvas that says why it is blank.
               Three different causes render identically -- an unprojected
               graph, a corpus whose entities all sit edgeless behind the
               default `hideIsolated`, and a predicate list disjoint from the
               ontology, which `graph/queries.py` answers with an empty payload
               and no error at all. Drawing nothing and explaining nothing is
               indistinguishable from the canvas being broken.

               It lives in the overlay stack rather than centred in the void:
               same left edge as the readout, which is where anyone looking at
               an empty graph is already reading. -->
          <EmptyState
            v-if="!graph.loading && !graph.error && !apiDown && !neo4jDown && visibleNodes === 0"
            :title="emptyCanvas.title"
          >
            <template v-if="emptyCanvas.cause === 'unprojected'">
              The graph is drawn from Venona's records and this one is empty.
              Start a research run to fill it.
            </template>
            <template v-else-if="emptyCanvas.cause === 'isolated'">
              Every name here is unconnected. A relationship is drawn once both
              of its names are confirmed by a second source.
            </template>
            <template v-else-if="emptyCanvas.cause === 'filtered'">
              No name survives the filters on the left.
            </template>
            <template v-else>
              There is nothing on the canvas to draw. Try a wider view, or
              rebuild the graph.
            </template>
            <template v-if="emptyCanvas.cause === 'isolated' || emptyCanvas.cause === 'filtered'" #action>
              <Button
                v-if="emptyCanvas.cause === 'isolated'"
                variant="outline"
                icon="eye"
                label="Show unconnected"
                @click="setHideIsolated(false)"
              >
                show unconnected
              </Button>
              <Button v-else variant="outline" icon="undo" label="Reset filters" @click="resetFilters">reset filters</Button>
            </template>
          </EmptyState>
        </div>

        <!-- What the projection cannot show. Only `active` entities are
             projected, so a relationship with one uncorroborated endpoint never
             reaches Neo4j. Saying so turns "the extraction looks weak" into
             "the corroboration gate is holding relationships back", which is a
             different problem with a different fix. -->
        <!-- `max-xs:hidden` on both coverage notes: at phone width they land on
             the hint and the two render as interleaved text. The phone view is
             read-only context anyway (ADR 0152) -- the counts return with the
             larger screen. -->
        <!-- Rendered when something is hidden, AND whenever the reader has
             asked for the names seen once: a corpus with nothing left out then
             drops the readout entirely, which takes away the one confirmation
             that the setting did anything. -->
        <p v-if="graph.mode === 'all' && graph.coverage && (graph.coverage.relationships_hidden || graph.coverage.includes_unconfirmed)" class="coverage absolute left-4 bottom-6 max-xs:hidden m-0 max-w-coverage px-3 py-2 bg-surface-overlay border border-border-subtle rounded-sm text-xs leading-normal text-text-tertiary">
          Showing {{ graph.coverage.relationships_shown }} of
          {{ graph.coverage.relationships_known }} known relationships.
          <!-- The sentence changes with the setting because the FACT does.
               With names seen once drawn, what stays out is the quiet material
               rather than the unconfirmed material, so telling this reader to
               wait for a second source would describe the wrong population. -->
          <template v-if="graph.coverage.includes_unconfirmed">
            <template v-if="graph.coverage.relationships_hidden">
              {{ graph.coverage.relationships_hidden }} rest on names no source has
              written about lately.
            </template>
            <template v-else>Names seen once are included, drawn hollow.</template>
          </template>
          <template v-else>
            {{ graph.coverage.relationships_hidden }} are hidden until a second source confirms
            both of their names ({{ graph.coverage.entities_uncorroborated }} such names).
            <!-- The sentence states an absence, so it owes the reader the way
                 out of it. Labelled with the rail control's own words rather
                 than "show them": this is the same setting, and a reader who
                 turns it on here has to be able to find it again to turn it
                 off. Same `variant="text"` action the readout offers beside
                 `unconnected hidden`, which is the same shape of fact. -->
            <Button class="ml-1" variant="text" @click="setIncludeUnconfirmed(true)">
              show names seen once
            </Button>
          </template>
        </p>

        <!-- The brief's own coverage. Deliberately NOT the corpus-wide figure
             above, which describes a different population from the one on
             screen -- `BriefScope` in `api/schemas/graph.py` carries the case. -->
        <p v-else-if="graph.mode === 'brief' && graph.briefScope" class="coverage absolute left-4 bottom-6 max-xs:hidden m-0 max-w-coverage px-3 py-2 bg-surface-overlay border border-border-subtle rounded-sm text-xs leading-normal text-text-tertiary">
          This brief named {{ graph.briefScope.mentioned }}
          {{ graph.briefScope.mentioned === 1 ? 'name' : 'names' }};
          {{ graph.briefScope.present }}
          {{ graph.briefScope.present === 1 ? 'is' : 'are' }} in the graph.
          <template v-if="graph.briefScope.missing?.length">
            {{ graph.briefScope.missing?.length }} are not yet confirmed by a second source, or were merged away.
          </template>
        </p>

        <!-- Muted while an outage notice is up: the failed fetch is a symptom
             of the same fact, and one failure gets one report. -->
        <StatusNote v-if="graph.error && !apiDown && !neo4jDown" kind="error" class="overlay-error absolute bottom-6 left-3">
          {{ graph.error }}
        </StatusNote>
        <p class="hint absolute bottom-3 left-3 text-xs text-text-tertiary">
          <!-- The phone hint says what this view IS instead of promising
               clicks whose results -- the rails -- are hidden at this width. -->
          <template v-if="isPhone">
            read-only view · pan and zoom · filters and detail need a larger screen
          </template>
          <template v-else-if="graph.mode === 'brief'">
            click a node to select it · this graph is scoped to the brief
          </template>
          <template v-else-if="graph.mode === 'focus'">
            click a neighbour to move the focus · click the background to go up ·
            <Kbd>R</Kbd> resets
          </template>
          <template v-else>
            click a node to focus it · click an edge for provenance
          </template>
          <template v-if="!ui.fullscreen && !isPhone"> · <Kbd>F</Kbd> full screen</template>
        </p>


        <!-- The way out of full screen. The exit toggle lives in the header,
             which full screen collapses, so without this the only exit is a key
             nothing tells you about.

             BOTTOM on a phone, and the top corner is why: that corner is where
             any chrome the lift has not taken lands, and the canvas fills the
             window under it, so a control there is drawn over whatever is still
             stuck up there. The bottom corner is the canvas and nothing else --
             the hint sits opposite it. `Esc` goes with the same move: a phone
             has no key to name, and a cap saying so is an instruction a reader
             cannot follow. A full-screen mode leaves a visible way out: ADR 0243. -->
        <Button
          v-if="ui.fullscreen"
          class="escape absolute right-3 z-overlay bg-surface-overlay"
          :class="isPhone ? 'bottom-3' : 'top-3'"
          variant="outline"
          @click="ui.exitFullscreen()"
        >
          <Kbd v-if="!isPhone">Esc</Kbd> exit full screen
        </Button>
      </main>

      <ListRail
        side="right"
        wide
        label="detail"
        :collapsed="!ui.graphRailRight"
        @toggle="ui.toggleGraphRail('right')"
      >
        <div class="detail p-3">
          <Panel v-if="graph.provenance" title="Provenance" :padded="true">
            <EdgeProvenancePanel :provenance="graph.provenance" />
          </Panel>

          <template v-else-if="graph.selectedNode">
            <!-- Node data first, card when it arrives: everything in the node
                 payload is already resident, so showing a spinner over it would
                 be a downgrade. -->
            <EntityCardBody
              v-if="selectedCard"
              :card="selectedCard"
              :mention-count="graph.selectedNode.mention_count"
              :graph-width="280"
              :title-href="router.resolve(toEntityRoute(selectedCard.id)).href"
              @open="openSelectedName($event, selectedCard!.id)"
              @select="selectFromMiniGraph"
            />
            <Panel v-else :title="graph.selectedNode.type || 'Name'">
              <h3 class="node-name text-md font-semibold mb-2">{{ graph.selectedNode.name }}</h3>
              <p v-if="graph.selectedNode.summary" class="text-text-secondary">
                {{ graph.selectedNode.summary }}
              </p>
              <p class="text-text-tertiary text-xs mono">{{ graph.selectedNode.id }}</p>
              <p class="text-text-tertiary text-xs">
                {{ graph.selectedNode.mention_count || 0 }} mentions
              </p>
              <!-- The same sentence `EntityCardBody` renders once the card
                   arrives. Said here too because this panel is what the rail
                   shows in the meantime, and a qualifier that appears a beat
                   later reads as something that just became true. -->
              <p v-if="graph.selectedNode.status === 'provisional'" class="text-text-tertiary text-xs">
                Only one source has named this so far.
              </p>
            </Panel>

            <!-- Everything the rail cannot hold: every connection rather than
                 ten, every article rather than four, and the briefs that named
                 it. The canvas keeps owning SELECTION; this is the way out of
                 the rail's column.

                 A GLYPH, matching the hover card's footer, where the same
                 action is one of a row of square controls. A worded button here
                 was the only one of its kind on the surface and read as the
                 rail's primary act rather than as its way out.

                 `as="span"`: this is the nesting case IconButton's `as` exists
                 for -- a real button element inside a `RouterLink` is invalid
                 HTML, and the header's user guide link already does it this
                 way. (Spelled in words because `lint_tokens.py` greps template
                 source for the tag and would read the explanation as the
                 offence.) `inline-block` so the margin applies at all; a link
                 is inline by default and would swallow it. -->
            <RouterLink
              :to="toEntityRoute(graph.selectedNode.id)"
              class="mt-4 inline-block"
            >
              <IconButton as="span" label="Open this name" bordered>
                <Icon name="external" :size="12" />
              </IconButton>
            </RouterLink>

            <Button
              v-if="graph.mode === 'brief'"
              variant="text"
              icon="external"
              @click="exploreBeyondBrief(graph.selectedNode.id)"
            >
              explore beyond the brief
            </Button>

          </template>

          <EmptyState v-else title="Nothing selected">
            Click a node to expand its neighbours, or an edge to see every
            article behind it.
          </EmptyState>
        </div>
      </ListRail>
    </div>
  </ViewShell>
</template>

<style scoped>
/* The ONE rule that cannot be a utility, which is what this file is
   allowlisted for.

   Rail widths are CSS custom properties set inline, so collapsing is a track
   resize -- never a `v-if`. Unmounting anything above this view would take
   Cytoscape with it and pressing `[` would wipe the exploration. Tailwind can
   express the grid, but not a transition over a `grid-template-columns` whose
   tracks are two arbitrary custom properties. The rails yield and the canvas
   does not; `--rail-canvas-min` in `tokens.css` carries why.

   Nothing else belongs here: canvas positioning, overlay chrome, readout,
   coverage note, escape hatch and detail pane are ordinary component styling. */
.grid {
  display: grid;
  grid-template-columns: minmax(0, var(--left)) minmax(var(--rail-canvas-min), 1fr) minmax(0, var(--right));
  height: 100%;
  min-height: 0;
  transition: grid-template-columns var(--motion-base) var(--motion-ease);
}
</style>
