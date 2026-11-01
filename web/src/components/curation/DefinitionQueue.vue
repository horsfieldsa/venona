<script setup lang="ts">
/**
 * Definitions review — one SURFACE at a time, converged on the merge queue.
 *
 * NOT a scrolling list with a cursor and flat keybindings. Three properties are
 * what make the merge queue work, and this needs all three:
 *
 *   * **the unit of work is a DECISION, not a record.** One row per EXPANSION
 *     puts `FCC` on screen twice and `CPE` five times, and makes "how much is
 *     left" a row count rather than a question count. A surface is the thing you
 *     decide about; its readings are what you decide between.
 *   * **a settled surface LEAVES.** `curation.merge()` calls `drop()` and the
 *     next pair is on screen. Calling `load()` refetches and keeps the row if it
 *     still matches the filter, so a surface you have just settled sits there
 *     looking untouched.
 *   * **it PROPOSES.** The merge queue fills the button matching the
 *     adjudicator's suggestion, so the common case is one keystroke. The signals
 *     for that are all here already — corroboration counts, the corpus check,
 *     the shape of the readings.
 *
 * So: `Enter` does the suggested thing, the verdicts on offer are only the ones
 * that apply to THIS surface, and every decision drops it and advances.
 *
 * The queue machinery is `stores/curation.ts`, not this file. Holding items,
 * cursor, loading, busy, error, history and drop/move/undo here would converge
 * on the merge queue's DESIGN while reimplementing its code, so any fix to how
 * a queue settles would have to be made twice. What belongs here is the
 * surface: which verdicts apply, what the keys do, and what a reading looks
 * like on screen.
 */
import { computed, nextTick, ref, watch } from 'vue'

import EntityPicker from '@/components/curation/EntityPicker.vue'
import Button from '@/components/ui/Button.vue'
import Dialog from '@/components/ui/Dialog.vue'
import Chip from '@/components/ui/Chip.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Kbd from '@/components/ui/Kbd.vue'
import Spinner from '@/components/ui/Spinner.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import { apiPatch, apiPost } from '@/api/client'
import { useDismissable, useKeyBindings } from '@/composables/useKeyboard'
import { useCurationStore } from '@/stores/curation'
import { useToastsStore } from '@/stores/toasts'
import Input from '@/components/ui/Input.vue'
import type { QueueReading } from '@/types/curation'

const curation = useCurationStore()
const toasts = useToastsStore()

/** Local because it is view state and nothing else can want it: which control
 *  is open, and what is half-typed into it. Everything that survives a verdict
 *  — the items, the cursor, the history, what is in flight — is the store's. */
const attaching = ref(false)
const editing = ref(false)
const draft = ref('')
const draftUrl = ref('')
// `InstanceType<typeof Input>`, not `HTMLInputElement`: a template ref on a
// component yields the component. It was annotated as the element, which
// type-checked while `.focus()` optional-chained into nothing. See `Input.vue`.
const editField = ref<InstanceType<typeof Input> | null>(null)

const item = computed(() => curation.currentDefinition)
const readings = computed(() => item.value?.readings ?? [])
const suggestion = computed(() => item.value?.suggestion ?? null)

/** The current surface's in-flight lookup, if any. While it is pending this
 *  surface's verdicts are frozen — its readings are about to change — but
 *  navigation and every other surface stay live. */
const currentPending = computed(() =>
  item.value ? curation.pendingLookups.get(item.value.surface) : undefined,
)

const METHOD_TONE: Record<string, 'ok' | 'accent' | 'info' | 'neutral'> = {
  in_article: 'ok',
  in_corpus: 'accent',
  web_grounded: 'info',
  manual: 'accent',
  undefined: 'neutral',
}

/** The wire's method enum, in reader words. Display only; a value the map has
 *  never met renders with its underscores opened up. */
const METHOD_LABEL: Record<string, string> = {
  in_article: 'in the article',
  in_corpus: "in Venona's reading",
  web_grounded: 'from the web',
  manual: 'by hand',
  undefined: 'undefined',
}

const verdict = curation.definitionVerdict

const call = {
  accept: (entry: QueueReading) =>
    apiPatch(`/glossary/${encodeURIComponent(entry.id)}`, { status: 'active' }),
  reject: () =>
    Promise.all(
      readings.value.length
        ? readings.value.map((r) => apiPatch(`/glossary/${encodeURIComponent(r.id)}`, { status: 'rejected' }))
        : [apiPatch(`/glossary/gloss:${item.value?.normalized.replace(/\s+/g, '-')}`, { status: 'rejected' })],
    ),
  choose: (entry: QueueReading) =>
    apiPost(`/glossary/${encodeURIComponent(item.value!.surface)}/choose`, { entry_id: entry.id }),
  keepBoth: () => apiPost(`/glossary/${encodeURIComponent(item.value!.surface)}/keep-both`, {}),
  sameThing: (entry: QueueReading) =>
    apiPost(`/glossary/${encodeURIComponent(item.value!.surface)}/same-thing`, {
      entry_id: entry.id,
    }),
  notAcronym: () =>
    apiPost(`/glossary/${encodeURIComponent(item.value!.surface)}/not-an-acronym`, {}),
}

function accept() {
  if (currentPending.value) return
  if (readings.value.length === 1) void verdict('accept', () => call.accept(readings.value[0]))
}
function choose(entry: QueueReading) {
  if (currentPending.value) return
  void verdict('choose', () => call.choose(entry))
}
function keepBoth() {
  if (currentPending.value) return
  if (readings.value.length > 1) void verdict('keep both', call.keepBoth)
}
function sameThing() {
  if (currentPending.value) return
  if (readings.value.length > 1) void verdict('same thing', () => call.sameThing(readings.value[0]))
}
function reject() {
  if (currentPending.value) return
  void verdict('reject', call.reject)
}

/**
 * Opens the confirmation; the verdict is recorded by `confirmNotAnAcronym`.
 *
 * `Dialog` is asynchronous — it renders and emits, it does not return a boolean
 * — so this cannot be a one-line guard the way a native `window.confirm` is.
 * That is
 * the point: the house dialog is themed, focus-trapped, keyboard-dismissable
 * and holds the key registry while it is open, none of which the browser's own
 * modal does.
 */
const confirmingIgnore = ref(false)

function notAnAcronym() {
  if (!item.value || currentPending.value) return
  confirmingIgnore.value = true
}

function confirmNotAnAcronym() {
  confirmingIgnore.value = false
  if (!item.value || currentPending.value) return
  void verdict('not an acronym', call.notAcronym)
}

async function attachTo(entity: { id: string; name: string }) {
  const surface = item.value?.surface
  if (!surface) return
  await verdict('entity', async () => {
    const result = await apiPost<{ promoted: boolean }>(
      `/glossary/${encodeURIComponent(surface)}/attach-entity`,
      { entity_id: entity.id },
    )
    attaching.value = false
    toasts.push({
      kind: 'success',
      text: result.promoted
        ? `${surface} now names ${entity.name}, and it is now active`
        : `${surface} now names ${entity.name}`,
    })
  })
}

function startEdit() {
  if (currentPending.value) return
  editing.value = true
  draft.value = readings.value[0]?.expansion ?? ''
  draftUrl.value = ''
  void nextTick(() => editField.value?.focus())
}

async function saveEdit() {
  const expansion = draft.value.trim()
  const url = draftUrl.value.trim()
  editing.value = false
  if (!expansion) return
  const entryId = readings.value[0]?.id ?? `gloss:${item.value!.normalized.replace(/\s+/g, '-')}`
  await verdict('define', () =>
    apiPatch(`/glossary/${encodeURIComponent(entryId)}`, {
      expansion,
      status: 'active',
      grounding_url: url || undefined,
    }),
  )
}

/** Do the suggested thing. The one keystroke this redesign is for. */
function applySuggestion() {
  const proposed = suggestion.value
  if (!proposed || !item.value || currentPending.value) return
  if (proposed.verdict === 'choose') {
    const target = readings.value.find((r) => r.id === proposed.entry_id) ?? readings.value[0]
    if (target) choose(target)
  } else if (proposed.verdict === 'same_thing') sameThing()
  else if (proposed.verdict === 'keep_both') keepBoth()
  else if (proposed.verdict === 'accept') accept()
  else if (proposed.verdict === 'attach_entity') attaching.value = true
  else if (proposed.verdict === 'lookup') void curation.lookupDefinition()
}

const SUGGESTION_LABEL: Record<string, string> = {
  choose: 'Use the stronger reading',
  same_thing: 'Fold them into one',
  keep_both: 'Keep them all',
  accept: 'Accept',
  attach_entity: 'It names a thing',
  lookup: 'Look it up',
}

function hostOf(url: string) {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

useKeyBindings([
  { keys: ['enter'], label: 'Do the suggested thing', group: 'definitions', run: applySuggestion },
  { keys: ['a'], label: 'Accept the definition', group: 'definitions', run: accept },
  { keys: ['b'], label: 'Keep every reading', group: 'definitions', run: keepBoth },
  { keys: ['s'], label: 'Same thing: fold into the strongest', group: 'definitions', run: sameThing },
  { keys: ['r'], label: 'Reject', group: 'definitions', run: reject },
  { keys: ['e'], label: 'Define by hand', group: 'definitions', run: startEdit },
  {
    keys: ['shift+e'],
    label: 'This term names a thing',
    group: 'definitions',
    run: () => {
      if (!currentPending.value) attaching.value = true
    },
  },
  {
    keys: ['1', '2', '3', '4', '5', '6', '7', '8', '9'],
    label: 'Pick one reading',
    group: 'definitions',
    run: (event) => {
      if (currentPending.value) return
      const chosen = readings.value[Number((event).key) - 1]
      if (chosen && readings.value.length > 1) choose(chosen)
    },
  },
  { keys: ['l'], label: 'Look it up on the web (~$0.13)', group: 'definitions', run: () => void curation.lookupDefinition() },
  // ORDER IN THIS ARRAY DOES NOT SEPARATE THESE FROM `l` AND `e`.
  // `useKeyboard::candidates` tries `shift+l` BEFORE `l`, so these sit beside
  // their siblings because that is where they read best. A registry that
  // ignored `shiftKey` would match each of these to its own bare letter, lose
  // to it by array position, and silently run a plain lookup -- about $0.13 --
  // in place of the re-lookup.
  {
    keys: ['shift+l'],
    label: 'Look again for other meanings',
    group: 'definitions',
    run: () => void curation.lookupDefinition(true),
  },
  { keys: ['n'], label: 'Not an acronym: ignore it', group: 'definitions', run: notAnAcronym },
  { keys: ['arrowright'], label: 'Skip', group: 'definitions', run: () => curation.moveDefinition(1) },
  { keys: ['arrowleft'], label: 'Back', group: 'definitions', run: () => curation.moveDefinition(-1) },
  { keys: ['u'], label: 'Undo the last verdict', group: 'definitions', run: () => void curation.undoDefinition() },
])

watch(item, () => (editing.value = false))

/**
 * Esc closes the editor, and ONLY the editor.
 *
 * It was `@keydown.esc.prevent` on the field, which had never once fired: the
 * registry listens on `window` in the CAPTURE phase and its `escape` binding is
 * `allowInInput`, so it calls `stopImmediatePropagation` before the event
 * reaches any input. Pressing Esc mid-edit popped whatever was topmost on the
 * dismissable stack instead -- exiting graph full screen, closing a hover card
 * -- and left the editor open. `GraphSearch.vue` documents this exact trap and
 * fixes it this way; the fix was never carried over here.
 */
const dismissable = useDismissable(() => (editing.value = false))
watch(editing, (open) => (open ? dismissable.push() : dismissable.pop()))
void curation.loadDefinitions()
void curation.seedPendingLookups()
</script>

<template>
  <div class="queue flex flex-col gap-3 min-h-0 h-full">
    <EntityPicker
      :open="attaching"
      :surface="item?.surface ?? ''"
      :busy="curation.busy"
      @update:open="attaching = $event"
      @choose="attachTo($event)"
    />

    <div class="flex-none flex items-baseline gap-2 flex-wrap">
      <span class="text-xs text-text-tertiary">
        {{ curation.definitionsPending }} term{{ curation.definitionsPending === 1 ? '' : 's' }} to decide
      </span>
      <span v-if="curation.definitions.length" class="text-xs text-text-tertiary">
        · {{ curation.definitionsCursor + 1 }} of {{ curation.definitions.length }} loaded
      </span>
      <span v-if="curation.pendingLookups.size" class="text-xs text-text-tertiary">
        · {{ curation.pendingLookups.size }} lookup{{ curation.pendingLookups.size === 1 ? '' : 's' }} in progress
      </span>
      <span class="grow min-w-0" />
      <Button
        v-if="curation.definitionsHistory.length"
        variant="text"
        :disabled="curation.busy"
        @click="curation.undoDefinition()"
      >
        Undo {{ curation.definitionsHistory[0].verdict }} <Kbd muted>U</Kbd>
      </Button>
      <Button variant="text" @click="curation.moveDefinition(1)">Skip <Kbd muted>→</Kbd></Button>
    </div>

    <StatusNote v-if="curation.error" kind="error">{{ curation.error }}</StatusNote>

    <div v-if="curation.definitionsLoading" class="px-4 py-6 text-text-tertiary flex items-center gap-2">
      <Spinner :size="14" /> loading…
    </div>

    <EmptyState v-else-if="!item" title="Nothing left to decide">
      Every acronym has been ruled on. New ones arrive with each run.
    </EmptyState>

    <!-- One surface, on the recessed surface the merge queue uses for its
         cards. The whole decision is on screen at once: what it is, what the
         evidence says, and only the verdicts that apply to it. -->
    <div
      v-else
      class="card flex-1 min-h-0 overflow-y-auto bg-surface-inset border border-border-subtle rounded-md p-4 flex flex-col gap-3"
    >
      <div class="flex items-baseline gap-2 flex-wrap">
        <span class="surface text-lg font-semibold text-text-primary mono">{{ item.surface }}</span>
        <span class="text-xs text-text-tertiary">
          {{ item.sightings }}× in {{ item.articles }} article(s)
        </span>
        <Chip v-if="item.attempted" size="sm" tone="neutral">
          looked up {{ item.attempt_count }}×
        </Chip>
        <Chip v-if="currentPending" size="sm" tone="info">
          <Spinner :size="12" />
          {{ currentPending.status === 'queued' ? 'lookup queued' : 'looking it up…' }}
        </Chip>
      </div>

      <!-- The proposal. It fills a button; it never presses one. -->
      <div
        v-if="suggestion"
        class="flex items-baseline gap-2 flex-wrap border border-accent-muted rounded-sm px-3 py-2"
      >
        <Button :disabled="curation.busy || !!currentPending" @click="applySuggestion()">
          {{ SUGGESTION_LABEL[suggestion.verdict] ?? suggestion.verdict }} <Kbd muted>⏎</Kbd>
        </Button>
        <span class="text-sm text-text-secondary">{{ suggestion.why }}</span>
      </div>

      <p v-if="!readings.length" class="m-0 text-sm text-text-tertiary italic">
        No sourced definition.
        <template v-if="item.attempted">
          A web lookup ran and found nothing that passed the checks.
        </template>
      </p>

      <!-- The readings, strongest evidence first, each with the sentence that
           grounded it. This is the merge queue's two-card layout applied to a
           list that can be longer than two. -->
      <div
        v-for="(reading, index) in readings"
        :key="reading.id"
        class="reading border-l-2 border-l-border-default pl-3 flex flex-col gap-1"
      >
        <div class="flex items-baseline gap-2 flex-wrap">
          <Kbd v-if="readings.length > 1 && index < 9">{{ index + 1 }}</Kbd>
          <span class="text-sm text-text-primary">{{ reading.expansion }}</span>
          <Chip v-if="reading.method" size="sm" :tone="METHOD_TONE[reading.method] ?? 'neutral'">
            {{ METHOD_LABEL[reading.method] ?? reading.method.replace(/_/g, ' ') }}
          </Chip>
          <span class="text-xs text-text-tertiary mono">
            {{ reading.confidence?.toFixed(2) ?? '—' }} · {{ reading.corroboration_count }}
            source{{ reading.corroboration_count === 1 ? '' : 's' }}
          </span>
          <Button
            v-if="readings.length > 1"
            variant="outline"
            icon="check"
            :disabled="curation.busy || !!currentPending"
            @click="choose(reading)"
          >
            only this
          </Button>
        </div>
        <blockquote
          v-if="reading.grounding?.quote"
          class="m-0 text-sm text-text-tertiary leading-normal"
        >
          {{ reading.grounding?.quote }}
        </blockquote>
        <a
          v-if="reading.grounding?.url"
          class="text-xs text-text-tertiary no-underline border-b border-b-border-default self-start"
          :href="reading.grounding!.url!"
          target="_blank"
          rel="noopener noreferrer"
        >
          {{ hostOf(reading.grounding!.url!) }}
        </a>
      </div>

      <div v-if="editing" class="flex flex-col gap-2">
        <Input
          ref="editField"
          v-model="draft"
          aria-label="Expansion"
          placeholder="what does it stand for?"
          @keydown.enter.prevent="saveEdit()"
        />
        <Input
          v-model="draftUrl"
          type="url"
          aria-label="Source URL"
          placeholder="source URL: a person may skip the sentence check; the record of who said so is kept"
        />
        <div class="flex items-center gap-2">
          <Button icon="check" label="Save" @click="saveEdit()">Save</Button>
          <Button variant="text" icon="close" label="Cancel" @click="editing = false">Cancel</Button>
        </div>
      </div>
    </div>

    <!-- Only the verdicts that apply to THIS surface: no Accept on a row with
         nothing to accept, no "choose" on a row with one reading. The merge
         queue renders no decision at all for a pair it cannot decide. -->
    <div
      v-if="item && !curation.definitionsLoading"
      class="actions flex-none pt-2 border-t border-t-border-subtle flex items-center gap-2 flex-wrap"
    >
      <Button
        v-if="readings.length === 1"
        icon="check"
        label="Accept"
        :disabled="curation.busy || !!currentPending"
        @click="accept()"
      >
        Accept <Kbd muted>A</Kbd>
      </Button>
      <Button
        v-if="readings.length > 1"
        icon="copy"
        :label="`Keep all ${readings.length}`"
        :disabled="curation.busy || !!currentPending"
        @click="keepBoth()"
      >
        Keep all {{ readings.length }} <Kbd muted>B</Kbd>
      </Button>
      <Button
        v-if="readings.length > 1"
        variant="outline"
        icon="merge"
        label="Same thing"
        :disabled="curation.busy || !!currentPending"
        @click="sameThing()"
      >
        Same thing <Kbd>S</Kbd>
      </Button>
      <Button
        v-if="!readings.length"
        icon="search"
        label="Look it up"
        :disabled="curation.busy || !!currentPending"
        @click="curation.lookupDefinition()"
      >
        Look it up <Kbd muted>L</Kbd>
      </Button>
      <Button
        v-if="readings.length"
        variant="outline"
        label="Look again"
        :disabled="curation.busy || !!currentPending"
        @click="curation.lookupDefinition(true)"
      >
        Look again <Kbd>⇧L</Kbd>
      </Button>
      <Button
        variant="outline"
        label="Define"
        :disabled="curation.busy || !!currentPending"
        @click="startEdit()"
      >
        Define <Kbd>E</Kbd>
      </Button>
      <Button
        variant="outline"
        label="It names a thing"
        :disabled="curation.busy || !!currentPending"
        @click="attaching = true"
      >
        It names a thing <Kbd>⇧E</Kbd>
      </Button>
      <Button
        variant="outline"
        label="Not an acronym"
        :disabled="curation.busy"
        @click="notAnAcronym()"
      >
        Not an acronym <Kbd>N</Kbd>
      </Button>
      <Button
        variant="outline"
        tone="critical"
        label="Reject"
        :disabled="curation.busy || !!currentPending"
        @click="reject()"
      >
        Reject <Kbd>R</Kbd>
      </Button>
      <span class="grow min-w-0" />
      <Spinner v-if="curation.busy" :size="12" />
    </div>

    <Dialog
      v-model:open="confirmingIgnore"
      title="Stop treating this as an acronym?"
      confirm-label="Stop detecting it"
      @confirm="confirmNotAnAcronym"
    >
      <strong>{{ item?.surface }}</strong> stops being read as an acronym
      anywhere. Venona keeps a record of the change, so you can put it back.
    </Dialog>
  </div>
</template>
