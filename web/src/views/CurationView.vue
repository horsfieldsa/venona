<script setup lang="ts">
/**
 * Curation: the review queues.
 *
 * They are the same job — read the evidence, make a call — so they share one
 * shell, one keyboard model and one place to look. Merges were CLI-only before
 * this; definitions had a screen that could only reject.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import DefinitionQueue from '@/components/curation/DefinitionQueue.vue'
import QuestionReviewQueue from '@/components/learn/QuestionReviewQueue.vue'
import ApiUnreachable from '@/components/shared/ApiUnreachable.vue'
import { useLearnQuestionsStore } from '@/stores/learnQuestions'
import MergeHistory from '@/components/curation/MergeHistory.vue'
import MergeReview from '@/components/curation/MergeReview.vue'
import Button from '@/components/ui/Button.vue'
import Panel from '@/components/ui/Panel.vue'
import SegmentedControl from '@/components/ui/SegmentedControl.vue'
import Spinner from '@/components/ui/Spinner.vue'
import EmptyCorpus from '@/components/shared/EmptyCorpus.vue'
import AdminRail from '@/components/nav/AdminRail.vue'
import RailLayout from '@/components/ui/RailLayout.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import Chip from '@/components/ui/Chip.vue'
import { KIND_HELP, KIND_TONE, kindLabel } from '@/lib/curation'
import { useCorpusEmpty } from '@/composables/useCorpusEmpty'
import { useServiceHealth } from '@/composables/useServiceHealth'
import { useCurationStore } from '@/stores/curation'
import { useKeyBindings } from '@/composables/useKeyboard'

type Tab = 'merges' | 'definitions' | 'questions' | 'history'

const curation = useCurationStore()
const { apiDown } = useServiceHealth()
const learnQuestions = useLearnQuestionsStore()
const { filling } = useCorpusEmpty()
const tab = ref<Tab>('merges')

/** The counts live in the LABEL, not beside it: a segment reading "merges 9" is
 *  one thing to look at, where a chip plus a count is two. `history` has no
 *  number because it is a record rather than a queue. */
const queues = computed(() => [
  { value: 'merges' as const, label: `merges ${curation.status?.open_conflicts ?? 0}` },
  { value: 'definitions' as const, label: `definitions ${curation.status?.pending_definitions ?? 0}` },
  // Proposed study questions (epic O): the same verdict grammar as the two
  // queues above -- a model proposal, a person's ruling.
  { value: 'questions' as const, label: `questions ${learnQuestions.total}` },
  { value: 'history' as const, label: 'history' },
])
/**
 * `[` and `]` move between the three queues, which were mouse-only.
 *
 * NOT DIGITS, and not `alt`+anything. Bare `1`-`9` are taken twice over inside
 * this very view -- `MergeReview` arms the nth predicate, `DefinitionQueue`
 * picks the nth reading -- and macOS composes `Option+[` into `"`, so an
 * alt-punctuation binding is dead on a Mac for the same reason `alt+1` was.
 * Brackets are unbound in both queue components, so the last-scope-wins rule
 * never has to arbitrate, and Graph already uses them to move between panes.
 *
 * CLAMPED, not wrapping: `]` on the last queue should do nothing, rather than
 * silently returning you to the first one you were trying to leave.
 */
const TABS = ['merges', 'definitions', 'questions', 'history'] as const

/**
 * `?queue=` makes a queue a link, the way `/learn?section=` does.
 *
 * `replace`, not `push`: moving between the queues is one sitting, and a
 * history entry per tab turns Back into an undo of your own attention. It is
 * also the only way the phone smoke pass can reach a queue that is not the
 * default one.
 */
const route = useRoute()
const router = useRouter()

const queue = computed({
  get: () => tab.value,
  set: (next: Tab) => {
    tab.value = next
    void router.replace({ query: next === 'merges' ? {} : { queue: next } })
  },
})

watch(
  () => route.query.queue,
  (value) => {
    const found = TABS.find((name) => name === value)
    tab.value = found ?? 'merges'
  },
  { immediate: true },
)

function stepTab(by: number) {
  const at = TABS.indexOf(tab.value)
  tab.value = TABS[Math.min(TABS.length - 1, Math.max(0, at + by))]
}

const kind = ref<string | null>(null)




const byKind = computed(() => curation.status?.by_kind ?? {})

/** `kind === null` means "every kind", and a segment needs a value to be, so
 *  the absence gets a name. Prefixed because a merge kind is a bare word and
 *  `@` cannot appear in one. */
const ALL_KINDS = '@all'
/* The count rides in `hint`, which `SegmentedControl` renders as the segment's
 * `title`, rather than in the label. Appended to six sentence-length labels it
 * was the difference between a row that fits its pane and one that scrolls
 * most of itself out of view, and "how many of this kind" is a question asked
 * once rather than read at a glance. */
const kindOptions = computed(() => [
  { value: ALL_KINDS, label: 'all' },
  ...Object.entries(byKind.value).map(([k, n]) => ({
    value: k,
    label: kindLabel(k),
    hint: `${n} waiting${KIND_HELP[k] ? `. ${KIND_HELP[k]}` : ''}`,
  })),
])

/**
 * The notice, as ONE sentence.
 *
 * Assembled here rather than as four sibling spans in the template, because
 * that is what made the row three lines tall: each fragment was its own flex
 * child competing for the same width, and the narrowest of them wrapped. A
 * single string cannot do that -- it truncates, and `bannerTitle` keeps the
 * untruncated version one hover away.
 *
 * `pendingRebuild` and `blocked` are genuinely different facts and the wording
 * says which: pending changes are queued work that WILL land on the next
 * projection; blocked relations are work that will NOT, until more articles
 * arrive. Merging them into one number was the original bug behind the question
 * "as I merge things I don't see the rebuild count increment as much as I
 * merge" -- it should not, because only corroborated entities are ever
 * projected.
 */
const bannerText = computed(() => {
  const pending = curation.pendingRebuild
  const parts = [`${pending} graph change${pending === 1 ? '' : 's'} pending`]
  if (curation.status?.run_active) parts.push('a run is active, so the graph rebuilds at its end')
  return parts.join(' \u00b7 ') + '.'
})

/** Worth having on the one occasion somebody asks "why?", not worth its own
 *  line -- 60px on every visit. */
const bannerTitle = computed(() =>
  curation.pendingRebuild
    ? bannerText.value
    : 'Rebuilding will not surface these. A relationship only reaches the graph once BOTH ' +
      'names have been confirmed by a second source, which happens as more articles come in.',
)

/** The ContextBar states this now, so the view carries no masthead: two
 *  headings for one page is worse than none. */
/** Only when the corpus itself has nothing -- an empty queue on a full corpus
 *  is the goal, and `MergeReview` says "Queue clear." for that. */
const isEmpty = computed(
  () => filling.value && !curation.status?.open_conflicts && !curation.status?.pending_definitions,
)

useKeyBindings([
  // Navigation, so holding is a legitimate way to move.
  //
  // GETTERS, and registered BELOW `isEmpty` so they can read it. An empty corpus
  // renders no tabs at all -- `#controls` is behind `v-if="!isEmpty"` -- and
  // `keyboardGroups` drops a binding whose label resolves to empty, so the `?`
  // overlay stops offering a queue there is no way to see. The keys stay bound
  // and stay harmless: they move a local ref nothing is rendering.
  {
    keys: ['['],
    label: () => (isEmpty.value ? '' : 'Previous queue'),
    group: 'curation',
    repeatable: true,
    run: () => stepTab(-1),
  },
  {
    keys: [']'],
    label: () => (isEmpty.value ? '' : 'Next queue'),
    group: 'curation',
    repeatable: true,
    run: () => stepTab(1),
  },
])

const identity = computed(() => `admin · curation · ${curation.status?.open_conflicts ?? 0} open`)

watch(kind, (next) => void curation.load(next))

onMounted(() => {
  // The questions tab's count, so the segment label is right before the tab
  // is ever opened. Same courtesy-tick argument as the status below.
  void learnQuestions.load()
  // In parallel: the queue never reads the status banner's answer, and
  // serializing them made time-to-queue the SUM of two round trips. The
  // status is also already polled app-wide (`useAppPolling`), so its refresh
  // here is a courtesy tick, not a dependency.
  void curation.refreshStatus()
  void curation.load(null)
})
</script>

<template>
  <ViewShell width="wide" :scroll="isEmpty ? 'page' : 'none'" :identity="identity">
    <!-- Three queues as tabs on one surface, not three destinations. They
         change WHAT YOU SEE, so they are controls; Rebuild changes the data, so
         it is the action. -->
    <!-- No queue tabs and no actions on an empty corpus: three tabs reading
         `merges 0 / definitions 0 / history` is chrome describing nothing. -->
    <template v-if="!isEmpty" #controls>
      <SegmentedControl
        v-model="queue"
        ariaLabel="curation queue"
        :options="queues"
        scrollable
      />
    </template>


    <RailLayout :fill="!isEmpty">
      <template #rail>
        <AdminRail current="curation" />
      </template>

      <!-- ONE ELEMENT in this slot, and this column is it: `RailLayout` wraps
           neither slot, so a notice and a queue placed here as siblings would
           become two grid columns beside the rail. Under `scroll="none"` the
           grid is the shell's direct last child and takes its
           `flex:1;min-height:0`; `fill` passes that to this column, and `grow`
           on the queue passes it to the `Panel` whose inner region scrolls. -->
      <div class="pane flex min-h-0 flex-col gap-3">
        <!-- A merge lands in SQLite instantly but reaches Neo4j only on the
             next projection. Saying so beats letting the graph quietly
             disagree with the screen the curator is working on.

             IN THE PANE, not `ViewShell`'s `#notice` slot. That slot is full
             width above everything, so a standing condition stood across the
             rail too and pushed the sidebar down the page. It belongs beside
             the queue it is about, in the column that holds it. `flex-none`
             so the queue below keeps the rest of the frame. -->
        <div v-if="curation.pendingRebuild" class="flex-none">
        <!-- ONE LINE. This wrapped to three: `flex-wrap` plus a `basis-full`
             paragraph forced the explanation onto its own row and the button onto
             another, so a standing condition — most of the queue is usually
             waiting on corroboration — occupied a third of the screen above the
             thing you came to read. The explanation is not deleted, it moves to
             the `title`: it is worth having on the one occasion someone asks
             "why?", and not worth 60px on every visit. -->
        <div class="banner flex items-center gap-3 border border-accent-muted rounded-md bg-surface-raised px-3 py-2">
          <!-- ONE sentence and ONE control, and that is the whole rule for this
               row. It carried four children — the count, "a run is active", the
               "rebuilding will not surface these" hint, and the button — which at
               this width squeezed the run note to 157px, wrapped it to three
               lines, and made a 73px block out of a standing condition. Merging
               them into one sentence is what makes a single line achievable
               rather than something enforced with `truncate` until it says
               nothing.

               `min-w-0 truncate` so the sentence shortens instead of wrapping if
               the window is narrow; the untruncated text is always in `title`. -->
          <span
            class="banner__text min-w-0 grow truncate text-sm text-text-secondary"
            :title="bannerTitle"
            >{{ bannerText }}</span
          >
          <Spinner v-if="curation.busy" :size="12" class="flex-none" />
          <!-- The only control. Its disabled label already says a run is going,
               which is why the separate "a run is active" note is gone rather than
               shortened: two elements saying the same thing is not information. -->
          <!-- The rebuild sits WITH the sentence that explains it. In the bar it
               was a third action on the definitions tab, beside two controls that
               spend; here it is the one thing to do about the one condition named
               to its left. -->
          <Button
            class="flex-none"
            icon="waypoints"
            label="Rebuild graph"
            :disabled="curation.busy || curation.status?.run_active"
            @click="curation.rebuild()"
          >
            Rebuild graph
          </Button>
        </div>
        </div>

        <!-- BEFORE the empty-corpus screen, which is the wrong answer here and
             the most confidently wrong one: every count this page reads is zero
             when the load failed, so an outage rendered as "nothing has been
             read yet" to a curator with a full queue. -->
        <ApiUnreachable v-if="apiDown" />

        <EmptyCorpus v-else-if="isEmpty" />

        <Panel v-else-if="tab === 'merges'" variant="bare" fill class="grow" :padded="true">
          <!-- Only when there is something to choose BETWEEN. With one kind in the
               queue this rendered `all` and that kind, which select the same rows;
               with none it rendered `all` on its own -- a lone button that visibly
               does nothing, which is how it was reported. Same rule as the graph's
               scope control: a radio group of one is not a choice. -->
          <!-- The pair's identity rides on the FILTER ROW, which already exists and
               was half empty. A row of its own inside `MergeReview` -- a badge, the
               surface, a sentence of help, the position counter -- is four things on
               a line whose only job is to name one pair, directly above the two
               cards that name it again. Folding it here gives the cards that line
               back.
               The help sentence is gone from the screen entirely and lives on the
               badge's `title`: it is the same sentence for every pair of a kind, so
               it is read once and then costs a line on all ninety. -->
          <template v-if="kindOptions.length > 2 || curation.current" #header>
            <!-- One width-bounded, wrapping row for BOTH header halves. Without
                 it the filter and the pair id sat side by side at their content
                 widths, and the min-width:auto chain carried that width up
                 through the panel into MAIN itself — the whole page scrolled
                 sideways because of one header. On a phone the pair id wraps
                 under the filter, and the filter (scrollable) finally has a
                 real width to scroll inside. -->
            <div class="flex w-full min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
              <SegmentedControl
                v-if="kindOptions.length > 2"
                :model-value="kind ?? ALL_KINDS"
                ariaLabel="merge kind"
                :options="kindOptions"
                scrollable
                @update:model-value="(v) => (kind = v === ALL_KINDS ? null : (v as string))"
              />
              <span v-else class="grow" />

              <div v-if="curation.current" class="pairid ml-auto flex items-center gap-2 min-w-0">
                <Chip
                  :tone="KIND_TONE[curation.current.kind ?? ''] ?? 'neutral'"
                  dot
                  :title="KIND_HELP[curation.current.kind ?? '']"
                  >{{ kindLabel(curation.current.kind) }}</Chip
                >
                <span class="surface mono text-sm text-text-primary truncate">{{
                  curation.current.surface
                }}</span>
                <span class="flex-none text-xs text-text-tertiary"
                  >{{ curation.position }} of {{ curation.remaining }}</span
                >
              </div>
            </div>
          </template>
          <MergeReview />
        </Panel>

        <Panel v-else-if="tab === 'definitions'" variant="bare" fill class="grow" :padded="true">
          <DefinitionQueue />
        </Panel>

        <Panel v-else-if="tab === 'questions'" variant="bare" fill class="grow" :padded="true">
          <QuestionReviewQueue />
        </Panel>

        <Panel v-else variant="bare" fill class="grow" :padded="true">
          <MergeHistory />
        </Panel>
      </div>
    </RailLayout>
  </ViewShell>
</template>
