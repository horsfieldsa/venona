<script setup lang="ts">
import Rail from '@/components/ui/Rail.vue'
import RailLink from '@/components/ui/RailLink.vue'
import { NAV } from '@/router'

/**
 * The one operator sidebar, shared by every page.
 *
 * TWO ANNOTATED ROWS, and their marks are not interchangeable, so they must not
 * be drawn the same way. A count is a BACKLOG waiting for you; the dot is a
 * channel that has STOPPED, which is the one state nothing else on this surface
 * would report -- a struck-out destination is silent by design, and the thing
 * that broke is the telling.
 *
 * Never colour alone. The count states its number, and the dot carries an
 * `aria-label` naming the count in words for anyone who cannot see the hue.
 */
defineProps<{ current: string; pending: number; strickenOut: number }>()
</script>

<template>
  <Rail label="Admin" name="admin">
    <RailLink v-for="item in NAV" :key="item.key" :to="item.path" :active="current === item.key">
      <span class="flex items-center justify-between gap-2">
        <span>{{ item.label }}</span>
        <span
          v-if="item.key === 'requests' && pending"
          class="mono text-2xs text-signal-high"
          >{{ pending }}</span
        >
        <span
          v-else-if="item.key === 'notifications' && strickenOut"
          class="flex-none text-signal-critical"
          :aria-label="`${strickenOut} not receiving`"
          role="img"
          >&bull;</span
        >
      </span>
    </RailLink>
  </Rail>
</template>
