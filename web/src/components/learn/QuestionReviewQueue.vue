<script setup lang="ts">
/**
 * Proposed study questions, one verdict per row. Model output is never
 * self-admitting (docs/adr/0157): a question enters learners' decks only
 * after Approve here. The grounding quote renders as a real quotation
 * because the run verified it verbatim before the row was ever written.
 */
import { onMounted } from 'vue'

import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import PaneState from '@/components/ui/PaneState.vue'
import Panel from '@/components/ui/Panel.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import { useLearnQuestionsStore } from '@/stores/learnQuestions'

const questions = useLearnQuestionsStore()

//: The generator's kind names, in reviewer words.
const KIND_LABELS: Record<string, string> = {
  qa: 'question',
  choice: 'multiple choice',
  cloze: 'fill the blank',
}

onMounted(() => void questions.load())
</script>

<template>
  <PaneState :loading="questions.loading && !questions.items.length" :error="null">
    <!-- The pane owns the scroll: the curation frame is scroll="none", so a
         queue without its own overflow container just clips past the fold.
         Same shape as MergeHistory's scroll box. -->
    <div class="min-h-0 overflow-y-auto flex flex-col gap-4">
      <StatusNote v-if="questions.error" kind="error">{{ questions.error }}</StatusNote>

      <Panel v-for="q in questions.items" :key="q.id" variant="bare">
        <div class="flex flex-col gap-2 border-b border-border-subtle py-4">
          <div class="flex items-center gap-2">
            <Chip size="sm" variant="bare" dot>{{ KIND_LABELS[q.kind] ?? q.kind }}</Chip>
          </div>
          <p class="text-sm text-text-primary">{{ q.prompt }}</p>
          <ul v-if="q.choices?.length" class="flex flex-wrap gap-2">
            <li v-for="choice in q.choices" :key="choice">
              <Chip size="sm" :tone="choice === q.answer ? 'ok' : 'neutral'">{{ choice }}</Chip>
            </li>
          </ul>
          <p v-else class="text-sm text-text-secondary">
            <span class="text-text-tertiary">→</span> {{ q.answer }}
          </p>
          <blockquote
            class="border-l border-border-default pl-3 font-prose text-sm leading-prose text-text-secondary"
          >
            {{ q.grounding_quote }}
          </blockquote>
          <div class="flex flex-wrap gap-2 pt-1">
            <Button
              variant="outline"
              icon="check"
              label="Approve"
              @click="questions.decide(q, 'approve')"
            >
              Approve
            </Button>
            <Button
              variant="outline"
              icon="ban"
              label="Reject"
              tone="critical"
              @click="questions.decide(q, 'reject')"
            >
              Reject
            </Button>
          </div>
        </div>
      </Panel>

      <EmptyState v-if="!questions.items.length && !questions.loading" title="no proposed questions">
        Generate questions writes proposals here; nothing reaches a learner's
        deck until it is approved.
      </EmptyState>
    </div>
  </PaneState>
</template>
