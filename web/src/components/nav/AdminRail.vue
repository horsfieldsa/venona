<script setup lang="ts">
/**
 * The one admin sidebar, shared by every admin page.
 *
 * Overview, People, Agents, Data, Notifications and Configuration -- what the
 * instance IS -- and then Curation and Runs, which is where it is worked.
 * `/settings` is the reader's own account and holds none of it.
 *
 * THE LAST TWO ARE THE ONLY ANNOTATED ROWS, and their two marks are not
 * interchangeable, so they must not be drawn the same way: a count is a
 * BACKLOG waiting for you and it is amber; the spine is work IN FLIGHT, and
 * its position IS the progress -- the same object `RunsView` draws at full
 * width, so it needs no learning. This rail is the only place either is shown,
 * because it is the only place either destination is named.
 *
 * IT LISTS DESTINATIONS, NOT DOCUMENTS. Twenty configuration documents are a
 * library, and a library belongs on a page that can give each entry its name,
 * its sentence and its age -- which is `/admin/config`. A rail can give a
 * document a row and a truncated title, so putting them here cost the four
 * destinations above them their place on a short window.
 *
 * NO `isAdmin()` GUARD ON THE FETCH, deliberately. Every route that mounts this
 * rail carries `meta.operator`, and `router/index.ts`'s guard redirects before
 * the component mounts -- so the check is unreachable and its comment would be
 * false. (A rail that also rendered on `/settings/account` would need one, to
 * keep a reader's 403 off the global forbidden toast.)
 */
import { computed } from 'vue'

import RailLink from '@/components/ui/RailLink.vue'
import Rail from '@/components/ui/Rail.vue'
import { useRunProgress } from '@/composables/useRunProgress'
import { useCurationStore } from '@/stores/curation'
import { useNotificationsStore } from '@/stores/notifications'

defineProps<{
  /** Which top-level destination is current. */
  current:
    | 'overview'
    | 'people'
    | 'agents'
    | 'data'
    | 'notifications'
    | 'model'
  | 'config'
    | 'curation'
    | 'runs'
    | 'setup'
}>()

const notifications = useNotificationsStore()
const curation = useCurationStore()

/** Polled app-wide by `useAppPolling`, never by a view -- a count that is only
 *  correct on the page it describes is worse than no count. */
const openConflicts = computed(() => curation.status?.open_conflicts ?? 0)

const { progress, progressLabel, lightCount } = useRunProgress()

/** A static map, not an interpolated class -- Tailwind scans source text, so
 *  `bg-${...}` is never generated and the failure looks like a data bug. */
function segmentClass(i: number): string {
  if (!progress.value) return ''
  if (i < progress.value.index) return 'bg-border-strong'
  if (i === progress.value.index) return 'bg-accent-primary animate-pulse-stage'
  return 'bg-surface-overlay'
}
</script>

<template>
  <Rail label="Admin" name="admin">
    <RailLink to="/admin" :active="current === 'overview'">Overview</RailLink>
    <RailLink to="/admin/people" :active="current === 'people'">People</RailLink>
    <RailLink to="/admin/agents" :active="current === 'agents'">Agents</RailLink>
    <RailLink to="/admin/data" :active="current === 'data'">Data</RailLink>
    <RailLink to="/admin/notifications" :active="current === 'notifications'">
      <span class="flex items-center justify-between gap-2">
        <span>Notifications</span>
        <!-- The chicken-and-egg mark. A struck-out destination is silent by
             design and the thing that broke is the TELLING, so nothing else in
             the app would report it. Never colour alone: the label names the
             count in words for a screen reader and for anyone who cannot see
             the hue. -->
        <span
          v-if="notifications.strickenOut"
          class="draft flex-none text-signal-critical"
          :aria-label="`${notifications.strickenOut} not receiving`"
          role="img"
          >•</span
        >
      </span>
    </RailLink>
    <RailLink to="/admin/model" :active="current === 'model'">Model</RailLink>
    <RailLink to="/admin/config" :active="current === 'config'">Configuration</RailLink>
    <RailLink to="/admin/curation" :active="current === 'curation'">
      <span class="flex items-center justify-between gap-2">
        <span>Curation</span>
        <!-- A backlog, so it states the number: "how many are waiting" is the
             only question this row raises, and a dot cannot answer it. -->
        <span v-if="openConflicts" class="badge mono text-2xs text-signal-high">{{
          openConflicts
        }}</span>
      </span>
    </RailLink>
    <RailLink to="/admin/runs" :active="current === 'runs'">
      <span class="flex items-center justify-between gap-2">
        <span>Runs</span>
        <!-- Never colour and motion alone: the spine carries the same sentence
             as a label for anyone who cannot see it advance. -->
        <span
          v-if="progress"
          class="spine flex gap-(--space-half) w-5 h-(--space-half)"
          role="img"
          :aria-label="progressLabel"
          :title="progressLabel"
        >
          <span
            v-for="i in progress.total"
            :key="i"
            class="seg flex-1"
            :class="segmentClass(i - 1)"
          />
        </span>
        <span
          v-else-if="lightCount"
          class="mono text-2xs text-text-tertiary"
          :title="`${lightCount} lookup${lightCount === 1 ? '' : 's'} in flight`"
          >{{ lightCount }}</span
        >
      </span>
    </RailLink>
    <RailLink to="/setup" :active="current === 'setup'">Domain setup</RailLink>
  </Rail>
</template>
