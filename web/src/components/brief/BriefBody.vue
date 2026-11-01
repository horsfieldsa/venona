<script setup lang="ts">
import BriefBlockView from '@/components/brief/BriefBlock.vue'
import BriefItemView from '@/components/brief/BriefItem.vue'
import { useMentionEvents } from '@/composables/useMentionEvents'
import type { BriefSection } from '@/types/brief'
import SectionHead from '@/components/ui/SectionHead.vue'

/**
 * DELEGATED event handling, deliberately -- see `useMentionEvents` for the
 * argument. This container owns the body's six listeners; the masthead in
 * `BriefView` binds the same set for the title's mentions.
 */
const props = defineProps<{ sections: BriefSection[]; activeId?: string | null }>()

/** Ordinals run across the whole brief, not per section -- "item 14" has to
 *  mean the same thing in the Top N list as it does in the margin. */
function ordinalOf(sectionIndex: number, itemIndex: number): number {
  let n = 0
  for (let s = 0; s < sectionIndex; s++) n += props.sections[s].items?.length ?? 0
  return n + itemIndex + 1
}

const { onPointerOver, onPointerOut, onFocusIn, onFocusOut, onClick, onKeydown } =
  useMentionEvents()
</script>

<template>
  <div
    class="body prose"
    @mouseover="onPointerOver"
    @mouseout="onPointerOut"
    @focusin="onFocusIn"
    @focusout="onFocusOut"
    @click="onClick"
    @keydown="onKeydown"
  >
    <section v-for="(section, si) in sections" :key="si" class="section mb-7">
      <SectionHead
        v-if="section.heading"
        as="h2"
        class="section__heading mb-5"
        :label="section.heading"
        rule
      />
      <BriefItemView
        v-for="(item, ii) in section.items"
        :key="item.id"
        :item="item"
        :active-id="activeId"
        :ordinal="ordinalOf(si, ii)"
      />
      <!-- Narrative blocks: empty in the item format, kept so the pre-item
           archive still renders rather than showing a headed, empty section. -->
      <template v-for="block in section.blocks" :key="block.id">
        <BriefBlockView :block="block" :active-id="activeId" />
      </template>
    </section>
  </div>
</template>
