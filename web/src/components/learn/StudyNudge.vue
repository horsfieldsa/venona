<script setup lang="ts">
/**
 * The study nudge, as a sticky toast in the bottom-right stack.
 *
 * Renderless on purpose: this component decides WHEN to speak; the toast
 * host decides where and how, which is what makes the nudge reach a phone
 * and survive full screen -- the in-flow notice above the reading column
 * did neither, and sat on top of the first paragraph besides.
 *
 * Quiet in three directions: nothing ready says nothing, a count that
 * cannot load says nothing (an apologetic error where a hint would have
 * been is worse than silence), and one dismissal silences it for the day
 * on this device -- dismissing an invitation IS the answer, and the cards
 * are still waiting on the Learn page either way.
 */
import { onMounted, watch } from 'vue'

import { useLearnStore } from '@/stores/learn'
import { useToastsStore } from '@/stores/toasts'

const KEY = 'venona.study-nudge.1'

const learn = useLearnStore()
const toasts = useToastsStore()

function today(): string {
  return new Date().toISOString().slice(0, 10)
}

function dismissedToday(): boolean {
  try {
    return localStorage.getItem(KEY) === today()
  } catch {
    return true
  }
}

function rememberDismissal() {
  try {
    localStorage.setItem(KEY, today())
  } catch {
    /* private mode; the nudge simply offers again */
  }
}

// The stack holds one `study-nudge` at a time, so a remount adopts the toast
// already on screen rather than adding a second -- which matters because this
// component lives in a slot the brief tears down on every load, and because
// the day guard below only closes once the reader has answered.
let shownId: number | null = null

onMounted(() => void learn.loadCounts())

watch(
  () => learn.ready,
  (ready) => {
    if (shownId !== null || ready <= 0 || dismissedToday()) return
    shownId = toasts.push({
      kind: 'info',
      sticky: true,
      key: 'study-nudge',
      text: `${ready} card${ready === 1 ? '' : 's'} ready to study, drawn from what you are reading`,
      to: '/learn',
      toLabel: 'study',
    })
  },
  { immediate: true },
)

// The reader ANSWERED, which is not the same as the toast leaving.
//
// Every toast in the stack leaves on its own, so "gone" and "dealt with" are
// different facts. Closing the day on departure alone would let the CLOCK
// answer: look away for half a minute and the nudge is marked handled until
// tomorrow, having been read by nobody. A burst eviction says as little.
//
// Only the reader's own act closes the day -- the ×, or following the link,
// both of which dismiss with `manual`.
watch(
  () => toasts.lastDismissal,
  (dismissal) => {
    if (dismissal && dismissal.id === shownId && dismissal.reason === 'manual') {
      rememberDismissal()
    }
  },
  { deep: true },
)
</script>

<template>
  <span aria-hidden="true" class="hidden" />
</template>
