<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

/**
 * The Claude Code session behind the running stage.
 *
 * A run spends most of its wall clock inside `claude` subprocesses — a full
 * night is ~230 analyze calls plus discovery sweeps that take minutes each. The
 * stage strip could only say "analyze, still going", which is indistinguishable
 * from "analyze, wedged". This shows what the model is actually doing.
 *
 * Deliberately NOT a chat UI. It is a tail: newest at the bottom, auto-scrolled
 * unless the operator has scrolled up to read something, bounded by the store.
 * Rendering is per-`kind` because a tool call and a paragraph of reasoning want
 * different weights — the point is to be scannable at a glance, not readable
 * word for word.
 */

import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import { useRunsStore } from '@/stores/runs'
import SectionHead from '@/components/ui/SectionHead.vue'

withDefaults(
  defineProps<{
    /** Drop the heading and the count when a parent already carries them --
     *  inside a `Disclosure`, whose summary IS the heading, they render the
     *  same two words twice, one line apart. */
    bare?: boolean
  }>(),
  { bare: false },
)

const runs = useRunsStore()
const scroller = ref<HTMLElement | null>(null)
// Auto-follow is off the moment the operator scrolls away from the bottom;
// yanking someone back to the tail while they are reading is the fastest way
// to make a live panel useless.
const following = ref(true)

const entries = computed(() => runs.transcript)

function onScroll() {
  const el = scroller.value
  if (!el) return
  following.value = el.scrollHeight - el.scrollTop - el.clientHeight < 32
}

// Watch the newest entry's `seq`, NOT `entries.length`. The store caps the
// transcript at TRANSCRIPT_LIMIT and splices off the front, so once a run
// passes 200 messages the length is pinned at 200 forever -- the watcher never
// fired again and auto-follow froze exactly when a long run needed it most.
// `seq` is monotonic for the life of a run and changes on every append.
watch(
  () => entries.value[entries.value.length - 1]?.seq,
  async () => {
    if (!following.value) return
    await nextTick()
    const el = scroller.value
    if (el) el.scrollTop = el.scrollHeight
  },
)

/**
 * The session line, saying only what the service actually reported.
 *
 * One names the model it chose and the tools it granted; another reports
 * neither, and only a thread id. Rendering the absent halves anyway produced
 * a leading separator and "0 tools" -- which reads as a session that was
 * granted none, rather than as a service that does not say.
 */
function sessionLine(entry: Record<string, unknown>): string {
  const parts: string[] = []
  if (typeof entry.model === 'string' && entry.model) parts.push(entry.model)
  if (Array.isArray(entry.tools)) parts.push(`${entry.tools.length} tools`)
  return parts.join(' · ') || 'session started'
}

const KIND_LABEL: Record<string, string> = {
  session_started: 'session',
  text: 'says',
  thinking: 'thinking',
  tool_use: 'calls',
  tool_result: 'result',
  rate_limit: 'rate limit',
}
</script>

<template>
  <div class="transcript flex flex-col min-h-0">
    <div v-if="!bare" class="head pb-2 flex items-center gap-2 items-center">
      <SectionHead class="title grow" label="Model session" />
      <!-- The run's message count, not the buffer's. `entries.length` pins at
           TRANSCRIPT_LIMIT once a long run passes it, so a 900-call run read
           "200" for the rest of its life. -->
      <Chip v-if="runs.transcriptTotal" tone="info">{{ runs.transcriptTotal }}</Chip>
      <!-- Said out loud when the two diverge, so "showing 200 of 900" is a
           stated fact rather than a silent truncation. -->
      <span v-if="runs.transcriptTotal > entries.length" class="text-2xs text-text-tertiary">
        last {{ entries.length }}
      </span>
      <!-- THE GLYPH ALONE. A downward arrow at the end of a scrolling
           transcript reads without a caption, and this row already carries a
           count, a "last N" and a heading. `label` is still set, so the
           accessible name and the tooltip say what it does. -->
      <Button
        v-if="!following"
        variant="outline"
        icon="down"
        label="Jump to live"
        class="ml-auto"
        @click="following = true; scroller && (scroller.scrollTop = scroller.scrollHeight)"
      />
    </div>

    <div ref="scroller" class="body max-h-(--scroll-lg) overflow-y-auto border border-border-subtle rounded-md bg-surface-inset p-2 overflow-y-auto" @scroll="onScroll">
      <!-- Two empty states, because "nothing yet" is a promise and a finished
           run is not going to keep it. -->
      <p v-if="!entries.length" class="empty text-text-tertiary text-sm m-0 p-2">
        {{
          bare
            ? 'Nothing was recorded for this run.'
            : 'Nothing yet. Messages appear here while a step is talking to the model.'
        }}
      </p>

      <div v-for="entry in entries" :key="entry.seq" class="entry py-2 border-b border-b-border-subtle last:border-b-0" :class="`kind-${entry.kind}`">
        <div class="meta text-xs text-text-tertiary flex items-center gap-2 items-baseline">
          <span class="task text-text-secondary">{{ entry.task }}</span>
          <span class="kind uppercase tracking-label">{{ KIND_LABEL[entry.kind] || entry.kind }}</span>
          <span v-if="entry.subagent" class="sub border border-border-subtle rounded-sm px-1">helper</span>
          <span v-if="entry.tool" class="tool border border-border-subtle rounded-sm px-1 text-accent-primary">{{ entry.tool }}</span>
        </div>

        <p v-if="entry.kind === 'session_started'" class="line dim m-0 mt-1 text-sm leading-normal [overflow-wrap:anywhere] text-text-tertiary">
          {{ sessionLine(entry) }}
        </p>
        <p v-else-if="entry.kind === 'rate_limit'" class="line warn m-0 mt-1 text-sm leading-normal [overflow-wrap:anywhere] text-signal-high">
          {{ (entry.limit_type || '').replace(/_/g, ' ') }} · {{ entry.status
          }}{{ entry.using_overage ? ' · over the plan limit' : '' }}
        </p>
        <p v-else-if="entry.kind === 'tool_use'" class="line mono m-0 mt-1 leading-normal [overflow-wrap:anywhere] text-xs text-text-secondary">{{ entry.input }}</p>
        <p v-else-if="entry.kind === 'tool_result'" class="line dim m-0 mt-1 text-sm leading-normal [overflow-wrap:anywhere] text-text-tertiary">
          <span v-if="entry.is_error" class="err text-signal-critical">error · </span>{{ entry.chars }} chars ·
          {{ entry.preview }}
        </p>
        <p
          v-else
          class="line m-0 mt-1 text-sm leading-normal [overflow-wrap:anywhere]"
          :class="entry.kind === 'thinking' ? 'text-text-secondary italic' : 'text-text-primary'"
        >
          {{ entry.text }}
        </p>
      </div>
    </div>
  </div>
</template>
