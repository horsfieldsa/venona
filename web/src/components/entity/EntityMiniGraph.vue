<script setup lang="ts">
import { computed, ref } from 'vue'

import type { MiniGraph, MiniGraphNeighbor } from '@/types/brief'
import { entityColourVar } from '@/lib/entityColour'
import { predicateLabel } from '@/lib/learnCopy'

/**
 * Neighbour preview, rendered as inline SVG.
 *
 * Deliberately NOT a graph library: no force simulation, no rAF loop, no
 * Cytoscape on the reading surface. The server sends neighbours already sorted
 * by weight; this places them at deterministic angles, so the same entity
 * always draws the same picture and nothing jitters between renders. Costs
 * about a millisecond.
 *
 * NO TEXT IN THE PICTURE. At 308x150 there is room for ten nodes or for ten
 * labels, never both: only the top five were ever labelled, those were cut at
 * sixteen characters, and they collided with each other and with the nodes. The
 * names live in the readout below instead, one at a time, in full.
 *
 * That readout replaces a native SVG `<title>`, which is the browser's own
 * tooltip: it waits about a second, cannot be styled, and vanishes if the
 * pointer drifts. Hover state here is one `ref` set from `pointerenter`, so it
 * lands on the same frame as the pointer.
 *
 * Colours come from `var(--entity-${prefix})`, so the mini-graph themes with
 * everything else and there is no parallel JS colour table.
 */
const props = defineProps<{
  graph: MiniGraph
  width?: number
  height?: number
}>()

/** The component does not navigate. The right destination differs by caller --
 *  the hover card goes to the graph route, the graph's own detail rail selects
 *  and expands in place -- so it says what was chosen and lets them decide. */
const emit = defineEmits<{ (e: 'select', id: string): void }>()

const W = computed(() => props.width ?? 300)
const H = computed(() => props.height ?? 150)
const CX = computed(() => W.value / 2)
const CY = computed(() => H.value / 2)

/* The node radius plus a hair, with no reserve for label text -- there are no
   labels. That puts the nodes as far apart as the box allows, which is what
   stops them overlapping and makes them worth pointing at. */
const RX = computed(() => W.value / 2 - 14)
const RY = computed(() => H.value / 2 - 14)


const centerColor = computed(() => entityColourVar({ id: props.graph.center, kind: 'entity' }))

const placed = computed(() => {
  const neighbors = (props.graph.neighbors ?? []).slice(0, 10)
  const n = neighbors.length
  if (n === 0) return []

  const weights = neighbors.map((x) => x.weight)
  const max = Math.max(...weights, 1)
  const min = Math.min(...weights, 0)

  return neighbors.map((neighbor, i) => {
    // Start at the top and go clockwise, strongest first: the most important
    // neighbour is always at 12 o'clock.
    const angle = -Math.PI / 2 + (i * 2 * Math.PI) / n
    const x = CX.value + RX.value * Math.cos(angle)
    const y = CY.value + RY.value * Math.sin(angle)
    const norm = max === min ? 1 : (neighbor.weight - min) / (max - min)

    return {
      ...neighbor,
      x,
      y,
      color: entityColourVar({ id: neighbor.id, kind: 'entity' }),
      stroke: 0.8 + norm * 1.6,
      // Generous, because there are no labels competing for the space and
      // weight should be readable as size rather than only as the thickness of
      // a line.
      radius: 4 + norm * 3,
    }
  })
})

// ── what the pointer, or the keyboard, is on ─────────────────────────

const activeId = ref<string | null>(null)

const active = computed<MiniGraphNeighbor | null>(
  () => placed.value.find((node) => node.id === activeId.value) ?? null,
)

/** Full strength for the node under the pointer and its edge, and about a
 *  third for the rest. OPACITY ONLY: docs/DESIGN.md allows it for hover and
 *  forbids anything that moves under the cursor, so nothing here resizes or
 *  shifts. */
function dimmed(id: string): boolean {
  return activeId.value !== null && activeId.value !== id
}

/** Direction relative to the CENTRE, which the arrowless edges never showed. */
function arrow(node: MiniGraphNeighbor): string {
  return node.dir === 'out' ? '→' : '←'
}

function sources(node: MiniGraphNeighbor): string {
  return `${node.weight} source${node.weight === 1 ? '' : 's'}`
}

/** The accessible name, and deliberately NOT a `<title>` -- that element is
 *  what produces the native tooltip this component exists to replace. */
function label(node: MiniGraphNeighbor): string {
  return `${node.name}, ${predicateLabel(node.predicate)} ${node.dir === 'out' ? 'to' : 'from'} this name, ${sources(node)}`
}
</script>

<template>
  <div class="minigraph flex flex-col gap-1 min-w-0">
    <!-- `group`, not `img`. `role="img"` makes the whole subtree
         presentational, which would hide the focusable nodes inside it from
         assistive tech and silently undo the keyboard route below. -->
    <svg
      class="mini block max-w-full"
      :viewBox="`0 0 ${W} ${H}`"
      :width="W"
      :height="H"
      role="group"
      aria-label="Related names"
    >
      <g class="edges [&_line]:stroke-edge-confirmed">
        <line
          v-for="node in placed"
          :key="`e-${node.id}`"
          :x1="CX"
          :y1="CY"
          :x2="node.x"
          :y2="node.y"
          :stroke-width="node.stroke"
          :stroke-dasharray="node.weight <= 1 ? '2 2' : undefined"
          :opacity="dimmed(node.id) ? 0.15 : 0.55"
          class="transition-opacity duration-fast ease-standard"
        />
      </g>

      <g class="nodes">
        <!-- One group per node: it is the hover target, the focus target and
             the click target at once, so the three can never disagree about
             which node the reader means. -->
        <g
          v-for="node in placed"
          :key="`n-${node.id}`"
          class="node cursor-pointer focus:outline-none"
          role="button"
          tabindex="0"
          :aria-label="label(node)"
          @pointerenter="activeId = node.id"
          @pointerleave="activeId = null"
          @focus="activeId = node.id"
          @blur="activeId = null"
          @click="emit('select', node.id)"
          @keydown.enter.prevent="emit('select', node.id)"
          @keydown.space.prevent="emit('select', node.id)"
        >
          <circle
            class="dot transition-opacity duration-fast ease-standard"
            :cx="node.x"
            :cy="node.y"
            :r="node.radius"
            :fill="node.color"
            :opacity="dimmed(node.id) ? 0.35 : 1"
          />
          <!-- A 4-7px dot is not a pointer target. This one is transparent, so
               it changes nothing on screen and everything about whether the
               node can be reached. -->
          <circle class="hit" :cx="node.x" :cy="node.y" r="13" fill="transparent" />
        </g>
      </g>

      <circle
        class="center stroke-surface-overlay [stroke-width:2]"
        :cx="CX"
        :cy="CY"
        r="6"
        :fill="centerColor"
      />

      <text
        v-if="graph.more > 0"
        class="more font-mono text-(length:--label-micro) fill-text-quiet"
        :x="W - 4"
        :y="H - 4"
        text-anchor="end"
      >
        +{{ graph.more }}
      </text>
    </svg>

    <!-- The readout. A FIXED LINE, not a floating tooltip: this sits inside a
         340px popover whose scroll container clips absolutely-positioned
         children, and it needs no placement maths to get right. `min-h` holds
         two lines so the card does not reflow as the pointer moves between
         nodes.

         At rest it names the centre, which with the labels gone is otherwise
         the one node on screen with no way to identify it. -->
    <!-- `min-h-6` is 36px on this scale: one --text-xs line plus one --text-2xs
         line, which is the tallest the readout ever gets. Reserved so the card
         does not reflow as the pointer crosses between nodes. -->
    <p class="readout min-h-6 text-xs leading-snug min-w-0" aria-live="polite">
      <template v-if="active">
        <span class="block text-text-secondary [overflow-wrap:anywhere]">{{ active.name }}</span>
        <span class="block mono text-2xs text-text-quiet">
          {{ predicateLabel(active.predicate) }} {{ arrow(active) }} · {{ sources(active) }}
        </span>
      </template>
      <template v-else>
        <span class="block mono text-2xs text-text-quiet">
          {{ placed.length + graph.more }} connection{{
            placed.length + graph.more === 1 ? '' : 's'
          }}<template v-if="graph.more"> · {{ graph.more }} not drawn</template>
        </span>
      </template>
    </p>
  </div>
</template>
