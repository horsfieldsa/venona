<script setup lang="ts">
/**
 * The vertical rail beside a full-bleed view: the prompts file list, the
 * graph's filter groups.
 *
 * Both were hand-rolled and had drifted -- one used a 2px left accent bar for
 * the active row and the other a border-bottom section rule, with different
 * label casing. Sections and rows are both supported because the two consumers
 * need different shapes from the same surface.
 *
 * Collapsing is the rail's own business: at `--rail-collapsed` it keeps a
 * chevron and, when given one, a badge -- a rail that vanishes without trace
 * leaves the user wondering what they lost.
 */
import IconButton from '@/components/ui/IconButton.vue'
import Icon from '@/components/ui/Icon.vue'
import SectionHead from '@/components/ui/SectionHead.vue'

withDefaults(
  defineProps<{
    side?: 'left' | 'right'
    collapsed?: boolean
    /** Wide variant for the graph's detail panel. */
    wide?: boolean
    /** Shown on the collapsed stub, e.g. the number of active filters. */
    badge?: number | null
    label?: string
  }>(),
  { side: 'left', collapsed: false, wide: false, badge: null, label: 'panel' },
)

defineEmits<{ (e: 'toggle'): void }>()
</script>

<template>
  <aside
    class="rail flex flex-col min-h-0 bg-surface-raised overflow-hidden transition-[width] duration-base ease-standard"
    :class="[
      `rail--${side}`,
      side === 'left' ? 'border-r border-r-border-subtle' : 'border-l border-l-border-subtle',
      { 'rail--collapsed': collapsed, 'rail--wide': wide },
    ]"
  >
    <div v-if="collapsed" class="stub flex flex-col items-center gap-2 pt-2">
      <IconButton size="sm" :label="`Show ${label}`" @click="$emit('toggle')">
        <Icon :name="side === 'left' ? 'right' : 'left'" :size="12" />
      </IconButton>
      <span v-if="badge" class="badge mono text-2xs text-accent-primary">{{ badge }}</span>
    </div>

    <div v-else class="body overflow-y-auto flex-1 min-h-0">
      <SectionHead class="head px-3 py-2" :label="label" rule>
        <IconButton size="sm" :label="`Hide ${label}`" @click="$emit('toggle')">
          <Icon :name="side === 'left' ? 'left' : 'right'" :size="12" />
        </IconButton>
      </SectionHead>
      <slot />
    </div>
  </aside>
</template>
