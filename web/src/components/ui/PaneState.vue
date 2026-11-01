<script setup lang="ts">
/**
 * ViewShell's loading/error swap, scoped to one pane.
 *
 * ViewShell's `:loading` replaces its WHOLE slot — right for a page that is a
 * single column, wrong for the railed pages: the admin and settings rails
 * render from stores and are valid before any fetch, yet every first load
 * blanked them along with the content, which read as the sidebar reloading.
 * Those views render the rail outside this component and the fetched content
 * inside, so the spinner and the error own only the pane they describe.
 *
 * Renders as a fragment in the content branch, so a flex/grid parent lays the
 * slotted children out exactly as if they were its own.
 */
import ApiUnreachable from '@/components/shared/ApiUnreachable.vue'
import Spinner from '@/components/ui/Spinner.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import { useServiceHealth } from '@/composables/useServiceHealth'

defineProps<{ loading?: boolean; error?: string | null }>()

const { apiDown } = useServiceHealth()
</script>

<template>
  <!-- `self-start`: a line at the top of the pane, like every other loading
       state. As a grid/flex item this div stretches to its siblings' height
       (the rail's), and `items-center` then reads as a spinner floating
       mid-pane rather than a section that is loading. -->
  <div v-if="loading" class="flex items-center gap-2 self-start">
    <Spinner /><span class="text-text-secondary">loading</span>
  </div>
  <!-- AN OUTAGE IS NOT THE PANE'S OWN FAILURE. Eleven railed pages hand their
       store's message straight here, so when the API stops answering each one
       reported the status line it caught beside a rail that was still working.
       The rail is the reason this is worth saying in the pane rather than over
       the page: the page IS still partly usable. -->
  <ApiUnreachable v-else-if="error && apiDown" class="self-start" />
  <StatusNote v-else-if="error" kind="error" class="self-start">{{ error }}</StatusNote>
  <slot v-else />
</template>
