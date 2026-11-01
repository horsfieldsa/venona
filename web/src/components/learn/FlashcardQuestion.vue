<script setup lang="ts">
/**
 * The recall formats: a plain flashcard and a cloze. Both are
 * front-then-reveal; the difference is only what the front is — a term, or
 * the card's own grounding quote with the term blanked out.
 *
 * The reveal is an instant swap, no flip: the house motion budget is two
 * animations and a card flip is decoration, which also makes this
 * reduced-motion-safe by construction.
 */
import EntityCardBody from '@/components/entity/EntityCardBody.vue'
import Chip from '@/components/ui/Chip.vue'
import { entityColourVar } from '@/lib/entityColour'
import { cardTypeLabel, predicateLabel, promptFor } from '@/lib/learnCopy'
import type { DeckCard } from '@/types/learning'

const props = defineProps<{
  card: DeckCard
  revealed: boolean
}>()

function typeColour(): string | null {
  return props.card.card ? entityColourVar(props.card.card) : null
}

function answerLabel(): string | null {
  if (!props.card.answer) return null
  return props.card.namespace === 'rel' ? predicateLabel(props.card.answer) : props.card.answer
}
</script>

<template>
  <div class="flex flex-col gap-4">
    <div class="flex flex-col gap-2">
      <div class="flex items-center gap-3">
        <Chip
          v-if="
            cardTypeLabel(card.card?.type ?? card.card?.kind ?? card.namespace) &&
            !(revealed && card.card)
          "
          variant="bare"
          dot
          :style="typeColour() ? { color: typeColour()! } : undefined"
        >
          {{ cardTypeLabel(card.card?.type ?? card.card?.kind ?? card.namespace) }}
        </Chip>
        <span v-if="promptFor(card)" class="text-xs text-text-tertiary">
          {{ promptFor(card) }}
        </span>
      </div>

      <blockquote
        v-if="card.format === 'cloze'"
        class="border-l border-border-default pl-3 font-prose text-md leading-prose text-text-primary"
      >
        {{ card.front }}
      </blockquote>
      <p v-else class="text-xl font-semibold tracking-tight text-text-primary">{{ card.front }}</p>
    </div>

    <template v-if="revealed">
      <p v-if="answerLabel()" class="text-md text-text-primary">
        <span class="text-text-tertiary">→</span>
        {{ answerLabel() }}
      </p>
      <EntityCardBody v-if="card.card" :card="card.card" compact />
      <blockquote
        v-else-if="card.grounding?.quote"
        class="border-l border-border-default pl-3 font-prose text-sm leading-prose text-text-secondary"
      >
        {{ card.grounding.quote }}
      </blockquote>
    </template>
  </div>
</template>
