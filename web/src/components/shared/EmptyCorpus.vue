<script setup lang="ts">
/**
 * A fresh install, before anything has been ingested. ONE screen, five views.
 *
 * Brief, Graph, Lookup, Curation and Archive were each empty in their own
 * words -- "No brief to show", a blank canvas, "Nothing matches that filter",
 * "Queue clear.", "No briefs yet" -- and four of the five offered nothing to do
 * about it. They are all the same fact: the corpus is empty and the answer is a
 * run. Saying it five ways taught the reader that the app was broken in five
 * places.
 *
 * NOT the same "No brief to show" a broken link produces: that makes the very
 * first thing a new install shows an error about a brief that was never supposed
 * to exist yet. This is not an error. It is the state the app is designed to
 * start in, and the only thing missing is the run.
 *
 * THE FEED COUNT IS REAL, and it has to be: "Venona reads the feeds in
 * sources.yaml" followed by an invented number is the kind of detail that
 * teaches a new operator not to trust the interface. `/api/health` carries a
 * source total, but it counts rows in `source_health`, which is empty until a
 * run has happened — i.e. it reads 0 in exactly this state. So the count comes
 * from the config document itself.
 *
 * The article estimate the design bundle shows beside it ("~180 articles") has
 * no source anywhere in the app and is deliberately omitted rather than
 * hardcoded. It would be a promise this screen cannot keep.
 */
import { computed, onMounted, ref } from 'vue'

import Button from '@/components/ui/Button.vue'
import Panel from '@/components/ui/Panel.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import { apiGet, apiPost, errorText } from '@/api/client'
import { useRunsStore } from '@/stores/runs'
import { useHealthStore } from '@/stores/health'
import { useSessionStore } from '@/stores/session'
import Spinner from '@/components/ui/Spinner.vue'

withDefaults(
  defineProps<{
    /**
     * The four numbered steps. Brief only: it is the landing page and the one
     * place a new operator is owed a description of what they are about to
     * start. On Graph or Curation the same panel is a second explanation beside
     * a screen they navigated to on purpose.
     */
    steps?: boolean
  }>(),
  { steps: false },
)

const runs = useRunsStore()
const health = useHealthStore()
/**
 * BOTH CONTROLS BELOW ARE ADMIN. `POST /api/runs` spends model budget and
 * `/admin/config/sources` is admin including the read, so a reader was offered
 * two buttons that answer with a 403 toast and a bounce back to this same page.
 * This screen is on the brief, which is the first thing a reader sees, so it was
 * the most visible dead end in the app.
 */
const session = useSessionStore()

/**
 * The first run, WHILE it runs.
 *
 * Without this the screen kept saying "the first run takes about six minutes"
 * beside a button, for the entire six minutes -- so the one moment the copy is
 * literally describing is the moment it looked like nothing had happened. The
 * stage and its position come from the same stores the header spine reads, so
 * this and the nav never disagree about which stage is going.
 */
const running = computed(() => Boolean(health.data?.active_run) || runs.researchRunning)

const stage = computed(() => {
  const index = runs.stages.findIndex((s) => s.status === 'running')
  if (index < 0) return null
  return { name: runs.stages[index].name, at: index + 1, of: runs.stages.length }
})

const feedCount = ref<number | null>(null)
const starting = ref(false)
const failure = ref('')

const STEPS = [
  { ordinal: '01', title: 'Fetch', body: 'Every source Venona follows, duplicates folded together.' },
  { ordinal: '02', title: 'Extract', body: 'Names, acronyms, relationships.' },
  { ordinal: '03', title: 'Ground', body: 'Definitions only where a source supports one.' },
  { ordinal: '04', title: 'Write', body: 'One brief, ordered by what your reading profile says matters.' },
] as const

const feedLabel = computed(() =>
  feedCount.value === null
    ? 'the feeds in sources.yaml'
    : `${feedCount.value} ${feedCount.value === 1 ? 'feed' : 'feeds'}`,
)

onMounted(async () => {
  try {
    // The config API serves documents as raw text — there is no structured
    // source-list route — so this counts entries rather than parsing YAML.
    // A dependency-free count of `- id:` under the document is exact for the
    // shape `sources.yaml` actually has, and degrades to "the feeds in
    // sources.yaml" rather than to a wrong number if that ever changes.
    const doc = await apiGet<{ content: string }>('/config/sources')
    const matches = doc.content.match(/^\s*-\s+id:/gm)
    feedCount.value = matches ? matches.length : null
  } catch {
    feedCount.value = null
  }
})

async function runPipeline() {
  starting.value = true
  failure.value = ''
  try {
    await apiPost('/runs', { kind: 'on_demand' })
    await useRunsStore().launched()
  } catch (err) {
    failure.value = errorText(err, 'could not start the run')
  } finally {
    starting.value = false
  }
}
</script>

<template>
  <!-- The message column is capped at `max-w-shell` -- the SAME width
       ViewShell gives a `wide` column -- and the steps panel sits BESIDE it
       rather than inside it. As a `1fr` grid track the panel ate a third of the
       row, so the Brief's first-run screen was 718px wide against 1088px on
       Graph, Lookup, Curation and Archive: one screen shared by five views,
       rendered at two widths depending on which one you landed on.

       The utility form, not the raw custom property: that token is declared in
       `@theme inline`, so its value is inlined into utilities and never emitted
       as a variable -- reading it with `var()` resolves to nothing. -->
  <div class="empty-corpus flex flex-col gap-8 lg:flex-row lg:items-start">
    <section class="max-w-shell grow">
      <p class="font-ui text-2xs uppercase tracking-label text-accent-primary">
        Nothing read yet
      </p>

      <!-- The one place inside the app that uses `--text-3xl`. It is a
           marketing rung and this is the one screen speaking to somebody who
           has not seen the product work yet. -->
      <h1
        class="mt-3 max-w-[22ch] font-ui text-3xl font-medium leading-tight tracking-display text-text-primary"
      >
        The first run takes about six minutes.
      </h1>

      <p class="mt-5 max-w-hint font-prose text-md leading-normal text-text-secondary">
        <template v-if="running">
          Venona is reading {{ feedLabel }} now. The brief appears here when it lands; you can
          leave this page and come back.
        </template>
        <template v-else>
          Venona reads {{ feedLabel }}, picks out the names, and writes the first brief. Nothing here
          needs configuring to start.
        </template>
      </p>

      <StatusNote v-if="failure" kind="error" class="mt-4">{{ failure }}</StatusNote>

      <!-- While it runs, the CTA becomes the progress. Offering "Run the
           pipeline" during a run is offering the one action the lane will
           refuse with a 409. -->
      <div v-if="running" class="mt-6 flex items-center gap-2 font-ui text-sm text-accent-primary">
        <Spinner :size="14" />
        <span v-if="stage">{{ stage.name.replace(/_/g, ' ') }} · step {{ stage.at }} of {{ stage.of }}</span>
        <span v-else>starting…</span>
      </div>

      <div v-else-if="session.isAdmin()" class="mt-6 flex gap-3">
        <Button icon="play" :disabled="starting" @click="runPipeline">
          {{ starting ? 'Starting…' : 'Start the first run' }}
        </Button>
        <!-- `inline-flex no-underline` on the wrapper, matching AppHeader's gear.
             A bare RouterLink is a `display: block` anchor, so it was 33px tall
             around a 28px Button and the two controls in this row sat on
             different baselines. -->
        <RouterLink to="/admin/config/sources" class="inline-flex no-underline hover:no-underline">
          <Button variant="outline" icon="right">Review sources first</Button>
        </RouterLink>
      </div>

      <!-- A reader gets the fact rather than two controls that refuse them, and
           the fact is about THEM: nothing to read, nothing wrong, nothing to do.
           What happens next, and who makes it happen, is the other side of a
           boundary this account is on the reading side of -- and a reading
           surface that narrates the machinery behind it is telling somebody
           about a room they cannot enter. -->
      <p v-else class="mt-6 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
        There is nothing to read here yet. Nothing is wrong, and there is nothing for you to do.
      </p>
    </section>

    <Panel
      v-if="steps"
      variant="inset"
      class="p-4 self-start shrink-0 lg:w-(--rail-width-wide)"
    >
      <SectionHead as="h2" label="What the run will do" />
      <ol class="mt-4 flex flex-col gap-4">
        <li v-for="(step, i) in STEPS" :key="step.ordinal" class="flex gap-3">
          <!-- The first ordinal is accented and the rest are not: it marks
               where the run starts, which is the only thing the numbers have to
               say. Four accented numerals would be four primary actions. -->
          <span
            class="mono text-2xs tabular-nums"
            :class="i === 0 ? 'text-accent-primary' : 'text-text-quiet'"
            >{{ step.ordinal }}</span
          >
          <div class="min-w-0">
            <p class="font-ui text-sm text-text-primary">{{ step.title }}</p>
            <p class="mt-1 font-prose text-sm leading-normal text-text-tertiary">{{ step.body }}</p>
          </div>
        </li>
      </ol>
    </Panel>
  </div>
</template>
