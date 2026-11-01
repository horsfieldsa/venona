<script setup lang="ts">
/**
 * A modal for decisions that need confirming. Replaces the native `confirm()`
 * the prompts editor used.
 *
 * Note that `confirm()` is SYNCHRONOUS -- it can guard with an early `return`
 * inside a handler -- and this is not. Any caller that used it as a guard needs
 * restructuring, not a drop-in swap.
 */
import { ref } from 'vue'

import Button from '@/components/ui/Button.vue'
import { toRef, useOverlay } from '@/composables/useOverlay'

const props = defineProps<{
  open: boolean
  title: string
  /** Label for the affirmative action. */
  confirmLabel?: string
  /** Name of a leading `Icon` on the affirmative action. */
  confirmIcon?: string
  /** Renders the affirmative action in the critical tone. */
  destructive?: boolean
  busy?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
  (e: 'confirm'): void
}>()

const panel = ref<HTMLElement | null>(null)

function close() {
  emit('update:open', false)
}

// A dialog asks a question, and until it is answered the keys belong to it.
// Without this, Tab to Cancel and press `a` and the surface behind the scrim
// records a verdict you never saw — a write nobody can attribute to a keystroke
// they aimed at a modal.
useOverlay(toRef(props, 'open'), panel, close, { ownsKeyboard: true })
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="scrim fixed inset-0 z-dialog flex items-center justify-center bg-scrim p-5"
      @click.self="close"
    >
      <!-- A COLUMN WITH A SCROLLING MIDDLE, not one block that grows.
           The panel is a centred flex item, so a tall one overflows the scrim
           at BOTH ends: the title goes off the top and the confirm button below
           the fold, with nothing to scroll because the scrim is `fixed` and the
           panel had no height limit of its own. At 390x844 that is a dialog
           that cannot be answered.

           The title and the actions stay put and the body takes the scroll, so
           the question and the buttons are always the two things on screen. -->
      <div
        ref="panel"
        class="dialog flex flex-col w-overlay-sm max-h-full p-4 border border-border-default rounded-lg bg-surface-overlay"
        role="dialog"
        aria-modal="true"
        :aria-label="title"
      >
        <h2 class="title shrink-0 text-md font-semibold text-text-primary mb-3">{{ title }}</h2>
        <div class="body min-h-0 overflow-y-auto text-sm text-text-secondary leading-normal">
          <slot />
        </div>
        <div class="actions shrink-0 flex items-center gap-2 mt-4 justify-end">
          <slot name="actions">
            <Button variant="outline" :disabled="busy" @click="close">Cancel</Button>
            <Button
              :tone="destructive ? 'critical' : 'default'"
              :icon="confirmIcon"
              :disabled="busy"
              @click="$emit('confirm')"
            >
              {{ confirmLabel ?? 'Confirm' }}
            </Button>
          </slot>
        </div>
      </div>
    </div>
  </Teleport>
</template>
