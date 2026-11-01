<script setup lang="ts">
/**
 * The session loop: one card at a time, graded on the four FSRS keys.
 *
 * The QUEUE lives in the learn store (it must survive navigation); what
 * lives here is the transient per-card phase — front or revealed, and the
 * selected option on a choice card — which correctly resets to the front on
 * return. Grades submit optimistically: the store advances first and
 * re-queues the card at the tail if the write fails.
 */
import { computed, ref, watch } from 'vue'

import ChoiceQuestion from '@/components/learn/ChoiceQuestion.vue'
import FlashcardQuestion from '@/components/learn/FlashcardQuestion.vue'
import GradeBar from '@/components/learn/GradeBar.vue'
import StatusBar from '@/components/marks/StatusBar.vue'
import Button from '@/components/ui/Button.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Kbd from '@/components/ui/Kbd.vue'
import Panel from '@/components/ui/Panel.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import { useKeyBindings } from '@/composables/useKeyboard'
import { useLearnStore } from '@/stores/learn'
import { choiceRating, type Rating } from '@/types/learning'

const learn = useLearnStore()

const revealed = ref(false)
const selected = ref<string | null>(null)

// A new current card always opens on its front -- unless a graded choice is
// being HELD for its feedback beat: the optimistic submit advances the store
// immediately, so without the guard this fired mid-beat and wiped the
// verdict and the marked answer before they were ever seen.
watch(
  () => learn.current?.card_key,
  () => {
    if (held.value) return
    revealed.value = false
    selected.value = null
  },
)

const isChoice = computed(() => learn.current?.format === 'choice')

const progressSegments = computed(() => [
  { value: learn.position, color: 'var(--accent-primary)', title: 'reviewed' },
  { value: learn.remaining, color: 'var(--surface-selected)', title: 'remaining' },
])

function reveal() {
  if (!learn.current || isChoice.value) return
  revealed.value = true
}

function grade(rating: Rating) {
  const card = learn.current
  if (!card || !revealed.value || isChoice.value) return
  void learn.submitReview(card, rating)
}

function select(choice: string) {
  const card = learn.current
  if (!card || selected.value !== null) return
  selected.value = choice
  void learn.submitReview(card, choiceRating(choice === card.answer))
  // The store advanced; hold THIS card on screen for the feedback beat.
  held.value = card
}

/** The card kept visible through choice feedback after the store moved on. */
const held = ref<typeof learn.current>(null)
const showing = computed(() => held.value ?? learn.current)

function advance() {
  held.value = null
  selected.value = null
  revealed.value = false
}

function primaryAction() {
  if (held.value) return advance()
  if (!learn.current) return
  if (isChoice.value) return
  if (!revealed.value) return reveal()
}

function keyNumber(n: number) {
  const card = showing.value
  if (!card) return
  if (held.value) return
  if (isChoice.value) {
    const choice = card.choices?.[n - 1]
    if (choice) select(choice)
    return
  }
  if (revealed.value) grade(n as Rating)
}

useKeyBindings([
  {
    // `event.key` for the spacebar IS a space character -- the registry
    // matches on it verbatim, so 'space' would never fire.
    keys: [' ', 'enter'],
    label: 'Reveal / next card',
    group: 'learn',
    run: primaryAction,
  },
  ...([1, 2, 3, 4] as const).map((n) => ({
    keys: [String(n)],
    label: n === 1 ? 'Grade (1 Again … 4 Easy) / pick option' : '',
    group: 'learn',
    run: () => keyNumber(n),
  })),
])
</script>

<template>
  <div class="flex flex-col gap-4">
    <StatusBar
      v-if="learn.session.length"
      :segments="progressSegments"
      :label="`${learn.position} of ${learn.session.length} reviewed`"
    />

    <StatusNote v-if="learn.reviewError" kind="error">{{ learn.reviewError }}</StatusNote>

    <template v-if="showing">
      <!-- Orientation, only while everything is still new: before the first
           grade of the first day. After that the loop explains itself. -->
      <p
        v-if="learn.reviewedToday === 0 && learn.position === 0"
        class="text-xs text-text-secondary"
      >
        Every card comes from Venona's reading and can show its source. Reveal,
        grade yourself honestly, and each card returns just before you would
        forget it.
      </p>
      <Panel variant="raised" padded>
        <ChoiceQuestion v-if="showing.format === 'choice'" :card="showing" :selected="selected" @select="select" />
        <FlashcardQuestion v-else :card="showing" :revealed="revealed" />
      </Panel>

      <div v-if="held" class="flex justify-end">
        <Button variant="primary" @click="advance">
          <span class="flex items-center gap-2"><Kbd muted>space</Kbd> Next</span>
        </Button>
      </div>
      <div v-else-if="!isChoice && revealed" class="flex flex-col gap-2">
        <p class="text-2xs text-text-quiet">did you know it?</p>
        <GradeBar @grade="grade" />
      </div>
      <div v-else-if="!isChoice" class="flex justify-end">
        <Button variant="outline" @click="reveal">
          <span class="flex items-center gap-2"><Kbd muted>space</Kbd> Reveal</span>
        </Button>
      </div>
    </template>

    <EmptyState v-else-if="learn.finished && learn.session.length" title="done for today">
      {{ learn.session.length }} reviewed · streak {{ learn.streak }}
      <template v-if="learn.deckRemaining > 0" #action>
        <Button variant="outline" icon="play" @click="learn.continueStudying()">
          Keep studying · {{ learn.deckRemaining }} more in the deck
        </Button>
      </template>
    </EmptyState>
    <EmptyState v-else title="nothing due">
      {{
        learn.deckRemaining > 0
          ? 'Nothing needs review right now. New cards are ready whenever you are.'
          : 'New cards arrive as Venona defines more terms; reviews return as their intervals come round.'
      }}
      <template v-if="learn.deckRemaining > 0" #action>
        <Button variant="outline" icon="play" @click="learn.continueStudying()">
          Study new cards · {{ learn.deckRemaining }} available
        </Button>
      </template>
    </EmptyState>
  </div>
</template>
