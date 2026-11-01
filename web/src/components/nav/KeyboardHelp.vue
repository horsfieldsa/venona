<script setup lang="ts">
import { ref } from 'vue'

/**
 * The `?` overlay, GENERATED from the keyboard registry.
 *
 * It replaces a hand-written two-column cheatsheet in `CurationView` that
 * listed keys as HTML. That version could drift from the handlers beside it and
 * had no way to show anything from another view; this one shows exactly what is
 * currently registered and nothing else.
 */

import Icon from '@/components/ui/Icon.vue'
import IconButton from '@/components/ui/IconButton.vue'
import Kbd from '@/components/ui/Kbd.vue'
import { displayKey, keyboardGroups } from '@/composables/useKeyboard'
import { toRef, useOverlay } from '@/composables/useOverlay'
import SectionHead from '@/components/ui/SectionHead.vue'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>()

const panel = ref<HTMLElement | null>(null)

function close() {
  emit('update:open', false)
}

/**
 * The overlay OWNS the keyboard while it is up (`ownsKeyboard`).
 *
 * The focus trap parks focus on a `<button>`, which is not editable, so without
 * this the capture-phase listener still reaches whatever view is mounted
 * behind the scrim. Someone opening `?` to find out what `M` does, and pressing
 * `M`, merged the pair underneath it. `CommandPalette` had this and this did
 * not; the only reason the palette needed it first is that people type into it.
 *
 * Scope 0 is exempt by design, so Esc and `?` still close this.
 */
useOverlay(toRef(props, 'open'), panel, close, { ownsKeyboard: true })
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="scrim fixed inset-0 z-dialog flex items-center justify-center bg-scrim p-5" @click.self="close">
      <div ref="panel" class="sheet w-overlay-lg max-h-[80vh] overflow-y-auto p-4 border border-border-default rounded-md bg-surface-overlay" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts">
        <header class="head flex items-center justify-between mb-4">
          <h2 class="title text-md font-semibold">Keyboard</h2>
          <IconButton label="Close" @click="close"><Icon name="close" :size="12" /></IconButton>
        </header>

        <div class="groups grid grid-cols-[repeat(auto-fit,minmax(260px,1fr))] gap-5">
          <section v-for="group in keyboardGroups" :key="group.name" class="group">
            <SectionHead as="h3" class="mb-2" :label="group.name" />
            <p v-for="binding in group.bindings" :key="binding.label" class="binding grid grid-cols-[7rem_1fr] gap-2 items-baseline mb-1 text-sm text-text-secondary">
              <span class="keys flex gap-1 flex-wrap">
                <Kbd v-for="combo in binding.keys" :key="combo">{{ displayKey(combo) }}</Kbd>
              </span>
              <span class="label">{{ binding.label }}</span>
            </p>
          </section>
        </div>
      </div>
    </div>
  </Teleport>
</template>
