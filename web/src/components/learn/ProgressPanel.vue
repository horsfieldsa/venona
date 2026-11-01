<script setup lang="ts">
/**
 * How the learner is doing, in the house's instrument grammar: a StatBlock
 * KPI row, a WorkBars-shaped daily chart, and a coverage bar per the
 * ontology's own types. Everything shown is the signed-in reader's own.
 */
import { computed, onMounted } from 'vue'

import ReviewBars from '@/components/learn/ReviewBars.vue'
import TypeBadge from '@/components/learn/TypeBadge.vue'
import StatBlock from '@/components/shared/StatBlock.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import PaneState from '@/components/ui/PaneState.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import { useGraphStore } from '@/stores/graph'
import { useLearnStore } from '@/stores/learn'

const learn = useLearnStore()
const graph = useGraphStore()

onMounted(() => {
  void learn.loadProgress()
  // The "ready now" half of today's target; COUNTs only, so this is cheap.
  void learn.loadCounts(true)
  // The ontology maps a coverage row's type name to its colour prefix; App
  // already loads this at boot, so this is a cache hit everywhere but a
  // hard refresh straight onto this tab.
  void graph.loadOntology()
})

const progress = computed(() => learn.progress)

// "The goal" is clearing what is ready today: due reviews plus the day's
// new cards. A fixed target confused everyone whose day offered fewer
// cards than the number (a fresh learner's day one offers ten).
const readyStill = computed(() => (learn.counts?.due ?? 0) + (learn.counts?.new_available ?? 0))
const dayTarget = computed(() => (progress.value?.reviewed_today ?? 0) + readyStill.value)
const dayCleared = computed(
  () => (progress.value?.reviewed_today ?? 0) > 0 && readyStill.value === 0,
)

/** `--entity-<prefix>` for an ontology type name; the pseudo-types (gloss)
 *  take the glossary colour. A static fallback, never an invented hue. */
function colourFor(typeName: string): string {
  if (typeName === 'gloss') return 'var(--entity-glossary)'
  const spec = graph.ontology?.entity_types?.find((t) => t.name === typeName)
  return spec?.prefix ? `var(--entity-${spec.prefix})` : 'var(--accent-muted)'
}

const totals = computed(() => {
  const rows = progress.value?.coverage ?? []
  return {
    started: rows.reduce((n, r) => n + r.started, 0),
    total: rows.reduce((n, r) => n + r.total, 0),
  }
})
</script>

<template>
  <PaneState :loading="!progress && !learn.progressError" :error="learn.progressError">
    <div v-if="progress" class="flex flex-col gap-6">
      <!-- StatBlock labels are nowrap by design, so every label here must be
           short enough for a half-width phone column -- a long one walks
           into its neighbour rather than wrapping. The daily-goal fraction
           is the "reviewed" tile's value, not a clause in the streak's
           label, for exactly that reason. -->
      <div class="grid grid-cols-4 gap-4 max-sm:grid-cols-2">
        <StatBlock
          :value="progress.streak"
          label="day streak"
          size="lg"
          :tone="dayCleared ? 'ok' : 'neutral'"
        />
        <StatBlock :value="readyStill" label="ready now" />
        <StatBlock
          :value="dayTarget ? `${progress.reviewed_today}/${dayTarget}` : `${progress.reviewed_today}`"
          label="reviewed today"
          :tone="dayCleared ? 'ok' : 'neutral'"
        />
        <StatBlock
          :value="totals.total ? `${totals.started}/${totals.total}` : '—'"
          label="terms studied"
        />
      </div>

      <div>
        <SectionHead label="reviews" rule />
        <div class="mt-3">
          <ReviewBars :days="progress.series" :span="Math.min(progress.series_days, 90)" />
        </div>
      </div>

      <div v-if="progress.coverage.length">
        <SectionHead label="coverage" rule />
        <!-- The thing being worked toward, stated once: the whole set of
             terms the corpus can teach, and how much of it is held. -->
        <p class="mt-2 text-xs text-text-tertiary">
          {{ totals.total }} terms to learn so far, with more arriving as
          Venona reads. Studied means you've seen it in a session; strong
          means it's sticking, and you'd still know it weeks from now.
        </p>
        <!-- The column floor must clear the badge's 92px drawing or the
             rings overlap instead of wrapping: --field-md is the smallest
             field token that does (7rem), and 390px still fits three. -->
        <ul class="mt-4 grid grid-cols-[repeat(auto-fill,minmax(var(--field-md),1fr))] gap-x-3 gap-y-5">
          <li v-for="row in progress.coverage" :key="row.type" class="flex justify-center">
            <TypeBadge :row="row" :colour="colourFor(row.type)" />
          </li>
        </ul>
      </div>

      <EmptyState v-else title="nothing to measure yet">
        Coverage appears once Venona has defined some terms.
      </EmptyState>
    </div>
  </PaneState>
</template>
