<script setup lang="ts">
/**
 * Confirm stopping one run, and say which one.
 *
 * Cancelling is the only press in the app that throws away work already done
 * and paid for, and the run it ends may be forty minutes in. It fired on a
 * single press until 2026-09-13, beside a Clear history button that has always
 * asked.
 *
 * **The run is NAMED, and that is the reason this is a component rather than a
 * `Dialog` written twice.** There are three places to press cancel now: the
 * header button, which acts on the pipeline, a row in the In progress table,
 * and a run's own page. A modal that asks "cancel this run?" beside three
 * live runs is asking about none of them.
 */
import { computed } from 'vue'

import Button from '@/components/ui/Button.vue'
import Dialog from '@/components/ui/Dialog.vue'

const props = defineProps<{
  open: boolean
  /** The run's name in the reader's words, from its type (`Name review`). */
  label?: string | null
  /** Which thing it is about, where a kind has one (`MOSA`). */
  subject?: string | null
}>()

defineEmits<{
  (e: 'update:open', value: boolean): void
  (e: 'confirm'): void
}>()

const name = computed(() => {
  const label = props.label?.trim() || 'This run'
  const subject = props.subject?.trim()
  return subject ? `${label}: ${subject}` : label
})
</script>

<template>
  <!-- The actions are spelled out rather than left to `Dialog`'s default pair:
       this is the one modal in the app where the dismiss button's own word is
       "Cancel", which here would mean the opposite of the question. -->
  <Dialog :open="open" title="Cancel this run?" @update:open="$emit('update:open', $event)">
    <p class="font-medium text-text-primary">{{ name }}</p>
    <p class="mt-2">
      Venona stops within a few seconds, giving up whatever it is in the middle
      of. Everything it has already read and saved is kept. It does not pick up
      where it left off; starting again starts from the beginning.
    </p>
    <template #actions>
      <Button variant="outline" @click="$emit('update:open', false)">Keep it running</Button>
      <Button tone="critical" icon="close" @click="$emit('confirm')">Cancel the run</Button>
    </template>
  </Dialog>
</template>
