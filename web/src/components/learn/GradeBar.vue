<script setup lang="ts">
/**
 * The four FSRS ratings, as one bar. Key hints are drawn on the buttons
 * because a session lives on these four keys; the tones are a static map
 * (Tailwind never sees an interpolated class). Again wears critical because
 * it is the honest "I forgot", not a destructive act — the colour is a
 * signal, not a warning.
 */
import Button from '@/components/ui/Button.vue'
import Kbd from '@/components/ui/Kbd.vue'
import type { Rating } from '@/types/learning'

defineProps<{
  /** Next-interval hints per rating (e.g. "10m", "3d"), when known. */
  hints?: Partial<Record<Rating, string>> | null
}>()

const emit = defineEmits<{ (e: 'grade', rating: Rating): void }>()

const GRADES: { rating: Rating; label: string; variant: 'primary' | 'outline'; critical: boolean }[] = [
  { rating: 1, label: 'Again', variant: 'outline', critical: true },
  { rating: 2, label: 'Hard', variant: 'outline', critical: false },
  { rating: 3, label: 'Good', variant: 'primary', critical: false },
  { rating: 4, label: 'Easy', variant: 'outline', critical: false },
]
</script>

<template>
  <div class="grid w-full grid-cols-4 gap-2 max-xs:grid-cols-2">
    <Button
      v-for="grade in GRADES"
      :key="grade.rating"
      :variant="grade.variant"
      :tone="grade.critical ? 'critical' : 'default'"
      @click="emit('grade', grade.rating)"
    >
      <span class="flex items-center gap-2">
        <Kbd muted>{{ grade.rating }}</Kbd>
        {{ grade.label }}
      </span>
    </Button>
  </div>
</template>
