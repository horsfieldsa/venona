<script setup lang="ts">
/**
 * Multiple choice — used by the choice-rendered entity, gloss, relation and
 * generated-question cards alike. Select-then-feedback: the pick grades
 * itself (right = Good, wrong = Again), so after selection the correct
 * option is marked and the wrong pick, if any, marked against it. The
 * feedback colours are a static map on the two signal tones.
 */
import EntityCardBody from '@/components/entity/EntityCardBody.vue'
import Kbd from '@/components/ui/Kbd.vue'
import { predicateLabel, promptFor } from '@/lib/learnCopy'
import type { DeckCard } from '@/types/learning'

const props = defineProps<{
  card: DeckCard
  selected: string | null
}>()

const emit = defineEmits<{ (e: 'select', choice: string): void }>()

function pick(choice: string) {
  if (props.selected === null) emit('select', choice)
}

/** Wire values stay the identity; only relationship names get reader
 *  spelling (`PARTNERS_WITH` → `partners with`). */
function display(choice: string): string {
  return props.card.namespace === 'rel' ? predicateLabel(choice) : choice
}

function optionClass(choice: string): string {
  if (props.selected === null) {
    return 'border-border-default text-text-primary hover:bg-surface-inset cursor-pointer'
  }
  if (choice === props.card.answer) return 'border-signal-ok text-signal-ok'
  if (choice === props.selected) return 'border-signal-critical text-signal-critical'
  return 'border-border-subtle text-text-quiet'
}
</script>

<template>
  <div class="flex flex-col gap-4">
    <p v-if="promptFor(card)" class="text-xs text-text-tertiary">{{ promptFor(card) }}</p>
    <p class="font-prose text-md leading-prose text-text-primary">{{ card.front }}</p>

    <ol class="flex flex-col gap-2">
      <li v-for="(choice, index) in card.choices ?? []" :key="choice">
        <button type="button" class="flex w-full items-start gap-3 rounded-sm border px-3 py-2 text-left text-sm transition-colors duration-(--motion-base)" :class="optionClass(choice)" :disabled="selected !== null" @click="pick(choice)"><!-- token-lint: ok -- a quiz option, not a Button: full-width, multi-line, its border IS the feedback state -->
          <Kbd muted>{{ index + 1 }}</Kbd>
          <span class="min-w-0">{{ display(choice) }}</span>
        </button>
      </li>
    </ol>

    <template v-if="selected !== null">
      <!-- The verdict in words, not just border colour: colour alone is
           invisible to a colourblind reader and easy to miss for everyone
           else. -->
      <p
        class="text-sm font-semibold"
        :class="selected === card.answer ? 'text-signal-ok' : 'text-signal-critical'"
      >
        {{ selected === card.answer ? 'Correct.' : 'Not quite. The right answer is marked.' }}
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
