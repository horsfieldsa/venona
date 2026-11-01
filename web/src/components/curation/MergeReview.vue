<script setup lang="ts">
/**
 * One pair at a time, decided by keyboard.
 *
 * A queue of 147 is 30+ minutes of copy-pasting ids at the CLI and about ten
 * minutes here (2026-08-06), and the difference is entirely that the evidence
 * is on screen: `Anduril Industries` vs `Anduril`, both active, both
 * PRODUCES/COMPETES_FOR, obviously one company.
 *
 * The default action depends on `kind`, and that is deliberate. Roughly 40% of
 * containment rows were CORRECT distinctions (2026-08-06) — `Thales` / `Thales
 * Australia`, `Air Force` / `Air Force Test Pilot School` — so merge must not
 * be the path of least resistance for them. `resolver_error` is our bug rather
 * than a curation decision at all, and says so instead of offering a merge.
 */
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import EntityCardBody from '@/components/entity/EntityCardBody.vue'
import { STATUS_TONE, predicateLabel, statusLabel, suggestionLabel } from '@/lib/curation'
import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import Kbd from '@/components/ui/Kbd.vue'
import Spinner from '@/components/ui/Spinner.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import { useKeyBindings } from '@/composables/useKeyboard'
import { emptyIntent, toGraphQuery } from '@/router/graphRoute'
import { useStaleGuard } from '@/composables/useStaleGuard'
import { useCurationStore } from '@/stores/curation'
import { useGraphStore } from '@/stores/graph'
import { useEntitiesStore } from '@/stores/entities'
import type { Card } from '@/types/brief'
import type { ConflictOptions } from '@/types/curation'

const curation = useCurationStore()
const entities = useEntitiesStore()
// The ontology's own type list, so a domain with different kinds offers its
// own. `loadOntology` returns early once it has one, so asking here costs a
// call only if no other view got there first.
const graph = useGraphStore()
void graph.loadOntology()

/** Which side survives. Defaults to the longer name — the expansion or the
 *  fuller company name — which is right far more often than not, and is one
 *  keystroke to flip when it is not. */
const survivorIsExisting = ref(true)
const router = useRouter()

/** The survivor of the last merge, so "did that land?" is one click rather
 *  than leaving, remembering a name and retyping it into the graph's search. */
function openSurvivorInGraph() {
  const last = curation.history[0]
  if (!last || last.action !== 'merged') return
  const survivor = last.conflict.existing_entity_id
  if (!survivor) return
  void router.push({
    name: 'graph',
    query: toGraphQuery({ ...emptyIntent('focus'), focusId: survivor }),
  })
}

const pair = computed(() => curation.current)

const existingCard = computed<Card | null>(() =>
  pair.value ? (entities.get(pair.value.existing_entity_id) ?? null) : null,
)
const proposedCard = computed<Card | null>(() =>
  pair.value ? (entities.get(pair.value.proposed_entity_id) ?? null) : null,
)

/**
 * The pair on screen always refetches itself when its cards are not in the
 * cache. The cards arrive by prefetch, and a prefetch is allowed to fail
 * silently or be invalidated from outside (a freshness tick during a run) --
 * without this the fallback is a spinner that nothing will ever resolve,
 * which is exactly how "the next card never loads" presented. `fetchOne`
 * de-duplicates in-flight requests, so re-firing on every recompute is one
 * request per missing card, not a stream.
 */
watch(
  [() => pair.value?.existing_entity_id, () => pair.value?.proposed_entity_id, existingCard, proposedCard],
  () => {
    const p = pair.value
    if (!p) return
    if (!existingCard.value) void entities.fetchOne(p.existing_entity_id)
    if (!proposedCard.value) void entities.fetchOne(p.proposed_entity_id)
  },
  { immediate: true },
)

const isError = computed(() => pair.value?.kind === 'resolver_error')

/** An anchor proposal is never a merge. The two entities are known to be
 *  different things -- the only question is how they relate -- so offering
 *  Merge here invites the one irreversible-feeling mistake in the queue. */
const isAnchor = computed(() => pair.value?.kind === 'anchor')

/** Two entities of different types that share a name -- a program and the
 *  platform it delivers. The ontology draws that line on purpose, so Merge is
 *  not on offer here either; the open question is whether one belongs to the
 *  other, or whether one of them simply needs a clearer name. */
const isCrossType = computed(() => pair.value?.kind === 'cross_type')

/** One thing filed under two kinds the ontology says are easy to confuse. The
 *  action is neither Merge nor Keep apart: name the kind, and both readings
 *  fold into a single entry under it. */
const isTypeConflict = computed(() => pair.value?.kind === 'type_conflict')

/** The two kinds on offer, plus every other kind this instance has -- because
 *  the right answer is sometimes neither side, which is exactly the case that
 *  needs an entry minting. */
const typeChoices = computed(() => {
  const onScreen = [existingCard.value?.type, proposedCard.value?.type].filter(
    Boolean,
  ) as string[]
  const all = (graph.ontology?.entity_types ?? []).map((t) => t.name)
  return [...new Set([...onScreen, ...all])]
})

async function doCorrectType(entityType: string) {
  if (!pair.value || curation.busy) return
  await curation.correctType(pair.value, entityType)
}

/** Merge is withheld for all three, for the same reason: the two sides are
 *  known to be different things before the curator reads them. Weakening this
 *  is what would turn a reading into a merge — ADR 0208. */
const isUnmergeable = computed(() => isAnchor.value || isCrossType.value || isTypeConflict.value)

/**
 * Clicking anywhere in a column picks that side -- except on a link.
 *
 * The card inside each column renders its sources, and those are the evidence
 * the curator opens to decide. A click that both follows the link and flips the
 * survivor changes the answer while you are checking it, and the change is not
 * visible from the tab you just opened.
 */
function chooseSide(event: MouseEvent, key: 'existing' | 'proposed') {
  if ((event.target as HTMLElement | null)?.closest('a')) return
  survivorIsExisting.value = key === 'existing'
}

/** The two candidates as DATA, so the template renders one column twice. Two
 *  hand-written columns are how the sides drift into different frames: a change
 *  to one does not reach the other, and nothing says so. */
const sides = computed(() => {
  const decided = !isError.value && !isUnmergeable.value
  const pairSides = [
    {
      key: 'existing' as const,
      card: existingCard.value,
      isSurvivor: decided && survivorIsExisting.value,
      label: isAnchor.value
        ? 'parent'
        : isCrossType.value
          ? ''
          : decided && survivorIsExisting.value
            ? 'survivor'
            : 'merges away',
    },
    {
      key: 'proposed' as const,
      card: proposedCard.value,
      isSurvivor: decided && !survivorIsExisting.value,
      label: isAnchor.value
        ? 'child'
        : isCrossType.value
          ? ''
          : decided && !survivorIsExisting.value
            ? 'survivor'
            : 'merges away',
    },
  ]

  // THE SURVIVOR IS ALWAYS THE LEFT COLUMN -- ADR 0236. Unmergeable pairs are
  // exempt: parent/child is a direction, not a survival, and `parent` reads
  // correctly on the left already.
  if (isUnmergeable.value) return pairSides
  return [...pairSides].sort((a, b) => Number(b.isSurvivor) - Number(a.isSurvivor))
})

/** The proposed action is filled, the alternatives are outlined. One function
 *  rather than the rule inline on each of the three buttons, which is three
 *  places for it to drift. */
function variantFor(action: 'merge' | 'part_of' | 'distinct') {
  return pair.value?.suggestion === action ? 'primary' : 'outline'
}

/** Pick the fuller name to survive when a new pair loads. */
watch(
  pair,
  (next) => {
    if (!next) return
    // A `part_of` suggestion names the CHILD, which fixes the direction: the
    // other side is the parent, so it is the one that would survive a merge and
    // the object of the relation.
    if (next.suggestion === 'part_of' && next.suggestion_child) {
      survivorIsExisting.value = next.suggestion_child !== next.existing_entity_id
      return
    }
    const a = next.existing_name ?? ''
    const b = next.proposed_name ?? ''
    survivorIsExisting.value = a.length >= b.length
  },
  { immediate: true },
)

const survivorId = computed(() =>
  survivorIsExisting.value ? pair.value?.existing_entity_id : pair.value?.proposed_entity_id,
)
const loserId = computed(() =>
  survivorIsExisting.value ? pair.value?.proposed_entity_id : pair.value?.existing_entity_id,
)

async function doMerge() {
  if (!pair.value || curation.busy || isError.value || isUnmergeable.value) return
  if (!survivorId.value || !loserId.value) return
  await curation.merge(pair.value, loserId.value, survivorId.value)
}

/** Predicates the ontology allows for this pair, in the chosen direction.
 *  Fetched rather than hardcoded so the options offered and the rules
 *  `check_relation` enforces cannot drift.
 *
 *  BOTH directions are held, and the visible list is DERIVED from
 *  `survivorIsExisting`. Picking one inside the `pair` watcher would not track
 *  it -- `survivorIsExisting` is not a source of that watcher -- so every swap
 *  (`x`, the Swap button, clicking the other card) would leave the list showing
 *  one direction while `doRelate` submitted the other. That is a 422 for a one-way predicate
 *  and, for a predicate legal both ways, a silently inverted relationship in
 *  the graph. */
const options = ref<ConflictOptions>({
  forward: [],
  reverse: [],
  subject_type: null,
  object_type: null,
})
/**
 * The predicate to write. `null` means "not chosen yet", and that has to be
 * representable: a `ref('PART_OF')` default submits the one predicate a country
 * can never be the object of, and the server answers 422 (ADR 0089). A guess
 * the server then refuses is worse than an empty control.
 */
const predicate = ref<string | null>(null)

// Guards against a slow response for a pair the curator has already left.
const optionsGuard = useStaleGuard()

/**
 * Whether `options` describes THIS pair yet.
 *
 * An empty predicate list is three different states -- in flight, failed, and
 * "the ontology genuinely allows nothing" -- and only the third has a sentence
 * to show. The watcher below clears `options` synchronously on every advance,
 * so gating the sentence on emptiness alone flashed "no relationship in the
 * ontology connects these to those this way round" for the length of a round
 * trip, on every pair, with the literal placeholder words in it because the
 * types had been cleared too. Then it reflowed away as the chips arrived,
 * moving the action bar under the cursor. On a failed request it never went
 * away at all.
 */
const optionsState = ref<'loading' | 'ready' | 'failed'>('loading')

watch(
  pair,
  async (next) => {
    const isCurrent = optionsGuard.claim()
    options.value = { forward: [], reverse: [], subject_type: null, object_type: null }
    optionsState.value = 'loading'
    if (!next) return
    const fetched = await curation.options(next)
    if (!isCurrent()) return
    optionsState.value = fetched ? 'ready' : 'failed'
    if (fetched) options.value = fetched
  },
  { immediate: true },
)

// `survivorIsExisting` doubles as the direction: the side NOT surviving is the
// child, which is the subject of PART_OF.
const predicates = computed(() =>
  ((survivorIsExisting.value ? options.value.reverse : options.value.forward) ?? []).map(
    (p) => p.name,
  ),
)

/**
 * Keep the choice legal, and prefer what the pipeline worked out.
 *
 * `suggestion_predicate` is the adjudicator's `part_of` verdict translated
 * through the ontology, and is NULL where several predicates fit and the call is
 * genuinely the curator's (ADR 0089). When it is NULL and more than one option
 * exists, NOTHING is preselected: Relate stays disabled until someone chooses,
 * which is the difference between asking a question and guessing an answer.
 *
 * A lone option is NOT preselected when the adjudicator suggested a merge.
 * COMPONENT_OF (ontology v4) made Platform/Platform pairs single-option, and
 * those containment pairs are overwhelmingly name-containment -- merges --
 * so preselecting would turn a mis-keyed `R` on a merge candidate into a
 * component claim, where before it was a safe no-op.
 */
watch(
  [predicates, pair],
  ([list]) => {
    if (predicate.value && list.includes(predicate.value)) return
    const suggested = pair.value?.suggestion_predicate
    if (suggested && list.includes(suggested)) {
      predicate.value = suggested
      return
    }
    const mergeSuggested = pair.value?.suggestion === 'merge'
    predicate.value = list.length === 1 && !mergeSuggested ? list[0] : null
  },
  { immediate: true },
)

async function doRelate() {
  if (!pair.value || curation.busy || isError.value) return
  // No predicate, no write. `R` reaches here from the keyboard whatever is on
  // screen, and the server defaults nothing either.
  if (!predicate.value) return
  // The child is the subject. `survivorIsExisting` already marks which side is
  // the parent, so the direction follows from the swap the curator can see.
  const subject = survivorIsExisting.value
    ? pair.value.proposed_entity_id
    : pair.value.existing_entity_id
  const object = survivorIsExisting.value
    ? pair.value.existing_entity_id
    : pair.value.proposed_entity_id
  await curation.relate(pair.value, subject, object, predicate.value)
}

/** What `R` would write, for the `?` overlay and nothing else. */
const relateLabel = computed(() =>
  predicate.value ? `Relate (${predicateLabel(predicate.value)})` : 'Relate',
)

/**
 * A digit per legal predicate: `1` selects the first, `2` the second.
 *
 * BOUND TO THE LIST, never to predicate names. `MANAGES` is legal for one pair
 * and not the next, and the list is whatever
 * `GET /curation/conflicts/{id}/options` returned for THIS pair -- so a
 * hardcoded `p` for PART_OF would be a dead key on most of the queue and, worse,
 * would keep firing after someone narrowed that predicate's range in the
 * ontology editor. Resolving the digit at PRESS time against the current list
 * means narrowing a range removes the key with it.
 *
 * IT ARMS, IT DOES NOT WRITE, because the caption is printed INSIDE the chip
 * and a caption's whole promise is "this key does what clicking this control
 * does". When the digit also wrote and dropped the pair, a curator disagreeing
 * with the preselected predicate and pressing `3` to move the selection --
 * exactly what clicking the chip captioned `3` does -- wrote a COMPETES_WITH
 * assertion and advanced the queue instead. Recoverable with `u`, and invisible
 * at speed because the next pair is already on screen.
 *
 * So the two gestures stay one apart: digits (or clicks) choose, `R` writes.
 * `m` and `k` are single-keystroke writes and stay that way -- they mean the
 * same thing on every pair, where `1` means whatever `/options` returned for
 * whichever pair is current.
 *
 * Nine, because past that a digit is slower to find than the chip it names.
 */
const DIGITS = ['1', '2', '3', '4', '5', '6', '7', '8', '9']

/** The key that selects predicate `name`, or nothing if it is past the ninth. */
function keyFor(name: string): string | null {
  const at = predicates.value.indexOf(name)
  return at >= 0 && at < DIGITS.length ? DIGITS[at] : null
}

/** Select the nth listed predicate. Same effect as clicking its chip, which is
 *  what the chip's own caption promises. */
function armNth(index: number) {
  const name = predicates.value[index]
  // No predicate at that position: the pair has fewer options than the digit
  // pressed. Silence, not a guess -- this is the "no dead keys" case.
  if (!name || !pair.value || isError.value) return
  predicate.value = name
}

/** Types, for the sentence explaining why there is no relationship to offer.
 *  They come from `/options`, which reports the RESOLVED types -- the same ones
 *  `check_relation` will judge against. */
const subjectTypeLabel = computed(() =>
  (survivorIsExisting.value ? options.value.object_type : options.value.subject_type) ?? 'these',
)
const objectTypeLabel = computed(() =>
  (survivorIsExisting.value ? options.value.subject_type : options.value.object_type) ?? 'those',
)

async function doKeepApart() {
  if (!pair.value || curation.busy) return
  await curation.keepApart(pair.value)
}

/**
 * Bindings go through the shared registry, not a `window` listener of this
 * component's own. `r` here means *relate* and `r` in `DefinitionQueue` means
 * *reject* -- both write to the database, and two `window` listeners cannot be
 * separated by `stopPropagation`. That pair is why the global keyboard layer
 * emits no bare letters at all (ADR 0070).
 */
useKeyBindings([
  { keys: ['m'], label: 'Merge', group: 'merges', run: () => void doMerge() },
  { keys: ['k'], label: 'Keep apart', group: 'merges', run: () => void doKeepApart() },
  // A GETTER, not `relateLabel.value`. Bindings are captured once, at
  // registration, so reading the computed here freezes the caption at whatever
  // the first pair armed -- "Relate (PART_OF)" for the rest of the session --
  // which is the drift the generated overlay exists to prevent.
  {
    keys: ['r'],
    label: () => relateLabel.value,
    group: 'merges',
    run: () => void doRelate(),
  },
  {
    keys: ['s', 'arrowright'],
    label: 'Skip',
    group: 'merges',
    // Navigation, so holding it is a legitimate way to move: the worst case is
    // landing further down the queue than intended, and `arrowleft` comes back.
    repeatable: true,
    run: () => curation.move(1),
  },
  {
    keys: ['arrowleft'],
    label: 'Back',
    group: 'merges',
    repeatable: true,
    run: () => curation.move(-1),
  },
  {
    keys: ['x'],
    label: 'Swap which side survives',
    group: 'merges',
    run: () => (survivorIsExisting.value = !survivorIsExisting.value),
  },
  { keys: ['u'], label: 'Undo the last decision', group: 'merges', run: () => void curation.undo() },
  // ONE binding carrying all nine keys, resolving the digit from the event --
  // the shape `DefinitionQueue` already uses. Nine separate bindings would need
  // eight blank labels to keep the overlay readable, and the overlay keys its
  // rows BY label (`useKeyboard.ts::keyboardGroups`).
  //
  // The label is generic because the predicate behind each digit changes with
  // every pair. The ACCURATE naming is on the chips themselves, which render
  // their own key from the same list -- so what a digit selects is legible at
  // the moment it matters rather than in an overlay describing a different pair.
  {
    keys: DIGITS,
    label: 'Choose the nth listed relationship',
    group: 'merges',
    run: (event) => armNth(DIGITS.indexOf(event.key)),
  },
])
</script>

<template>
  <div class="review flex flex-col gap-2 min-h-0 h-full">
    <StatusNote v-if="curation.error" kind="error">{{ curation.error }}</StatusNote>

    <div v-if="curation.loading" class="empty px-4 py-7 text-text-tertiary text-center flex items-center gap-2">
      <Spinner :size="14" /> loading the queue…
    </div>

    <div v-else-if="!pair" class="empty px-4 py-7 text-text-tertiary text-center">
      <p class="done text-lg text-text-secondary mt-0 mx-0 mb-1">Queue clear.</p>
      <p class="text-text-tertiary text-xs">Nothing left to decide. New conflicts appear after the next run.</p>
    </div>

    <template v-else>
      <!-- This component starts at the evidence: the kind badge, the surface and
           the position counter ride on `CurationView`'s filter row instead. -->

      <!-- The pipeline's proposal. Shown, never applied: token containment
           cannot separate "X Corp is the full name of X" from "X Division is
           part of X", so the model reads the names — and a curator still
           confirms, because a wrong edge is quieter than a wrong keystroke. -->
      <p v-if="pair.suggestion" class="proposal m-0 text-text-secondary flex-none text-xs flex items-center gap-2 items-baseline flex-wrap">
        <!-- Named with the BUTTON's word, not the model's. The verdict enum is
             merge / part_of / distinct, and "suggests distinct" leaves the
             reader to work out that the control is called "Keep apart". -->
        <Chip size="sm" :tone="pair.suggestion === 'distinct' ? 'neutral' : 'accent'">
          suggests {{ suggestionLabel(pair.suggestion) }}
        </Chip>
        <span v-if="pair.suggestion_note">{{ pair.suggestion_note }}</span>
      </p>
      <!-- The ontology allows no relationship between these two types in this
           direction. Saying so beats an absent control: `x` swaps the direction
           and may well produce one.

           Conditioned on the PREDICATE LIST, not on the model's suggestion, and
           on `optionsState` rather than `!predicates.length` (see the ref). This
           sentence above a row of PART_OF / PARTNERS_WITH / COMPETES_WITH chips
           is two contradictory claims about the ontology at once, and the
           sentence is the one a reader believes.

           `v-if`, not `v-else-if` after the proposal above: chained, the
           sentence becomes a property of the model having had no opinion, and a
           pair suggested `merge` between two types the ontology connects with
           nothing shows no Relate control and no reason for its absence. The
           two lines answer different questions. -->
      <p
        v-if="!isError && !isAnchor && optionsState === 'ready' && !predicates.length"
        class="guidance flex-none m-0 text-text-quiet text-xs"
      >
        Venona has no relationship that connects
        {{ subjectTypeLabel }} to {{ objectTypeLabel }} this way round; <Kbd>X</Kbd> to swap
      </p>

      <!-- `mt-4` is not decoration: the label tags sit ABOVE their frames' top
           edge, so at the container's own gap they collided with the model's
           verdict line directly above and the whole region read as one crowded
           block. -->
      <!-- ONE CARD for one decision: the two candidates side by side inside a
           single frame, split by a vertical hairline, with the actions below.
           The frame is what says "these two are one question"; the divider is
           what says "and you are choosing between them". Survivorship is carried
           INSIDE the card by the `survivor` tag and the ground on the chosen
           column, not by giving one side a different frame from the other. -->
      <div
        class="pair flex-1 min-h-0 mt-4 flex flex-col overflow-hidden border border-border-default rounded-lg bg-surface-inset"
      >
        <!-- The two candidates. `divide-x` draws the one hairline between them
             rather than a border on each, so there is never a doubled rule. -->
        <div class="grid grid-cols-2 max-md:grid-cols-1 divide-x max-md:divide-x-0 max-md:divide-y divide-border-subtle flex-1 min-h-0">
          <!-- The column is pointer convenience; the tag inside it is the
               control. A column that is itself a button nests the card's source
               links inside a control, and a link inside a control flips the
               survivor on the way to its own href -- so checking the evidence
               changes the answer being checked. -->
          <div
            v-for="side in sides"
            :key="side.key"
            class="side flex flex-col min-h-0 min-w-0 p-3 cursor-pointer transition-colors duration-fast ease-standard"
            :class="side.isSurvivor ? 'survivor bg-surface-selected' : 'bg-transparent hover:bg-surface-base'"
            @click="chooseSide($event, side.key)"
          >
            <!-- The tag is AMBER (`text-signal-high`), not the accent. The
                 accent means active, focus, or the primary action, and the
                 survivor is none of those: it is a CLAIM ABOUT THE DATA the
                 curator has to check before pressing anything. -->
            <!-- The column's top row: what this side IS on the left, what
                 Venona knows about it on the right. Both are annotations on the
                 whole card, so they share the corner rather than pushing the
                 evidence down a line each. -->
            <div class="side__head flex-none flex items-center justify-between gap-2 mb-2">
              <button
                type="button"
                class="side__tag font-ui text-2xs uppercase tracking-label cursor-pointer border-0 bg-transparent p-0"
                :class="side.isSurvivor ? 'text-signal-high' : 'text-text-quiet'"
                :aria-pressed="side.isSurvivor"
                @click.stop="survivorIsExisting = side.key === 'existing'"
              >
                {{ side.label }}
              </button>
              <Chip
                v-if="side.card?.status"
                size="sm"
                class="flex-none"
                :tone="STATUS_TONE[side.card.status] ?? 'neutral'"
              >
                {{ statusLabel(side.card.status) }}
              </Chip>
            </div>
            <div class="overflow-y-auto min-h-0">
              <EntityCardBody v-if="side.card" :card="side.card" compact />
              <div v-else class="side__loading text-text-tertiary text-sm flex items-center gap-2">
                <Spinner :size="12" /> loading…
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="actions flex-none pt-2 border-t border-t-border-subtle flex items-center gap-2 flex-wrap">
        <template v-if="isError">
          <span class="text-xs text-text-tertiary">
            No merge offered: Venona paired these by mistake. Skip it.
          </span>
        </template>
        <template v-else>
          <!-- Fixed labels. The target is already named on the card marked
               "survives", and putting a variable-length entity name in the
               button made it resize between pairs — so the control under the
               cursor moved, which is the one thing a keyboard queue must not
               do. -->
          <!-- Naming the kind IS the verdict here, so the choices sit where
               Merge would be. The two kinds already on screen come first,
               because one of them is usually right; the rest are there for the
               case where neither is and an entry has to be made. -->
          <template v-if="isTypeConflict">
            <span class="text-xs text-muted">it is a</span>
            <Button
              v-for="choice in typeChoices"
              :key="choice"
              variant="outline"
              :disabled="curation.busy"
              @click="doCorrectType(choice)"
            >
              {{ choice }}
            </Button>
          </template>
          <Button
            v-if="!isUnmergeable"
            :variant="variantFor('merge')"
            icon="merge"
            label="Merge"
            :disabled="curation.busy"
            @click="doMerge"
          >
            Merge <Kbd :muted="variantFor('merge') === 'primary'">M</Kbd>
          </Button>
          <!-- The predicate chips sit WITH the button they arm, not in a row of
               their own two thirds of a screen away: split, relating is two
               gestures separated by the whole card, and the chips cost a full
               row of the vertical budget the cards need. Together they are one
               control: choose, press, done. -->
          <template v-if="predicates.length">
            <span class="flex-none font-ui text-2xs uppercase tracking-label text-text-quiet">as</span>
            <!-- Each chip carries the key that SELECTS it, which is what
                 clicking the chip does -- the caption and the control it sits
                 in mean the same thing. Rendered from the same list the binding
                 resolves against, so a pair with two legal predicates shows two
                 keys and there is never a caption for a key that does nothing.
                 Writing is `R`, captioned on the Relate button beside them. -->
            <Chip
              v-for="p in predicates"
              :key="p"
              interactive
              size="sm"
              :selected="predicate === p"
              @click="predicate = p"
              >{{ predicateLabel(p) }}<Kbd v-if="keyFor(p)" :muted="predicate === p">{{ keyFor(p) }}</Kbd></Chip
            >
            <Button
              :variant="variantFor('part_of')"
              icon="waypoints"
              label="Relate"
              :disabled="curation.busy || !predicate"
              @click="doRelate"
            >
              Relate <Kbd :muted="variantFor('part_of') === 'primary'">R</Kbd>
            </Button>
          </template>
          <Button
            :variant="variantFor('distinct')"
            icon="split"
            label="Keep apart"
            :disabled="curation.busy"
            @click="doKeepApart"
          >
            Keep apart <Kbd :muted="variantFor('distinct') === 'primary'">K</Kbd>
          </Button>
        </template>

        <span class="grow min-w-0" />

        <!-- Modifiers and navigation, not decisions. Undo is RECOVERY, so it
             is not toned critical — the consequential buttons are above. -->
        <Button
          v-if="!isError"
          variant="text"
          label="Swap"
          @click="survivorIsExisting = !survivorIsExisting"
        >
          Swap <Kbd>X</Kbd>
        </Button>
        <Button variant="text" label="Skip" @click="curation.move(1)">
          Skip <Kbd>S</Kbd>
        </Button>
        <Button
          v-if="curation.history.length"
          variant="text"
          label="Undo"
          :disabled="curation.busy"
          @click="curation.undo()"
        >
          Undo <Kbd>U</Kbd>
        </Button>
      </div>

      <p v-if="curation.history.length" class="last m-0 text-xs text-text-tertiary">
        Last: {{ curation.history[0].action === 'merged' ? 'merged' : 'kept apart' }}
        “{{ curation.history[0].conflict.existing_name }}” /
        “{{ curation.history[0].conflict.proposed_name }}”
        <Button
          v-if="curation.history[0].action === 'merged' && curation.history[0].conflict"
          variant="text"
          icon="waypoints"
          label="Open in graph"
          @click="openSurvivorInGraph"
        >
          open in graph
        </Button>
      </p>
    </template>
  </div>
</template>
