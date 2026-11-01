<script setup lang="ts">
/**
 * Deliberate practice over the corpus (docs/adr/0157). Three lenses on one
 * learner's state — the session, the progress read, the memory browser — as
 * sections of one view, the CurationView shape: they change WHAT YOU SEE, so
 * they are controls.
 *
 * Everything shown here is the signed-in reader's own; the server scopes
 * every row to the actor and this view never has anyone else's numbers to
 * leak.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ProgressPanel from '@/components/learn/ProgressPanel.vue'
import StudySession from '@/components/learn/StudySession.vue'
import EmptyCorpus from '@/components/shared/EmptyCorpus.vue'
import Chip from '@/components/ui/Chip.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import SegmentedControl from '@/components/ui/SegmentedControl.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { useCorpusEmpty } from '@/composables/useCorpusEmpty'
import { memoryLabel } from '@/lib/learnCopy'
import { useLearnStore } from '@/stores/learn'
import type { OverviewCard } from '@/types/learning'

const SECTIONS = [
  { value: 'study', label: 'study' },
  { value: 'progress', label: 'progress' },
  // The value stays `cards` so deep links and the smoke pass keep working;
  // the reader-facing word is what changed.
  { value: 'cards', label: 'terms', hint: 'what needs another look, and what you know well' },
] as const
type Section = (typeof SECTIONS)[number]['value']

const learn = useLearnStore()
const router = useRouter()
const route = useRoute()
const { filling } = useCorpusEmpty()

// Deep-linkable (`/learn?section=progress`): shareable, and the only way the
// phone smoke pass can reach the non-default sections at all.
function sectionFrom(value: unknown): Section {
  return SECTIONS.some((s) => s.value === value) ? (value as Section) : 'study'
}
const section = ref<Section>(sectionFrom(route.query.section))

onMounted(() => void learn.loadSession())
watch(
  section,
  (next) => {
    if (next === 'progress') void learn.loadProgress()
    if (next === 'cards') void learn.loadOverview()
    const query = next === 'study' ? {} : { section: next }
    void router.replace({ query })
  },
  { immediate: true },
)

const identity = computed(() =>
  learn.sessionLoaded && !learn.loading ? `learn · ${learn.dueCount} due` : 'learn',
)

// ── the memory browser ─────────────────────────────────────────────────

/** Relative, coarse: a schedule is read in days, not timestamps. */
function dueLabel(due: string): string {
  const days = Math.round((Date.parse(due) - Date.now()) / 86_400_000)
  if (Number.isNaN(days)) return '—'
  if (days <= 0) return 'back now'
  return days < 14 ? `back in ${days}d` : `back in ${Math.round(days / 7)}w`
}

function openInGraph(row: OverviewCard) {
  if (row.entity_id) {
    void router.push({ path: '/graph', query: { mode: 'focus', focus: row.entity_id } })
  }
}
</script>

<template>
  <ViewShell
    :width="section === 'study' ? 'prose' : 'wide'"
    scroll="page"
    :identity="identity"
    :loading="learn.loading"
    :error="learn.error"
  >
    <template #controls>
      <SegmentedControl v-model="section" :options="SECTIONS" ariaLabel="learn section" />
    </template>

    <EmptyCorpus v-if="filling" />

    <StudySession v-else-if="section === 'study'" />

    <ProgressPanel v-else-if="section === 'progress'" />

    <div v-else class="flex flex-col gap-6">
      <StatusNote v-if="learn.overviewError" kind="error">{{ learn.overviewError }}</StatusNote>

      <EmptyState v-if="!learn.overview || learn.overview.total === 0" title="no terms yet">
        Grade cards in a study session and they gather here: what needs
        another look, what comes back next, and what you know well.
      </EmptyState>

      <template v-else>
        <div v-if="learn.overview.attention.length">
          <SectionHead label="worth another look" rule />
          <p class="mt-2 text-xs text-text-tertiary">
            These haven't stuck yet. They'll come round again soon, and a
            quick look now makes the next time easier.
          </p>
          <ul class="mt-2 flex flex-col">
            <li
              v-for="row in learn.overview.attention"
              :key="row.card_key"
              class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 border-b border-border-subtle py-2"
            >
              <div class="min-w-0">
                <RouterLink
                  v-if="row.entity_id"
                  :to="{ path: '/graph', query: { mode: 'focus', focus: row.entity_id } }"
                  class="text-sm text-text-primary hover:underline"
                >
                  {{ row.term }}
                </RouterLink>
                <span v-else class="text-sm text-text-primary">{{
                  row.term === row.card_key ? "no longer in Venona's reading" : row.term
                }}</span>
                <p class="truncate text-xs text-text-secondary">{{ row.detail ?? '' }}</p>
              </div>
              <span class="text-xs text-text-quiet">
                {{ memoryLabel(row.stability).word }} · {{ dueLabel(row.due) }}
              </span>
            </li>
          </ul>
        </div>

        <div v-if="learn.overview.upcoming.length">
          <SectionHead label="coming back next" rule />
          <ul class="mt-2 flex flex-col">
            <li
              v-for="row in learn.overview.upcoming"
              :key="row.card_key"
              class="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 border-b border-border-subtle py-2"
            >
              <div class="min-w-0">
                <span class="text-sm text-text-primary">{{ row.term }}</span>
                <p class="truncate text-xs text-text-secondary">{{ row.detail ?? '' }}</p>
              </div>
              <span class="text-xs text-text-quiet">{{ dueLabel(row.due) }}</span>
            </li>
          </ul>
        </div>

        <div v-if="learn.overview.known.length">
          <SectionHead label="known well" rule />
          <p class="mt-2 text-xs text-text-tertiary">
            Held strongly. Hover for the meaning, click to see one in the
            graph.
          </p>
          <ul class="mt-2 flex flex-wrap gap-2">
            <li v-for="row in learn.overview.known" :key="row.card_key">
              <Chip
                size="sm"
                :interactive="!!row.entity_id"
                :title="row.detail ?? undefined"
                @click="openInGraph(row)"
              >
                {{ row.term }}
              </Chip>
            </li>
          </ul>
        </div>

        <p class="text-2xs text-text-quiet">
          {{ learn.overview.total }} terms in your deck · every successful
          recall strengthens a memory, and strong ones return less often
        </p>
      </template>
    </div>
  </ViewShell>
</template>
