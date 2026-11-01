<script setup lang="ts">
/**
 * One configuration document, open for editing.
 *
 * Full width and no rail, with `/admin/config` as the library: the file you
 * came here to read runs to 29KB of YAML (`config/ontology.yaml`, measured
 * 2026-09-03) and a sidebar listing every other document is not what that
 * space is for. `⌘K` switches documents without going back. ADR 0200.
 *
 * UNDER `/admin`, not `/settings`, and admin-only INCLUDING THE READS:
 * `GET /api/config/{key}` returns `profile/relevance_profile.md` whole, and
 * that document is the operator's own thesis about what matters and who they
 * compete with. ADR 0134. One surface for all of them, because editing the
 * relevance profile and editing the source list are the same job (ADR 0081).
 *
 * The documents are raw YAML and Markdown, not forms. Both config files carry
 * load-bearing comments -- `sources.yaml` records in its `removed:` block every
 * feed that was tried and did not work, `ontology.yaml` annotates its type
 * vocabularies -- and any form that re-serialises the parsed document destroys
 * them. `routers/admin.py` splices a setting rather than re-serialising for
 * the same reason.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { NetworkError, apiGet, apiPost, errorText } from '@/api/client'
import ConfigEditor from '@/components/config/ConfigEditor.vue'
import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import Dialog from '@/components/ui/Dialog.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Disclosure from '@/components/ui/Disclosure.vue'
import PaneState from '@/components/ui/PaneState.vue'
import Spinner from '@/components/ui/Spinner.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { hintFor } from '@/lib/configHints'
import { dayTimeLabel } from '@/lib/dates'
import { predicateLabel } from '@/lib/curation'
import { applyTitle } from '@/router'
import { useConfigDocs } from '@/composables/useConfigDocs'
import { useKeyBindings } from '@/composables/useKeyboard'
import { useConfigDocsStore } from '@/stores/configDocs'
import { useCurationStore } from '@/stores/curation'
import { useHealthStore } from '@/stores/health'
import { useToastsStore } from '@/stores/toasts'
import type {
  AnchorApplyResult,
  ConfigHistory,
  ConfigSnapshot,
  ConfigSnapshotFull,
  OntologyRejectedShape,
} from '@/types/config'

const route = useRoute()
const health = useHealthStore()
const toasts = useToastsStore()
const curation = useCurationStore()
const store = useConfigDocsStore()


const {
  active,
  doc,
  draft,
  dirty,
  drafts,
  draftDiverged,
  saving,
  loading,
  conflict,
  notice,
  readonly,
  loadList,
  select,
  onDraftChanged,
  save,
  discardDraft,
  serverDraft,
  serverDraftShadowed,
  discardServerDraft,
  reviewServerDraft,
  restoredFrom,
  restoreSnapshot,
  keepMyEdits,
} = useConfigDocs()

/**
 * The document, named: the title, because the index is a list of titles and
 * the bar has to name the row that was clicked. Nothing in front of it -- the
 * back-link beside this already names where the row came from, and repeating
 * that here spends the width that identifies the file.
 */
const identity = computed(() => (problem.value ? problem.value.key : (doc.value?.title ?? 'configuration')))

/**
 * Where the back-link goes, which is not always the index.
 *
 * The setup wizard drafts every document in `setup_service.ALL_DOCS` and hands
 * them over one at a time, so during onboarding the page they came from -- and
 * the checklist saying which drafts are left -- is `/setup`. `SetupView` says
 * so in the link it sends them here with.
 */
const parent = computed(() =>
  route.query.from === 'setup'
    ? { label: 'domain setup', to: '/setup' }
    : { label: 'configuration', to: '/admin/config' },
)

/**
 * Unsaved work in OTHER documents.
 *
 * The index marks these per row, and the index is the page you have to leave
 * this one to reach -- so a forgotten buffer in another file would be invisible
 * for as long as you stayed here. The count follows you instead. A chip and not
 * a dialog, for the reason `useConfigDocs::select` gives for asking nothing on
 * a switch: this is information, not an interruption.
 */
const unsavedElsewhere = computed(
  () => [...drafts.value].filter((key) => key !== active.value).length,
)

/** What saving this document will and will not do. On the Save button rather
 *  than as a paragraph over the editor -- see `lib/configHints.ts`. */
const saveHint = computed(() => hintFor(doc.value?.rescore_hint))

/**
 * Source health, shown only on the Sources document.
 *
 * The answer to "I fixed the feed and it still does not run": a source
 * auto-disabled at `registry.STRIKE_LIMIT` consecutive failures is excluded
 * from every run by `active_specs`, and the strike counter is a database row,
 * so rewriting `sources.yaml` does not clear it.
 */
const disabledSources = computed(() =>
  active.value === 'sources' ? (health.data?.sources?.auto_disabled ?? []) : [],
)

/** Which source is mid-retry, so its button can say so. */
const enabling = ref<string | null>(null)

/**
 * Shapes runs keep asserting that this ontology refuses, shown only on the
 * Ontology document. Why they are evidence for an edit here rather than
 * run-warning scroll is in `routers/config.py::ontology_rejections`.
 *
 * Keyed on the document's version as well as the selection: that endpoint
 * re-checks every shape against the CURRENT ontology, so a save that widens a
 * contract must refresh the list to show it dissolving.
 */
const rejectedShapes = ref<OntologyRejectedShape[]>([])
watch(
  () => [active.value, doc.value?.version] as const,
  async ([key]) => {
    if (key !== 'ontology') {
      rejectedShapes.value = []
      return
    }
    try {
      const body = await apiGet<{ shapes: OntologyRejectedShape[] }>('/config/ontology/rejections')
      rejectedShapes.value = body.shapes
    } catch {
      rejectedShapes.value = [] // advisory panel; the editor must open regardless
    }
  },
  { immediate: true },
)

/**
 * Put a switched-off source back in the rotation.
 *
 * Clears the strike counter and nothing else -- it does not claim the source
 * works. If whatever broke it is still broken it earns a fresh set of strikes
 * and switches itself off again, which is the machinery doing its job rather
 * than a failure of this button. `routers/meta.py::enable_source`.
 */
async function retrySource(id: string) {
  enabling.value = id
  try {
    await apiPost(`/sources/${encodeURIComponent(id)}/enable`, {})
    await health.refresh()
    useToastsStore().push({ kind: 'success', text: `${id} is back in the rotation` })
  } catch (err) {
    useToastsStore().push({
      kind: 'error',
      text: errorText(err, `could not switch ${id} back on`),
    })
  } finally {
    enabling.value = null
  }
}

// ── route <-> selection ──────────────────────────────────────────────
//
// `AppShell` renders `<component :is>` with no `:key`, so moving from one
// document to another -- from ⌘K, or from a run's link -- reuses this instance
// and `onMounted` never fires again. THE WATCHER IS WHAT MAKES THAT WORK:
// without it the URL changes and the pane keeps showing the previous file.

function keyFromRoute(): string | null {
  const key = route.params.key
  return typeof key === 'string' && key ? key : null
}

/**
 * The key in the URL could not be opened, and why.
 *
 * NOT COSMETIC. `select` rolls its state back to the previous document when a
 * load fails, and it is the only thing that does -- so without this the pane
 * keeps rendering that document, whole and plausible, under the new key's URL
 * and tab title, while `save()` PUTs the buffer to `active`. The index's rows
 * are links, so the router has already navigated by the time `open` runs and
 * there is no push to withhold: refusing to draw a document the URL does not
 * name is the only guard left.
 */
const problem = ref<{ key: string; reason: 'unknown' | 'failed' } | null>(null)

/** The document list itself could not be fetched. */
const listError = ref<string | null>(null)

async function open(key: string) {
  problem.value = null
  if (!store.items.some((d) => d.key === key)) {
    problem.value = { key, reason: 'unknown' }
    return
  }
  await select(key)
  // `select` swallows the failure into a toast and a rollback. A toast clears
  // itself in seconds; the wrong document under the right URL does not.
  if (active.value !== key) problem.value = { key, reason: 'failed' }
}

onMounted(async () => {
  // `ensure`, not `refresh`: the poll loop owns health's cadence and this view
  // only needs A reading for the source-strike panel. See `stores/health.ts`.
  void health.ensure()
  // THE LIST IS NOT OPTIONAL HERE. `select` reads it to choose between
  // `/config/{key}` and `/config/assembled/{key}`, and nothing else on this
  // route loads it -- so without this every assembled prompt 404s on a deep
  // link.
  try {
    await loadList()
  } catch (err) {
    // Unguarded, this rejection ends `onMounted` before anything selects, and
    // the route renders as an empty frame -- with no rail beside it to say the
    // page is alive.
    listError.value = errorText(err, 'could not load the configuration documents')
    return
  }
  // The router names this tab before the list exists, so every document would
  // keep the fallback title without a second pass here.
  applyTitle()
  const wanted = keyFromRoute()
  if (wanted) await open(wanted)
})

watch(
  () => route.params.key,
  (value) => {
    // `select` no-ops when the key is already active, so a navigation that only
    // adds a query string cannot bounce into a second fetch.
    if (typeof value === 'string' && value) void open(value)
  },
)

// ── anchors: saving is not loading ───────────────────────────────────

const applying = ref(false)
const applyOpen = ref(false)
const preview = ref<AnchorApplyResult | null>(null)

async function openApply() {
  preview.value = null
  applyOpen.value = true
  try {
    preview.value = await apiPost<AnchorApplyResult>('/config/anchors/apply?dry_run=true')
  } catch (err) {
    // An `ApiError` means the server answered and said no -- it names the
    // source id or axis that failed to load, which is worth reading -- while a
    // `NetworkError` means it never answered at all. Collapsing the two would
    // report a validation failure as a network outage.
    notice.value =
      err instanceof NetworkError
        ? 'could not reach the server'
        : errorText(err, 'anchors are not loadable')
  }
}

async function confirmApply() {
  applying.value = true
  try {
    const body = await apiPost<AnchorApplyResult>('/config/anchors/apply')
    toasts.push({ kind: 'success', text: `anchors: ${body.entities} names, ${body.edges} connections` })
    curation.anchorsAwaitingRebuild = body.rebuild_required
  } catch (err) {
    notice.value =
      err instanceof NetworkError
        ? 'could not reach the server, so anchors were not applied'
        : errorText(err, 'apply failed')
  } finally {
    applying.value = false
    applyOpen.value = false
  }
}

/**
 * The projection, refreshed, through the store that owns the action.
 *
 * Not a second `apiPost` here: `curation.rebuild()` reads the refusal code, so
 * a 409 of `rebuilding` reports as a wait rather than a failure (its own
 * docstring carries that argument), and it clears the flag only when the
 * rebuild actually landed, which is the point of the banner.
 */
const rebuilding = ref(false)

async function rebuild() {
  if (rebuilding.value) return
  rebuilding.value = true
  try {
    if (await curation.rebuild()) toasts.push({ kind: 'success', text: 'graph rebuilt' })
    else if (curation.error) toasts.push({ kind: 'error', text: curation.error })
  } finally {
    rebuilding.value = false
  }
}

// ── saved versions ───────────────────────────────────────────────────

const snapshots = ref<ConfigSnapshot[]>([])
const historyError = ref<string | null>(null)
/** The row awaiting a confirmation, when restoring it would destroy work. */
const pendingRestore = ref<ConfigSnapshot | null>(null)
const restoring = ref(false)

/** Whether the panel has ever been opened, so it can be kept current after. */
const historyLoaded = ref(false)

async function loadHistory() {
  historyError.value = null
  historyLoaded.value = true
  const key = active.value
  try {
    const body = await apiGet<ConfigHistory>(`/config/${key}/history`)
    // The document may have changed under a slow response.
    if (active.value !== key) return
    snapshots.value = body.items ?? []
  } catch (err) {
    if (active.value !== key) return
    historyError.value = errorText(err, 'could not list the saved versions')
  }
}

/**
 * The panel follows the document, and follows a save.
 *
 * `Disclosure` fires `@open` once and never again, so nothing else would ever
 * refresh this: switching documents would leave one file's versions listed
 * under another's name, and saving -- which is precisely what CUTS a snapshot
 * -- would leave the list one short of the truth. Keyed on the version as well
 * as the key, which also catches a save made from the CLI or another tab.
 */
watch(
  () => [active.value, doc.value?.version] as const,
  ([key], [previousKey]) => {
    if (key !== previousKey) {
      snapshots.value = []
      historyError.value = null
      historyLoaded.value = false
      return
    }
    if (historyLoaded.value) void loadHistory()
  },
)

/**
 * Restoring destroys something only sometimes, and only then does it ask.
 *
 * `discardDraft` deliberately has no dialog, because the pane says what differs
 * and the discard is one click to reproduce by retyping. This is on the other
 * side of that line: what a restore overwrites is unsaved work, or a draft a
 * run spent money producing, and neither comes back by retyping.
 */
async function chooseSnapshot(row: ConfigSnapshot) {
  if (dirty.value || serverDraft.value) {
    pendingRestore.value = row
    return
  }
  await applyRestore(row)
}

async function applyRestore(row: ConfigSnapshot) {
  restoring.value = true
  // The key this restore belongs to. A snapshot fetched for one document must
  // never land in the buffer of another, and `save()` PUTs that buffer to
  // whatever `active` is by the time it is pressed.
  const key = active.value
  try {
    const full = await apiGet<ConfigSnapshotFull>(
      `/config/${key}/history/${encodeURIComponent(row.file)}`,
    )
    if (active.value !== key) return
    restoreSnapshot(full)
  } catch (err) {
    if (active.value === key) notice.value = errorText(err, 'could not read that saved version')
  } finally {
    restoring.value = false
    pendingRestore.value = null
  }
}

/**
 * When a version was saved: "Aug 7, 3:11 PM".
 *
 * A DATE AND A TIME, not "27 days ago". Measured 2026-09-03 over
 * `config/.history`: seven snapshots of the ontology inside two days, so a
 * relative label renders the whole list as the same sentence seven times and
 * there is nothing left to choose between rows by. A missing stamp says so
 * rather than rendering as 1 January 1970.
 */
function savedAt(row: ConfigSnapshot): string {
  if (!row.timestamp) return 'on a date that was not recorded'
  return dayTimeLabel(new Date(row.timestamp * 1000).toISOString())
}

/** How this version differs from the file as it stands, in what it costs to
 *  take it: bytes, signed. An absolute size hides the drop -- measured
 *  2026-09-03, the newest snapshot of a 29KB ontology was 9KB. */
function delta(row: ConfigSnapshot): string {
  const current = doc.value?.bytes ?? 0
  const diff = row.bytes - current
  if (!current) return `${row.bytes} bytes`
  if (diff === 0) return 'same size as the file'
  return `${diff > 0 ? '+' : ''}${diff} bytes against the file`
}

function takeDisk() {
  draft.value = conflict.value ?? ''
  conflict.value = null
}

watch(draft, (value) => onDraftChanged(value))

useKeyBindings([
  {
    keys: ['mod+s'],
    label: 'Save',
    // The group string is rendered VERBATIM as a heading in the `?` overlay
    // (`KeyboardHelp.vue` feeds it straight to `SectionHead`), so it has to
    // name where the shortcut actually exists.
    group: 'configuration',
    allowInInput: true,
    run: () => void save(),
  },
])
</script>

<template>
  <!-- `wide` is the gutter every admin surface uses. `scroll="none"` stays: the
       editor owns its own scroller, and the pane below is the shell's stretch
       target. -->
  <ViewShell width="wide" scroll="none" :identity="identity" :parent="parent">
    <!-- The document's title is the identity; these are facts ABOUT it. -->
    <template #controls>
      <template v-if="doc && !problem">
        <Chip v-if="doc.readonly" tone="neutral">read-only</Chip>
        <Chip v-else :tone="doc.format === 'yaml' ? 'info' : 'neutral'">{{ doc.format }}</Chip>
        <!-- Chips, not bare spans: this zone is an outlined vocabulary on every
             other route, and two loose words among boxed controls read as a
             caption that wandered in. `high` for unsaved because it is the one
             thing here you must not walk away from. -->
        <Chip v-if="dirty" tone="high">unsaved changes</Chip>
        <Chip v-if="doc.version" tone="neutral">v{{ doc.version }}</Chip>
      </template>
      <!-- Outside the `v-if`: work waiting in another file is true whether or
           not this one has finished loading. -->
      <Chip v-if="unsavedElsewhere" tone="high" interactive @click="$router.push('/admin/config')">
        {{ unsavedElsewhere }} other {{ unsavedElsewhere === 1 ? 'document' : 'documents' }} unsaved
      </Chip>
    </template>

    <!-- `readonly` is false while nothing is loaded, and `doc` still holds the
         PREVIOUS document after a failed load, so both gates are needed or Save
         draws itself over a pane showing no document and writes to whatever
         `active` rolled back to. -->
    <template v-if="doc && !problem" #actions>
      <Spinner v-if="saving" :size="12" />
      <Button
        v-if="active === 'anchors'"
        variant="outline"
        icon="check"
        label="Apply"
        @click="openApply"
      >
        Apply
      </Button>
      <Button
        v-if="!readonly"
        icon="check"
        label="Save"
        :title="saveHint"
        :disabled="!dirty || saving"
        @click="save"
      >
        Save
      </Button>
    </template>

    <!-- Under `scroll="none"` the last child is the shell's stretch target, so
         this pane is what fills the frame and owns the scroller. -->
    <PaneState :loading="loading" :error="listError">
      <EmptyState
        v-if="problem"
        :title="
          problem.reason === 'unknown'
            ? `There is no document called ${problem.key}`
            : `${problem.key} could not be opened`
        "
      >
        {{
          problem.reason === 'unknown'
            ? 'It may have been renamed, or the link may be out of date.'
            : 'The server did not answer for it. Nothing has been changed.'
        }}
        <!-- `RouterLink` around the Button, the shape `NotFoundView` uses:
             `Button`'s own `href` form is an `<a target="_blank">`, for external
             links, and this is a route. -->
        <template #action>
          <RouterLink to="/admin/config" class="inline-flex no-underline hover:no-underline">
            <Button variant="outline">Open configuration</Button>
          </RouterLink>
        </template>
      </EmptyState>

      <div v-else-if="doc" class="pane min-h-0 flex flex-col flex-1 gap-3">
        <!-- What this document IS, first. `description` is the one string here
             written for the person reading it, as against the file's own
             header, which is written for whoever maintains it. ADR 0200. -->
        <p class="lead max-w-hint font-prose text-sm leading-normal text-text-tertiary">
          {{ doc.description }}
        </p>

        <StatusNote v-if="serverDraft && serverDraftShadowed && !restoredFrom" kind="info">
          A run wrote a draft of this document while you had unsaved edits of your own. Only
          one of them can be kept.
          <template #hint>
            <span class="inline-flex items-center gap-3">
              <Button variant="text" @click="reviewServerDraft">
                Review the draft: discards your edits
              </Button>
              <Button variant="text" @click="discardServerDraft">
                Keep your edits: discards the draft
              </Button>
            </span>
          </template>
        </StatusNote>

        <StatusNote v-else-if="serverDraft && !restoredFrom" :kind="serverDraft.valid ? 'info' : 'error'">
          Run <span class="mono">{{ serverDraft.run_id || 'unknown' }}</span> drafted this
          document for you.{{
            serverDraft.valid
              ? ' The editor is showing it, and Save applies it.'
              : ` It cannot be saved as it stands: ${serverDraft.error ?? 'no reason recorded'}. Fix it here, or discard it.`
          }}
          <template #hint>
            <Button variant="text" icon="close" label="Discard draft" @click="discardServerDraft">Discard draft</Button>
          </template>
        </StatusNote>

        <!-- A restore is a THIRD origin for this buffer, beside your own edits
             and a staged draft, so it says which one is on screen and offers
             every way out. "discard" is the file, not your edits -- those come
             back through the middle button, which is the only thing that has
             them. -->
        <StatusNote v-if="restoredFrom" kind="info">
          You are looking at the version saved
          {{ savedAt(restoredFrom) }}. Save to make it the current file.
          <template #hint>
            <span class="inline-flex items-center gap-3">
              <Button variant="text" @click="keepMyEdits">Go back to my edits</Button>
              <Button variant="text" @click="discardDraft">Discard and take the file</Button>
            </span>
          </template>
        </StatusNote>

        <StatusNote v-if="curation.anchorsAwaitingRebuild" kind="info">
          The anchors are saved. The graph still shows the previous version until it is rebuilt.
          <template #hint>
            <Button variant="text" icon="refresh-cw" label="Rebuild the graph" :disabled="rebuilding" @click="rebuild">
              {{ rebuilding ? 'Rebuilding…' : 'Rebuild the graph' }}
            </Button>
          </template>
        </StatusNote>

        <!-- A banner that ends in a command to type is a dead end: it asks the
             reader to leave the app and know an incantation, for the one action
             that cannot happen on its own. An auto-disabled source is excluded
             from every run, so nothing but this clears it. -->
        <StatusNote v-if="disabledSources.length" kind="error">
          {{ disabledSources.length }}
          {{ disabledSources.length === 1 ? 'source is' : 'sources are' }}
          switched off after repeated failures, and excluded from every run until
          switched back on.
          <template #hint>
            <span
              v-for="s in disabledSources"
              :key="s.id"
              class="inline-flex items-center gap-2 mr-3"
            >
              <span class="mono">{{ s.id }}</span>
              <span class="text-text-quiet">{{ s.last_error ?? 'no reason recorded' }}</span>
              <Button
                variant="outline"
                icon="refresh-cw"
                :disabled="enabling === s.id"
                @click="retrySource(s.id)"
              >
                try again
              </Button>
            </span>
          </template>
        </StatusNote>

        <!-- COLLAPSED, alone among the notices above it. The test is whether
             the block holds a control: the staged-draft choice, the rebuild and
             the switched-off sources each carry the one button that acts on
             them, and folding a button hides the fix. This is evidence for an
             edit you make below it, so it folds with its count on the summary
             and its list is fetched by the watcher rather than `@open`. -->
        <Disclosure
          v-if="rejectedShapes.length"
          label="Relationships Venona is not allowed to record"
          :hint="`${rejectedShapes.length}`"
        >
          <p class="mb-2 max-w-hint font-prose text-sm leading-normal text-text-tertiary">
            Venona keeps finding these but cannot record them. Allow one here and it will be
            recorded from the next run.
          </p>
          <span
              v-for="shape in rejectedShapes"
              :key="`${shape.predicate}:${shape.subject_type}:${shape.object_type}`"
              class="block mb-2"
            >
              <span class="mono">
                {{ shape.subject_type || '?' }} {{ predicateLabel(shape.predicate) }} →
                {{ shape.object_type || '?' }}
              </span>
              <span class="text-text-quiet">
                · refused {{ shape.n }}× across
                {{ shape.runs === 1 ? 'one run' : `${shape.runs} runs` }} · e.g.
                “{{ shape.example_subject }} → {{ shape.example_object }}” ·
              </span>
              <RouterLink class="underline text-text-quiet" :to="`/admin/runs/${shape.last_run_id}`">
                last run
              </RouterLink>
              <span class="block text-text-quiet">{{ shape.problem }}</span>
          </span>
        </Disclosure>

        <ConfigEditor
          v-model="draft"
          :doc="doc"
          :conflict="conflict"
          :notice="notice"
          :dirty="dirty"
          :diverged="draftDiverged"
          @take-disk="takeDisk"
          @discard="discardDraft"
        />

        <!-- BELOW the editor, which is where this app puts collapsed bulk, and
             `flex-none` so it costs one line closed and takes its space from
             the editor when opened. `config_service._prune_history` keeps
             twenty per document, and most documents have none. -->
        <Disclosure
          v-if="!doc.readonly"
          class="flex-none"
          label="Saved versions"
          :hint="snapshots.length ? String(snapshots.length) : ''"
          @open="loadHistory"
        >
          <StatusNote v-if="historyError" kind="error">{{ historyError }}</StatusNote>
          <p v-else-if="!snapshots.length" class="text-text-tertiary text-sm">
            Venona keeps the last twenty versions of this document. There are none yet:
            it has not been changed since Venona was set up here.
          </p>
          <ul v-else class="m-0 p-0 list-none">
            <li
              v-for="row in snapshots"
              :key="row.file"
              class="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-border-subtle py-2"
            >
              <span class="font-ui text-sm text-text-body">saved {{ savedAt(row) }}</span>
              <span class="mono text-xs text-text-quiet">{{ delta(row) }}</span>
              <!-- The hash, because two saves in one second share a timestamp
                   and an edit the same length as what it replaced shares a byte
                   count. Both are true of real snapshots in `config/.history`,
                   checked 2026-09-03. -->
              <span v-if="row.version" class="mono text-2xs text-text-quiet">{{ row.version }}</span>
              <Button
                class="ml-auto"
                variant="outline"
                icon="undo"
                :disabled="restoring"
                @click="chooseSnapshot(row)"
              >
                Restore
              </Button>
            </li>
          </ul>
          <p v-if="active === 'settings'" class="mt-2 max-w-hint text-text-tertiary text-xs">
            A version saved before a configuration update was applied cannot be restored as
            it stands, and Save will say so.
          </p>
          <p v-if="active === 'ontology'" class="mt-2 max-w-hint text-text-tertiary text-xs">
            Restoring this changes what the model is asked, so the readings Venona has
            already cached for it stop matching.
          </p>
        </Disclosure>
      </div>
    </PaneState>

    <Dialog
      :open="pendingRestore !== null"
      title="Restore this version?"
      confirm-label="Restore"
      confirm-icon="undo"
      destructive
      :busy="restoring"
      @update:open="pendingRestore = null"
      @confirm="pendingRestore && applyRestore(pendingRestore)"
    >
      <p v-if="serverDraft">
        <strong>A draft written for you by a run is on screen.</strong> Restoring replaces it,
        and the run that produced it will not write it again.
      </p>
      <p v-else>
        <strong>You have unsaved edits in this document.</strong> Restoring replaces them.
      </p>
      <p v-if="pendingRestore" class="preview pt-2 mono text-xs">
        saved {{ savedAt(pendingRestore) }} · {{ delta(pendingRestore) }}
      </p>
    </Dialog>

    <Dialog
      v-model:open="applyOpen"
      title="Load anchors into the graph?"
      confirm-label="Apply"
      confirm-icon="check"
      destructive
      :busy="applying"
      @confirm="confirmApply"
    >
      <p>
        This replaces the anchor relationships:
        <strong>every anchor connection is deleted and rebuilt</strong> from the file. Nothing
        else is touched, and the graph keeps showing the previous version until you rebuild it.
      </p>
      <p v-if="preview" class="preview pt-2 mono text-xs">
        {{ preview.entities }} names · {{ preview.edges }} connections · {{ preview.merges }} merges
      </p>
      <p v-else class="text-text-tertiary text-xs">Checking the file…</p>
    </Dialog>
  </ViewShell>
</template>
