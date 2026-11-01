<script setup lang="ts">
/**
 * Data management. One screen, one irreversible button.
 *
 * `make reset` is the workstation answer to "start over" and it is the right
 * one when there is a terminal. On a deployed instance there is a browser and
 * nothing else, and starting over is a real thing to want -- a week of runs
 * against a misconfigured relevance profile fills the corpus with the wrong
 * industry, and there is no way to un-read it one merge at a time.
 *
 * A HARD delete. `runs` already carries `hidden_at` for tidying a listing, and
 * that is a different feature; this is the answer to "I want it gone", so it
 * removes rows. Nothing here is recoverable from inside the app.
 *
 * THE CONFIRMATION IS NOT A YES/NO. It states the real row counts, names what
 * survives, and requires the phrase to be typed. A dialog that says "are you
 * sure?" is answered reflexively; one that makes you type `delete everything`
 * under a list saying `2,312 entities` is answered deliberately. The API
 * demands the same phrase independently -- see `schemas/data.py` -- so the
 * guard is not something a determined click can route around.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'

import AdminRail from '@/components/nav/AdminRail.vue'
import Button from '@/components/ui/Button.vue'
import Dialog from '@/components/ui/Dialog.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import PaneState from '@/components/ui/PaneState.vue'
import RailLayout from '@/components/ui/RailLayout.vue'
import Table, { type Column } from '@/components/ui/Table.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { useAdminPagesStore } from '@/stores/adminPages'
import { useRunsStore } from '@/stores/runs'
import { useToastsStore } from '@/stores/toasts'
import { apiGet, apiPost, errorText } from '@/api/client'
import type { DataExport, DataExportList, DataStats, PurgeResult } from '@/types/data'
import type { Run, RunStarted } from '@/types/run'
import { verdictFor } from '@/lib/runOutcome'
import { count, dayTimeLabel } from '@/lib/dates'
import Input from '@/components/ui/Input.vue'

// Held in the store so a revisit paints instantly; the mount fetch is a
// revalidation (and the counts DO drift under a run, so it stays per-mount).
const { dataStats: stats } = storeToRefs(useAdminPagesStore())
const loading = ref(false)
const error = ref<string | null>(null)

const runs = useRunsStore()
const toasts = useToastsStore()

const exports = ref<DataExport[]>([])
const keep = ref(0)
const exportError = ref('')
const starting = ref(false)

/**
 * The export in flight, from the polled health probe rather than a local flag.
 *
 * A `ref` set on press is wrong the moment the administrator leaves this page
 * and comes back, opens a second tab, or the run was started somewhere else --
 * and it is wrong for the whole length of the run, which is exactly when the
 * page is being looked at. `lightRuns` is fed by `/health`, which the app frame
 * polls every three seconds while any light run is live.
 *
 * Deliberately NOT `runs.attach()`. There is one event stream per tab and
 * `attach` closes whatever is on it, so launching from here would detach the
 * operator from a research run they were watching.
 */
const exporting = computed(() => runs.lightRuns.find((r) => r.kind === 'export') ?? null)

interface ExportRow extends Record<string, unknown> {
  id: string
  filename: string
  made: string
  contents: string
  size: string
  url: string | null
}

const EXPORT_COLUMNS: Column<ExportRow>[] = [
  { key: 'filename', label: 'file' },
  { key: 'made', label: 'made', hideBelow: 'sm' },
  // Detail rather than identity, so this is the first to stand down: at 390px
  // the row has to say WHICH copy and offer the button before anything else.
  { key: 'contents', label: 'holds', hideBelow: 'sm' },
  { key: 'size', label: 'size', numeric: true },
  // No label. The control names itself, and a header over a button is a word
  // describing a word.
  { key: 'url', label: '' },
]

/** Bytes in the units a person reads. Not `count()`, which is for row counts:
 *  `4,283,912` is a true answer to a question nobody asked. */
function fileSize(bytes: number): string {
  if (bytes < 1000) return `${bytes} B`
  if (bytes < 1000 * 1000) return `${Math.round(bytes / 1000)} KB`
  return `${(bytes / 1000 / 1000).toFixed(1)} MB`
}

const exportRows = computed<ExportRow[]>(() =>
  exports.value.map((item) => ({
    id: item.id,
    filename: item.filename,
    made: dayTimeLabel(item.created_at),
    contents: `${count(item.counts.notes)} notes`,
    size: fileSize(item.bytes),
    url: item.download_url,
  })),
)

async function loadExports() {
  try {
    const data = await apiGet<DataExportList>('/data/exports')
    exports.value = data.items
    keep.value = data.keep
    exportError.value = ''
  } catch (err) {
    exportError.value = errorText(err, 'could not read the copies on this machine')
  }
}

async function startExport() {
  starting.value = true
  exportError.value = ''
  try {
    const { run_id, status } = await apiPost<RunStarted>('/data/exports', {})
    // A toast rather than a line on this page alone. The copy takes minutes and
    // the operator is free to go and read something; the toast host is the one
    // surface that reaches them wherever they went. Same shape as
    // `LookupAllButton`, which is the other admin control that starts a light
    // run somebody walks away from.
    toasts.push({
      kind: 'success',
      // `queued` is a real answer and not a failure: the light lane was full
      // (contract C11) and this starts when a slot frees.
      text: status === 'queued' ? 'queued: a copy for Obsidian' : 'writing a copy for Obsidian',
      to: `/admin/runs/${run_id}`,
      toLabel: 'watch',
    })
    // After EVERY launch, from anywhere: the In flight table reads
    // `health.data.runs.active`, not the history list, so a fresh run looks
    // already-finished without this.
    await runs.launched()
    follow(run_id)
  } catch (err) {
    exportError.value = errorText(err, 'the copy could not be started')
  } finally {
    starting.value = false
  }
}

/** Runs already announced, so two paths noticing one finish say it once. */
const announced = new Set<string>()

/**
 * What the run turned out to be, in the words the history table uses.
 *
 * `runs.ts` toasts on completion only for the run its single stream is attached
 * to, and this page deliberately does not attach -- there is one stream per tab
 * and taking it would detach the operator from a research run they were
 * watching. So the finish is announced here, and the words come from
 * `verdictFor`, which is the only place in the app a run becomes a phrase.
 */
async function announceFinish(runId: string, known?: Run) {
  if (announced.has(runId)) return
  announced.add(runId)
  await loadExports()
  try {
    const run = known ?? (await apiGet<Run>(`/runs/${encodeURIComponent(runId)}`))
    const verdict = verdictFor(run)
    toasts.push({
      kind: verdict.tone === 'critical' ? 'error' : 'success',
      text: `export ${verdict.text}`,
      to: verdict.isProblem ? `/admin/runs/${runId}` : '/admin/data',
      toLabel: verdict.isProblem ? 'open run' : 'download',
    })
  } catch {
    // The run finished either way, and the table above already says what
    // landed. A toast that could not be built is not worth an error banner.
  }
}

let following: number | null = null

/**
 * Follow the run we just started until it ends.
 *
 * THE HEALTH PROBE IS NOT ENOUGH ON ITS OWN, and this is measured rather than
 * defensive: on the live corpus an export finishes in about 300ms (2026-09-11),
 * and the probe's fast cadence only engages once it has already SEEN a light run. So
 * the run starts and ends between two ticks, `exporting` never becomes
 * truthy, and a page that depended on watching it went right on showing a
 * stale table with no finish ever announced. Polling the run we hold the id
 * for makes the fast case and the several-minute case behave identically.
 */
function follow(runId: string) {
  stopFollowing()
  const started = Date.now()
  async function tick() {
    // A ceiling, so a run that never reports does not leave a timer running
    // for the life of the tab. The table is still correct on the next visit.
    if (Date.now() - started > 20 * 60 * 1000) return stopFollowing()
    try {
      const run = await apiGet<Run>(`/runs/${encodeURIComponent(runId)}`)
      if (!run.finished_at) return
      stopFollowing()
      void announceFinish(runId, run)
    } catch {
      stopFollowing()
    }
  }
  // `void tick()` rather than an async callback: `setInterval` expects a void
  // return, and handing it a promise is a floating one nothing can await.
  following = window.setInterval(() => void tick(), 1200)
}

function stopFollowing() {
  if (following !== null) window.clearInterval(following)
  following = null
}

onBeforeUnmount(stopFollowing)

/**
 * Reload when the run leaves the probe.
 *
 * No second timer: the list only changes when an export finishes, and the app
 * frame is already asking `/health` every three seconds while one is running.
 */
// The other way in: arriving while a copy started elsewhere is still running,
// where there is no launch to follow. `announceFinish` is guarded, so whichever
// of the two notices first is the one that speaks.
watch(exporting, (now, before) => {
  if (!before || now) return
  void announceFinish(before.run_id)
})

const confirming = ref(false)
const phrase = ref('')
const purging = ref(false)
const result = ref<PurgeResult | null>(null)
const failure = ref('')

const CONFIRM_PHRASE = 'delete everything'
const canPurge = computed(() => phrase.value.trim().toLowerCase() === CONFIRM_PHRASE)

/**
 * Table names in reader words. The counts and `preserved` arrive as raw
 * Postgres table names -- wire values, so the translation happens at render
 * time. A name the map does not know renders with its underscores opened up.
 */
const TABLE_LABEL: Record<string, string> = {
  data_exports: 'copies you have saved',
  documents: 'documents',
  entities: 'names',
  assertions: 'relationships',
  sightings: 'name sightings',
  aliases: 'other names',
  alias_conflicts: 'name conflicts',
  ambiguous_surfaces: 'ambiguous terms',
  acronym_definitions: 'acronym definitions',
  entity_definitions: 'name definitions',
  entity_mappings: 'name merges',
  clusters: 'story clusters',
  briefs: 'briefs',
  brief_items: 'brief items',
  brief_blocks: 'brief text',
  brief_clusters: 'brief story links',
  runs: 'runs',
  run_events: 'run events',
  run_stages: 'run steps',
  run_metrics: 'run metrics',
  run_source_stats: 'per-run source stats',
  run_rejected_shapes: 'refused relationships',
  llm_calls: 'model calls',
  llm_questions: 'proposed study questions',
  learning_cards: 'study cards',
  learning_reviews: 'study reviews',
  learning_settings: 'study settings',
  source_health: 'source health',
  sources: 'sources',
  mcp_tokens: 'agent connections',
  users: 'accounts',
  user_events: 'authority log',
  access_requests: 'access requests',
  oauth_clients: 'sign-in clients',
  oauth_codes: 'sign-in codes',
  schema_migrations: 'database version history',
}

function tableLabel(name: string): string {
  return TABLE_LABEL[name] ?? name.replace(/_/g, ' ')
}

/** Biggest first. The confirmation is meant to be read, and `assertions 6122`
 *  is the line that makes someone stop; `sources 18` is not. */
const rows = computed(() =>
  Object.entries(stats.value?.counts ?? {})
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]),
)

async function load() {
  const first = stats.value === null
  if (first) loading.value = true
  try {
    stats.value = await apiGet<DataStats>('/data/stats')
  } catch (err) {
    if (first) error.value = errorText(err, 'could not read the database')
  } finally {
    if (first) loading.value = false
  }
}

function open() {
  phrase.value = ''
  failure.value = ''
  result.value = null
  confirming.value = true
}

async function purge() {
  if (!canPurge.value) return
  purging.value = true
  failure.value = ''
  try {
    result.value = await apiPost<PurgeResult>('/data/purge', { confirm: phrase.value.trim() })
    confirming.value = false
    await Promise.all([load(), loadExports()])
  } catch (err) {
    // The 409 is the one worth spelling out: a research run in flight holds
    // entities and assertions that would land after the delete.
    failure.value = errorText(err, 'the purge did not run')
  } finally {
    purging.value = false
  }
}

onMounted(() => {
  void load()
  void loadExports()
})
</script>

<template>
  <ViewShell width="wide" identity="admin · data">
    <RailLayout>
      <template #rail>
        <AdminRail current="data" />
      </template>

      <PaneState :loading="loading" :error="error">
      <div class="flex flex-col">
        <SectionHead as="h2" label="what is stored" rule />
        <p class="mt-3 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
          Everything Venona has read, learned and written. The graph is drawn from these
          records and can always be rebuilt from them.
        </p>

        <StatusNote v-if="result" kind="success" class="mt-4">
          Deleted {{ count(result.total) }} rows.
          <template #hint>
            {{
              result.graph_cleared
                ? 'The graph was cleared too.'
                : 'The graph could not be cleared. Run venona graph rebuild on the server.'
            }}
          </template>
        </StatusNote>

        <table v-if="rows.length" class="mt-4 w-full max-w-measure border-collapse">
          <tbody>
            <tr v-for="[table, n] in rows" :key="table" class="border-b border-border-subtle">
              <td class="py-1 font-ui text-sm text-text-body">{{ tableLabel(table) }}</td>
              <td class="py-1 text-right font-mono text-sm tabular-nums text-text-secondary">
                {{ count(n) }}
              </td>
            </tr>
            <tr>
              <td class="py-1 font-ui text-2xs uppercase tracking-label text-text-quiet">total</td>
              <td
                class="py-1 text-right font-mono text-sm tabular-nums text-text-primary"
              >
                {{ count(stats?.total) }}
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="mt-4 font-ui text-sm text-text-tertiary">Venona has read nothing yet.</p>

        <SectionHead as="h2" label="save a copy" rule class="mt-7" />
        <p class="mt-3 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
          Writes everything Venona has read and learned as a folder of Markdown files, one
          for each name, with the links between them kept. Open that folder in Obsidian and
          every link works. A copy is a file rather than a record, so nothing below touches
          it.
          <span v-if="keep">
            Venona keeps the {{ keep }} most recent and removes older ones to save room.
          </span>
        </p>

        <div class="mt-4 flex flex-wrap items-center gap-3">
          <Button icon="download" :disabled="!!exporting || starting" @click="startExport">
            {{ exporting ? 'Making a copy…' : 'Export for Obsidian' }}
          </Button>
          <span v-if="exporting" class="font-ui text-xs text-text-tertiary">
            This keeps going if you leave the page.
            <RouterLink :to="`/admin/runs/${exporting.run_id}`">Watch it</RouterLink>
          </span>
        </div>

        <StatusNote v-if="exportError" kind="error" class="mt-3">{{ exportError }}</StatusNote>

        <Table
          class="mt-4"
          :columns="EXPORT_COLUMNS"
          :rows="exportRows"
          row-key="id"
        >
          <!-- The anchor the run detail page links to, so finishing a run and
               following it lands on the row it wrote. -->
          <template #cell:filename="{ row }">
            <span :id="(row as ExportRow).filename" class="font-mono text-xs">
              {{ (row as ExportRow).filename }}
            </span>
          </template>

          <template #cell:url="{ row }">
            <div class="text-right">
              <!-- A real link, not a click handler. The session cookie is
                   `SameSite=Lax` and rides a top-level navigation, the SPA is
                   same-origin with `/api`, and a navigation is exempt from the
                   client's 30s timeout that a large file would otherwise hit.
                   `Button href` is not used: it forces `target="_blank"`, which
                   opens and abandons a tab for every download. -->
              <a
                v-if="(row as ExportRow).url"
                :href="(row as ExportRow).url ?? undefined"
                download
                class="font-ui text-xs uppercase tracking-label text-accent-primary hover:underline"
              >
                Download
              </a>
              <!-- Not hidden. "There was one and it is gone" is a different fact
                   from "there was never one", and only the first teaches the
                   reader that older copies are removed. -->
              <span v-else class="font-ui text-xs text-text-quiet">removed to save room</span>
            </div>
          </template>

          <template #empty>
            <EmptyState title="No copies yet.">
              Press Export for Obsidian and the first one lands here.
            </EmptyState>
          </template>
        </Table>

        <SectionHead as="h2" label="delete everything" rule class="mt-7" />
        <p class="mt-3 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
          Removes everything listed above. Nothing in Venona brings it back. Copies you have
          already saved are files on this machine and are left where they are.
          <span v-if="stats?.preserved?.length" class="block mt-2">
            Kept: <span class="font-ui text-text-body">{{ stats.preserved.map(tableLabel).join(', ') }}</span>.
          </span>
        </p>

        <div class="mt-4">
          <Button tone="critical" variant="outline" icon="trash-2" :disabled="!stats?.total" @click="open">
            Delete all data
          </Button>
        </div>
      </div>
      </PaneState>
    </RailLayout>

    <!-- The default `actions` slot is overridden so the affirmative button can
         be gated on the typed phrase. `Dialog`'s built-in confirm is disabled
         only while `busy`, which is right for an ordinary confirm and not
         enough for this one. -->
    <Dialog v-model:open="confirming" title="Delete all data" destructive :busy="purging">
      <p class="font-prose text-base leading-normal text-text-secondary">
        This permanently deletes
        <span class="font-mono text-text-primary">{{ count(stats?.total) }}</span>
        records: every document, name, relationship, definition, brief and run. It cannot be
        undone. Copies you have saved are files rather than records, and stay where they are.
      </p>

      <StatusNote v-if="failure" kind="error" class="mt-3">{{ failure }}</StatusNote>

      <label
        for="purge-confirm"
        class="mt-4 block font-ui text-2xs uppercase tracking-label text-text-tertiary"
      >
        Type <span class="font-mono normal-case tracking-normal">{{ CONFIRM_PHRASE }}</span> to
        confirm
      </label>
      <Input
        id="purge-confirm"
        v-model="phrase"
        family="mono"
        autocomplete="off"
        spellcheck="false"
        class="mt-2"
      />

      <template #actions>
        <Button variant="outline" icon="close" :disabled="purging" @click="confirming = false">Cancel</Button>
        <Button icon="trash-2" tone="critical" :disabled="!canPurge || purging" @click="purge">
          {{ purging ? 'Deleting…' : 'Delete everything' }}
        </Button>
      </template>
    </Dialog>
  </ViewShell>
</template>
