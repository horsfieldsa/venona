<script setup lang="ts">
/**
 * The entity card, as pure presentation. ONE skeleton, three fillings.
 *
 *   TYPE          what kind of thing this is
 *   DEFINITION    what it is, and how we know
 *   SOURCES       the articles behind it
 *   CONNECTIONS   what it is attached to
 *
 * Every card kind renders those four in that order, including the ones that
 * cannot fill a section -- an empty section states its absence rather than
 * disappearing, so the shape of the card never depends on the data. Before
 * this the three kinds were three different documents: an entity led with a
 * summary that was always blank (nothing has ever written `entities.summary`),
 * a glossary card led with a definition and had no graph, and an undefined card
 * was a paragraph of apology. A reader learned each one separately.
 *
 * Extracted from `EntityHoverCard` so it can be rendered more than once on a
 * page. The hover card is a teleported singleton with hand-rolled viewport
 * positioning and a ResizeObserver -- deliberately, because a dense brief
 * carries hundreds of mentions and one instance is the point. None of that has
 * anything to do with *drawing* an entity, and the merge queue needs two side
 * by side.
 *
 * `compact` drops connections and trims prose for side-by-side use, where the
 * question is "are these the same thing?" rather than "what is this?" -- and
 * two full cards will not fit on screen together.
 */
import { computed } from 'vue'

import EntityDefinition from '@/components/entity/EntityDefinition.vue'
import EntityMiniGraph from '@/components/entity/EntityMiniGraph.vue'
import Chip from '@/components/ui/Chip.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import { entityColourVar } from '@/lib/entityColour'
import { entityTypeLabel } from '@/lib/entityType'
import type { Card } from '@/types/brief'
import { dayLabel } from '@/lib/dates'
import { safeHref } from '@/lib/safeHref'

const props = withDefaults(
  defineProps<{
    card: Card
    compact?: boolean
    graphWidth?: number
    /** Only the graph knows this -- it comes from the Neo4j node payload, not
     *  from the card. Passed through so the graph's detail rail does not lose a
     *  number it was already showing when it swapped to this component. */
    mentionCount?: number | null
    /**
     * The name's own page, as an `href` STRING rather than a route location.
     *
     * A string because this component is mounted into the extension's shadow
     * root, which has no router: importing `RouterLink` here would pull
     * vue-router into a content bundle that today contains none of it, to
     * render a component that would throw in the one place the import was for.
     * So the caller resolves the address and this draws a plain link, and an
     * in-app caller keeps SPA navigation by handling `open` below.
     *
     * Absent means the title is not a link, which is right for the merge queue
     * and the study card: navigating away mid-decision loses the decision.
     */
    titleHref?: string
    /** The extension's card opens Venona in a new tab; the app's does not. */
    titleNewTab?: boolean
  }>(),
  { compact: false, graphWidth: 368, mentionCount: null, titleHref: '', titleNewTab: false },
)

/** Forwarded, not handled. The card body draws; where a neighbour leads is the
 *  caller's decision, and it differs -- see EntityMiniGraph. */
const emit = defineEmits<{
  (e: 'select', id: string): void
  /** The title was clicked with an unmodified left button. An in-app caller
   *  calls `preventDefault()` and pushes, so the link navigates without a
   *  reload; a caller that does nothing leaves the browser to follow the href,
   *  which is what the extension wants. */
  (e: 'open', event: MouseEvent): void
}>()

/**
 * A modified click is the BROWSER'S, always.
 *
 * Cmd/Ctrl/Shift/middle-click are how a reader opens a name in a second tab,
 * which is most of the point of the name having an address at all. Swallowing
 * them into a router push is the bug every hand-rolled link ships with, so the
 * emit fires only for the plain case and the href does the rest.
 */
function onTitle(event: MouseEvent) {
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return
  emit('open', event)
}

const isEntity = computed(() => props.card.kind === 'entity')

/** The title line. An entity has a name; a glossary or undefined card has the
 *  surface as it appeared in the prose. */
const title = computed(() => props.card.name || props.card.surface || props.card.id)

/** The type indicator, present on every kind so the eyebrow never moves. */
const typeLabel = computed(() => {
  // `name`, not "entity": the eyebrow is read by a person, and an entity is
  // this repo's word for the thing they would call a name.
  if (isEntity.value) return entityTypeLabel(props.card.type) || 'name'
  return props.card.kind === 'glossary' ? 'acronym' : 'undefined'
})

/** The hue for this card, on the eyebrow here and on the shell's border in both
 *  wrappers. See `lib/entityColour.ts` for why this is not an inline
 *  `var(--entity-${prefix})` -- `gloss:` does not name its own token. */
const colour = computed(() => entityColourVar(props.card))

/**
 * Aliases MINUS the name already at the top of the card.
 *
 * The resolver's alias set is what it matches on, so it contains the canonical
 * name by construction -- and the card was printing "Office of Naval Research"
 * as the first of its own aliases, directly under the title reading "Office of
 * Naval Research". The genuine variants (O2NR, OONR, ONR) are the information;
 * the repeat was noise sitting in front of them.
 */
const aliases = computed(() => {
  const name = title.value.trim().toLowerCase()
  return (props.card.aliases ?? []).filter((alias) => alias.trim().toLowerCase() !== name)
})

const sources = computed(() => {
  if (props.card.definition_sources?.length) return props.card.definition_sources
  return props.card.signals ?? []
})

/**
 * Three, then a count.
 *
 * Sources were the bulk of the card: 777px of content in a 479px window, four
 * full headlines of it, so connections and the actions sat below a fold nobody
 * scrolled to. Three is enough to see WHAT kind of coverage this has and to
 * click the one you recognise; the rest is a number, and the graph is where
 * exhaustive lives.
 */
const SOURCE_LIMIT = 3
const shownSources = computed(() => sources.value.slice(0, SOURCE_LIMIT))
const moreSources = computed(() => Math.max(0, sources.value.length - SOURCE_LIMIT))
</script>

<template>
  <div class="body flex flex-col gap-3 min-w-0">
    <header class="head flex flex-col gap-(--space-half)">
      <!-- The type is an EYEBROW, not a badge beside the name.
           As a `Chip` it shared the title's row, and `Chip` is
           `whitespace-nowrap` while the title carries `overflow-wrap:anywhere`
           -- so the title was always the thing that broke. "US Department of
           Defense" wrapped to two lines to make room for the word
           "Organization". Above the title it costs one 11px row, the name gets
           the full width, and the hue lands on the label that names the type
           rather than on a box around it. -->
      <!-- The status badge is NOT here. It qualifies the whole card rather than
           any field in it, so in the merge queue it is rendered by
           `MergeReview` on the column's own top row, level with the
           survivor/merges-away tag -- the actual top-right corner of the box.
           Inside this component it could only ever sit below that row. -->
      <p class="type text-2xs font-medium tracking-label uppercase" :style="{ color: colour }">
        {{ typeLabel }}
      </p>
      <!-- The link sits INSIDE the heading rather than replacing it: the card
           still has a heading for a screen reader, and the anchor is the part
           that is actionable. -->
      <h3
        class="name font-ui text-md font-semibold leading-tight text-text-primary m-0 [overflow-wrap:anywhere]"
      >
        <component
          :is="titleHref ? 'a' : 'span'"
          v-bind="
            titleHref
              ? {
                  href: titleHref,
                  ...(titleNewTab ? { target: '_blank', rel: 'noopener noreferrer' } : {}),
                }
              : {}
          "
          class="text-text-primary"
          @click="titleHref ? onTitle($event) : undefined"
        >
          {{ title }}
        </component>
      </h3>
      <span v-if="card.expansion && card.expansion !== card.name" class="text-text-tertiary text-xs mono">
        {{ card.expansion }}
      </span>

      <!-- No canonical id. It is derived from the name directly above it
           (`U.S. Marine Corps (USMC)` -> `unit:u-s-marine-corps-usmc`), so a
           full-width row for it tells a curator nothing they cannot already
           read, and costs a line in the region where the evidence goes. A
           surface that genuinely needs one copyable renders it itself, as the
           graph rail does. -->
    </header>

    <!-- ── definition ─────────────────────────────────────────────── -->
    <!-- Shared with the entity page, which is why it is a component and not
         markup: `in_corpus` is a SYNTHESIS and must never render as a
         quotation, and one renderer is what keeps that true on both surfaces.
         The sources section directly below is what its "below" wording
         promises. -->
    <EntityDefinition :card="card" :compact="compact" />

    <!-- Aliases are the single most useful signal when comparing two entities:
         they are what the resolver actually matched on. -->
    <section v-if="aliases.length" class="section flex flex-col gap-1 min-w-0">
      <SectionHead as="h4" label="also known as" />
      <div class="flex items-center flex-wrap gap-1">
        <Chip v-for="alias in aliases" :key="alias" size="sm">{{ alias }}</Chip>
      </div>
    </section>

    <!-- ── sources ────────────────────────────────────────────────── -->
    <section class="section sources flex flex-col gap-1 min-w-0">
      <SectionHead as="h4" label="sources" />
      <!-- A fact about the EVIDENCE, so it belongs here rather than beside the
           name: the header comment above records why a whole-card qualifier
           does not go up there. The graph draws the same fact as a hollow node
           and the rail says it in the same words, so the three surfaces cannot
           describe one state three ways (ADR 0247). -->
      <p v-if="card.status === 'provisional'" class="text-text-tertiary text-xs">
        Only one source has named this so far.
      </p>
      <!-- STACKED, not three columns on one line. Date, publisher and title
           side by side left the title about 150px of a 340px card, so every
           headline truncated to nothing while the two metadata columns -- the
           parts you do not read -- kept their width. The title now gets the
           full line and the metadata sits under it, quiet and mono. -->
      <!-- `component :is` rather than an href that may be absent: an `<a>` with
           no href still looks and tabs like a link and goes nowhere, so an
           article whose URL did not survive the scheme check renders as the
           text it is. -->
      <component
        :is="safeHref(signal.url) ? 'a' : 'div'"
        v-for="signal in shownSources"
        :key="signal.article_id"
        class="signal group flex flex-col gap-(--space-half) no-underline min-w-0 py-1 border-b border-b-border-subtle last:border-b-0 hover:no-underline"
        :href="safeHref(signal.url)"
        :target="safeHref(signal.url) ? '_blank' : undefined"
        :rel="safeHref(signal.url) ? 'noopener noreferrer' : undefined"
      >
        <span
          class="signal__title text-sm leading-snug text-text-secondary group-hover:text-text-primary line-clamp-2"
          >{{ signal.title }}</span
        >
        <span class="signal__meta mono text-2xs text-text-quiet">
          <template v-if="signal.published_at">{{ dayLabel(signal.published_at) }}</template>
          <template v-if="signal.published_at && signal.publisher"> · </template>
          {{ signal.publisher }}
        </span>
      </component>
      <!-- Stated, not silently dropped. A list that stops at three without
           saying so reads as the whole of the evidence, and the number is the
           part that says how well corroborated this is. -->
      <p v-if="moreSources" class="m-0 mono text-2xs text-text-quiet">+{{ moreSources }} more</p>
      <!-- A web-grounded definition's source is a page, not an ingested
           article, so it has no row above. -->
      <a
        v-if="!sources.length && safeHref(card.grounding?.url)"
        class="text-sm text-text-secondary no-underline hover:text-text-primary truncate"
        :href="safeHref(card.grounding?.url)"
        target="_blank"
        rel="noopener noreferrer"
        >{{ card.grounding?.article_title || card.grounding?.url }}</a
      >
      <p v-else-if="!sources.length" class="m-0 text-sm text-text-tertiary">
        Nothing Venona has read mentions this yet.
      </p>
    </section>

    <!-- ── connections ────────────────────────────────────────────── -->
    <section v-if="!compact" class="section connections flex flex-col gap-1 min-w-0">
      <SectionHead as="h4" label="connections" />
      <EntityMiniGraph
        v-if="card.mini_graph?.neighbors?.length"
        :graph="card.mini_graph"
        :width="graphWidth"
        :height="150"
        @select="emit('select', $event)"
      />
      <!-- Stated, not omitted: the shape of the card must not depend on the
           data, or the reader relearns it per kind. -->
      <p v-else-if="!isEntity" class="m-0 text-sm text-text-tertiary">An acronym is a term, not a name in the graph.</p>
      <p v-else class="m-0 text-sm text-text-tertiary">No confirmed relationship touches this yet.</p>
    </section>

    <!-- Only the graph passes this; it comes from the Neo4j node payload rather
         than the card, and the graph's detail rail was already showing it
         before this component took the rail over. -->
    <p v-if="mentionCount != null" class="text-text-tertiary text-xs mono">{{ mentionCount }} mentions</p>
  </div>
</template>
