<script setup lang="ts">
/**
 * Find an entity and go to it.
 *
 * Standalone, not just the path finder's from/to picker: "where is Anduril in
 * here?" should not need an id and a hand-edited URL.
 *
 * Results carry `degree` and `mention_count` because a name alone does not let
 * you choose: "army" matches a dozen entities, and it is
 * "41 connections" versus "1 connection" that tells you which one you meant.
 */
import { ref, watch } from 'vue'

import { useDismissable } from '@/composables/useKeyboard'
import { useStaleGuard } from '@/composables/useStaleGuard'
import { entityColourVar } from '@/lib/entityColour'
import { useGraphStore } from '@/stores/graph'
import FilterField from '@/components/ui/FilterField.vue'
import type { GraphSearchHit } from '@/types/graph'

const emit = defineEmits<{ (e: 'pick', id: string): void }>()

const graph = useGraphStore()
const term = ref('')
const hits = ref<GraphSearchHit[]>([])
const busy = ref(false)
const open = ref(false)
const active = ref(-1)

const guard = useStaleGuard()

watch(term, async (value) => {
  const isCurrent = guard.claim()
  if (value.trim().length < 2) {
    hits.value = []
    open.value = false
    // Clearing the box back under two characters must stop the spinner too:
    // there is no request left to answer it.
    busy.value = false
    return
  }
  busy.value = true
  try {
    const found = await graph.search(value)
    // Out-of-order responses would otherwise show results for a prefix the user
    // has already typed past.
    if (!isCurrent()) return
    hits.value = found
    open.value = found.length > 0
    active.value = found.length ? 0 : -1
  } finally {
    // The spinner belongs to the newest request: a superseded one clearing it
    // would report the live search as finished.
    if (isCurrent()) busy.value = false
  }
})

function pick(hit: GraphSearchHit) {
  emit('pick', hit.id)
  term.value = ''
  hits.value = []
  open.value = false
}

function onKeydown(event: KeyboardEvent) {
  if (!open.value || !hits.value.length) return
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    active.value = (active.value + 1) % hits.value.length
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    active.value = (active.value - 1 + hits.value.length) % hits.value.length
  } else if (event.key === 'Enter' && active.value >= 0) {
    event.preventDefault()
    pick(hits.value[active.value])
  }
  // Escape is NOT handled here. It cannot be: the keyboard registry listens on
  // `window` in the CAPTURE phase and calls `stopImmediatePropagation`, so the
  // event never reaches this input, so a branch here can never fire. It would
  // be harmless only while nothing else is on the dismissable stack: with full
  // screen pushing onto it, Esc to close this dropdown would exit full screen
  // and reflow the canvas instead. So the dropdown registers properly, below.
}

const dismissable = useDismissable(() => (open.value = false))
watch(open, (isOpen) => (isOpen ? dismissable.push() : dismissable.pop()))

/** For `GraphView`'s `/`, which has to leave full screen before the field it is
 *  focusing exists on screen at all. */
const field = ref<InstanceType<typeof FilterField> | null>(null)
defineExpose({ focus: () => field.value?.focus() })
</script>

<template>
  <!-- The list anchors to the frame `FilterField` positions, which is why the
       dropdown needs nothing from this wrapper but a name to be found by. When
       the field is collapsed there is no frame and no list: `open` only ever
       becomes true through the field, which cannot be typed into until it is. -->
  <div class="search">
    <FilterField
      ref="field"
      v-model="term"
      collapsible
      label="Find an entity"
      placeholder="find an entity…"
      :busy="busy"
      @keydown="onKeydown"
      @focus="open = hits.length > 0"
    >
    <ul v-if="open" class="hits absolute z-overlay top-[calc(100%+var(--space-1))] left-0 right-0 m-0 p-1 list-none bg-surface-overlay border border-border-default rounded-md max-h-(--scroll-md) overflow-y-auto" role="listbox">
      <li
        v-for="(hit, index) in hits"
        :key="hit.id"
        role="option"
        :aria-selected="index === active"
        class="flex items-baseline gap-2 px-2 py-1 rounded-sm cursor-pointer text-sm"
        :class="{ 'active bg-surface-inset': index === active }"
        @mousedown.prevent="pick(hit)"
        @mouseenter="active = index"
      >
        <span class="dot w-(--dot-md) h-(--dot-md) rounded-[50%] flex-none" :style="{ background: entityColourVar({ id: hit.id, kind: 'entity' }) }" />
        <span class="name text-text-primary truncate">{{ hit.name }}</span>
        <span class="type text-text-tertiary text-xs">{{ hit.type }}</span>
        <span class="degree ml-auto text-text-tertiary text-xs tabular-nums whitespace-nowrap">
          {{ hit.degree ?? 0 }} link{{ (hit.degree ?? 0) === 1 ? '' : 's' }}
        </span>
      </li>
    </ul>
    </FilterField>
  </div>
</template>
